"""Cross-process requests-per-minute limiter for one deployment.

State lives in a small JSON file guarded by a file lock, so every server process on
the machine (Claude Code sessions, Cowork, the Desktop Extension) shares one view of
the deployment's quota. A 429 from the service sets a shared cooldown.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
from filelock import FileLock

WINDOW_SECONDS = 60.0
# Small safety margin so a request recorded at the window's edge is not re-used early.
WINDOW_MARGIN = 0.5
MAX_SLEEP_STEP = 5.0

WaitCallback = Callable[[float, str], Awaitable[None]]


class RateLimitExceeded(Exception):
    """Waiting for a slot would exceed the caller's deadline."""

    def __init__(self, retry_at: float, reason: str):
        self.retry_at = retry_at
        self.reason = reason
        wait = max(0.0, retry_at - time.time())
        at = time.strftime("%H:%M:%S", time.localtime(retry_at))
        super().__init__(f"Rate limit ({reason}): the next request slot opens at {at} (in about {wait:.0f} s).")


class RateLimiter:
    def __init__(
        self,
        state_dir: Path,
        key: str,
        rpm: int,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = anyio.sleep,
    ):
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        directory = state_dir / "ratelimit"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{digest}.json"
        self._lock = FileLock(str(self.path) + ".lock", timeout=10)
        self.rpm = rpm
        self._clock = clock
        self._sleep = sleep

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        requests = [float(t) for t in data.get("requests", []) if isinstance(t, int | float)]
        return {
            "requests": requests,
            "cooldown_until": float(data.get("cooldown_until") or 0.0),
            "observed_limit": data.get("observed_limit"),
        }

    def _save(self, state: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(self.path)

    def _prune(self, state: dict[str, Any], now: float) -> None:
        horizon = now - WINDOW_SECONDS - WINDOW_MARGIN
        state["requests"] = sorted(t for t in state["requests"] if t > horizon)

    def effective_rpm(self, state: dict[str, Any]) -> int:
        observed = state.get("observed_limit")
        if isinstance(observed, int) and 0 < observed < self.rpm:
            return observed
        return self.rpm

    def _try_reserve(self) -> tuple[float, str]:
        """Reserve a slot; return (0, '') on success or (seconds to wait, reason)."""
        with self._lock:
            now = self._clock()
            state = self._load()
            self._prune(state, now)
            if state["cooldown_until"] > now:
                return state["cooldown_until"] - now, "service asked us to back off"
            rpm = self.effective_rpm(state)
            if len(state["requests"]) < rpm:
                state["requests"].append(now)
                self._save(state)
                return 0.0, ""
            oldest = state["requests"][-rpm]
            return oldest + WINDOW_SECONDS + WINDOW_MARGIN - now, f"{rpm} requests per minute"

    async def acquire(self, deadline: float, on_wait: WaitCallback | None = None) -> None:
        """Wait for a request slot, or raise RateLimitExceeded if it would pass `deadline` (epoch s)."""
        while True:
            wait, reason = self._try_reserve()
            if wait <= 0:
                return
            now = self._clock()
            if now + wait > deadline:
                raise RateLimitExceeded(now + wait, reason)
            if on_wait is not None:
                await on_wait(wait, reason)
            await self._sleep(min(wait, MAX_SLEEP_STEP))

    def set_cooldown(self, seconds: float) -> None:
        with self._lock:
            state = self._load()
            until = self._clock() + max(0.0, seconds)
            state["cooldown_until"] = max(state["cooldown_until"], until)
            self._save(state)

    def observe_limit(self, limit: int) -> None:
        """Record a request limit reported by the service (adopted when below the configured RPM)."""
        if limit <= 0:
            return
        with self._lock:
            state = self._load()
            if state.get("observed_limit") != limit:
                state["observed_limit"] = limit
                self._save(state)

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            state = self._load()
            self._prune(state, now)
            return {
                "rpm_limit": self.effective_rpm(state),
                "configured_rpm": self.rpm,
                "service_reported_limit": state.get("observed_limit"),
                "requests_last_minute": len(state["requests"]),
                "cooldown_remaining_s": round(max(0.0, state["cooldown_until"] - now), 1),
                "state_file": str(self.path),
            }
