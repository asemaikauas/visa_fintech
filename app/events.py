"""A tiny in-memory event log that the dashboard polls."""
import threading
import time
from typing import Optional


class Bus:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.events = []
        self.seq = 0
        self.scene = 0
        self.version = 0

    def reset(self) -> None:
        with self.lock:
            self.events = []
            self.scene = 0
            self.version += 1

    def new_scene(self) -> int:
        with self.lock:
            self.scene += 1
            self.version += 1
            return self.scene

    def emit(self, lane: str, title: str, detail: str = "", tone: str = "info",
             kind: str = "text", scene: Optional[int] = None, **data) -> dict:
        with self.lock:
            self.seq += 1
            self.version += 1
            event = {
                "id": self.seq,
                "ts": time.strftime("%H:%M:%S"),
                "scene": self.scene if scene is None else scene,
                "lane": lane,
                "title": title,
                "detail": detail,
                "tone": tone,
                "kind": kind,
            }
            event.update(data)
            self.events.append(event)
            return event

    def update(self, event: dict, **changes) -> dict:
        """Change a card that's already on screen, so a repeat updates its counter instead of adding a card."""
        with self.lock:
            event.update(changes)
            self.version += 1
            return event

    def touch(self) -> None:
        with self.lock:
            self.version += 1

    def scene_events(self) -> list:
        with self.lock:
            return [dict(e) for e in self.events if e["scene"] == self.scene]
