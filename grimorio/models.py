from django.db import models

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True

class Rune(TimeStampedModel):
    slug = models.SlugField(unique=True, max_length=64)
    name = models.CharField(max_length=120)
    description_html = models.TextField(blank=True, default="")
    domain = models.CharField(max_length=64, blank=True, default="")
    def __str__(self):
        return self.name

class Spell(TimeStampedModel):
    slug = models.SlugField(unique=True, max_length=64)
    name = models.CharField(max_length=140)
    school = models.CharField(max_length=64, blank=True, default="")
    manual_html = models.TextField()
    attributes_json = models.JSONField(default=dict, blank=True)
    rune_effects_json = models.JSONField(default=dict, blank=True)
    version = models.CharField(max_length=16, blank=True, default="1.0")

    def __str__(self):
        return self.name
