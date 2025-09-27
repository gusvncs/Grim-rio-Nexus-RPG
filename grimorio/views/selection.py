from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from grimorio.services.selectors import list_all_spells, list_all_runes

@require_http_methods(['GET','POST'])
def selection_view(request):
    if request.method == 'POST':
        spells = request.POST.getlist('spells[]')
        runes = request.POST.getlist('runes[]')
        request.session['sel_spells'] = spells
        request.session['sel_runes'] = runes
        qs = []
        if spells: qs.append('spells=' + ','.join(spells))
        if runes:  qs.append('runes='  + ','.join(runes))
        return redirect('/grimorio/' + ('?' + '&'.join(qs) if qs else ''))
    ctx = {
        'spells': list_all_spells(),
        'runes': list_all_runes(),
    }
    return render(request, 'grimorio/selection.html', ctx)
