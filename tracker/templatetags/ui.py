"""Small presentation helpers shared by all pages."""

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core import clock
from core.models import Person

register = template.Library()


@register.filter
def person(emp_id):
    if not emp_id:
        return ""
    p = Person.objects.filter(pk=emp_id).first()
    return p.display_name if p else emp_id


@register.filter
def group_label(value):
    from tracker.services.triage import GROUP_LABELS
    return GROUP_LABELS.get(value, value)


@register.filter
def role_label(value):
    from core.models import Role
    try:
        return Role(value).label
    except ValueError:
        return value


@register.filter
def cap(value):
    return value[:1].upper() + value[1:] if value else value


@register.filter
def days_ago(dt):
    n = clock.days_since(dt)
    return "" if n is None else f"{n}d ago"


@register.filter
def days_until(d):
    n = clock.days_until(d)
    return "" if n is None else f"{n}d"


@register.filter
def short_date(d):
    return f"{d:%b} {d.day}, {d.year}" if d else "—"


@register.simple_tag
def vis_tag(visibility):
    label = {"internal": "Tech Risk only", "owner": "Hidden from execs", "exec": "Executives and audit"}.get(visibility, visibility)
    return format_html('<span class="tag {}">{}</span>', visibility, label)


@register.simple_tag
def rag_pill(value, text=None):
    return format_html('<span class="pill {}">{}</span>', value, text or cap(value))


@register.simple_tag
def readiness_pill(value, text=None):
    color = {"ready": "green", "in_progress": "amber", "submitted": "amber", "sent_back": "amber", "not_started": "red"}.get(value, "grey")
    return format_html('<span class="pill {}">{}</span>', color, text or value.replace("_", " "))


@register.simple_tag
def rating_pill(value):
    color = {"H": "red", "M": "amber", "L": "green"}.get(value, "grey")
    return format_html('<span class="pill {}">{}</span>', color, value)


@register.filter
def get(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def safe_svg(markup):
    return mark_safe(markup)
