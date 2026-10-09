from django.db.models import F
from django.db.models.functions import Greatest

from apps.core.text import extract_hashtags

from .models import Hashtag


def resolve(names):
    """Get-or-create hashtags for the given lowercase names."""
    if not names:
        return []
    existing = {h.name: h for h in Hashtag.objects.filter(name__in=names)}
    missing = [Hashtag(name=n) for n in names if n not in existing]
    if missing:
        Hashtag.objects.bulk_create(missing, ignore_conflicts=True)
        existing = {h.name: h for h in Hashtag.objects.filter(name__in=names)}
    return [existing[n] for n in names if n in existing]


def sync(instance, through_model, fk_name, counter_field, text):
    """Make ``instance``'s hashtags match ``text``; keeps per-tag counters right."""
    wanted = set(extract_hashtags(text))
    current = dict(through_model.objects.filter(**{fk_name: instance}).values_list("hashtag__name", "hashtag_id"))
    to_add = wanted - set(current)
    to_remove = set(current) - wanted
    if to_remove:
        ids = [current[n] for n in to_remove]
        through_model.objects.filter(**{fk_name: instance, "hashtag_id__in": ids}).delete()
        Hashtag.objects.filter(id__in=ids).update(**{counter_field: Greatest(F(counter_field) - 1, 0)})
    if to_add:
        tags = resolve(sorted(to_add))
        through_model.objects.bulk_create(
            [through_model(**{fk_name: instance, "hashtag": t}) for t in tags], ignore_conflicts=True
        )
        Hashtag.objects.filter(id__in=[t.id for t in tags]).update(**{counter_field: F(counter_field) + 1})


def release(instance, through_model, fk_name, counter_field):
    ids = list(through_model.objects.filter(**{fk_name: instance}).values_list("hashtag_id", flat=True))
    if ids:
        Hashtag.objects.filter(id__in=ids).update(**{counter_field: Greatest(F(counter_field) - 1, 0)})
