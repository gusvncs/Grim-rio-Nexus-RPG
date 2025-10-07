# grimorio/management/commands/sync_from_gmbinder.py
import os
import re
import yaml
import requests
from bs4 import BeautifulSoup, Tag
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from unidecode import unidecode


# ------------------------
# Utilidades de texto/HTML
# ------------------------
ATTR_KEYS_PT = ["Execução", "Alcance", "Alvo", "Área", "Duração"]


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


def html_of(nodes) -> str:
    return "".join(str(n) for n in nodes if n is not None)


# ------------------------
# Download HTML renderizado
# ------------------------
def fetch_rendered_html(url: str, debug_fetch: bool = False) -> str:
    """
    Baixa a página 'bonita' do GM Binder (renderizada).
    Se vier '/source', removemos para garantir HTML.
    """
    u = url.strip()
    if u.rstrip("/").endswith("/source"):
        u = u.rstrip("/")[:-7]  # remove '/source'
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/127 Safari/537.36"
        ),
        "Accept": "text/html, */*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    }
    r = requests.get(u, timeout=60, headers=headers, allow_redirects=True)
    if debug_fetch:
        ctype = r.headers.get("Content-Type", "")
        print("[fetch] GET:", u)
        print("[fetch] status:", r.status_code, "content-type:", ctype, "len:", len(r.text))
    r.raise_for_status()
    return r.text


# ------------------------
# Recortes por headings
# ------------------------
def slice_between_h1s(soup: BeautifulSoup, start_title: str, end_title: str | None):
    """
    Retorna um mini-soup contendo tudo ENTRE H1=start_title (inclusive após ele)
    e H1=end_title (exclusivo). Compara títulos normalizados.
    """
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
    """
    Varre em ORDEM DE DOCUMENTO, coletando tudo depois de h3
    até encontrar um novo h1/h2/h3.
    Robusto a <div>, tabelas, \columnbreak etc.
    """
    nodes = []
    node = node_h3.next_element
    while node:
        if isinstance(node, Tag) and node.name in ("h1", "h2", "h3"):
            break
        nxt = node.next_element
        nodes.append(node)
        node = nxt
    return nodes


# ------------------------
# Separação manual/runas
# ------------------------
def split_manual_and_runes(nodes):
    """
    Separa:
      - manual_nodes: tudo antes da primeira runa
      - rune_sections: [(nome_runa, [nós html...]), ...]
    Detecta runas por:
      - H4..H6 contendo 'Runa ...'
      - Linha em negrito no início de <p>/<li> com 'Runa de/da/do ...'
    """
    def _is_runa_heading(tag: Tag) -> str | None:
        if not (isinstance(tag, Tag) and tag.name in ("h4", "h5", "h6")):
            return None
        title = tag.get_text(" ", strip=True)
        m = re.search(r"(?i)\bRUNA\s+(?:DE|DA|DO)\s+(.+)", title or "")
        if m:
            return m.group(1).strip()
        if "RUNA" in (title or "").upper():
            after = title.upper().split("RUNA", 1)[1]
            tail = title[-len(after):].strip(" :.-")
            return tail or title
        return None

    def _is_runa_boldline(tag: Tag) -> str | None:
        if not (isinstance(tag, Tag) and tag.name in ("p", "li")):
            return None
        first = None
        for c in tag.contents:
            if isinstance(c, Tag) or (isinstance(c, str) and c.strip()):
                first = c
                break
        if not isinstance(first, Tag) or first.name not in ("strong", "b"):
            return None
        txt = first.get_text(" ", strip=True)
        m = re.match(r"(?i)^\s*RUNA\s+(?:DE|DA|DO)\s+(.+?)[\.:]?\s*$", txt or "")
        return m.group(1).strip() if m else None

    # Contêiner temporário
    tmp = BeautifulSoup("<div></div>", "html.parser")
    cont = tmp.div
    for n in nodes:
        cont.append(n)

    # Âncoras em ordem de documento
    anchors = []
    for el in cont.descendants:
        if not isinstance(el, Tag):
            continue
        nm = _is_runa_heading(el)
        if nm:
            anchors.append((el, nm))
            continue
        nm = _is_runa_boldline(el)
        if nm:
            anchors.append((el, nm))

    # Manual = antes da primeira âncora
    if not anchors:
        return list(cont.children), []

    first_anchor = anchors[0][0]
    manual_nodes = []
    for child in list(cont.children):
        if child is first_anchor:
            break
        manual_nodes.append(child.extract())

    # Conteúdo de cada runa = da âncora até a próxima âncora
    rune_sections = []
    for i, (start_tag, rune_name) in enumerate(anchors):
        end_tag = anchors[i + 1][0] if i + 1 < len(anchors) else None
        if start_tag.parent is None:
            continue
        collected = []
        node = start_tag
        while node and node is not end_tag:
            nxt = node.next_sibling
            collected.append(node.extract())
            node = nxt
        rune_sections.append((rune_name, collected))

    return manual_nodes, rune_sections


def extract_attrs_from_manual(manual_nodes):
    text_flat = re.sub(
        r"\s+",
        " ",
        BeautifulSoup(html_of(manual_nodes), "html.parser").get_text(" ", strip=True),
    )
    attrs = {}
    for k in ATTR_KEYS_PT:
        m = re.search(rf"{k}\s*[:\.]\s*([^•\|\n]+?)(?=\s{{1,3}}[•\|]\s|$)", text_flat, flags=re.I)
        if m:
            attrs[k.lower()] = m.group(1).strip()
    return attrs


def extract_spells_from_section(section_soup: BeautifulSoup):
    """
    Para cada H3 dentro da seção, coleta bloco da magia, separa manual/runas
    e retorna a estrutura usada no import.
    """
    spells = []
    for h3 in section_soup.find_all("h3"):
        title = h3.get_text(" ", strip=True)
        if not title:
            continue

        nodes = collect_until_next_h3(h3)
        manual_nodes, rune_sections = split_manual_and_runes(nodes)

        spells.append({
            "name": title,
            "slug": slugify(title),
            "manual_html": html_of(manual_nodes).strip(),
            "attributes": extract_attrs_from_manual(manual_nodes),
            "rune_effects": {slugify(nm): html_of(ns).strip() for nm, ns in rune_sections},
        })
    return spells


# ------------------------
# Management Command
# ------------------------
class Command(BaseCommand):
    help = "Baixa o manual renderizado do GM Binder, extrai magias (H3) e runas (H4+/negrito), e importa no app."

    def add_arguments(self, p):
        p.add_argument("--url", required=True, help="URL pública do GM Binder (página renderizada; com ou sem /source).")
        p.add_argument("--out-root", default="content", help="Pasta raiz para YAML (content/).")
        p.add_argument("--strict", action="store_true", help="Falha se houver runa desconhecida nos efeitos.")
        p.add_argument("--debug-fetch", action="store_true", help="Mostra info do download (headers, length).")
        p.add_argument("--debug-headings", action="store_true", help="Imprime H1/H2/H3 após parse.")

    def handle(self, *a, **o):
        url = (o.get("url") or os.getenv("GMBINDER_SOURCE_URL", "")).strip()
        if url.lower().startswith("value:"):
            url = url.split(":", 1)[1].strip()
        if not url.startswith(("http://", "https://")):
            raise CommandError(f"URL inválida: {url!r}")

        out_root = o["out_root"]
        strict = bool(o.get("strict"))
        dbg_fetch = bool(o.get("debug_fetch"))
        dbg_heads = bool(o.get("debug_headings"))

        spells_dir = os.path.join(out_root, "spells")
        runes_dir = os.path.join(out_root, "runes")
        os.makedirs(spells_dir, exist_ok=True)
        os.makedirs(runes_dir, exist_ok=True)

        # 1) Baixar HTML renderizado
        self.stdout.write(self.style.NOTICE(f"Baixando (HTML): {url}"))
        html = fetch_rendered_html(url, debug_fetch=dbg_fetch)
        soup = BeautifulSoup(html, "html.parser")

        # 2) (Opcional) inspecionar headings
        if dbg_heads:
            heads = soup.find_all(re.compile(r"^h[1-6]$"))
            print("=== HEADINGS (HTML renderizado) ===")
            for h in heads:
                if h.name in ("h1", "h2", "h3"):
                    print(f"{h.name.upper()}: {h.get_text(' ', strip=True)}")
            print("=== FIM HEADINGS ===")

        # 3) Recortar seção H1 "MAGIAS ARCANAS" -> "MAGIAS DIVINAS"
        section = slice_between_h1s(soup, "MAGIAS ARCANAS", "MAGIAS DIVINAS")

        # 4) Extrair magias
        spells = extract_spells_from_section(section)
        if not spells:
            raise CommandError("Nenhuma magia encontrada dentro da seção 'MAGIAS ARCANAS'.")

        # 5) Gravar YAMLs (pulando magias sem runas)
        seen_runes = set()
        kept = 0
        skipped = 0

        for sp in spells:
            if not sp.get("rune_effects"):
                skipped += 1
                self.stdout.write(f"  ! {sp['name']} (0 runas) — ignorada")
                continue

            y = {
                "slug": sp["slug"],
                "name": sp["name"],
                "school": "",
                "version": "1.0",
                "attributes": sp["attributes"],
                "manual_html": sp["manual_html"],
                "rune_effects": sp["rune_effects"],
            }
            with open(os.path.join(spells_dir, f"{sp['slug']}.yml"), "w", encoding="utf-8") as f:
                yaml.safe_dump(y, f, sort_keys=False, allow_unicode=True)

            seen_runes.update(sp["rune_effects"].keys())
            kept += 1
            self.stdout.write(f"  ✓ {sp['name']} ({len(sp['rune_effects'])} runas)")

        # 6) Criar YAML stub para runas vistas (se não existir)
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

        self.stdout.write(self.style.SUCCESS(
            f"Importação de magias concluída: {kept} mantidas, {skipped} ignoradas (0 runas)."
        ))

        # 7) Importar para o DB
        call_command("import_content", content_root=out_root, strict=strict)
