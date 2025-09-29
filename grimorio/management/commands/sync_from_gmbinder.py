import os, re, yaml, requests, json
from bs4 import BeautifulSoup, Tag
from django.core.management.base import BaseCommand, CommandError
from django.core.management import call_command
from unidecode import unidecode

def slugify(text: str) -> str:
    text = unidecode((text or "").strip().lower())
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")

def html_of(nodes): return "".join(str(n) for n in nodes if n is not None)

def extract_spells(html: str):
    soup = BeautifulSoup(html, "html.parser")
    heads = soup.find_all(re.compile(r"^h[1-6]$"))
    tops = [h.get_text(" ", strip=True).upper() for h in heads]

    try:
        i0 = next(i for i,t in enumerate(tops) if "MAGIAS ARCANAS" in t)
    except StopIteration:
        raise CommandError("Não encontrei a seção 'MAGIAS ARCANAS'.")
    try:
        i1 = next(i for i,t in enumerate(tops[i0+1:], start=i0+1) if "MAGIAS DIVINAS" in t)
    except StopIteration:
        i1 = len(heads)

    spells = []
    for i in range(i0+1, i1):
        h = heads[i]
        if h.name != "h3": continue
        title = h.get_text(" ", strip=True)
        if "LISTA DE MAGIAS" in title.upper(): continue

        nodes, cur = [], h.next_sibling
        while cur and not (isinstance(cur, Tag) and cur.name == "h3"):
            if isinstance(cur, Tag) and cur.name in ("h1","h2") and "MAGIAS DIVINAS" in cur.get_text(" ", strip=True).upper():
                break
            nodes.append(cur); cur = cur.next_sibling

        manual_nodes, rune_sections = [], []
        in_runes, rname, rnodes = False, None, []
        for n in nodes:
            if isinstance(n, Tag) and re.match(r"^h[4-6]$", n.name or ""):
                txt = n.get_text(" ", strip=True)
                if "RUNA" in txt.upper():
                    if rname is not None: rune_sections.append((rname, rnodes))
                    m = re.search(r"RUNA\s+DE\s+(.+)", txt, flags=re.I)
                    rname = (m.group(1) if m else txt).strip(); rnodes = []; in_runes = True; continue
            (rnodes if in_runes else manual_nodes).append(n)
        if rname is not None: rune_sections.append((rname, rnodes))

        # atributos (best-effort)
        text_flat = re.sub(r"\s+", " ", BeautifulSoup(html_of(manual_nodes), "html.parser").get_text(" ", strip=True))
        attrs, keys = {}, ["Execução","Alcance","Alvo","Área","Duração"]
        for k in keys:
            m = re.search(rf"{k}\s*[:\.]\s*([^•\|\n]+?)(?=\s{{1,3}}[•\|]\s|$)", text_flat, flags=re.I)
            if m: attrs[k.lower()] = m.group(1).strip()

        spells.append({
            "name": title,
            "slug": slugify(title),
            "manual_html": html_of(manual_nodes).strip(),
            "attributes": attrs,
            "rune_effects": { slugify(nm): html_of(ns).strip() for nm, ns in rune_sections },
        })
    return spells

class Command(BaseCommand):
    help = "Baixa o manual do GM Binder, extrai magias/efeitos e importa no app."

    def add_arguments(self, p):
        p.add_argument("--url", required=True, help="URL pública do GM Binder")
        p.add_argument("--out-root", default="content", help="Pasta content/ para YAML")
        p.add_argument("--strict", action="store_true", help="Falhar se houver magia com runa desconhecida")

    def handle(self, *a, **o):
        url = o["url"]; out_root = o["out_root"]; strict = o["strict"]
        spells_dir = os.path.join(out_root, "spells")
        runes_dir  = os.path.join(out_root, "runes")
        os.makedirs(spells_dir, exist_ok=True); os.makedirs(runes_dir, exist_ok=True)

        self.stdout.write(self.style.NOTICE(f"Baixando: {url}"))
        r = requests.get(url, timeout=60); r.raise_for_status()
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
                    yaml.safe_dump({
                        "slug": rs, "name": rs.replace("-", " ").title(),
                        "description_html": "", "domain": ""
                    }, f, sort_keys=False, allow_unicode=True)

        self.stdout.write(self.style.SUCCESS(f"YAML gerado em {out_root}/spells e {out_root}/runes"))
        call_command("import_content", content_root=out_root, strict=strict)
