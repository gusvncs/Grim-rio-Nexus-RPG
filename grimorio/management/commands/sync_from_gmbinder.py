import os, re, yaml, requests
from bs4 import BeautifulSoup, Tag
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from unidecode import unidecode
import markdown as md

SKIP_H3_KEYWORDS = {
    "CONJURANDO MAGIAS",
    "DESCRIÇÃO DAS RUNAS",
    "DESCRICAO DAS RUNAS",
    "ESCOLHA A MAGIA",
    "DETERMINE O CÍRCULO",
    "DETERMINE O CIRCULO",
    "APLIQUE RUNAS",
    "LISTA DE MAGIAS",
}

ATTR_KEYS_PT = ["Execução", "Alcance", "Alvo", "Área", "Duração"]

def slugify(text: str) -> str:
    text = unidecode((text or "").strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")

def html_of(nodes):
    return "".join(str(n) for n in nodes if n is not None)

def ensure_html(text: str) -> str:
    """Se não parecer HTML, converte Markdown -> HTML."""
    sniff = text[:4000]
    if re.search(r"<h[1-6]\b", sniff, flags=re.I):
        return text  # já é HTML
    return md.markdown(text, extensions=["extra", "sane_lists"])

def split_spell_block_from(h3: Tag):
    """Coleta os nós entre este <h3> e o próximo <h3> (ou até um <h1>/<h2> de outra seção)."""
    nodes, cur = [], h3.next_sibling
    while cur and not (isinstance(cur, Tag) and cur.name == "h3"):
        if isinstance(cur, Tag) and cur.name in ("h1", "h2"):
            break
        nodes.append(cur)
        cur = cur.next_sibling
    return nodes

def separate_manual_and_runes(nodes):
    """Separa manual_nodes e blocos de runas (H4+ com 'Runa ...')."""
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
    return manual_nodes, rune_sections

def extract_attrs_from_manual(manual_nodes):
    """Extrai atributos básicos (best-effort) do bloco manual."""
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

def looks_like_spell(title: str, manual_nodes, rune_sections):
    """Heurística: é magia se tiver ao menos 2 atributos OU alguma seção de runa."""
    attrs = extract_attrs_from_manual(manual_nodes)
    has_attrs = sum(1 for _ in attrs.keys()) >= 2
    has_runes = len(rune_sections) > 0
    title_norm = unidecode(title).upper().strip()
    if any(key in title_norm for key in SKIP_H3_KEYWORDS):
        return False
    return has_attrs or has_runes

def find_bounds_by_titles_or_fallback(html: str):
    """
    Tenta recortar entre 'MAGIAS ARCANAS' e 'MAGIAS DIVINAS'.
    Se não achar, retorna soup e heads completos para fallback.
    """
    soup = BeautifulSoup(html, "html.parser")
    heads = soup.find_all(re.compile(r"^h[1-6]$"))
    tops = [unidecode(h.get_text(" ", strip=True)).upper() for h in heads]

    try:
        i0 = next(i for i, t in enumerate(tops) if "MAGIAS ARCANAS" in t)
    except StopIteration:
        return soup, heads, None, None  # fallback

    try:
        i1 = next(i for i, t in enumerate(tops[i0 + 1 :], start=i0 + 1) if "MAGIAS DIVINAS" in t)
    except StopIteration:
        i1 = len(heads)

    return soup, heads, i0, i1

def extract_spells(raw_text: str):
    """
    Aceita texto HTML ou Markdown.
    1) Converte para HTML se necessário.
    2) Tenta extrair entre 'Magias Arcanas' e 'Magias Divinas'.
    3) Se não achar, faz fallback: percorre todos os h3 e filtra por heurística.
    """
    html = ensure_html(raw_text)
    soup, heads, i0, i1 = find_bounds_by_titles_or_fallback(html)

    spells = []

    if i0 is not None and i1 is not None:
        # caminho “clássico”: dentro da seção Magias Arcanas
        for i in range(i0 + 1, i1):
            h = heads[i]
            if h.name != "h3":
                continue
            title = h.get_text(" ", strip=True)
            nodes = split_spell_block_from(h)
            manual_nodes, rune_sections = separate_manual_and_runes(nodes)
            if not looks_like_spell(title, manual_nodes, rune_sections):
                continue
            spells.append(
                {
                    "name": title,
                    "slug": slugify(title),
                    "manual_html": html_of(manual_nodes).strip(),
                    "attributes": extract_attrs_from_manual(manual_nodes),
                    "rune_effects": {slugify(nm): html_of(ns).strip() for nm, ns in rune_sections},
                }
            )
    else:
        # fallback: percorre todos os H3 do documento
        for h in soup.find_all("h3"):
            title = h.get_text(" ", strip=True)
            if not title:
                continue
            if any(key in unidecode(title).upper() for key in SKIP_H3_KEYWORDS):
                continue
            nodes = split_spell_block_from(h)
            manual_nodes, rune_sections = separate_manual_and_runes(nodes)
            if not looks_like_spell(title, manual_nodes, rune_sections):
                continue
            spells.append(
                {
                    "name": title,
                    "slug": slugify(title),
                    "manual_html": html_of(manual_nodes).strip(),
                    "attributes": extract_attrs_from_manual(manual_nodes),
                    "rune_effects": {slugify(nm): html_of(ns).strip() for nm, ns in rune_sections},
                }
            )

    return spells

class Command(BaseCommand):
    help = "Baixa o manual do GM Binder, extrai magias/efeitos e importa no app."

    def add_arguments(self, p):
        p.add_argument("--url", required=True, help="URL do GM Binder (de preferência, o /source)")
        p.add_argument("--out-root", default="content", help="Pasta content/ para YAML")
        p.add_argument("--strict", action="store_true", help="Falhar se houver magia com runa desconhecida")
        p.add_argument("--debug-headings", action="store_true", help="Imprime H1..H3 detectados (diagnóstico)")

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
        debug = bool(o.get("debug_headings"))

        spells_dir = os.path.join(out_root, "spells")
        runes_dir  = os.path.join(out_root, "runes")
        os.makedirs(spells_dir, exist_ok=True)
        os.makedirs(runes_dir,  exist_ok=True)

        self.stdout.write(self.style.NOTICE(f"Baixando: {url}"))
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        raw = r.text

        if debug:
            # imprime H1..H3 detectados
            html = ensure_html(raw)
            soup = BeautifulSoup(html, "html.parser")
            heads = soup.find_all(re.compile(r"^h[1-6]$"))
            print("=== HEADINGS DETECTADOS (H1..H3) ===")
            for h in heads:
                if h.name in ("h1", "h2", "h3"):
                    print(f"{h.name.upper()}: {h.get_text(' ', strip=True)}")
            print("=== FIM HEADINGS ===")

        spells = extract_spells(raw)
        if not spells:
            raise CommandError(
                "Nenhuma magia passou no filtro. "
                "Verifique a estrutura do manual ou ajuste SKIP_H3_KEYWORDS/heurísticas."
            )

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
