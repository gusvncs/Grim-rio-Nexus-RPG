import os
import re
import yaml
import requests
from bs4 import BeautifulSoup, Tag
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from unidecode import unidecode
import markdown as md

# Campos que tentamos extrair do bloco manual (melhora a exibição)
ATTR_KEYS_PT = ["Execução", "Alcance", "Alvo", "Área", "Duração"]


def slugify(text: str) -> str:
    text = unidecode((text or "").strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")


def norm_text(txt: str) -> str:
    """Normaliza texto para comparação: tira acentos, troca nbsp, upper, comprime espaços."""
    t = (txt or "").replace("\u00A0", " ")
    t = unidecode(t)
    t = re.sub(r"\s+", " ", t).strip().upper()
    return t


def md_to_html(markdown_text: str) -> str:
    """Converte SEMPRE o /source (Markdown) em HTML estável para parse."""
    return md.markdown(markdown_text, extensions=["extra", "sane_lists"])


def html_of(nodes) -> str:
    return "".join(str(n) for n in nodes if n is not None)


def slice_between_h1s(soup: BeautifulSoup, start_title: str, end_title: str | None):
    """
    Recorta a região do documento entre H1 == start_title e H1 == end_title (se existir).
    Retorna a lista de tags nessa região (exclui o H1 inicial em si).
    """
    heads = soup.find_all(re.compile(r"^h[1-6]$"))
    # acha H1 'MAGIAS ARCANAS'
    i0 = None
    for i, h in enumerate(heads):
        if h.name == "h1" and norm_text(h.get_text(" ", strip=True)) == norm_text(start_title):
            i0 = i
            break
    if i0 is None:
        raise CommandError(f"Não encontrei a seção '{start_title}'.")
    # acha H1 'MAGIAS DIVINAS' (opcional)
    i1 = None
    if end_title:
        for j in range(i0 + 1, len(heads)):
            if heads[j].name == "h1" and norm_text(heads[j].get_text(" ", strip=True)) == norm_text(end_title):
                i1 = j
                break

    start_tag = heads[i0]
    end_tag = heads[i1] if i1 is not None else None

    # Coleta todos os irmãos entre o H1 inicial e o próximo H1 (ou fim do documento)
    nodes, cur = [], start_tag.next_sibling
    while cur and cur is not end_tag:
        nodes.append(cur)
        cur = cur.next_sibling
    # Retorna uma soup "temporária" contendo somente essa fatia
    tmp = BeautifulSoup("<div></div>", "html.parser")
    container = tmp.div
    for n in nodes:
        container.append(n)
    return tmp


def collect_until_next_h3(node_h3: Tag):
    """Coleta nós após este H3 até o próximo H3 (ou H1/H2, por segurança)."""
    nodes, cur = [], node_h3.next_sibling
    while cur:
        if isinstance(cur, Tag) and cur.name in ("h1", "h2", "h3"):
            break
        nodes.append(cur)
        cur = cur.next_sibling
    return nodes


def split_manual_and_runes(nodes):
    """
    Em uma magia, separa:
      - manual_nodes: conteúdo antes das runas
      - rune_sections: lista de (nome_da_runa, [nós HTML...]) para H4..H6 que comecem com 'Runa '
    """
    manual_nodes, rune_sections = [], []
    in_runes, rname, rnodes = False, None, []

    for n in nodes:
        if isinstance(n, Tag) and re.match(r"^h[4-6]$", n.name or ""):
            title = n.get_text(" ", strip=True)
            if norm_text(title).startswith("RUNA "):
                # fecha runa anterior
                if rname is not None:
                    rune_sections.append((rname, rnodes))
                # extrai o nome (“Runa de Fogo” -> “Fogo”)
                m = re.search(r"(?i)\bRUNA\s+(?:DE|DA|DO)\s+(.+)", title)
                rname = (m.group(1) if m else title).strip()
                rnodes = []
                in_runes = True
                continue
        (rnodes if in_runes else manual_nodes).append(n)

    if rname is not None:
        rune_sections.append((rname, rnodes))
    return manual_nodes, rune_sections


def extract_attrs_from_manual(manual_nodes):
    """Extrai Execução/Alcance/Alvo/Área/Duração (best-effort)."""
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


def extract_spells_from_markdown(md_text: str):
    """
    Pipeline rígido:
      1) Converte Markdown -> HTML
      2) Recorta somente a seção H1 == 'MAGIAS ARCANAS' até H1 == 'MAGIAS DIVINAS' (se houver)
      3) Dentro do recorte, cada H3 é uma magia; runas são H4..H6 iniciando por 'Runa ...'
    """
    html = md_to_html(md_text)
    soup = BeautifulSoup(html, "html.parser")
    section = slice_between_h1s(soup, "MAGIAS ARCANAS", "MAGIAS DIVINAS")  # se DIVINAS não existir, ok

    spells = []
    for h3 in section.find_all("h3"):
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


class Command(BaseCommand):
    help = "Baixa o markdown do GM Binder (/source), extrai magias e runas (H3/H4+) e importa."

    def add_arguments(self, p):
        p.add_argument("--url", required=True, help="URL do GM Binder TERMINANDO em /source (markdown).")
        p.add_argument("--out-root", default="content", help="Pasta raiz para YAML (content/).")
        p.add_argument("--strict", action="store_true", help="Falha se houver runa desconhecida nos efeitos.")
        p.add_argument("--debug-headings", action="store_true", help="Imprime H1/H2/H3 detectados após conversão de Markdown.")

    def handle(self, *a, **o):
        # URL sanitizada
        url = (o.get("url") or os.getenv("GMBINDER_SOURCE_URL", "")).strip()
        if url.lower().startswith("value:"):
            url = url.split(":", 1)[1].strip()
        if "gmbinder.com/share/" in url and not url.rstrip("/").endswith("/source"):
            url = url.rstrip("/") + "/source"
        if not url.startswith(("http://", "https://")):
            raise CommandError(f"URL inválida: {url!r}")

        out_root = o["out_root"]
        strict = bool(o.get("strict"))
        debug_headings = bool(o.get("debug_headings"))

        spells_dir = os.path.join(out_root, "spells")
        runes_dir = os.path.join(out_root, "runes")
        os.makedirs(spells_dir, exist_ok=True)
        os.makedirs(runes_dir, exist_ok=True)

        self.stdout.write(self.style.NOTICE(f"Baixando (markdown): {url}"))
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        md_text = r.text

        # (opcional) debug de headings após converter para HTML
        if debug_headings:
            html = md_to_html(md_text)
            soup = BeautifulSoup(html, "html.parser")
            heads = soup.find_all(re.compile(r"^h[1-6]$"))
            print("=== HEADINGS (após converter Markdown -> HTML) ===")
            for h in heads:
                if h.name in ("h1", "h2", "h3"):
                    print(f"{h.name.upper()}: {h.get_text(' ', strip=True)}")
            print("=== FIM HEADINGS ===")

        spells = extract_spells_from_markdown(md_text)
        if not spells:
            raise CommandError("Nenhuma magia encontrada dentro da seção 'MAGIAS ARCANAS' (H1).")

        # grava YAMLs e roda import_content
        seen_runes = set()
        for sp in spells:
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
