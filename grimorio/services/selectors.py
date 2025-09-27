from typing import Iterable
from grimorio.models import Spell, Rune

def list_all_spells():
    return Spell.objects.all().order_by('name')

def list_all_runes():
    return Rune.objects.all().order_by('name')

def get_spells_by_slugs(slugs: Iterable[str]):
    return Spell.objects.filter(slug__in=list(slugs)).order_by('name')

def get_runes_by_slugs(slugs: Iterable[str]):
    if not slugs:
        return Rune.objects.none()
    return Rune.objects.filter(slug__in=list(slugs)).order_by('name')
