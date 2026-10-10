"""Shared helpers for the end-to-end checks: API users and fake WebRTC media."""

import asyncio
import os
import secrets
import string

import requests
from livekit import rtc

API = os.environ.get("FLASHX_API", "http://127.0.0.1:8000/api").rstrip("/")
PASSWORD = "Str0ng-pass-123"


class ApiUser:
    """A freshly registered account and a small request helper."""

    def __init__(self, prefix: str):
        suffix = "".join(secrets.choice(string.ascii_lowercase) for _ in range(5))
        self.username = f"{prefix}{suffix}"
        response = requests.post(
            f"{API}/auth/register/",
            json={
                "full_name": self.username.title(),
                "username": self.username,
                "email": f"{self.username}@example.com",
                "password": PASSWORD,
                "confirm_password": PASSWORD,
            },
            timeout=20,
        )
        if response.status_code != 201:
            raise RuntimeError(f"register failed: {response.status_code} {response.text[:300]}")
        data = response.json()["data"]
        self.id = data["user"]["id"]
        self._headers = {"Authorization": f"Bearer {data['tokens']['access']}"}

    def request(self, method: str, path: str, **kwargs) -> tuple[int, dict | str]:
        response = requests.request(method, API + path, headers=self._headers, timeout=20, **kwargs)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text[:300]

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)


def follow_each_other(a: ApiUser, b: ApiUser) -> None:
    a.post(f"/users/{b.id}/follow/")
    b.post(f"/users/{a.id}/follow/")


class FakeCamera:
    """Publishes a solid video track (and optionally a silent microphone) to a room."""

    def __init__(self, room: rtc.Room, width: int = 160, height: int = 120, fps: int = 10):
        self.room = room
        self.width, self.height, self.fps = width, height, fps
        self.source = rtc.VideoSource(width, height)
        self._task: asyncio.Task | None = None

    async def publish(self, with_audio: bool = True) -> None:
        video = rtc.LocalVideoTrack.create_video_track("camera", self.source)
        await self.room.local_participant.publish_track(
            video, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
        )
        if with_audio:
            audio = rtc.LocalAudioTrack.create_audio_track("mic", rtc.AudioSource(48000, 1))
            await self.room.local_participant.publish_track(
                audio, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            )
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        frame = rtc.VideoFrame(
            self.width, self.height, rtc.VideoBufferType.RGBA, bytearray(self.width * self.height * 4)
        )
        while True:
            self.source.capture_frame(frame)
            await asyncio.sleep(1 / self.fps)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"ok  {message}")
