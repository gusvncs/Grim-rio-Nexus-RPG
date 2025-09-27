from django import template
register = template.Library()

@register.filter
def get_item(d, key):
    try:
        return (d or {}).get(key)
    except AttributeError:
        return None
