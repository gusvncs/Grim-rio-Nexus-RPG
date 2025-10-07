# grimorio/management/commands/sync_from_gmbinder.py
import os
import re
import yaml
import requests
from bs4 import BeautifulSoup, Tag
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from unidecode import unidecode
import markdown as md

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

def normalize_markdown(md_text: str) -> str:
    t = md_text.replace("\ufeff", "").replace("\u00A0", " ")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    # força '# Título' (se vier '#Título')
    t = re.sub(r'^(#{1,6})(?!\s)(.+)$', r'\1 \2', t, flags=re.M)
    # remove espaços à direita
    t = re.sub(r'^(#{1,6})\s+(.+?)\s*$', r'\1 \2', t, flags=re.M)
    return t

def md_to_html(markdown_text: str) -> str:
    return md.markdown(markdown_text, extensions=["extra", "sane_lists"])

def html_of(nodes) -> str:
    return "".join(str(n) for n in nodes if n is not None)

def slice_between_h1s(soup: BeautifulSoup, start_title: str, end_title: str | None):
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
    nodes, cur = [], node_h3.next_sibling
    while cur:
        if isinstance(cur, Tag) and cur.name in ("h1", "h2", "h3"):
            break
        nodes.append(cur)
        cur = cur.next_sibling
    return nodes

# --- Passagem 1: usa headings H4–H6 ---
def split_manual_and_runes_hx(nodes):
    manual_nodes, rune_sections = [], []
    in_runes, rname, rnodes = False, None, []

    for n in nodes:
        if isinstance(n, Tag) and re.match(r"^h[4-6]$", n.name or ""):
            title = n.get_text(" ", strip=True)
            if norm_text(title).startswith("RUNA "):
                # fecha runa anterior
                if rname is not None:
                    rune_sections.append((rname, rnodes))
                m = re.search(r"(?i)\bRUNA\s+(?:DE|DA|DO)\s+(.+)", title)
                rname = (m.group(1) if m else title).strip()
                rnodes = []
                in_runes = True
                continue
        (rnodes if in_runes else manual_nodes).append(n)

    if rname is not None:
        rune_sections.append((rname, rnodes))
    return manual_nodes, rune_sections

# --- Passagem 2 (fallback): racha por marcadores “RUNA DE/DA/DO …” em qualquer tag ---
def split_manual_and_runes_fallback(nodes):
    # Concatena bloco em um <div> e volta a iterar pelos filhos de topo,
    # detectando “cabeçalhos” por regex no texto (não importa a tag).
    tmp = BeautifulSoup("<div></div>", "html.parser")
    for n in nodes:
        tmp.div.append(n)

    manual_nodes, rune_sections = [], []
    in_runes, rname, rnodes = False, None, []

    def is_rune_header(tag: Tag) -> str | None:
        if not isinstance(tag, Tag):
            return None
        txt = tag.get_text(" ", strip=True)
        if not txt:
            return None
        m = re.match(r"(?i)^\s*RUNA\s+(?:DE|DA|DO)\s+(.+?)\s*$", txt)
        return m.group(1).strip() if m else None

    for child in list(tmp.div.children):
        if isinstance(child, Tag):
            nm = is_rune_header(child)
            if nm:
                # fecha runa anterior
                if rname is not None:
                    rune_sections.append((rname, rnodes))
                rname, rnodes = nm, []
                in_runes = True
                continue
        (rnodes if in_runes else manual_nodes).append(child)

    if rname is not None:
        rune_sections.append((rname, rnodes))

    return manual_nodes, rune_sections

def split_manual_and_runes(nodes):
    # 1ª tentativa: headings H4–H6
    manual_nodes, rune_sections = split_manual_and_runes_hx(nodes)
    # Heurística: se menos de 5 runas, refaça com fallback textual (resolve “Armadura Arcana” no Render)
    if len(rune_sections) < 5:
        manual_nodes, rune_sections = split_manual_and_runes_fallback(nodes)
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

def extract_spells_from_section(section_soup: BeautifulSoup, *, drop_zero_runes: bool = True):
    spells = []
    for h3 in section_soup.find_all("h3"):
        title = h3.get_text(" ", strip=True)
        if not title:
            continue
        nodes = collect_until_next_h3(h3)
        manual_nodes, rune_sections = split_manual_and_runes(nodes)

        rune_effects = {slugify(nm): html_of(ns).strip() for nm, ns in rune_sections}
        # limpa runas vazias (título detectado mas corpo vazio)
        rune_effects = {k: v for k, v in rune_effects.items() if v}

        if drop_zero_runes and not rune_effects:
            # Ignora magias “textuais” (lista explicativa, cabeçalhos auxiliares etc.)
            continue

        spells.append({
            "name": title,
            "slug": slugify(title),
            "manual_html": html_of(manual_nodes).strip(),
            "attributes": extract_attrs_from_manual(manual_nodes),
            "rune_effects": rune_effects,
        })
    return spells

def fetch_source_text(url: str, debug_fetch: bool = False) -> str:
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

    # GM Binder /source normalmente é text/plain (markdown)
    if "text/plain" in ctype or (not "<html" in text[:200].lower()):
        return text

    # Se for HTML, tenta extrair markdown de <pre>, <code>, <textarea> — fallback para body.
    soup = BeautifulSoup(text, "html.parser")
    for pre in soup.find_all("pre"):
        t = pre.get_text("\n", strip=False)
        if "#" in t or "MAGIAS ARCANAS" in t.upper():
            return t
    for code in soup.find_all("code"):
        t = code.get_text("\n", strip=False)
        if "#" in t or "MAGIAS ARCANAS" in t.upper():
            return t
    for ta in soup.find_all("textarea"):
        t = ta.get_text("\n", strip=False)
        if "#" in t or "MAGIAS ARCANAS" in t.upper():
            return t
    body = soup.body.get_text("\n", strip=False) if soup.body else text
    return body

class Command(BaseCommand):
    help = "Baixa o GM Binder (/source), normaliza Markdown, extrai magias (H3) e runas (H4+), e importa."

    def add_arguments(self, p):
        p.add_argument("--url", required=True, help="URL do GM Binder TERMINANDO em /source (markdown).")
        p.add_argument("--out-root", default="content", help="Pasta raiz para YAML (content/).")
        p.add_argument("--strict", action="store_true", help="Falha se houver runa desconhecida nos efeitos.")
        p.add_argument("--debug-fetch", action="store_true", help="Mostra info do download (headers, length).")
        p.add_argument("--debug-headings", action="store_true", help="Imprime H1/H2/H3 após parse.")

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

        spells_dir = os.path.join(out_root, "spells")
        runes_dir = os.path.join(out_root, "runes")
        os.makedirs(spells_dir, exist_ok=True)
        os.makedirs(runes_dir, exist_ok=True)

        self.stdout.write(self.style.NOTICE(f"Baixando (markdown): {url}"))
        raw_md = fetch_source_text(url, debug_fetch=dbg_fetch)
        md_clean = normalize_markdown(raw_md)

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

        # Recorte “MAGIAS ARCANAS” → “MAGIAS DIVINAS”
        try:
            section = slice_between_h1s(soup, "MAGIAS ARCANAS", "MAGIAS DIVINAS")
        except CommandError:
            # fallback: tentar parsear o raw como HTML direto
            soup2 = BeautifulSoup(raw_md, "html.parser")
            section = slice_between_h1s(soup2, "MAGIAS ARCANAS", "MAGIAS DIVINAS")

        # Extrai magias, descartando as com 0 runas
        spells = extract_spells_from_section(section, drop_zero_runes=True)
        if not spells:
            raise CommandError("Nenhuma magia encontrada dentro da seção 'MAGIAS ARCANAS'.")

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

        # Gera arquivos de runa (metadados mínimos) para as que apareceram
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
