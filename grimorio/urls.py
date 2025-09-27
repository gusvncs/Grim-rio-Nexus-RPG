from django.urls import path
from .views.selection import selection_view
from .views.grimorio import grimorio_view, api_spells

app_name = 'grimorio'

urlpatterns = [
    path('', selection_view, name='selection'),
    path('grimorio/', grimorio_view, name='grimorio'),
    path('api/spells', api_spells, name='api_spells'),
]
