"""
End-to-end call check: the API plus a real LiveKit server and two headless
WebRTC clients. Proves ringing, accepting, two-way audio and video, ending
(which closes the room), busy detection, cancelling and the missed-call
notification.
"""

import asyncio

from client import ApiUser, FakeCamera, check, follow_each_other
from livekit import rtc


class CallClient:
    """Joins a call room, publishes camera and microphone, and records what it receives."""

    def __init__(self):
        self.room = rtc.Room()
        self.received: set = set()
        self.both = asyncio.Event()
        self.camera = FakeCamera(self.room)

        @self.room.on("track_subscribed")
        def _on_track(track, _publication, _participant):
            self.received.add(track.kind)
            if len(self.received) == 2:
                self.both.set()

    async def join(self, livekit: dict) -> None:
        await self.room.connect(livekit["url"], livekit["token"])
        await self.camera.publish()

    async def leave(self) -> None:
        self.camera.stop()
        await self.room.disconnect()


async def main() -> None:
    ama, ben = ApiUser("ama"), ApiUser("ben")
    follow_each_other(ama, ben)

    _, profile = ama.get(f"/users/{ben.id}/")
    check(profile["data"]["can_call"], "mutual followers can call each other")

    status, body = ama.post("/calls/", json={"user_id": ben.id, "kind": "video"})
    check(status == 201 and body["data"]["call"]["status"] == "ringing", "caller starts a ringing video call")
    call_id = body["data"]["call"]["id"]
    caller = CallClient()
    await caller.join(body["data"]["livekit"])

    _, body = ben.get(f"/calls/{call_id}/")
    check(body["data"]["call"]["status"] == "ringing", "callee sees the incoming call")
    check(not body["data"]["call"]["is_outgoing"], "the call is incoming for the callee")

    status, body = ben.post(f"/calls/{call_id}/accept/")
    check(status == 200 and body["data"]["call"]["status"] == "accepted", "callee accepts")
    callee = CallClient()
    await callee.join(body["data"]["livekit"])

    await asyncio.wait_for(asyncio.gather(caller.both.wait(), callee.both.wait()), 20)
    check(len(caller.received) == 2 and len(callee.received) == 2, "audio and video flow both ways")

    closed = asyncio.Event()

    @caller.room.on("disconnected")
    def _on_closed(*_args):
        closed.set()

    status, body = ben.post(f"/calls/{call_id}/end/")
    check(status == 200 and body["data"]["call"]["status"] == "ended", "callee hangs up")
    await asyncio.wait_for(closed.wait(), 10)
    check(True, "the server closed the call room for the caller")
    caller.camera.stop()
    await callee.leave()

    carl = ApiUser("carl")
    follow_each_other(carl, ben)
    _, first = ama.post("/calls/", json={"user_id": ben.id})
    _, second = carl.post("/calls/", json={"user_id": ben.id})
    check(second["data"]["call"]["status"] == "busy", "a second caller gets busy")

    _, body = ama.post(f"/calls/{first['data']['call']['id']}/cancel/")
    check(body["data"]["call"]["status"] == "cancelled", "caller cancels before an answer")
    _, body = ben.get("/notifications/?type=missed_call")
    check(len(body["data"]["results"]) >= 1, "callee gets a missed-call notification")
    print("CALLS E2E OK")


if __name__ == "__main__":
    asyncio.run(main())
