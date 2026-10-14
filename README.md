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
    users/          custom User, JWT auth with presence, register/login, password reset (code and link), profiles
    follows/        follow graph, counters and follow requests for private accounts
    blocks/         blocking and the filters that hide blocked users everywhere
    media/          MediaAsset, Cloudinary service, upload signing and validation, orphan purge
    posts/          posts, carousel media, hashtags, categories, tags, mood/music/event, feed, visibility
    comments/       threaded comments (one reply level), comment likes
    likes/          likes for posts, reels and comments
    saves/          saved posts and reels
    stories/        24-hour stories, views, reactions, replies to DM, expiry purge
    reels/          short vertical video with distinct view counting
    messaging/      direct and group conversations, group photos and system events, delete for me, read state, shared posts/reels/stories
    notifications/  activity feed, device tokens, push delivery and real-time sync events (FCM), per-user preferences
    search/         search, recent searches, explore and discovery
    reports/        content reports and moderation actions
    live/           live video: streams, presence, comments, guest invites, LiveKit tokens
    ads/            sponsored posts with scheduling, weighting, impressions and clicks
    music/          sound library: Jamendo and Openverse search with licence filtering, original and uploaded sounds, saved, recent and trending sounds, sound pages
    gifs/           GIF creator (from photos or a video segment) and personal GIF library
    wallet/         coins, wallet ledger, gift catalogue and live gifts (buying coins is not open yet)
    calls/          voice and video calls between mutual followers (LiveKit rooms, ringing, missed calls)
  tests/            API test suite (270 tests, runs against PostgreSQL)
  tools/e2e/        end-to-end live video and call checks against a real LiveKit server
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
| `FCM_SERVICE_ACCOUNT_JSON` | empty | Firebase service-account key (raw JSON or base64) for push notifications. Empty turns push off |

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
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | empty, `587`, empty, empty | SMTP connection. Setting `EMAIL_HOST` turns on SMTP delivery; blank means emails only go to the server log. Spaces in Gmail app passwords are ignored. `health/` reports `features.email` |
| `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_TIMEOUT` | TLS on 587, SSL on 465, `15` | |
| `EMAIL_BACKEND` | automatic | Only to override (an `EMAIL_BACKEND=...console...` left over from an old `.env` keeps delivery off) |
| `DEFAULT_FROM_EMAIL` | `FlashX <no-reply@flashx.app>` | |
| `PASSWORD_RESET_URL` | `flashx://reset-password?uid={uid}&token={token}` | Link in reset emails; `{uid}` and `{token}` are filled in |

Password reset sends a 6-digit code (valid 15 minutes, 5 attempts) and the link. With the console backend nobody receives it, so production needs SMTP. Gmail setup:

1. Turn on 2-Step Verification for the Google account.
2. Create an app password at <https://myaccount.google.com/apppasswords>.
3. Set in Vercel, then redeploy:

```
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=you@gmail.com
EMAIL_HOST_PASSWORD=<the 16-character app password>
DEFAULT_FROM_EMAIL=FlashX <you@gmail.com>
```

Gmail allows about 500 emails a day. For more volume use a transactional provider (Brevo, Mailgun, SendGrid, Amazon SES) with the same variables and that provider's SMTP host.

### Live video

| Variable | Default | Notes |
|---|---|---|
| `LIVEKIT_URL` | empty | `wss://<project>.livekit.cloud` |
| `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | empty | From the LiveKit project settings. Blank turns live video and calls off (`live/` reports `available: false`, `calls/` returns `503 calls_unavailable`) |
| `LIVE_MAX_GUESTS` | `99` | Guests on screen besides the host (rooms of up to 100). LiveKit plan limits still apply |

### GIFs in chats

| Variable | Default | Notes |
|---|---|---|
| `GIPHY_API_KEY` | empty | From <https://developers.giphy.com>. Used first when set |
| `TENOR_API_KEY` | empty | Alternative provider (Tenor API v2 key from Google Cloud) |
| `GIF_RATING` | `pg-13` | GIPHY content rating |

Both keys blank: `messages/gifs/` returns `enabled: false` and the app hides GIF search. Keys stay on the server; the app never sees them.

### Music

| Variable | Default | Notes |
|---|---|---|
| `JAMENDO_CLIENT_ID` | empty | Client id from <https://devportal.jamendo.com>. Blank hides the song catalogue; original sounds still work |
| `MUSIC_OPENVERSE` | `True` | Openverse (api.openverse.org) music search: openly licensed songs (CC0, public domain, CC BY, CC BY-SA) from Jamendo, ccMixter, Freesound, Wikimedia and others. Works without a key |
| `OPENVERSE_CLIENT_ID`, `OPENVERSE_CLIENT_SECRET` | empty | Strongly recommended on Vercel: anonymous Openverse access is rate limited per IP, and Vercel servers share IPs, so searches can come back empty. Register once (free): `curl -X POST https://api.openverse.org/v1/auth_tokens/register/ -H "Content-Type: application/json" -d '{"name":"FlashX","description":"Licensed music for user posts","email":"you@example.com"}'`, confirm the email Openverse sends, then set both values. `GET api/health/` shows `features.music_status` (`openverse_authenticated`, `openverse_last_error`, e.g. `HTTP 429`) |
| `MUSIC_COMMERCIAL_USE` | `True` | Keep `True` while the app shows ads: only tracks whose licence allows commercial use (no `NC`) are offered. Tracks with `ND` are never offered because posts mix and trim them |

### Wallet and gifts

| Variable | Default | Notes |
|---|---|---|
| `WALLET_WELCOME_COINS` | `500` | Coins added once, the first time someone opens their wallet or sends a gift. `0` turns the bonus off |
| `WALLET_KSH_PER_COIN` | `1.0` | Only for showing a coin balance in Ksh. Coins are not exchangeable for money |
| `WALLET_MAX_GIFT_QUANTITY` | `99` | Most gifts of one kind sent in one tap |

Buying coins is not open: there is no payment provider yet, and coins sold inside the Android app must go through Google Play Billing. `POST wallet/recharge/` and `POST wallet/transfer/` answer `503 coming_soon`.

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
| `MESSAGE_AUDIO_MAX_SECONDS` | `1800` | Voice notes and audio files in chats |
| `SOUND_MAX_SECONDS` | `600` | Longest audio file for an uploaded or recorded sound |
| `SOUND_CLIP_MAX_SECONDS` | `180` | Longest part of that file a sound plays |
| `GIF_MAX_BYTES` | `8388608` | Largest finished GIF (8 MB) |
| `GIF_VIDEO_MAX_SECONDS` | `10` | Longest video segment turned into a GIF |
| `GIF_MAX_FRAMES` | `30` | Most photos in one GIF |
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
| `THROTTLE_REACTION` | `120/min` | Message reactions and live reaction counts |
| `THROTTLE_TYPING` | `40/min` | Typing signals |
| `THROTTLE_GIF_SEARCH` | `60/min` | GIF and sticker search |
| `THROTTLE_LIVE_ACTION` | `60/min` | Live requests, invites, moderation and polls |

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

All paths are under `/api/`. Every endpoint needs a token except `health/`, `auth/register/`, `auth/login/`, `auth/refresh/`, `auth/password/reset/`, `auth/password/reset/confirm/`, `auth/password/reset/code/` and `auth/username-available/`. The cron endpoint uses its own secret.

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
| POST | `auth/password/reset/code/` | `email, code` (6 digits from the email), `new_password`. Signs out every session |
| GET | `auth/username-available/?username=` | `{available, reason}` |

### Users and follows

| Method | Path | Notes |
|---|---|---|
| GET / PATCH / DELETE | `users/me/` | PATCH: `full_name, username, bio, website, location, profile_image_id, cover_image_id` (`null` removes an image), `is_private`, `show_activity_status`, `allow_calls`, `notification_prefs` (partial map of switches: `pause_all, messages, calls, comments, likes, mentions, follows, stories, live`). DELETE needs `password` |
| POST | `users/me/presence/` | `online: bool`. The app sends `true` on start and every 60 s while open, `false` when it goes to the background |
| GET | `users/me/follow-requests/` | Pending requests to your private account |
| POST | `users/me/follow-requests/{user_id}/approve\|decline/` | |
| GET | `users/me/likes/` | Posts you liked, newest like first |
| GET | `users/me/saved/?type=posts\|reels` | Saved items, newest save first |
| GET | `users/{id}/`, `users/by-username/{username}/` | Profile with counts, `is_following`, `follows_you`, `is_me`, `is_blocked`. 404 if they blocked you |
| POST / DELETE | `users/{id}/follow/` | Follow (201 when created) or, for a private account, request to follow. Returns `follow_status` (`following\|requested\|none`). DELETE unfollows or cancels the request |
| DELETE | `users/{id}/remove-follower/` | |
| GET | `users/{id}/followers/`, `users/{id}/following/` | Optional `?q=` filter |
| GET | `users/suggested/?page=&page_size=` | People you may know: people who follow you, then mutual connections. Each card has `mutual_count`, `mutual_preview` (up to 2 first names) and `reason` ("Follows you", "Followed by Faith + 2 more"). `limit` works as the page size |
| POST / DELETE | `users/{id}/block/` | Block / unblock. Blocking removes follows both ways and the notifications between you |
| GET | `users/me/blocked/` | Accounts you blocked, with `blocked_at` (page style) |

**Blocking** works in both directions. Neither person sees the other's posts, reels, stories, comments, likes lists, search results, sounds, presence or suggestions. Neither can follow, message, call, tag or notify the other. The person who blocked can still open the other's profile (`is_blocked: true`) to unblock and gets `403 blocked` when sending. The blocked person is never told: their requests get the same generic `404 user_unavailable` ("This account isn't available") as a deleted account, and `is_blocked` is only ever true for the person who blocked. Existing direct threads stay listed with `can_message: false`; groups both people are in keep working.

**Private accounts.** Posts, reels, stories and follower lists are visible only to approved followers. Profiles show `follow_status`, `can_view_content` and `is_private`. Switching back to public approves every pending request.

**Activity status.** `is_online` and `last_seen_at` are returned only when both people have `show_activity_status` on and neither blocked the other (reciprocal, like other apps). Someone counts as online while their last heartbeat is under 150 s old.

### Media

| Method | Path | Notes |
|---|---|---|
| POST | `media/sign/` | `purpose, resource_type` returns signed Cloudinary parameters |
| POST | `media/` | Registers a direct upload and returns a `MediaAsset` (attach it by `id`) |
| POST | `media/upload/` | Multipart `purpose, file`; the server uploads to Cloudinary. Vercel rejects bodies over 4.5 MB |
| DELETE | `media/{id}/` | Discards an unattached upload |

Purposes: `avatar`, `cover` and `group` (images); `post`, `story` and `message` (image or video); `reel` (video); `sound` (an audio file, or a video to take the sound from, plus an optional cover image); `gif` (a GIF file, or the photos or video a GIF is made from). Media responses include `large` (viewer) and `original` (download) URLs.

### Posts and comments

| Method | Path | Notes |
|---|---|---|
| GET | `posts/feed/?seed=` | Ranked For you feed: people you follow, your own posts and public discovery, scored by engagement, freshness, relationship and interest (authors and categories you engage with), with posts you already liked or saw at the top of your last refresh stepping back and authors spread out. Send a new `seed` on each pull-to-refresh for a fresh mix; `next` keeps the seed so paging is stable (rankings are cached 15 minutes per seed). `?feed=following` gives followed accounts and your own posts, newest first |
| GET | `posts/?author=&username=&hashtag=&category=&tagged=&visibility=&q=&media=only` | `visibility=private` with your own id lists your archive |
| POST | `posts/` | `caption, location, visibility, category` (slug), `comments_enabled, media_ids[], tagged_user_ids[], mood, music_title, event_title, event_starts_at` |
| GET / PATCH / DELETE | `posts/{id}/` | Only the author can write. Media cannot change after posting |
| POST / DELETE | `posts/{id}/like/`, `posts/{id}/save/` | Idempotent; returns the new count |
| GET | `posts/{id}/likes/` | Likers |
| GET / POST | `posts/{id}/comments/?sort=oldest\|newest\|top` | The pinned comment comes first. POST `content, parent_id?, gif_url?, gif_width?, gif_height?` (text, a GIF or both; GIFs from GIPHY/Tenor or the sender's GIF library). `posts/{id}/comment/` is an alias. Reading comments doesn't count towards the posting rate limit |
| POST | `posts/{id}/share/` | `recipient_ids[], message?`, sent as DMs |
| GET / PATCH / DELETE | `comments/{id}/` | PATCH `content`: the comment author edits (`is_edited` turns true). The comment author or the post author can delete |
| POST / DELETE | `comments/{id}/pin/` | The post or reel author pins one top-level comment (pinning another replaces it) |
| POST / DELETE | `comments/{id}/like/` | |
| GET | `comments/{id}/replies/` | |

Post `type` is one of `text`, `image`, `carousel`, `video` or `mixed`.

### Reels

| Method | Path | Notes |
|---|---|---|
| GET | `reels/?feed=for_you\|following&author=&hashtag=&seed=` | For You lists reels you haven't watched first, then ranks by engagement and freshness with a shuffle. Send a new `seed` on every pull-to-refresh for a new mix; the same seed keeps pages stable. Use `page_size=5` and fetch the next page near the end |
| POST | `reels/` | `media_id, caption, audio_title, comments_enabled` |
| GET / PATCH / DELETE | `reels/{id}/` | |
| POST | `reels/{id}/view/` | `watched_seconds`; counted once per user |
| | `reels/{id}/like/`, `likes/`, `save/`, `comments/`, `share/` | Same as posts |

### Stories

| Method | Path | Notes |
|---|---|---|
| GET | `stories/` | Tray: `[{user, has_unseen, latest_at, stories[]}]`, you first, then unseen |
| POST | `stories/` | `media_id, caption?`, optional `sound_id, sound_start, sound_volume, original_volume` (a song over the story); expires after `STORY_LIFETIME_HOURS` |
| GET | `stories/user/{user_id}/` | One user's active stories |
| GET / DELETE | `stories/{id}/` | |
| POST | `stories/{id}/view/` | |
| POST / DELETE | `stories/{id}/react/` | `reaction`: `fire, love, laugh, wow, sad, clap` |
| POST | `stories/{id}/reply/` | `content`, sent as a DM to the author and linked to the story |
| GET | `stories/{id}/viewers/` | Author only, with each viewer's reaction |

### GIFs

| Method | Path | Notes |
|---|---|---|
| GET | `gifs/?q=` | Your GIF library, newest first. Paged |
| GET | `gifs/discover/?q=` | Other people's public GIFs, most used first |
| POST | `gifs/upload/` | `media_id` (a .gif uploaded with purpose `gif`), `title?, is_public?` |
| POST | `gifs/from-video/` | `media_id` (video uploaded with purpose `gif`), `start, end` (at most `GIF_VIDEO_MAX_SECONDS`), `width` (240, 320, 480), `fps` (5, 8, 10, 12, 15), `speed` (0.5, 1, 1.5, 2), `text?` (caption on the GIF), `title?, is_public?`. Rendered by Cloudinary; the source video is deleted afterwards |
| POST | `gifs/from-images/` | `media_ids[]` (2 to `GIF_MAX_FRAMES` photos uploaded with purpose `gif`, in order), `delay_ms` (60 to 3000 per photo), `width`, `aspect` (`square`, `portrait`, `landscape`), `loop` (false plays once), `text?, title?, is_public?`. Assembled on the server; the photos are deleted afterwards |
| GET / PATCH / DELETE | `gifs/{id}/` | Yours or a public one; owner PATCH `title, is_public`, DELETE removes the file |

A GIF's `url` can be sent as `gif_url` in messages and comments by its owner, or by anyone if it is public. GIFs are silent.

### Messages

| Method | Path | Notes |
|---|---|---|
| GET | `messages/?q=&archived=1` | Conversations with `unread_count`, `last_message`, participants (with `is_online`), `is_pinned`, `is_archived`, `marked_unread`, and `invite_code` for group admins. The first page starts with your pinned chats and carries `archived_count`. `archived=1` lists archived chats. Fetching marks messages as delivered |
| POST | `messages/` | `recipient_id` **or** `conversation_id`, plus any of `content`, `media_ids[]` (up to 10: photos, videos, audio/voice notes, documents), `attachment_names` (`{"<asset id>": "Report.pdf"}`), `gif_url` (GIPHY/Tenor CDN only), `reply_to_id`, `client_id` (a retry with the same value returns the first message instead of a duplicate). `media_id` still works |
| POST | `messages/conversations/` | `recipient_id` (direct), or `participant_ids[]` and `title` (group, up to 256 people) |
| GET | `messages/{conversation_id}/?q=` | Messages, newest first. `q` searches text and file names. Your own messages have `status: sent\|delivered\|read`. Each message has `attachments[]` (`kind: image\|video\|audio\|file`, URLs, `name`, `bytes`, `duration`), `reactions[]` (`{emoji, count, mine}`), `gif_url`, `forwarded`, `is_edited`, `is_pinned`, `client_id` |
| DELETE | `messages/{conversation_id}/` | Clears the thread for you |
| POST | `messages/{conversation_id}/read/` | Also clears "marked as unread" |
| POST | `messages/{conversation_id}/mute/` | `muted: bool` |
| POST | `messages/{conversation_id}/state/` | Your list only: `pinned` (up to 5), `archived`, `unread` (all optional booleans) |
| GET / POST | `messages/{conversation_id}/typing/` | POST `typing: bool` while typing (refresh every few seconds; it lapses after 6 s). GET: who is typing. Sending a message clears it |
| GET | `messages/{conversation_id}/pinned/` | Pinned messages |
| POST | `messages/{conversation_id}/leave/` | Leave a group. If the creator leaves, the longest-standing member takes over. An empty group is deleted |
| GET / POST | `messages/{conversation_id}/members/` | GET: every member, admins first, each with `is_admin` and `is_creator`. POST (admin): `user_ids[]` |
| PATCH / DELETE | `messages/{conversation_id}/members/{user_id}/` | PATCH (admin): `is_admin`. DELETE: admin removes someone; removing yourself is leaving |
| GET / POST / DELETE | `messages/{conversation_id}/invite/` | Admins: GET the invite code (created on first use), POST resets it, DELETE turns links off. The link is `https://<share host>/g/<code>/` |
| GET / POST | `messages/join/{code}/` | GET previews the group, POST joins (a "joined using the group link" event is recorded). Blocked people get the same answer as an invalid link |
| GET / PATCH | `messages/{conversation_id}/info/` | PATCH (group admin): `title`, `image_id`, `remove_image` |
| PATCH | `messages/message/{id}/` | Sender edits the text within 15 minutes |
| DELETE | `messages/message/{id}/?for=everyone\|me` | `everyone` (sender only): "Message deleted" for all and the files are removed unless a forwarded copy still uses them. `me`: hides it for you |
| POST | `messages/message/bulk-delete/` | `ids[]` (up to 100), `scope: me\|everyone` |
| POST | `messages/message/{id}/forward/` | `conversation_ids[]` and/or `recipient_ids[]` (up to 20 chats). Text, GIF and files are copied |
| GET / POST / DELETE | `messages/message/{id}/react/` | POST `emoji` (one per person; reacting again changes it), DELETE removes yours, GET lists who reacted |
| POST | `messages/message/{id}/pin/` | `pinned: bool`. Group admins only in groups; either person in a direct chat |
| GET | `messages/gifs/?q=&type=gifs\|stickers&offset=` | GIF/sticker search through the server (trending when `q` is empty). `{enabled, results: [{id, url, preview, width, height}], next_offset}` |
| GET | `messages/unread-count/` | |

Documents are uploaded like other media: `POST media/sign/` with `purpose: message`, `resource_type: raw` and `filename` (allowed: pdf, doc, docx, txt, rtf, xls, xlsx, csv, ppt, pptx, zip and the other types in `FLASHX_MEDIA_LIMITS["FILE_FORMATS"]`, up to 25 MB). Audio and voice notes use `resource_type: video` with an audio file (m4a, aac, mp3, wav, ogg, opus, amr, flac). Attachment URLs are long random Cloudinary paths; they are not signed, so anyone holding a URL can open that file.

Groups record system messages (`kind: system`, `event: created\|added\|removed\|left\|renamed\|photo\|photo_removed\|joined`). Real-time updates are FCM data messages (`sync: message\|message_edited\|message_deleted\|message_reaction\|message_pinned\|typing\|delivered\|conversation`), with polling as the fallback.

### Calls

Voice and video calls between people who follow each other, carried by the same LiveKit project as live video. Signalling is the API plus FCM data messages (`sync: call`), with polling as a fallback.

| Method | Path | Notes |
|---|---|---|
| GET | `calls/` | Your call history (missed, declined, ended...) |
| POST | `calls/` | `user_id, kind` (`voice\|video`). Requires a mutual follow, `allow_calls` on their side and no block. `409 already_in_call` if you are in one; the call comes back `busy` if they are |
| GET | `calls/{id}/` | Current status. Ringing calls expire to `missed` after 45 s |
| POST | `calls/{id}/accept/` | Callee. Returns the LiveKit `{url, token, room}`; `410` if the call already ended |
| POST | `calls/{id}/decline/`, `calls/{id}/cancel/`, `calls/{id}/end/` | `end` accepts `failed: true` when the connection could not be made |

Missed and cancelled calls create a `missed_call` notification. Ending a call deletes its LiveKit room.
### Live video

Video and audio travel through LiveKit. The API issues room tokens and keeps the stream, presence, comments, guests, moderation and polls. The app polls `comments/` every few seconds; that call is also the heartbeat. A stream whose host is silent for 90 seconds ends by itself. Floating reactions go device-to-device over LiveKit's data channel; the API only counts them.

Roles: **host** (everything), **moderator** (chosen by the host: comments, requests, mutes, removals, bans), **guest** (on screen, up to `LIVE_MAX_GUESTS`), **viewer**. Host and moderator actions are written to a moderation log (admin).

| Method | Path | Notes |
|---|---|---|
| GET | `live/` | `{available, results}`: streams on air you may watch, people you follow first |
| POST | `live/` | `title?, description?, category?` (chat, music, gaming, education, sports, fitness, food, travel, art, tech, business, news, comedy, beauty, other), `visibility: public\|followers\|private`, `allowed_user_ids[]` (private), `audio_only`, `allow_requests`, `notify_followers`. Returns `{stream, role, livekit}`. Followers (or the chosen people for a private live) are notified |
| GET / PATCH | `live/{id}/` | PATCH (host): `title, description, category, allow_requests` |
| POST | `live/{id}/join/` | Start watching. `role` is `viewer` or `guest`. `403 live_banned` for removed people |
| POST | `live/{id}/leave/` | Stop watching. The host leaving ends the stream |
| POST | `live/{id}/end/` | Host only |
| POST | `live/{id}/pause/` | Host: `on: bool` |
| GET | `live/{id}/comments/?after=` | Room state: `status, viewer_count, role, invited, requested, muted_by_host, is_moderator, guests[] (with muted_by_host), comments, removed_comment_ids, pinned_comment, pinned_user_id, moderator_ids, poll, title, description, category, audio_only, allow_requests, is_paused, reactions_count, duration_seconds, max_guests`; managers also get `requests[]` and `questions[]` |
| POST | `live/{id}/comments/` | `text` (max 300), `reply_to_id?`, `kind: comment\|question` |
| DELETE | `live/{id}/comments/{comment_id}/` | Author, host or moderator |
| POST | `live/{id}/comments/{comment_id}/answer/` | Host/moderator marks a question answered |
| GET | `live/{id}/viewers/` | People watching now; managers also see `live_status` and `is_moderator` |
| POST | `live/{id}/invite/` | Host/moderator: `user_id` of someone watching |
| POST | `live/{id}/invite/respond/` | `accept`. Accepting returns a token that can publish |
| GET / POST / DELETE | `live/{id}/requests/` | Viewer: POST asks to join on screen, DELETE cancels. Managers: GET the queue |
| POST | `live/{id}/requests/{user_id}/` | Managers: `on: true` accepts, `false` rejects |
| POST | `live/{id}/guests/{user_id}/mute/` | Managers: `on: bool`. Removes microphone publishing in LiveKit; the camera stays |
| POST | `live/{id}/guests/{user_id}/remove/` | Managers take a guest off screen, or a guest steps down |
| POST | `live/{id}/pin/` | `user_id` (host: large view for everyone, `null` = host) or `comment_id` (managers, `null` unpins) |
| POST | `live/{id}/moderators/` | Host: `user_id, on` |
| POST / DELETE | `live/{id}/bans/` | Managers: `user_id`. Disconnects them and blocks rejoining this live |
| POST | `live/{id}/polls/` | Managers: `question`, `options[]` (2 to 6). Opening a poll closes the previous one |
| POST | `live/{id}/polls/{poll_id}/vote/`, `.../close/` | `option` index; voting again changes it |
| POST | `live/{id}/reactions/` | `count` (1 to 50), batched by the app |
| GET | `live/{id}/stats/` | Host: duration, peak and total viewers, comments, reactions, questions, guests, polls, new followers, average watch time |
| POST | `live/{id}/report/` | `reason, details?`. Goes to the moderation queue against the host, tagged with the live |
| POST | `live/{id}/gifts/` | `gift` (slug), `quantity` (1 to `WALLET_MAX_GIFT_QUANTITY`), `recipient_id?` (host by default, or a guest on screen), `client_id` (retries with the same id never charge twice). Returns `{id, gift, coins, recipient_id, balance}`. `402 insufficient_coins` with `details: {balance, needed}`. Posts a `kind: gift` line in the live chat (with `gift: {slug, name, coins, size, quantity, coins_total, recipient}`) that every viewer's app animates; room state and stats include `gifts_coins`, stats also `gifts` and `top_gifters` |

Live is not recorded: there is no replay.

### Wallet

Every balance change is an append-only `WalletTransaction` written in the same database transaction as the balance, with both wallets locked in user-id order.

| Method | Path | Notes |
|---|---|---|
| GET | `wallet/` | `{balance, ksh_value, ksh_per_coin, received_coins, sent_coins, recent[5], payments_available}`. The first call adds the welcome bonus |
| GET | `wallet/transactions/?type=` | `all, gifts, gifts_sent, gifts_received, recharge, bonus, transfers`. Paged; each item `{id, kind, title, subtitle, amount (signed), balance_after, gift, counterparty, created_at}` |
| GET | `wallet/gift-history/?direction=received\|sent` | Paged: `{id, gift, coins, direction, user, stream_id, created_at}` |
| GET | `wallet/gifts/` | Gift catalogue: `slug, name, coins, size (small, medium, large, epic), category (gifts, special, events)`. Artwork ships in the app |
| GET / POST | `wallet/recharge/` | GET: payment methods and coin packages with `available: false`. POST: `503 coming_soon` |
| POST | `wallet/transfer/` | `503 coming_soon` |

### Sponsored posts and music

| Method | Path | Notes |
|---|---|---|
| GET | `ads/?count=3` | Running ads, weighted random. The app shows one after every few feed posts, labelled Sponsored |
| POST | `ads/{id}/impression/` | Ad was on screen |
| POST | `ads/{id}/click/` | Button tapped. Returns `{link_url}` to open |
| GET | `music/search/?q=&genre=&limit=&min_duration=&max_duration=` | `{catalogue, original, popular, trending, catalogue_enabled}`. `catalogue`: Jamendo tracks (when `JAMENDO_CLIENT_ID` is set) plus Openverse tracks (title, artist or genre match) whose Creative Commons licence fits the app (see `MUSIC_COMMERCIAL_USE`). `original`: FlashX users' original sounds. Empty `q` and `genre` return trending tracks and the most used sounds |
| GET | `music/genres/` | Genre filters for the picker |
| GET / PATCH / DELETE | `music/sounds/{id}/` | One stored sound with `uses_count`, `usable`, `available`, `is_saved`, `is_owner`. Owner: PATCH `title, description, allow_reuse, is_public` (public/private only for uploaded sounds); DELETE removes an uploaded sound everywhere (posts that used it keep the name, with no audio) or stops reuse of a video's sound |
| POST | `music/sounds/` | Publish an original sound: `media_id` (audio uploaded with purpose `sound`), `title, description?, start?, end?` (seconds of the file to use, at most `SOUND_CLIP_MAX_SECONDS`), `cover_media_id?, is_public, allow_reuse` |
| POST / DELETE | `music/sounds/{id}/save/` | Save or unsave a sound |
| POST | `music/sounds/{id}/report/` | `reason, details?` (`ip` for audio used without permission). Reports of sounds also work through `reports/` with `target_type=sound` |
| GET | `music/saved/`, `music/mine/` | Saved sounds; your original sounds (including private and switched-off ones). Paged |
| GET | `music/recent/`, `music/trending/` | Sounds you used recently; sounds used most on new posts, reels and stories this week |
| GET | `music/sounds/{id}/posts/`, `music/sounds/{id}/reels/` | Posts or reels using a sound, newest first |

Posts, reels and stories take `sound_id` (`"123"` for a stored sound, `"jamendo:456"` or `"openverse:<uuid>"` for a catalogue track), `sound_start` (seconds), `sound_volume` and `original_volume` (0..1). Catalogue details are re-read from Jamendo on the server, so the app cannot attach arbitrary audio. Reads return `sound` with the licence name and link, an `attribution` line, the mix, and `plays_separately` (false when the sound is the video's own soundtrack). A video posted without a chosen song becomes an **original sound** others can use, unless `allow_sound_reuse` is false or the post is not public. No YouTube or other unlicensed audio is used. Older posts keep their Deezer or iTunes preview as an inactive sound that still plays but cannot be added to new posts.

**Licensing.** The Jamendo API is free for non-commercial apps. FlashX shows ads, so before launching with the catalogue switched on, get Jamendo's agreement for commercial use of the API (contact Jamendo through the developer portal). Independently of that, `MUSIC_COMMERCIAL_USE=True` (the default) offers only tracks whose Creative Commons licence allows commercial use, and `ND` tracks are never offered. Show the `attribution` line and licence wherever the sound appears; the app does.

### Share links

`/p/{id}/`, `/r/{id}/`, `/u/{username}/`, `/l/{id}/` (public lives) and `/g/{code}/` (group invites) (outside `/api/`) are public pages with Open Graph tags for link previews. They show only public content and open the app through `flashx://open/...` (Android App Links also claim them).

### Notifications, search, discovery, reports, system

| Method | Path | Notes |
|---|---|---|
| GET | `notifications/?unread=true&type=like,comment` | Message notifications are excluded unless requested by `type`. `sender.is_following` is included |
| GET | `notifications/unread-count/` | `{notifications, messages}` |
| POST | `notifications/{id}/read/`, `notifications/read-all/` | |
| DELETE | `notifications/{id}/` | |
| POST / DELETE | `notifications/devices/` | Register this phone for push (`token, platform` = `android\|ios`, `app_version?`) after login and on token refresh; DELETE with `token` before logout |
| GET | `search/?q=&type=all\|users\|hashtags\|posts\|reels` | `all` returns 5 of each |
| GET / POST / DELETE | `search/recent/` | POST `kind` (`query\|user\|hashtag`), `value`. DELETE clears all |
| DELETE | `search/recent/{id}/` | Removes one entry |
| GET | `explore/?tab=for_you\|trending\|following&category=` | Media posts ranked by engagement |
| GET | `explore/overview/` | Categories, trending hashtags, suggested users, trending reels |
| GET | `explore/categories/`, `explore/hashtags/{name}/` | |
| POST | `reports/` | `target_type` (`post\|reel\|comment\|user`), `target_id, reason, details?` |
| GET | `reports/reasons/` | Reason list for the report sheet |
| GET | `health/` | Liveness, database and push check (no auth). `migrations_pending` > 0 (status 503) means the deploy has migrations the database lacks, so most requests fail with 500 until `manage.py migrate` runs. `features` reports `live`, `calls`, `push` and `music`; `api_version` is 3 |
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
- **Sponsored posts:** create and schedule ads (see below).
- **Live videos:** every stream with its comments; *End selected live videos* stops a stream for everyone.
- **Categories:** manage the Discover categories (name, slug, icon, cover, order). Without a cover, the newest public photo in the category is used.

### Running an ad

1. Open the admin and go to *Sponsored posts > Add*.
2. Fill in the advertiser name, headline, body text and the link people should open (`link_url`).
3. Upload the image (1080 x 1080 or 1080 x 1350 works best) and, optionally, the advertiser logo. Both go to Cloudinary under `<CLOUDINARY_ROOT_FOLDER>/ads`. You can paste an image URL instead.
4. Pick the button text, then set `starts_at` / `ends_at` to schedule it (leave blank to run immediately and indefinitely).
5. `weight` (1 to 10) makes an ad show more often relative to the others.
6. Save with *Active* ticked. Impressions, clicks and click-through rate appear in the list; use the *Activate* / *Deactivate* actions to pause campaigns.

If no ad is running, the feed simply shows no sponsored posts.

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

The 156 tests cover:

- **Accounts:** auth and token rotation, password reset by link and by 6-digit code (expiry, attempt limit), profile edits and image replacement, the follow graph, blocking in both directions, People you may know.
- **Media:** upload signing, ownership checks, size/type/duration limits.
- **Posts:** visibility, feed pagination and query count, likes, saves and shares, comment threading and deletion rights.
- **Stories and reels:** stories (tray, expiry, reactions, replies, viewers) and reels (distinct views, ranked For You with refresh shuffles).
- **Messaging and notifications:** direct and group threads, group membership (leave, add, remove, admin handover), read receipts, mute, soft delete, notifications, push delivery (FCM payloads, muted threads, dead tokens, rollbacks; FCM is mocked).
- **Discovery and moderation:** search and explore, reports and moderation.
- **Live, ads and music:** going live, LiveKit token grants, comments, presence, guest invites, stale-host expiry, blocking; ad scheduling and counters; song search with fallback (HTTP mocked) and sounds on posts and reels.
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
| `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Needed for password-reset emails. Without them, codes only appear in the function logs. Remove any `EMAIL_BACKEND` set to the console backend |
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

### Push notifications (Firebase)

Push is sent from the API through Firebase Cloud Messaging (HTTP v1). It is off until the key is set.

1. In the [Firebase console](https://console.firebase.google.com), create a project, then add the Android app (`app.flashx.flashx`) and the iOS app. Running `flutterfire configure` in `mobile/` does this for you.
2. Open *Project settings > Service accounts > Generate new private key*.
3. Add the key to Vercel as `FCM_SERVICE_ACCOUNT_JSON`. Paste the JSON on one line, or base64 it first: `base64 -i key.json | tr -d '\n'`. Then redeploy.
4. iOS delivery also needs an APNs key (Apple Developer account) uploaded under *Project settings > Cloud Messaging*.

Every notification the API stores (likes, comments, follows, follow requests, mentions, tags, story reactions and replies, messages, group invites, missed calls, shares) is pushed to the recipient's phones after the database commit, unless the recipient turned that group off in `notification_prefs`.

FCM also carries the real-time layer: small data-only messages (`sync: message | message_deleted | conversation | call | ...`, ids only, no content) tell open apps to fetch the change. Chat, the chat list, unread badges, groups and call signalling update within a second when push is configured, and fall back to polling when it is not.

- Muted conversations, blocked users and your own actions are never pushed.
- Messages go to the high-priority `flashx_messages` channel. Everything else goes to `flashx_activity`.
- Both channels play the bundled `flashx_notification` sound.
- Tokens that Firebase reports as dead are deleted automatically.

### Live video (LiveKit)

1. Create a free project at <https://cloud.livekit.io>.
2. In *Settings > Keys*, create an API key. Copy the WebSocket URL, the key and the secret.
3. Add `LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` to Vercel and redeploy.
4. `GET /api/live/` now returns `"available": true` and the Go Live button works in the app. Voice and video calls use the same project.

`tools/e2e/live_e2e.py` and `tools/e2e/calls_e2e.py` run a full broadcast and a full call against a real LiveKit server (see `tools/e2e/README.md`).

If Go Live fails in the app: a `404` on `POST /api/live/` means the server is running an older deploy without the live app (redeploy and run `python manage.py migrate`); `503 live_unavailable` means the three `LIVEKIT_*` variables are missing.

The secret never leaves the server. The app only receives short-lived room tokens.

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

- **Real-time delivery.** Chat, presence and unread counts use REST and polling; push notifications alert phones when the app is closed. `is_online` comes from API activity within `ONLINE_WINDOW_SECONDS`. WebSockets (Django Channels + Redis) are the next step for live chat.
- **Private accounts.** Visibility is set per post (`public`, `followers`, `private`).
- **Voice calls and live streaming** (next phases, on LiveKit).
- **Email verification** on sign-up.
- **Personalised ranking.** Explore ranks by engagement and recency.
