from django import template

register = template.Library()


@register.filter
def get_range(value):
    """
    Returns a range object so templates can loop N times.
    Usage: {% for i in teller.max_concurrent|get_range %}
    """
    try:
        return range(int(value))
    except (ValueError, TypeError):
        return range(0)