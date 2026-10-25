"""The camera's live video, for the detector: frames as fast as it sends them.

A snapshot over the camera's HTTP API takes it ~0.6 s to make, which held
the detector to ~1.6 frames a second. Its RTSP sub-stream (640×360 H.264,
10 frames a second) arrives continuously instead: one thread decodes it
with PyAV and keeps only the newest frame, and the detector takes whichever
is newest when it's ready for another. If the stream drops, the watch loop
falls back to snapshots until it's back.

RTSP has to be switched on in the Reolink app (docs/candy-watch.md).
"""

import io
import threading
import time
import urllib.parse

PATH = "h264Preview_01_sub"     # the sub stream: 640×360, 10 frames a second


class Stream:
    def __init__(self, host: str, user: str, password: str, opener=None,
                 sleep=time.sleep, clock=time.time):
        login = (f"{urllib.parse.quote(user, safe='')}:"
                 f"{urllib.parse.quote(password, safe='')}")
        self.url = f"rtsp://{login}@{host}:554/{PATH}"
        self.opener, self.sleep, self.clock = opener, sleep, clock
        self.frame = None               # the newest decoded frame (PyAV)
        self.frame_at = 0.0
        self.frames = 0
        self.connected = False
        self.error = ""
        self._jpeg = (0.0, b"")

    def run(self) -> None:
        """Forever: connect, decode, keep the newest; reconnect on trouble."""
        wait = 2.0
        while True:
            try:
                self._watch()
                wait = 2.0
            except Exception as error:      # PyAV raises its own kinds
                said = str(error)
                if "401" in said or "nauthorized" in said:
                    # A refused login counts towards the camera's lockout
                    # (five, then locked), so never retry that quickly.
                    self.error = "the camera refused the stream login"
                    wait = 600.0
                else:
                    self.error = f"stream lost — {type(error).__name__}"
                print(f"[candy] {self.error}; retrying in {wait:.0f} s",
                      flush=True)
            self.connected = False
            self.sleep(wait)
            wait = min(wait * 2, 600.0)

    def _watch(self) -> None:
        opener = self.opener
        if opener is None:
            import av
            opener = av.open
        container = opener(self.url, options={"rtsp_transport": "tcp"},
                           timeout=10)
        try:
            video = container.streams.video[0]
            for frame in container.decode(video):
                if not self.connected:
                    self.connected, self.error = True, ""
                    print("[candy] camera stream: connected", flush=True)
                self.frame, self.frame_at = frame, self.clock()
                self.frames += 1
        finally:
            container.close()
        raise ConnectionError("stream ended")

    def live(self, stale: float = 3.0) -> bool:
        return self.connected and self.clock() - self.frame_at < stale

    def newest(self):
        """(PIL image, when) of the newest frame, or (None, 0)."""
        frame, at = self.frame, self.frame_at
        if frame is None:
            return None, 0.0
        return frame.to_image(), at

    def jpeg(self) -> bytes:
        """The newest frame as a JPEG, for the dashboard's live view —
        made once per frame however many people are looking."""
        at, data = self._jpeg
        if at != self.frame_at:
            image, at = self.newest()
            if image is None:
                return b""
            out = io.BytesIO()
            image.save(out, "JPEG", quality=80)
            data = out.getvalue()
            self._jpeg = (at, data)
        return data

    def status(self) -> dict:
        return {"connected": self.connected, "frames": self.frames,
                "error": self.error}


def start(host: str, user: str, password: str) -> Stream:
    stream = Stream(host, user, password)
    threading.Thread(target=stream.run, daemon=True).start()
    return stream
