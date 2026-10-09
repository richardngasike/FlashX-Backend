# FlashX API

This is the Django REST API behind **FlashX**, a mobile social platform for Android and iOS with posts, stories, reels, messaging and discovery. The Flutter app talks only to this API. Media files live on Cloudinary and everything else lives in PostgreSQL.

> Create. Share. Connect.

---

## Contents

1. [Tech stack](#1-tech-stack)
2. [Project structure](#2-project-structure)
3. [Local setup](#3-local-setup)
4. [Configuration reference](#4-configuration-reference)
5. [Connecting the Flutter app](#5-connecting-the-flutter-app)
6. [API conventions](#6-api-conventions)
7. [Endpoint reference](#7-endpoint-reference)
8. [Media uploads](#8-media-uploads)
9. [Admin and moderation](#9-admin-and-moderation)
10. [Scheduled jobs](#10-scheduled-jobs)
11. [Testing and code quality](#11-testing-and-code-quality)
12. [Deployment](#12-deployment)
13. [Troubleshooting](#13-troubleshooting)
14. [Scope](#14-scope)

---

## 1. Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.12, 3.13 or 3.14 (all three are tested) |
| Framework | Django 5.2 LTS (security support until April 2028) |
| API | Django REST Framework 3.16, drf-spectacular (OpenAPI 3) |
| Auth | JWT via SimpleJWT: 15-minute access tokens, rotating refresh tokens, blacklist on logout |
| Database | PostgreSQL 14+ (uses the `pg_trgm` extension for search) |
| Media | Cloudinary: signed direct uploads from the phone, plus server-side uploads for small files |
| Static files | WhiteNoise (compressed, hashed admin assets) |
| Hosting | Vercel (zero-config Django), or any VPS / container host with Gunicorn |

## 2. Project structure

```
backend/
  config/
    settings/
      base.py           shared settings, read from environment variables
      development.py    DEBUG on, CORS open, browsable API
      production.py     HTTPS, HSTS, secure cookies, Vercel hostnames, pooled-DB support
      test.py           fast hashing, stubbed Cloudinary, quiet logs
    urls.py  wsgi.py  asgi.py
  apps/
    core/           response envelope, error handler, pagination, throttles, health, cron endpoint, seed_dev
    users/          custom User, JWT auth with presence, register/login, password reset, profiles
    follows/        follow graph and counters
    media/          MediaAsset, Cloudinary service, upload signing and validation, orphan purge
    posts/          posts, carousel media, hashtags, categories, tags, mood/music/event, feed, visibility
    comments/       threaded comments (one reply level), comment likes
    likes/          likes for posts, reels and comments
    saves/          saved posts and reels
    stories/        24-hour stories, views, reactions, replies to DM, expiry purge
    reels/          short vertical video with distinct view counting
    messaging/      direct and group conversations, read state, shared posts/reels/stories
    notifications/  activity feed
    search/         search, recent searches, explore and discovery
    reports/        content reports and moderation actions
  tests/            API test suite (93 tests, runs against PostgreSQL)
  .env.example      every environment variable, documented
  .env              ready-to-run local configuration (git-ignored)
  vercel.json       region, function timeout, daily cron
  Dockerfile        production image (Gunicorn, non-root)
  docker-compose.yml  local PostgreSQL 16 + API
  ruff.toml         lint and format rules
```

**How the code is organised**

- **Thin views.** Views stay small. Business rules live in each app's `services.py` (writes, counters, notifications) and `selectors.py` (reads with visibility rules and per-viewer flags such as `is_liked`). Only `apps/media/cloudinary_service.py` talks to Cloudinary.
- **Counters.** Fields such as `likes_count`, `followers_count` and `posts_count` are stored on the row. They are updated with `F()` expressions in the same transaction as the change they count, so list endpoints never run a `COUNT(*)` per item.
- **Visibility.** A post is `public`, `followers` or `private`. Every read path (feed, detail, search, explore, likes, comments, shares) goes through `posts.selectors.visible_posts(viewer)`. That function also drops hidden (moderated) content and content from deactivated accounts.

## 3. Local setup

### Prerequisites

- Python 3.12, 3.13 or 3.14
- PostgreSQL 14 or newer, installed locally or through Docker
- A free Cloudinary account (only needed for uploads; everything else works without it)

### Option A: Python on your machine

**1. Create the database.** Run this in `psql` as a PostgreSQL superuser:

```sql
CREATE ROLE flashx LOGIN PASSWORD 'flashx';
CREATE DATABASE flashx OWNER flashx;
```

These credentials match `DATABASE_URL` in the bundled `.env`. If you use different ones, update that line.

Migration `search.0003_trigram_indexes` runs `CREATE EXTENSION pg_trgm`. On PostgreSQL 13 and newer this is a trusted extension, so the database owner can create it. If your server refuses, run `CREATE EXTENSION IF NOT EXISTS pg_trgm;` once in the `flashx` database as a superuser.

**2. Install and run:**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser     # asks for username, email, full name, password
python manage.py seed_dev            # optional demo data
python manage.py runserver 0.0.0.0:8000
```

The bundled `.env` already has a generated `SECRET_KEY` and `CRON_SECRET`, development settings and the local database URL. To enable uploads, fill in `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY` and `CLOUDINARY_API_SECRET`.

If you start from a clean copy without `.env`, run `cp .env.example .env`, then set `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

**3. Open it:**

| URL | What |
|---|---|
| `http://localhost:8000/api/health/` | `{"success": true, "data": {"status": "ok", "database": true}}` |
| `http://localhost:8000/api/docs/` | Swagger UI for every endpoint |
| `http://localhost:8000/api/schema/` | Raw OpenAPI schema |
| `http://localhost:8000/admin/` | Admin site (log in with the superuser) |

### Option B: Docker Compose

```bash
cd backend
docker compose up --build
docker compose exec api python manage.py createsuperuser
docker compose exec api python manage.py seed_dev
```

Compose starts PostgreSQL 16 and the API on port 8000, applies migrations and reads `.env`. Inside the container the database URL is replaced with the Compose database (`db:5432`).

### Settings selection

The settings module is resolved in this order:

1. `DJANGO_SETTINGS_MODULE` in the shell environment
2. `DJANGO_SETTINGS_MODULE` in `.env`
3. `config.settings.production` when `VERCEL` is set, otherwise `config.settings.development`

`wsgi.py` and `asgi.py`, which production servers use, default to `config.settings.production`.

### Seed data

```bash
python manage.py seed_dev                 # 12 users, follows, text posts, likes, comments, 6 categories
python manage.py seed_dev --with-media    # also image posts, stories and reels from Cloudinary's "samples/" folder
python manage.py seed_dev --reset         # remove previous seed users first
```

- Every seed account uses the password `FlashX-dev-2026!`, for example `richyict`. Change it with `--password`.
- Seed accounts have `@seed.flashx.local` emails.
- `--with-media` reuses the sample assets that every Cloudinary account includes. Those rows are marked unmanaged, so deleting seed content never deletes the samples.
- The command refuses to run when `DEBUG=False` unless you pass `--force`.

## 4. Configuration reference

All settings come from environment variables: `.env` locally, or *Project Settings > Environment Variables* on Vercel. `.env.example` lists every variable with comments. The table below is the same list.

**Required:** `SECRET_KEY` and `DATABASE_URL`. Every other variable has a working default. Media endpoints return `503 media_service_unavailable` until the three Cloudinary keys are set.

### Core

| Variable | Default | Notes |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | development (production on Vercel) | Use `config.settings.production` for any deployment |
| `DEBUG` | `True` in development | Production always forces `False` |
| `SECRET_KEY` | required | 50+ random characters; never reuse across environments |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma separated. Vercel hostnames are added automatically. Add your LAN IP for phone testing |
| `ADMIN_URL` | `admin/` | Use a hard-to-guess path in production; keep the trailing slash |
| `TIME_ZONE` | `Africa/Nairobi` | Times are stored in UTC and returned in ISO 8601 |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Database

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | required | `postgres://user:password@host:5432/dbname`. On Vercel the Neon integration sets it |
| `DB_CONN_MAX_AGE` | `60` | Seconds to reuse a connection; health checks drop stale ones |
| `DB_DISABLE_SERVER_SIDE_CURSORS` | `True` | Production only. Required behind a transaction-mode pooler (Neon `-pooler`, PgBouncer, Supabase) |

### Cloudinary

| Variable | Default | Notes |
|---|---|---|
| `CLOUDINARY_CLOUD_NAME` | empty | Dashboard > Settings > API Keys |
| `CLOUDINARY_API_KEY` | empty | |
| `CLOUDINARY_API_SECRET` | empty | Server only; never put it in the app |
| `CLOUDINARY_ROOT_FOLDER` | `flashx` | Use a different value per environment, e.g. `flashx-preview` |
| `CLOUDINARY_VERIFY_WITH_ADMIN_API` | `True` | Re-reads size, format and duration from Cloudinary after each direct upload |
| `CLOUDINARY_SIGNATURE_TTL_SECONDS` | `3600` | How long signed upload parameters stay valid |

### Authentication and jobs

| Variable | Default | Notes |
|---|---|---|
| `JWT_ACCESS_TOKEN_LIFETIME` | `15` | Minutes |
| `JWT_REFRESH_TOKEN_LIFETIME` | `30` | Days. Refresh tokens rotate and the old one is blacklisted |
| `JWT_SIGNING_KEY` | blank, which uses `SECRET_KEY` | Set a separate 32+ character key to invalidate tokens without rotating `SECRET_KEY` |
| `CRON_SECRET` | empty | Bearer token for `/api/cron/maintenance/`. When empty, the endpoint returns 503 |

### CORS, CSRF and HTTPS

| Variable | Default | Notes |
|---|---|---|
| `CORS_ALLOW_ALL_ORIGINS` | `True` | Development only |
| `CORS_ALLOWED_ORIGINS` | empty | Browser origins only; the native app is not subject to CORS |
| `CSRF_TRUSTED_ORIGINS` | empty | Full origins for the admin behind a custom domain, e.g. `https://api.flashx.app` |
| `SECURE_SSL_REDIRECT` | `True` | Production only |
| `SECURE_HSTS_SECONDS` | `31536000` | Production only. Use `3600` for the first deploy on a new domain, then raise it |

### Email

| Variable | Default | Notes |
|---|---|---|
| `EMAIL_BACKEND` | console | The console backend prints emails to the server log. Use `django.core.mail.backends.smtp.EmailBackend` in production |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | empty, `587`, empty, empty, `True` | SMTP connection |
| `DEFAULT_FROM_EMAIL` | `FlashX <no-reply@flashx.app>` | |
| `PASSWORD_RESET_URL` | `flashx://reset-password?uid={uid}&token={token}` | Link in reset emails; `{uid}` and `{token}` are filled in |

### Media and content limits

| Variable | Default | Notes |
|---|---|---|
| `MEDIA_IMAGE_MAX_BYTES` | `15728640` | 15 MB |
| `MEDIA_VIDEO_MAX_BYTES` | `157286400` | 150 MB |
| `POST_MAX_MEDIA_ITEMS` | `10` | Carousel size |
| `POST_VIDEO_MAX_SECONDS` | `600` | |
| `STORY_VIDEO_MAX_SECONDS` | `60` | |
| `REEL_MAX_SECONDS` | `180` | |
| `MESSAGE_VIDEO_MAX_SECONDS` | `300` | |
| `STORY_LIFETIME_HOURS` | `24` | |
| `ONLINE_WINDOW_SECONDS` | `300` | A user counts as online if they used the API within this window |
| `REPORT_AUTO_HIDE_THRESHOLD` | `0` (off) | Hide content automatically after this many distinct open reports |

### Rate limits

The format is `number/sec|min|hour|day`.

| Variable | Default | Applies to |
|---|---|---|
| `THROTTLE_ANON` | `60/min` | Unauthenticated requests, per IP |
| `THROTTLE_USER` | `600/min` | Authenticated requests, per user |
| `THROTTLE_AUTH` | `10/min` | Register, login, refresh, per IP |
| `THROTTLE_PASSWORD_RESET` | `5/hour` | Password reset, per IP |
| `THROTTLE_UPLOAD` | `60/hour` | Upload signing and uploads |
| `THROTTLE_CONTENT_CREATE` | `60/hour` | New posts, reels, stories, comments |
| `THROTTLE_MESSAGE_SEND` | `120/min` | Messages and story replies |
| `THROTTLE_REPORT` | `30/hour` | Reports |

### Set by Vercel

Do not add these yourself. Vercel sets `VERCEL`, `VERCEL_URL`, `VERCEL_BRANCH_URL` and `VERCEL_PROJECT_PRODUCTION_URL`. Production settings add each hostname to `ALLOWED_HOSTS` and its `https://` origin to `CSRF_TRUSTED_ORIGINS`.

## 5. Connecting the Flutter app

The app needs one value: the API base URL, ending in `/api` with no trailing slash. It reads this value at build time from `mobile/env/*.json`.

| Where the API runs | App setting | Backend requirement |
|---|---|---|
| Vercel / production | `env/production.json`: `"API_BASE_URL": "https://<project>.vercel.app/api"` | Deployed with HTTPS (Vercel provides it) |
| This computer, Android emulator | default in debug builds: `http://10.0.2.2:8000/api` | `runserver 0.0.0.0:8000`; `10.0.2.2` is already in `ALLOWED_HOSTS` |
| This computer, iOS simulator | default in debug builds: `http://127.0.0.1:8000/api` | `runserver 0.0.0.0:8000` |
| This computer, physical phone on Wi-Fi | `env/local-device.json`: `"API_BASE_URL": "http://<computer LAN IP>:8000/api"` | `runserver 0.0.0.0:8000`; add the LAN IP to `ALLOWED_HOSTS` in `.env` |

```bash
cd mobile
flutter run --dart-define-from-file=env/production.json     # deployed API, Android and iOS
flutter run                                                 # local API, emulator / simulator
flutter run --dart-define-from-file=env/local-device.json   # local API, physical phone
```

Release builds accept only `https://` URLs. Plain HTTP is allowed only in Android debug/profile builds and through iOS local-network exceptions. The app never contains Cloudinary secrets or database credentials. See `mobile/README.md` for build commands.

## 6. API conventions

### Response envelope

Every response uses one of these two shapes:

```json
{"success": true,  "data": {}}
{"success": false, "error": {"code": "validation_error", "message": "username: This username is taken.", "details": {"username": ["This username is taken."]}}}
```

- `message` is safe to show to users.
- `code` is stable and meant for client logic. `204` responses have no body.
- Codes in use: `validation_error`, `not_authenticated`, `invalid_credentials`, `permission_denied`, `not_found`, `throttled` (with `retry_after` seconds), `file_too_large` (413), `unsupported_file_type` (415), `video_too_long`, `media_unavailable`, `media_service_unavailable` (503), `comments_disabled`, `already_reported` (409), `self_follow`, `self_message`, `upload_not_owned`, `invalid_upload_signature`, `server_error`.

### Authentication

Send `Authorization: Bearer <access>` with every request that needs a user. On a `401`:

1. Call `POST /api/auth/refresh/` with the refresh token.
2. Store **both** returned tokens, because refresh tokens rotate.
3. Retry the original request once. If the refresh fails, log the user out.

### Pagination

| Style | Used by | Shape |
|---|---|---|
| Cursor | feeds, reels, comments, messages, notifications, likers, saved, liked | `{"results": [], "next": url\|null, "previous": url\|null}`. Follow `next` as-is |
| Offset | search, explore | `{"results": [], "count", "next", "previous"}`, with `limit`/`offset` |
| Page | followers / following | `{"results": [], "count", "page", "total_pages", "next", "previous"}` |

Cursor endpoints accept `page_size` (max 50; 100 for chat).

## 7. Endpoint reference

All paths are under `/api/`. Every endpoint needs a token except `health/`, `auth/register/`, `auth/login/`, `auth/refresh/`, `auth/password/reset/`, `auth/password/reset/confirm/` and `auth/username-available/`. The cron endpoint uses its own secret.

### Auth

| Method | Path | Body / notes |
|---|---|---|
| POST | `auth/register/` | `full_name, username, email, password, confirm_password` returns `{user, tokens}` |
| POST | `auth/login/` | `identifier` (username or email), `password` returns `{user, tokens}` |
| POST | `auth/refresh/` | `refresh` returns `{access, refresh}` |
| POST | `auth/logout/` | `refresh` (blacklisted) |
| POST | `auth/password/change/` | `current_password, new_password` returns fresh tokens; all other sessions are revoked |
| POST | `auth/password/reset/` | `email` (always 200) |
| POST | `auth/password/reset/confirm/` | `uid, token, new_password` |
| GET | `auth/username-available/?username=` | `{available, reason}` |

### Users and follows

| Method | Path | Notes |
|---|---|---|
| GET / PATCH / DELETE | `users/me/` | PATCH: `full_name, username, bio, website, location, profile_image_id, cover_image_id` (`null` removes an image). DELETE needs `password` |
| GET | `users/me/likes/` | Posts you liked, newest like first |
| GET | `users/me/saved/?type=posts\|reels` | Saved items, newest save first |
| GET | `users/{id}/`, `users/by-username/{username}/` | Profile with counts, `is_following`, `follows_you`, `is_me` |
| POST / DELETE | `users/{id}/follow/` | Follow / unfollow |
| DELETE | `users/{id}/remove-follower/` | |
| GET | `users/{id}/followers/`, `users/{id}/following/` | Optional `?q=` filter |
| GET | `users/suggested/?limit=` | Ranked by mutual connections |

### Media

| Method | Path | Notes |
|---|---|---|
| POST | `media/sign/` | `purpose, resource_type` returns signed Cloudinary parameters |
| POST | `media/` | Registers a direct upload and returns a `MediaAsset` (attach it by `id`) |
| POST | `media/upload/` | Multipart `purpose, file`; the server uploads to Cloudinary. Vercel rejects bodies over 4.5 MB |
| DELETE | `media/{id}/` | Discards an unattached upload |

Purposes: `avatar` and `cover` (images); `post`, `story` and `message` (image or video); `reel` (video).

### Posts and comments

| Method | Path | Notes |
|---|---|---|
| GET | `posts/feed/` | Followed accounts plus your own posts |
| GET | `posts/?author=&username=&hashtag=&category=&tagged=&visibility=&q=&media=only` | `visibility=private` with your own id lists your archive |
| POST | `posts/` | `caption, location, visibility, category` (slug), `comments_enabled, media_ids[], tagged_user_ids[], mood, music_title, event_title, event_starts_at` |
| GET / PATCH / DELETE | `posts/{id}/` | Only the author can write. Media cannot change after posting |
| POST / DELETE | `posts/{id}/like/`, `posts/{id}/save/` | Idempotent; returns the new count |
| GET | `posts/{id}/likes/` | Likers |
| GET / POST | `posts/{id}/comments/` | POST `content, parent_id?`. `posts/{id}/comment/` is an alias |
| POST | `posts/{id}/share/` | `recipient_ids[], message?`, sent as DMs |
| GET / DELETE | `comments/{id}/` | The comment author or the post author can delete |
| POST / DELETE | `comments/{id}/like/` | |
| GET | `comments/{id}/replies/` | |

Post `type` is one of `text`, `image`, `carousel`, `video` or `mixed`.

### Reels

| Method | Path | Notes |
|---|---|---|
| GET | `reels/?feed=for_you\|following&author=&hashtag=` | Use `page_size=5` and fetch the next page near the end |
| POST | `reels/` | `media_id, caption, audio_title, comments_enabled` |
| GET / PATCH / DELETE | `reels/{id}/` | |
| POST | `reels/{id}/view/` | `watched_seconds`; counted once per user |
| | `reels/{id}/like/`, `likes/`, `save/`, `comments/`, `share/` | Same as posts |

### Stories

| Method | Path | Notes |
|---|---|---|
| GET | `stories/` | Tray: `[{user, has_unseen, latest_at, stories[]}]`, you first, then unseen |
| POST | `stories/` | `media_id, caption?`; expires after `STORY_LIFETIME_HOURS` |
| GET | `stories/user/{user_id}/` | One user's active stories |
| GET / DELETE | `stories/{id}/` | |
| POST | `stories/{id}/view/` | |
| POST / DELETE | `stories/{id}/react/` | `reaction`: `fire, love, laugh, wow, sad, clap` |
| POST | `stories/{id}/reply/` | `content`, sent as a DM to the author and linked to the story |
| GET | `stories/{id}/viewers/` | Author only, with each viewer's reaction |

### Messages

| Method | Path | Notes |
|---|---|---|
| GET | `messages/?q=` | Conversations with `unread_count`, `last_message` and participants (with `is_online`) |
| POST | `messages/` | `recipient_id` **or** `conversation_id`, plus `content`, `media_id?`, `reply_to_id?` |
| POST | `messages/conversations/` | `recipient_id` (direct), or `participant_ids[]` and `title` (group) |
| GET | `messages/{conversation_id}/` | Messages, newest first. Your own messages have `status: sent\|read` |
| DELETE | `messages/{conversation_id}/` | Clears the thread for you |
| GET | `messages/{conversation_id}/info/` | |
| POST | `messages/{conversation_id}/read/` | |
| POST | `messages/{conversation_id}/mute/` | `muted: bool` |
| DELETE | `messages/message/{id}/` | Sender only. Soft-deletes the message ("Message deleted") and removes its media |
| GET | `messages/unread-count/` | |

### Notifications, search, discovery, reports, system

| Method | Path | Notes |
|---|---|---|
| GET | `notifications/?unread=true&type=like,comment` | Message notifications are excluded unless requested by `type`. `sender.is_following` is included |
| GET | `notifications/unread-count/` | `{notifications, messages}` |
| POST | `notifications/{id}/read/`, `notifications/read-all/` | |
| DELETE | `notifications/{id}/` | |
| GET | `search/?q=&type=all\|users\|hashtags\|posts\|reels` | `all` returns 5 of each |
| GET / POST / DELETE | `search/recent/` | POST `kind` (`query\|user\|hashtag`), `value`. DELETE clears all |
| DELETE | `search/recent/{id}/` | Removes one entry |
| GET | `explore/?tab=for_you\|trending\|following&category=` | Media posts ranked by engagement |
| GET | `explore/overview/` | Categories, trending hashtags, suggested users, trending reels |
| GET | `explore/categories/`, `explore/hashtags/{name}/` | |
| POST | `reports/` | `target_type` (`post\|reel\|comment\|user`), `target_id, reason, details?` |
| GET | `reports/reasons/` | Reason list for the report sheet |
| GET | `health/` | Liveness and database check (no auth) |
| GET | `cron/maintenance/` | Scheduled clean-up; needs `Authorization: Bearer <CRON_SECRET>` |

## 8. Media uploads

In the preferred flow, the file goes straight from the phone to Cloudinary and the API secret never leaves the server:

```
1. POST /api/media/sign/      {"purpose": "post", "resource_type": "image"}
   <- {upload_url, api_key, timestamp, signature, public_id, overwrite, allowed_formats, max_bytes, max_duration}

2. POST <upload_url>          multipart: file + every signed field exactly as returned
   <- Cloudinary response {public_id, version, signature, ...}

3. POST /api/media/           {"purpose", "resource_type", "public_id", "version", "signature"}
   <- {"id": 41, ...}

4. POST /api/posts/           {"caption": "...", "media_ids": [41, 42]}
```

**Ownership and validation**

- Files are stored under `CLOUDINARY_ROOT_FOLDER/u<user_id>/<purpose>/<uuid>`. Only the server can sign uploads into a user's folder.
- Step 3 checks Cloudinary's response signature. With `CLOUDINARY_VERIFY_WITH_ADMIN_API=True`, size, format and duration are re-read from Cloudinary rather than trusted from the client. A file that breaks the limits is deleted and rejected.

**Delivery URLs**

- Every response includes ready-made URLs with `f_auto,q_auto` (`url`, `medium`, `thumbnail`).
- Videos also include `url_sd` (540 px) and `poster`.
- The app never builds Cloudinary URLs itself.

**Cleanup**

- Uploads not attached to anything within 24 hours are deleted by the maintenance job.
- Deleting a post, story, reel or message attachment, or replacing a profile image, removes the Cloudinary file after the database transaction commits.

`POST /api/media/upload/` is a simpler path for small files such as avatars. The API validates the bytes, then uploads them itself.

## 9. Admin and moderation

The admin is at `/<ADMIN_URL>` (`/admin/` by default). Every model is registered with search, filters, date drill-down and pagination, and thumbnails and previews come from Cloudinary.

- **Users:** verify or unverify, suspend or reactivate (superusers are protected).
- **Posts, reels, comments:** hide or unhide. Deleting through the admin runs the same service code as the API, so counters, hashtags, notifications and Cloudinary files stay consistent.
- **Stories:** expire now.
- **Reports:** shows the target and its number of open reports. Actions are *Dismiss*, *Mark as actioned*, *Hide reported content*, and *Hide content and suspend its owner*. The reviewer and time are recorded.
- **Messages:** metadata only. Message content is not shown, to protect privacy.
- **Uploaded media:** every Cloudinary asset with its owner, purpose, size and attachment state.
- **Categories:** manage the Discover categories (name, slug, icon, cover, order). Without a cover, the newest public photo in the category is used.

## 10. Scheduled jobs

Expired stories disappear from every endpoint the moment they expire. These two jobs then reclaim the storage:

| Job | Effect |
|---|---|
| Expired stories | Deletes stories past `expires_at`, and their Cloudinary files |
| Orphaned uploads | Deletes uploads never attached to content after 24 h, and their Cloudinary files |

**On Vercel:** both jobs run from `GET /api/cron/maintenance/`, which Vercel Cron calls daily at 01:00 UTC (`vercel.json`).

- The endpoint requires `Authorization: Bearer <CRON_SECRET>` and returns `503` when `CRON_SECRET` is not set.
- It returns `{"expired_stories_deleted": n, "orphaned_uploads_deleted": n}`.
- It is idempotent, so a missed or duplicate run is harmless.

**On a VPS or Docker host:** use the management commands:

```cron
*/15 * * * *  cd /srv/flashx/backend && .venv/bin/python manage.py purge_expired_stories
0 * * * *     cd /srv/flashx/backend && .venv/bin/python manage.py purge_orphan_media
```

`purge_orphan_media` accepts `--older-than-hours` (default 24) and `--dry-run`.

## 11. Testing and code quality

The suite runs against PostgreSQL, because constraints, partial unique indexes and trigram search are Postgres features. Cloudinary is stubbed, so no network calls are made. The test database is created and dropped automatically. The `DATABASE_URL` user needs the `CREATEDB` privilege:

```sql
ALTER ROLE flashx CREATEDB;
```

```bash
python manage.py test tests --settings=config.settings.test
```

The 93 tests cover:

- **Accounts:** auth and token rotation, password reset, profile edits and image replacement, the follow graph.
- **Media:** upload signing, ownership checks, size/type/duration limits.
- **Posts:** visibility, feed pagination and query count, likes, saves and shares, comment threading and deletion rights.
- **Stories and reels:** stories (tray, expiry, reactions, replies, viewers) and reels (distinct views).
- **Messaging and notifications:** direct and group threads, read receipts, mute, soft delete, notifications.
- **Discovery and moderation:** search and explore, reports and moderation.
- **Operations:** the cron endpoint and every admin page.

The suite passes on Python 3.12, 3.13 and 3.14 with no deprecation warnings.

Other checks:

```bash
python manage.py makemigrations --check --dry-run   # models and migrations are in sync
python manage.py spectacular --validate --file /dev/null   # OpenAPI schema is valid
DJANGO_SETTINGS_MODULE=config.settings.production python manage.py check --deploy
pip install ruff && ruff check . && ruff format --check .
```

## 12. Deployment

### Vercel (recommended)

Vercel deploys this project with no build configuration:

- It finds `manage.py`.
- It runs the app from `config/wsgi.py` as one Vercel Function.
- It runs `collectstatic` during the build and serves `/static/` (the admin's CSS and JS) from its CDN.

| File | Purpose |
|---|---|
| `vercel.json` | Region `fra1` (Frankfurt), 60 s function limit, daily cron for `/api/cron/maintenance/` |
| `.python-version` | Python 3.12 runtime |
| `.vercelignore` | Keeps `.env`, tests, Docker files and caches out of the deployment |
| `config/settings/production.py` | Adds Vercel hostnames to `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS`; disables server-side cursors for pooled Postgres |

**1. Create the database.**

1. In the Vercel dashboard, open *Storage* and add **Neon Postgres**.
2. Choose **Frankfurt (eu-central-1)** so the database sits next to the `fra1` function, and connect it to the project.
3. Neon adds `DATABASE_URL` (pooled; the app uses it) and `DATABASE_URL_UNPOOLED` (for migrations).

Any PostgreSQL 14+ provider works; keep it in the same region as the function.

**2. Import the project.** Push the repository to GitHub. In Vercel, choose *Add New > Project* and import it. Set **Root Directory** to `backend` and leave the framework, build and output settings at their defaults.

**3. Add environment variables** under *Settings > Environment Variables*, for Production and Preview:

| Variable | Value |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` |
| `SECRET_KEY` | New value from `python -c "import secrets; print(secrets.token_urlsafe(50))"`. Do not reuse the local one |
| `DATABASE_URL` | Set by the Neon integration |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | From the Cloudinary dashboard |
| `CLOUDINARY_ROOT_FOLDER` | `flashx` for Production, `flashx-preview` for Preview |
| `CRON_SECRET` | New value from `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ADMIN_URL` | Something other than `admin/`, e.g. `control-7f3a/` |
| `EMAIL_BACKEND` and the SMTP variables | Needed for password-reset emails. Without them, emails only appear in the function logs |
| `DB_CONN_MAX_AGE`, limits, throttles | Optional; defaults apply |

You do not need `ALLOWED_HOSTS` for `*.vercel.app` or your custom domain.

**4. Deploy.** Click *Deploy*. Later pushes to the main branch deploy automatically.

**5. Migrate and create an admin user** from your computer, against the **unpooled** URL:

```bash
cd backend
source .venv/bin/activate
export DJANGO_SETTINGS_MODULE=config.settings.production
export SECRET_KEY='<the SECRET_KEY set on Vercel>'
export DATABASE_URL='<DATABASE_URL_UNPOOLED from Vercel, ends with ?sslmode=require>'
python manage.py migrate
python manage.py createsuperuser
# Optional demo content (not for a live user base; also export the CLOUDINARY_* variables):
python manage.py seed_dev --force --with-media
```

Shell variables take priority over `.env`, so these commands target the Neon database. Run `migrate` again whenever a deploy includes new migrations.

**6. Verify the deployment.**

- `https://<project>.vercel.app/api/health/` returns `{"success": true, "data": {"status": "ok", "database": true}}`.
- `https://<project>.vercel.app/<ADMIN_URL>` shows the styled admin login.
- *Settings > Cron Jobs* lists `/api/cron/maintenance/`. Run it once and expect HTTP 200.
- Put the URL in `mobile/env/production.json` as `https://<project>.vercel.app/api`.

**Custom domain.** Add it under *Settings > Domains*. It becomes `VERCEL_PROJECT_PRODUCTION_URL` and is accepted automatically. Then update `API_BASE_URL` in the app.

**Vercel limits that matter**

- Request and response bodies are capped at 4.5 MB. FlashX uploads media straight from the phone to Cloudinary, so this only affects `POST /api/media/upload/`.
- The first request after a quiet period is slower while the function starts.
- Rate limits are counted per function instance (in-memory cache), so they are looser than on one server. Add a shared cache such as Redis if you need strict limits.
- On the Hobby plan, crons run once a day at a random minute within the scheduled hour.
- If you move the function region, move the database to the same region.

### Linux VPS

1. Install Python 3.12+, PostgreSQL and Nginx, and create the database (section 3).
2. Clone the repository, create a virtualenv and run `pip install -r requirements.txt`.
3. Write `.env` from `.env.example` with these values:
   - `DJANGO_SETTINGS_MODULE=config.settings.production`
   - a new `SECRET_KEY`
   - `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` for your domain
   - `DB_DISABLE_SERVER_SIDE_CURSORS=False` (direct connection)
   - the Cloudinary keys and SMTP settings
4. Run `python manage.py migrate && python manage.py collectstatic --noinput`.
5. Run Gunicorn under systemd: `gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60`.
6. Configure Nginx to proxy to Gunicorn, terminate TLS (Let's Encrypt) and set `X-Forwarded-Proto`. If you use `/api/media/upload/` for video, set `client_max_body_size` to at least `MEDIA_VIDEO_MAX_BYTES`.
7. Add the cron jobs from section 10. `python manage.py check --deploy` should report no issues.

### Docker image

```bash
docker build -t flashx-api .
docker run --env-file .env.production -p 8000:8000 flashx-api
```

The image uses Python 3.12-slim, collects static files at build time, runs Gunicorn as a non-root user and uses production settings. `.dockerignore` keeps `.env` out of the image, so pass configuration at runtime. Run migrations once with `docker run --env-file .env.production flashx-api python manage.py migrate`.

## 13. Troubleshooting

| Symptom | Fix |
|---|---|
| `SECRET_KEY not found` / `DATABASE_URL not found` | Create `.env` (`cp .env.example .env`). On Vercel, add the variable for the environment being built and redeploy |
| `connection refused` on port 5432 | PostgreSQL is not running, or `DATABASE_URL` points to the wrong host or port |
| `permission denied to create extension "pg_trgm"` | Run `CREATE EXTENSION pg_trgm;` in the database as a superuser, then `migrate` |
| `permission denied to create database` when testing | `ALTER ROLE flashx CREATEDB;` |
| `503 media_service_unavailable` | Cloudinary keys are missing, or Cloudinary is unreachable |
| `invalid_upload_signature` | Step 3 must send Cloudinary's `version` and `signature` unchanged, and `CLOUDINARY_API_SECRET` must belong to the same account |
| `upload_not_owned` | The `public_id` must be the one `media/sign/` returned for the same user and purpose |
| `DisallowedHost` / `400 Bad Request` | Add the host or IP to `ALLOWED_HOSTS`. On Vercel, attach the domain under *Settings > Domains* |
| Emulator or phone cannot reach the API | Run `runserver 0.0.0.0:8000`. Use `10.0.2.2` on the Android emulator and the LAN IP on phones, and check the firewall |
| Every request returns `401` after a deploy | `SECRET_KEY` or `JWT_SIGNING_KEY` changed, which invalidates tokens; log in again |
| Password-reset email not arriving | Development prints emails to the server log; set the SMTP variables in production |
| Admin has no styling on Vercel | Check the build log for the `collectstatic` step; `STATIC_ROOT` must stay set in `base.py` |
| `cursor "_django_curs_..." does not exist` | Pooled connection with server-side cursors on; keep `DB_DISABLE_SERVER_SIDE_CURSORS=True` |
| Migration fails on the pooled host | Run migrations with the unpooled URL |
| Cron shows 401 or 503 | `CRON_SECRET` was added or changed after the last deploy. Environment changes apply on the next deploy, so redeploy |

## 14. Scope

Everything in the endpoint reference is implemented and tested.

The following are not part of this version, and no placeholder code exists for them:

- **Real-time delivery.** Chat, presence and unread counts use REST and polling. `is_online` comes from API activity within `ONLINE_WINDOW_SECONDS`. WebSockets (Django Channels + Redis) are the next step for live chat.
- **Push notifications** (FCM / APNs). Notifications are stored and served by the API.
- **Blocking and private accounts.** Visibility is set per post (`public`, `followers`, `private`).
- **Email verification** on sign-up.
- **Personalised ranking.** Explore ranks by engagement and recency.
# FlashX-Backend
