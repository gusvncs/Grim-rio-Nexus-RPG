import os
import re
import yaml
import requests
from bs4 import BeautifulSoup, Tag, NavigableString
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from unidecode import unidecode
import markdown as md

# Títulos instrucionais que não são magias de fato e devem ser ignorados
SKIP_TITLES = {
    "APLIQUE RUNAS",
    "ESCOLHA A MAGIA",
    "DETERMINE O CÍRCULO",
    "DETERMINE O CIRCULO",
    "CONJURANDO MAGIAS",
    "DESCRIÇÃO DAS RUNAS",
    "DESCRICAO DAS RUNAS",
    "LISTA DE MAGIAS",
    "MAGIAS ARCANAS",
    "MAGIAS DIVINAS",
}

# -------------------- Utilidades de texto --------------------

def slugify(text: str) -> str:
    text = unidecode((text or "").strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")

def norm_text(txt: str) -> str:
    t = (txt or "").replace("\u00A0", " ").replace("\ufeff", "")
    t = unidecode(t)
    t = re.sub(r"\s+", " ", t).strip().upper()
    return t

def normalize_markdown(md_text: str) -> str:
    """
    Remove BOM/NBSP, normaliza quebras de linha e garante espaço após # em headings.
    Mantém o restante como está para preservar a formatação do manual.
    """
    t = md_text.replace("\ufeff", "").replace("\u00A0", " ")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    # Força '# Título' (se vier '#Título' sem espaço)
    t = re.sub(r'^(#{1,6})(?!\s)(.+)$', r'\1 \2', t, flags=re.M)
    # Remove espaços à direita nos headings
    t = re.sub(r'^(#{1,6})\s+(.+?)\s*$', r'\1 \2', t, flags=re.M)
    return t

def md_to_html(markdown_text: str) -> str:
    return md.markdown(markdown_text, extensions=["extra", "sane_lists"])

def html_of(nodes) -> str:
    return "".join(str(n) for n in nodes if n is not None)

# -------------------- Download do /source --------------------

def fetch_source_text(url: str, debug_fetch: bool = False) -> str:
    """
    Baixa o /source com User-Agent de navegador.
    Se vier HTML, tenta extrair Markdown de <pre>, <code>, <textarea>.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/127.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
    r = requests.get(url, timeout=60, headers=headers, allow_redirects=True)
    r.raise_for_status()

    ctype = r.headers.get("Content-Type", "")
    text = r.text or ""

    if debug_fetch:
        print("[fetch] status:", r.status_code)
        print("[fetch] content-type:", ctype)
        print("[fetch] length:", len(text))

    if "text/plain" in ctype or ("<html" not in text[:200].lower()):
        return text

    soup = BeautifulSoup(text, "html.parser")
    for tagname in ("pre", "code", "textarea"):
        for blk in soup.find_all(tagname):
            t = blk.get_text("\n", strip=False)
            # heurística mínima
            if "#" in t or "MAGIAS ARCANAS" in t.upper():
                if debug_fetch:
                    print(f"[fetch] markdown extraído de <{tagname}>")
                return t

    body = soup.body.get_text("\n", strip=False) if soup.body else text
    if debug_fetch:
        print("[fetch] fallback: devolvendo body text (pode não ser markdown real)")
    return body

# -------------------- Recorte de seções por headings --------------------

def slice_between_h1s(soup: BeautifulSoup, start_title: str, end_title: str | None):
    """Recorta a região entre H1 == start_title e H1 == end_title (se existir)."""
    heads = soup.find_all(re.compile(r"^h[1-6]$"))
    i0 = None
    for i, h in enumerate(heads):
        if h.name == "h1" and norm_text(h.get_text(" ", strip=True)) == norm_text(start_title):
            i0 = i
            break
    if i0 is None:
        raise CommandError(f"Não encontrei a seção '{start_title}'.")
    i1 = None
    if end_title:
        for j in range(i0 + 1, len(heads)):
            if heads[j].name == "h1" and norm_text(heads[j].get_text(" ", strip=True)) == norm_text(end_title):
                i1 = j
                break

    start_tag = heads[i0]
    end_tag = heads[i1] if i1 is not None else None

    nodes, cur = [], start_tag.next_sibling
    while cur and cur is not end_tag:
        nodes.append(cur)
        cur = cur.next_sibling

    tmp = BeautifulSoup("<div></div>", "html.parser")
    container = tmp.div
    for n in nodes:
        container.append(n)
    return tmp

def collect_until_next_h3(node_h3: Tag):
    """Coleta nós após este H3 até o próximo H3 (ou H1/H2)."""
    nodes, cur = [], node_h3.next_sibling
    while cur:
        if isinstance(cur, Tag) and cur.name in ("h1", "h2", "h3"):
            break
        nodes.append(cur)
        cur = cur.next_sibling
    return nodes

# -------------------- Detecção de runas --------------------

# regex para "Runa de/da/do X" (aceita com/sem negrito e com ponto/dois-pontos)
RUNA_LINE_RE = re.compile(
    r"""(?ix) ^ \s*
        (?: (\*\*|__) \s* )?      # início negrito opcional
        Runa \s+ (?: de | da | do ) \s+ (.+?)   # nome da runa
        (?: \s* (?:\*\*|__) )?    # fim negrito opcional
        \s* [\.:]? \s* $
    """
)

def is_runa_heading(tag: Tag) -> str | None:
    """Se tag for H4..H6 contendo 'Runa ...', retorna nome da runa; senão None."""
    if not (isinstance(tag, Tag) and tag.name in ("h4","h5","h6")):
        return None
    title = tag.get_text(" ", strip=True)
    m = re.search(r"(?i)\bRUNA\s+(?:DE|DA|DO)\s+(.+)", title or "")
    if m:
        return m.group(1).strip()
    tnorm = norm_text(title)
    if "RUNA " in tnorm:
        tail = title[title.upper().find("RUNA ")+5:].strip(" :.-")
        return tail if tail else title
    return None

def is_runa_bold_line(tag: Tag) -> str | None:
    """
    Detecta linha de parágrafo/lista cujo começo é '**Runa de X.** ...' (ou variantes).
    Retorna o nome da runa se casar, senão None.
    """
    if not (isinstance(tag, Tag) and tag.name in ("p","li")):
        return None
    txt = tag.get_text(" ", strip=True)
    m = RUNA_LINE_RE.match(txt or "")
    if m:
        return (m.group(2) or "").strip()
    return None

def remove_first_italic_paragraph(manual_nodes):
    """
    Remove o primeiro parágrafo de descrição em itálico logo após o título da magia.
    Aceita <em>…</em> ou <i>…</i>, inclusive quando o parágrafo tem só esse conteúdo.
    """
    for idx, n in enumerate(manual_nodes):
        if not isinstance(n, Tag):
            # pula strings em branco iniciais
            if isinstance(n, NavigableString) and str(n).strip() == "":
                continue
            # qualquer outra coisa interrompe
            return manual_nodes
        if n.name != "p":
            # se o primeiro elemento não for parágrafo, não removemos nada
            return manual_nodes
        # Checa se o parágrafo é inteiramente itálico (em/em+i) ou começa com itálico e nada mais relevante
        inner = [c for c in n.children if not (isinstance(c, NavigableString) and str(c).strip() == "")]
        if not inner:
            return manual_nodes
        if len(inner) == 1 and isinstance(inner[0], Tag) and inner[0].name in ("em", "i"):
            # Remove esse parágrafo
            del manual_nodes[idx]
            return manual_nodes
        # Caso seja <p><em>…</em></p> seguido só de separadores (brancos)
        if isinstance(inner[0], Tag) and inner[0].name in ("em","i"):
            # Garante que o restante não tem texto significativo
            rest_text = "".join(
                c for c in n.get_text("", strip=True)[len(inner[0].get_text("", strip=True)):]
            ).strip()
            # Mesmo se houver resto, a regra do cliente é retirar a descrição inicial:
            del manual_nodes[idx]
            return manual_nodes
        # Se o primeiro parágrafo já não é em itálico, não removemos
        return manual_nodes
    return manual_nodes

def split_manual_and_runes(nodes):
    """
    Separa:
      - manual_nodes: tudo antes da primeira runa (já sem o parágrafo em itálico inicial)
      - rune_sections: [(nome_runa, [nós html...]), ...]
    Robusto a lixo estrutural (||, tabelas, page/columnbreak, divs) varrendo DESCENDENTES.
    Também remove o parágrafo de descrição em itálico (se presente).
    """
    # 1) constrói um contêiner temporário com todos os nodes (para poder varrer descendentes)
    tmp = BeautifulSoup("<div></div>", "html.parser")
    cont = tmp.div
    for n in nodes:
        cont.append(n)

    # 2) coleta âncoras de "início de runa": h4..h6 “Runa …” OU p/li que comecem com “Runa de …”
    anchors = []
    for el in cont.descendants:
        if not isinstance(el, Tag):
            continue
        name = is_runa_heading(el)
        if name:
            anchors.append(("heading", el, name))
            continue
        name = is_runa_bold_line(el)
        if name:
            anchors.append(("boldline", el, name))

    # 3) manual_nodes = tudo ANTES da primeira âncora; em seguida removemos a descrição em itálico
    manual_nodes = []
    if anchors:
        first_anchor = anchors[0][1]
        for child in list(cont.children):
            if child is first_anchor:
                break
            manual_nodes.append(child.extract())
    else:
        manual_nodes = list(cont.children)

    manual_nodes = remove_first_italic_paragraph(manual_nodes)

    # 4) se não há âncoras, não há runas
    if not anchors:
        return manual_nodes, []

    # 5) para cada âncora, capturar até a próxima âncora
    rune_sections = []
    for idx, (_, anchor_tag, rune_name) in enumerate(anchors):
        next_anchor = anchors[idx + 1][1] if idx + 1 < len(anchors) else None
        if anchor_tag.parent is None:
            continue
        collected = []
        node = anchor_tag
        while node and node is not next_anchor:
            nxt = node.next_sibling
            collected.append(node.extract())
            node = nxt
        rune_sections.append((rune_name, collected))

    return manual_nodes, rune_sections

# -------------------- Extração das magias --------------------

def extract_spells_from_section(section_soup: BeautifulSoup, stdout=None):
    """
    Dentro da seção recortada, cada H3 é uma magia.
    - pula títulos instrucionais
    - se uma “magia” tiver 0 runas, NÃO importa
    - não extrai attributes (para evitar duplicação); mantemos a formatação do manual no manual_html
    """
    spells = []
    for h3 in section_soup.find_all("h3"):
        title = h3.get_text(" ", strip=True)
        if not title:
            continue
        tnorm = norm_text(title).lstrip("# ").strip()
        if tnorm in SKIP_TITLES or tnorm.startswith("#"):
            if stdout:
                stdout.write(f"  • pulando pseudo-magia: {title}")
            continue

        nodes = collect_until_next_h3(h3)
        manual_nodes, rune_sections = split_manual_and_runes(nodes)

        # se não detectou nenhuma runa, não importar
        if not rune_sections:
            if stdout:
                stdout.write(f"  ! {title} (sem runas) — ignorada")
            continue

        spells.append({
            "name": title,
            "slug": slugify(title),
            "manual_html": html_of(manual_nodes).strip(),
            "attributes": {},  # <- não duplicamos Execução/Alvo/etc; ficam só no manual_html
            "rune_effects": {slugify(nm): html_of(ns).strip() for nm, ns in rune_sections},
        })
    return spells

# -------------------- Pipeline principal --------------------

class Command(BaseCommand):
    help = "Baixa o GM Binder (/source), normaliza Markdown, extrai magias (H3) e runas (H4+ / linhas em negrito), e importa."

    def add_arguments(self, p):
        p.add_argument("--url", required=True, help="URL do GM Binder TERMINANDO em /source (markdown).")
        p.add_argument("--out-root", default="content", help="Pasta raiz para YAML (content/).")
        p.add_argument("--strict", action="store_true", help="Falha se houver runa desconhecida nos efeitos.")
        p.add_argument("--debug-fetch", action="store_true", help="Mostra info do download (headers, length).")
        p.add_argument("--debug-headings", action="store_true", help="Imprime H1/H2/H3 após parse.")
        p.add_argument("--debug-sample", action="store_true", help="Mostra amostra do /source normalizado.")

    def handle(self, *a, **o):
        url = (o.get("url") or os.getenv("GMBINDER_SOURCE_URL", "")).strip()
        if url.lower().startswith("value:"):
            url = url.split(":", 1)[1].strip()
        if "gmbinder.com/share/" in url and not url.rstrip("/").endswith("/source"):
            url = url.rstrip("/") + "/source"
        if not url.startswith(("http://", "https://")):
            raise CommandError(f"URL inválida: {url!r}")

        out_root = o["out_root"]
        strict = bool(o.get("strict"))
        dbg_fetch = bool(o.get("debug_fetch"))
        dbg_heads = bool(o.get("debug_headings"))
        dbg_sample = bool(o.get("debug_sample"))

        spells_dir = os.path.join(out_root, "spells")
        runes_dir = os.path.join(out_root, "runes")
        os.makedirs(spells_dir, exist_ok=True)
        os.makedirs(runes_dir, exist_ok=True)

        self.stdout.write(self.style.NOTICE(f"Baixando (markdown): {url}"))
        raw_md = fetch_source_text(url, debug_fetch=dbg_fetch)
        md_clean = normalize_markdown(raw_md)

        if dbg_sample:
            print("=== SAMPLE /source (limpo) ===")
            print(md_clean[:800])
            print("\n[contém 'MAGIAS ARCANAS'?]", "SIM" if "MAGIAS ARCANAS" in md_clean.upper() else "NÃO")
            print("=== FIM SAMPLE ===")

        # 1) Markdown -> HTML
        html = md_to_html(md_clean)
        soup = BeautifulSoup(html, "html.parser")
        heads = soup.find_all(re.compile(r"^h[1-6]$"))

        if dbg_heads:
            print("=== HEADINGS (após Markdown->HTML) ===")
            for h in heads:
                if h.name in ("h1", "h2", "h3"):
                    print(f"{h.name.upper()}: {h.get_text(' ', strip=True)}")
            print("=== FIM HEADINGS ===")

        # recorte Magias Arcanas → Magias Divinas
        try:
            section = slice_between_h1s(soup, "MAGIAS ARCANAS", "MAGIAS DIVINAS")
        except CommandError:
            # 2) fallback: bruto como HTML
            soup2 = BeautifulSoup(raw_md, "html.parser")
            section = slice_between_h1s(soup2, "MAGIAS ARCANAS", "MAGIAS DIVINAS")

        spells = extract_spells_from_section(section, stdout=self.stdout)
        if not spells:
            raise CommandError("Nenhuma magia encontrada dentro da seção 'MAGIAS ARCANAS'.")

        # Salva YAMLs e alimenta o import_content
        seen_runes = set()
        for sp in spells:
            y = {
                "slug": sp["slug"],
                "name": sp["name"],
                "school": "",
                "version": "1.0",
                "attributes": sp["attributes"],  # {} por decisão: manter só o manual_html
                "manual_html": sp["manual_html"],
                "rune_effects": sp["rune_effects"],
            }
            with open(os.path.join(spells_dir, f"{sp['slug']}.yml"), "w", encoding="utf-8") as f:
                yaml.safe_dump(y, f, sort_keys=False, allow_unicode=True)
            seen_runes.update(sp["rune_effects"].keys())
            self.stdout.write(f"  ✓ {sp['name']} ({len(sp['rune_effects'])} runas)")

        for rs in sorted(seen_runes):
            path = os.path.join(runes_dir, f"{rs}.yml")
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(
                        {
                            "slug": rs,
                            "name": rs.replace("-", " ").title(),
                            "description_html": "",
                            "domain": "",
                        },
                        f,
                        sort_keys=False,
                        allow_unicode=True,
                    )

        self.stdout.write(self.style.SUCCESS(f"YAML gerado em {out_root}/spells e {out_root}/runes"))
        call_command("import_content", content_root=out_root, strict=strict)
