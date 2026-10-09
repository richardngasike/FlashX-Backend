from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Greatest

from apps.core.targets import kind_of

from .models import SavedItem


def _count(target):
    return type(target).objects.filter(pk=target.pk).values_list("saves_count", flat=True).first() or 0


def save(user, target) -> int:
    kind = kind_of(target)
    try:
        with transaction.atomic():
            SavedItem.objects.create(user=user, **{kind: target})
            type(target).objects.filter(pk=target.pk).update(saves_count=F("saves_count") + 1)
    except IntegrityError:
        pass
    return _count(target)


def unsave(user, target) -> int:
    kind = kind_of(target)
    with transaction.atomic():
        deleted, _ = SavedItem.objects.filter(user=user, **{kind: target}).delete()
        if deleted:
            type(target).objects.filter(pk=target.pk).update(saves_count=Greatest(F("saves_count") - 1, 0))
    return _count(target)
