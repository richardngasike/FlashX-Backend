"""
End-to-end Go Live check: the API plus a real LiveKit server and headless
WebRTC clients. Proves that a viewer actually receives the host's video,
comments and notifications work, a guest can be invited and removed, and
ending the stream closes the room for everyone.
"""

import asyncio

from client import ApiUser, FakeCamera, check
from livekit import rtc


async def wait_for_video(room: rtc.Room, frames_needed: int = 5, timeout: float = 20) -> int:
    """Count frames of the first remote video track until [frames_needed]."""
    done = asyncio.Event()
    frames = 0

    async def read(track: rtc.Track) -> None:
        nonlocal frames
        async for _ in rtc.VideoStream(track):
            frames += 1
            if frames >= frames_needed:
                done.set()
                return

    @room.on("track_subscribed")
    def _on_track(track, _publication, _participant):
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            asyncio.ensure_future(read(track))

    await asyncio.wait_for(done.wait(), timeout)
    return frames


async def main() -> None:
    host, viewer = ApiUser("host"), ApiUser("fan")
    viewer.post(f"/users/{host.id}/follow/")

    status, body = host.post("/live/", json={"title": "E2E test"})
    check(status == 201, f"host goes live (HTTP {status})")
    session = body["data"]
    stream_id = session["stream"]["id"]
    check(session["role"] == "host", "host gets the host role")

    host_room = rtc.Room()
    await host_room.connect(session["livekit"]["url"], session["livekit"]["token"])
    camera = FakeCamera(host_room, 320, 240, fps=15)
    await camera.publish()

    status, body = viewer.post(f"/live/{stream_id}/join/")
    check(status == 200 and body["data"]["role"] == "viewer", "follower joins as a viewer")
    viewer_room = rtc.Room()
    receiving = asyncio.ensure_future(wait_for_video(viewer_room))
    await viewer_room.connect(body["data"]["livekit"]["url"], body["data"]["livekit"]["token"])
    frames = await receiving
    check(frames >= 5, f"viewer receives the host's video ({frames} frames)")

    status, _ = viewer.post(f"/live/{stream_id}/comments/", json={"text": "Hello host"})
    check(status == 201, "viewer comments")
    _, body = host.get(f"/live/{stream_id}/comments/")
    poll = body["data"]
    check(poll["viewer_count"] >= 1, f"host sees the viewer count ({poll['viewer_count']})")
    check(any(c["text"] == "Hello host" for c in poll["comments"]), "host sees the comment")

    _, body = viewer.get("/live/")
    check(body["data"]["available"], "live video reported available")
    check(stream_id in [s["id"] for s in body["data"]["results"]], "stream listed for the follower")
    _, body = viewer.get("/notifications/?type=live")
    check(len(body["data"]["results"]) >= 1, "follower was notified")

    status, _ = host.post(f"/live/{stream_id}/invite/", json={"user_id": viewer.id})
    check(status == 201, "host invites the viewer on screen")
    status, body = viewer.post(f"/live/{stream_id}/invite/respond/", json={"accept": True})
    check(status == 200 and body["data"]["role"] == "guest", "viewer accepts and becomes a guest")
    await viewer_room.disconnect()

    guest_room = rtc.Room()
    await guest_room.connect(body["data"]["livekit"]["url"], body["data"]["livekit"]["token"])
    guest_camera = FakeCamera(guest_room)
    await guest_camera.publish(with_audio=False)
    await asyncio.sleep(2)
    guest_tracks = [len(p.track_publications) for p in host_room.remote_participants.values()]
    check(any(n >= 1 for n in guest_tracks), "host receives the guest's camera")

    status, _ = host.post(f"/live/{stream_id}/guests/{viewer.id}/remove/")
    check(status == 200, "host removes the guest")
    await asyncio.sleep(1)
    check(not guest_room.local_participant.permissions.can_publish, "removed guest can no longer publish")

    closed = asyncio.Event()

    @guest_room.on("disconnected")
    def _on_closed(*_args):
        closed.set()

    status, body = host.post(f"/live/{stream_id}/end/")
    check(status == 200 and body["data"]["status"] == "ended", "host ends the stream")
    await asyncio.wait_for(closed.wait(), 10)
    check(True, "the server closed the room for the remaining participant")

    camera.stop()
    guest_camera.stop()
    await host_room.disconnect()
    status, body = viewer.post(f"/live/{stream_id}/join/")
    check(status >= 400, f"joining an ended stream is refused ({body['error']['code']})")
    print("E2E OK")


if __name__ == "__main__":
    asyncio.run(main())
