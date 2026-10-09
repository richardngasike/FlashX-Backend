from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from . import cloudinary_service as cld
from .models import MediaAsset


@receiver(post_delete, sender=MediaAsset)
def remove_cloudinary_file(sender, instance, **kwargs):
    if instance.is_managed:
        public_id, rtype = instance.public_id, instance.resource_type
        transaction.on_commit(lambda: cld.destroy(public_id, rtype))


def delete_attached_asset(sender, instance, **kwargs):
    """post_delete hook for content models with an ``asset`` FK."""
    asset_id = getattr(instance, "asset_id", None)
    if asset_id:
        MediaAsset.objects.filter(pk=asset_id).delete()


def connect_asset_cleanup(model):
    post_delete.connect(delete_attached_asset, sender=model, dispatch_uid=f"asset_cleanup_{model._meta.label}")
