import os, re, yaml, requests, json
from bs4 import BeautifulSoup, Tag
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from unidecode import unidecode
import markdown as md

def add_arguments(self, p):
    p.add_argument("--url", required=True, help="URL do GM Binder (de preferência, o /source)")
    p.add_argument("--out-root", default="content", help="Pasta content/ para YAML")
    p.add_argument("--strict", action="store_true", help="Falhar se houver magia com runa desconhecida")
    p.add_argument("--debug-headings", action="store_true", help="Imprime os H1..H3 detectados (diagnóstico)")

def slugify(text: str) -> str:
    text = unidecode((text or "").strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")

def html_of(nodes):
    return "".join(str(n) for n in nodes if n is not None)

def ensure_html(text: str) -> str:
    """
    Se o conteúdo ainda não parece HTML (sem <h1..h6>),
    tenta converter de Markdown -> HTML.
    """
    sniff = text[:4000]
    if re.search(r"<h[1-6]\b", sniff, flags=re.I):
        return text  # já é HTML
    # Se vier Markdown cru, converte:
    return md.markdown(text, extensions=["extra", "sane_lists"])

def find_heading_bounds_as_html(html: str):
    """
    Localiza a região entre 'MAGIAS ARCANAS' e 'MAGIAS DIVINAS' em HTML.
    Retorna (heads, i0, i1) onde heads são os <h1..h6>, e i0/i1 são índices.
    """
    soup = BeautifulSoup(html, "html.parser")
    heads = soup.find_all(re.compile(r"^h[1-6]$"))
    tops = [unidecode(h.get_text(" ", strip=True)).upper() for h in heads]

    try:
        i0 = next(i for i, t in enumerate(tops) if "MAGIAS ARCANAS" in t)
    except StopIteration:
        raise CommandError("Não encontrei a seção 'MAGIAS ARCANAS'.")

    try:
        i1 = next(i for i, t in enumerate(tops[i0 + 1 :], start=i0 + 1) if "MAGIAS DIVINAS" in t)
    except StopIteration:
        i1 = len(heads)

    return soup, heads, i0, i1

def extract_spells_from_html(soup: BeautifulSoup, heads, i0: int, i1: int):
    spells = []
    for i in range(i0 + 1, i1):
        h = heads[i]
        if h.name != "h3":
            continue
        title = h.get_text(" ", strip=True)
        if "LISTA DE MAGIAS" in unidecode(title).upper():
            continue

        # pega nós até o próximo h3 (ou até "Magias Divinas")
        nodes, cur = [], h.next_sibling
        while cur and not (isinstance(cur, Tag) and cur.name == "h3"):
            if isinstance(cur, Tag) and cur.name in ("h1", "h2"):
                ttxt = unidecode(cur.get_text(" ", strip=True)).upper()
                if "MAGIAS DIVINAS" in ttxt:
                    break
            nodes.append(cur)
            cur = cur.next_sibling

        # separa manual x seções de runas
        manual_nodes, rune_sections = [], []
        in_runes, rname, rnodes = False, None, []
        for n in nodes:
            if isinstance(n, Tag) and re.match(r"^h[4-6]$", n.name or ""):
                txt = n.get_text(" ", strip=True)
                if "RUNA" in unidecode(txt).upper():
                    if rname is not None:
                        rune_sections.append((rname, rnodes))
                    m = re.search(r"RUNA\s+DE\s+(.+)", txt, flags=re.I)
                    rname = (m.group(1) if m else txt).strip()
                    rnodes = []
                    in_runes = True
                    continue
            (rnodes if in_runes else manual_nodes).append(n)
        if rname is not None:
            rune_sections.append((rname, rnodes))

        # atributos (best-effort)
        text_flat = re.sub(
            r"\s+",
            " ",
            BeautifulSoup(html_of(manual_nodes), "html.parser").get_text(" ", strip=True),
        )
        attrs, keys = {}, ["Execução", "Alcance", "Alvo", "Área", "Duração"]
        for k in keys:
            m = re.search(rf"{k}\s*[:\.]\s*([^•\|\n]+?)(?=\s{{1,3}}[•\|]\s|$)", text_flat, flags=re.I)
            if m:
                attrs[k.lower()] = m.group(1).strip()

        spells.append(
            {
                "name": title,
                "slug": slugify(title),
                "manual_html": html_of(manual_nodes).strip(),
                "attributes": attrs,
                "rune_effects": {slugify(nm): html_of(ns).strip() for nm, ns in rune_sections},
            }
        )
    return spells

def extract_spells(raw_text: str):
    """
    Aceita texto HTML ou Markdown.
    Converte para HTML se necessário e extrai as magias arcanas.
    """
    html = ensure_html(raw_text)
    soup, heads, i0, i1 = find_heading_bounds_as_html(html)
    return extract_spells_from_html(soup, heads, i0, i1)

class Command(BaseCommand):
    help = "Baixa o manual do GM Binder, extrai magias/efeitos e importa no app."

    def add_arguments(self, p):
        p.add_argument("--url", required=True, help="URL do GM Binder (de preferência, o /source)")
        p.add_argument("--out-root", default="content", help="Pasta content/ para YAML")
        p.add_argument("--strict", action="store_true", help="Falhar se houver magia com runa desconhecida")

    def handle(self, *a, **o):
        # sanitiza URL (evita 'Value: https://...' na env)
        url = (o.get("url") or os.getenv("GMBINDER_SOURCE_URL", "")).strip()
        if url.lower().startswith("value:"):
            url = url.split(":", 1)[1].strip()

        # completa /source se faltar
        if "gmbinder.com/share/" in url and "/source" not in url:
            url = url.rstrip("/") + "/source"

        if not url.startswith(("http://", "https://")):
            raise CommandError(f"URL inválida: {url!r}")

        out_root = o["out_root"]
        strict = o["strict"]

        spells_dir = os.path.join(out_root, "spells")
        runes_dir  = os.path.join(out_root, "runes")
        os.makedirs(spells_dir, exist_ok=True)
        os.makedirs(runes_dir,  exist_ok=True)

        self.stdout.write(self.style.NOTICE(f"Baixando: {url}"))
        r = requests.get(url, timeout=60)
        r.raise_for_status()

        spells = extract_spells(r.text)
        if not spells:
            raise CommandError("Nenhuma magia encontrada; verifique a estrutura do manual.")

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
            self.stdout.write(f"  • {sp['name']} ({len(sp['rune_effects'])} runas)")

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

r = requests.get(url, timeout=60)
r.raise_for_status()
raw = r.text

if o.get("debug_headings"):
    from bs4 import BeautifulSoup
    import re, textwrap
    # Garante HTML (mesma função que você já usa internamente)
    def ensure_html(text):
        import markdown as md
        if re.search(r"<h[1-6]\b", text[:4000], flags=re.I):
            return text
        return md.markdown(text, extensions=["extra", "sane_lists"])
    html = ensure_html(raw)
    soup = BeautifulSoup(html, "html.parser")
    heads = soup.find_all(re.compile(r"^h[1-6]$"))
    print("=== HEADINGS DETECTADOS (até H3) ===")
    for h in heads:
        level = h.name
        txt = h.get_text(" ", strip=True)
        if level in ("h1","h2","h3"):
            print(f"{level.upper()}: {txt}")
    print("=== FIM HEADINGS ===")

spells = extract_spells(raw)
