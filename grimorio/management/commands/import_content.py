import os, glob, yaml, bleach
from django.core.management.base import BaseCommand, CommandError
from grimorio.models import Spell, Rune

ALLOWED_TAGS = [
    "p","ul","ol","li","em","strong","b","i","u",
    "h2","h3","h4","h5","span","div","br",
    "table","thead","tbody","tr","td","th","code","pre","blockquote"
]
ALLOWED_ATTRS = {"*": ["class","style"]}

def sanitize_html(html: str) -> str:
    return bleach.clean(html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)

class Command(BaseCommand):
    help = "Importa spells e runes da pasta content/"

    def add_arguments(self, parser):
        parser.add_argument("--content-root", default="content", help="Pasta raiz do conteúdo YAML")
        parser.add_argument("--strict", action="store_true", help="Falha se houver runa desconhecida em rune_effects")

    def handle(self, *args, **opts):
        root = opts["content_root"]
        spells_dir = os.path.join(root, "spells")
        runes_dir = os.path.join(root, "runes")
        strict = opts["strict"]

        if not os.path.isdir(spells_dir) or not os.path.isdir(runes_dir):
            raise CommandError(f"Pastas esperadas: {spells_dir} e {runes_dir}")

        self.stdout.write(self.style.NOTICE("Importando RUNAS..."))
        known_runes = set()
        for path in sorted(glob.glob(os.path.join(runes_dir, "*.yml"))):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            slug = data.get("slug")
            if not slug:
                self.stdout.write(self.style.WARNING(f"Runa sem slug em {path} — ignorada"))
                continue
            obj, _ = Rune.objects.get_or_create(slug=slug)
            obj.name = data.get("name", obj.name or slug)
            obj.domain = data.get("domain", obj.domain)
            obj.description_html = sanitize_html(data.get("description_html", ""))
            obj.save()
            known_runes.add(slug)
            self.stdout.write(f"  ✓ {obj.name}")

        self.stdout.write(self.style.NOTICE("Importando MAGIAS..."))
        for path in sorted(glob.glob(os.path.join(spells_dir, "*.yml"))):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            slug = data.get("slug")
            if not slug:
                self.stdout.write(self.style.WARNING(f"Magia sem slug em {path} — ignorada"))
                continue
            spell, _ = Spell.objects.get_or_create(slug=slug)
            spell.name = data.get("name", spell.name or slug)
            spell.school = data.get("school", spell.school)
            spell.version = data.get("version", spell.version or "1.0")
            spell.manual_html = sanitize_html(data.get("manual_html", ""))
            spell.attributes_json = data.get("attributes", {}) or {}

            raw_effects = data.get("rune_effects", {}) or {}
            rune_effects_json = {}
            for r_slug, r_html in raw_effects.items():
                if r_slug not in known_runes:
                    msg = f"  ! Runa '{r_slug}' referenciada em {slug} não existe em content/runes"
                    if strict:
                        raise CommandError(msg)
                    else:
                        self.stdout.write(self.style.WARNING(msg))
                rune_effects_json[r_slug] = sanitize_html(r_html)
            spell.rune_effects_json = rune_effects_json
            spell.save()

            missing = sorted(list(known_runes - set(rune_effects_json.keys())))
            if missing:
                self.stdout.write(self.style.WARNING(f"  ! {spell.name} sem efeitos para: {', '.join(missing)}"))
            self.stdout.write(f"  ✓ {spell.name} ({len(spell.rune_effects_json)} runas)")

        self.stdout.write(self.style.SUCCESS("Importação concluída."))
