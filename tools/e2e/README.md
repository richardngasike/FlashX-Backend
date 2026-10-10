# Live video and call end-to-end checks

These scripts drive the real API and a real LiveKit server with two or three
headless WebRTC clients, so they prove media actually flows, not just that
the endpoints answer.

```bash
pip install livekit requests numpy
# 1. LiveKit dev server (binary from github.com/livekit/livekit/releases)
livekit-server --dev --bind 127.0.0.1 --node-ip 127.0.0.1
# 2. API pointed at it
LIVEKIT_URL=ws://127.0.0.1:7880 LIVEKIT_API_KEY=devkey LIVEKIT_API_SECRET=secret \
  python manage.py runserver 127.0.0.1:8000
# 3. Checks
python tools/e2e/live_e2e.py    # go live, viewer gets video, comments, guest invite/removal, end closes the room
python tools/e2e/calls_e2e.py   # ring, accept, two-way audio/video, end closes the room, busy, missed
```

Both end with `E2E OK` / `CALLS E2E OK`.
