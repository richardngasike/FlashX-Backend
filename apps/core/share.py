"""
Public share pages: https://<api-host>/p/<id>/, /r/<id>/, /u/<username>/.

A link copied from the app opens a small preview page with Open Graph tags
(so chat apps show a card) and an "Open in FlashX" button. Phones with the app
installed offer to open the link in FlashX directly. Only public content of
public accounts is shown; anything else gets a neutral "not available" page,
so a link never leaks private or blocked content.
"""

from django.http import HttpResponse
from django.utils.html import escape

from apps.media import cloudinary_service as cld

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta property="og:site_name" content="FlashX"><meta property="og:title" content="{title}">
<meta property="og:description" content="{description}"><meta property="og:type" content="website">
{image_meta}<meta name="twitter:card" content="summary_large_image">
<style>
:root{{color-scheme:dark light}}body{{margin:0;font-family:Inter,system-ui,sans-serif;background:#0A0D10;color:#fff;
display:flex;min-height:100vh;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}}
.card{{max-width:420px;width:100%;background:#12161B;border-radius:18px;overflow:hidden}}
img{{width:100%;display:block;max-height:520px;object-fit:cover}}.body{{padding:20px}}
.brand{{font-weight:800;font-size:22px}}.brand span{{color:#C6F432}}h1{{font-size:17px;margin:14px 0 6px}}
p{{color:#9AA3AD;font-size:14px;line-height:1.5;margin:0 0 18px;white-space:pre-line}}
a.btn{{display:block;text-align:center;background:#C6F432;color:#0A0D10;font-weight:700;padding:13px;border-radius:12px;
text-decoration:none}}
</style></head><body><div class="card">{image_tag}<div class="body">
<div class="brand">Flash<span>X</span></div><h1>{title}</h1><p>{description}</p>
<a class="btn" href="{app_link}">Open in FlashX</a></div></div></body></html>"""


def _render(title, description, image, app_link, status=200):
    image_meta = f'<meta property="og:image" content="{escape(image)}">' if image else ""
    image_tag = f'<img src="{escape(image)}" alt="">' if image else ""
    html = PAGE.format(
        title=escape(title),
        description=escape(description[:300]),
        image_meta=image_meta,
        image_tag=image_tag,
        app_link=escape(app_link),
    )
    response = HttpResponse(html, status=status, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "public, max-age=300"
    return response


def _unavailable():
    return _render("Not available", "This content isn't available.", "", "flashx://open/", status=404)


def share_post(request, pk):
    from apps.posts.selectors import visible_posts

    post = visible_posts(None).select_related("author").prefetch_related("media").filter(pk=pk).first()
    if post is None:
        return _unavailable()
    media = next(iter(post.media.order_by("order")), None)
    image = (cld.variants(media.cloudinary_public_id, media.media_type) or {}).get("medium") if media else ""
    if media and media.media_type == "video":
        image = cld.video_poster_url(media.cloudinary_public_id)
    return _render(
        f"{post.author.full_name or post.author.username} on FlashX",
        post.caption or "See this post on FlashX.",
        image or "",
        f"flashx://open/p/{post.pk}",
    )


def share_reel(request, pk):
    from apps.reels.selectors import visible_reels

    reel = visible_reels(None).select_related("author").filter(pk=pk).first()
    if reel is None:
        return _unavailable()
    return _render(
        f"Reel by {reel.author.username}",
        reel.caption or "Watch this reel on FlashX.",
        cld.video_poster_url(reel.cloudinary_public_id) or "",
        f"flashx://open/r/{reel.pk}",
    )


def share_user(request, username):
    from apps.users.selectors import base_users

    user = base_users().filter(username__iexact=username).first()
    if user is None:
        return _unavailable()
    avatar = (
        cld.image_url(user.profile_image.public_id, 600, 600, crop="fill", gravity="face") if user.profile_image else ""
    )
    bio = user.bio if not user.is_private else "This account is private."
    return _render(
        f"{user.full_name or user.username} (@{user.username})",
        bio or "On FlashX.",
        avatar,
        f"flashx://open/u/{user.username}",
    )
