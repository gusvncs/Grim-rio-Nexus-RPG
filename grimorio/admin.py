from django.contrib import admin
from .models import Spell, Rune

@admin.register(Spell)
class SpellAdmin(admin.ModelAdmin):
    list_display = ('name','slug','updated_at','version')
    search_fields = ('name','slug')

@admin.register(Rune)
class RuneAdmin(admin.ModelAdmin):
    list_display = ('name','slug','domain','updated_at')
    search_fields = ('name','slug')
