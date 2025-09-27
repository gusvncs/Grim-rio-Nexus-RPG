from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from grimorio.services.selectors import get_spells_by_slugs, get_runes_by_slugs
from grimorio.services.builders import build_spell_panels

def _parse_csv_param(request, key, sess_key):
    raw = request.GET.get(key)
    if raw:
        return [s for s in raw.split(',') if s]
    return request.session.get(sess_key, [])

@require_http_methods(['GET'])
def grimorio_view(request):
    sel_spells = _parse_csv_param(request, 'spells', 'sel_spells')
    sel_runes  = _parse_csv_param(request, 'runes',  'sel_runes')
    spells_qs = get_spells_by_slugs(sel_spells)
    runes_qs  = get_runes_by_slugs(sel_runes)
    if not spells_qs.exists():
        return render(request, 'grimorio/empty.html', {})
    panels = build_spell_panels(spells_qs)
    ctx = {
        'spells': spells_qs,
        'runes': runes_qs,
        'panels': panels,
        'sel_spells': sel_spells,
        'sel_runes': sel_runes,
    }
    return render(request, 'grimorio/grimorio.html', ctx)

@require_http_methods(['GET'])
def api_spells(request):
    slugs = request.GET.get('ids', '')
    ids = [s for s in slugs.split(',') if s] if slugs else []
    runes_filter = request.GET.get('runes', '')
    runes = [s for s in runes_filter.split(',') if s] if runes_filter else None

    spells = get_spells_by_slugs(ids)
    data = []
    for sp in spells:
        effects = sp.rune_effects_json or {}
        if runes is not None:
            effects = {k: v for k, v in effects.items() if k in runes}
        data.append({
            'slug': sp.slug,
            'name': sp.name,
            'attributes': sp.attributes_json or {},
            'manual_html': sp.manual_html,
            'rune_effects': effects,
        })
    return JsonResponse({'spells': data})
