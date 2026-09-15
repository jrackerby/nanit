"""Dropout recovery must count a WebRTC viewer, like the keepalive does (#24).

WHAT THESE PROVE, AND WHAT THEY DO NOT. They drive this module's own gate
logic against a stubbed Home Assistant (see ha_stubs) with a hand-built
stream double. LAW.md §16: green on a stub authorises nothing about a
running instance. In particular nothing here says a real dropout recovers
-- that is exactly what #25 is still open to observe, and the reason this
change matters to it: the INFO line #25 watches for is emitted from
`_schedule_stream_recovery`, which returned before reaching it whenever
the viewer that survived the dropout was a WebRTC one.

THE DELTA IS ENCODED BY `test_webrtc_only_viewer_*`. Against master those
two fail: the gate reads `stream.outputs()`, which a go2rtc session never
appears in, so nothing is logged and no recovery is queued. The remaining
tests are the blast-radius net -- the HLS path must be untouched, and an
empty room must still be refused. Verified 2026-09-15 against master's
camera.py: 2 failed, 4 passed.

The `@callback` trap this file paid for is in ha_stubs: a fabricated stub
is a class, so decorating with one replaces the method outright. Before
that fix every test here read green against master too.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

import nanit.camera as camera


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def infos(self) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno == logging.INFO]


@pytest.fixture
def capture():
    """Capture on the component's own logger at the level #25 reads."""
    handler = _Capture()
    camera._LOGGER.addHandler(handler)
    previous = camera._LOGGER.level
    camera._LOGGER.setLevel(logging.INFO)
    yield handler
    camera._LOGGER.setLevel(previous)
    camera._LOGGER.removeHandler(handler)


class _Stream:
    """A Stream double: only the two methods the recovery path touches."""

    def __init__(self, outputs: bool) -> None:
        self._outputs = ["hls"] if outputs else []
        self.updated_to: list[str] = []

    def outputs(self):
        return self._outputs

    def update_source(self, source: str) -> None:
        self.updated_to.append(source)


def _entity(*, outputs: bool, webrtc: bool) -> camera.NanitCameraEntity:
    """An entity wired to one stream, with the viewers each path would see.

    `coordinator.data is None` makes `is_on` true without touching the
    property, which is what the gate reads first. The coordinator is
    re-bound on the instance because the stubbed `CoordinatorEntity.__init__`
    does not store it, and the stub base would otherwise fabricate a
    `.data` that reads as a camera in sleep mode.
    """
    ent = camera.NanitCameraEntity(SimpleNamespace(data=None), SimpleNamespace(uid="cam-uid"))
    ent.coordinator = SimpleNamespace(data=None)
    ent.entity_id = "camera.test_baby"
    stream = _Stream(outputs)
    ent.stream = stream
    ent._watched_stream = stream
    if webrtc:
        ent._webrtc_sessions["session-1"] = time.monotonic()
    ent.hass = SimpleNamespace(async_create_task=lambda coro, name=None: _drop(coro))
    return ent


def _drop(coro):
    """Close the coroutine the entity would have scheduled, and report it."""
    coro.close()
    return SimpleNamespace(done=lambda: True, cancel=lambda: None)


def test_webrtc_only_viewer_is_recovered(capture) -> None:
    """THE DELTA: a go2rtc session is in no outputs() and still counts."""
    ent = _entity(outputs=False, webrtc=True)
    ent._schedule_stream_recovery(ent.stream, 240.0)
    assert ent._stream_recovery_task is not None
    lines = capture.infos()
    assert len(lines) == 1, lines
    assert lines[0] == (
        "Nanit stream for camera cam-uid dropped after 240s; "
        "resuming the push in 1s (attempt 1)"
    )


def test_webrtc_only_viewer_resumes_the_push_without_restarting_the_worker() -> None:
    """THE DELTA, second half: the push is resumed, the worker is not.

    The worker serves `outputs()` and nothing else, so restarting it for a
    WebRTC-only viewer would serve nobody -- and counting that restart as
    unproven would demote the next dropout's line to DEBUG, hiding it from
    the #25 watch.
    """
    ent = _entity(outputs=False, webrtc=True)
    sent: list[str] = []

    async def _start(source, reconnect_on_failure=False):
        sent.append(source)
        return True

    ent._async_start_streaming_safe = _start
    ent._cached_stream_source = "rtmps://example/nanit/baby.token"
    ent._stream_source_started_at = time.monotonic()

    asyncio.run(ent._async_recover_stream(ent.stream, 0.0))

    assert sent == ["rtmps://example/nanit/baby.token"]
    assert ent.stream.updated_to == []
    assert ent._stream_recovery_failures == 0


def test_hls_viewer_is_unchanged(capture) -> None:
    """The blast-radius net: the path #23 shipped still behaves as it did."""
    ent = _entity(outputs=True, webrtc=False)
    ent._schedule_stream_recovery(ent.stream, 300.0)
    assert ent._stream_recovery_task is not None
    assert len(capture.infos()) == 1


def test_hls_viewer_still_restarts_the_worker() -> None:
    """An outputs() consumer still gets `update_source`, and the count moves."""
    ent = _entity(outputs=True, webrtc=False)

    async def _start(source, reconnect_on_failure=False):
        return True

    ent._async_start_streaming_safe = _start
    ent._cached_stream_source = "rtmps://example/nanit/baby.token"
    ent._stream_source_started_at = time.monotonic()
    ent._stream_was_available = False

    asyncio.run(ent._async_recover_stream(ent.stream, 0.0))

    assert ent.stream.updated_to == ["rtmps://example/nanit/baby.token"]
    assert ent._stream_recovery_failures == 1


def test_empty_room_is_still_refused(capture) -> None:
    """The benign state that produces the same silence (LAW §9).

    Nobody on either path: no line, no task. This is also this file's
    self-test -- it is the assertion that fails if `_has_live_viewers`
    ever starts answering true for an empty room, which would make the
    two delta tests above pass for the wrong reason.
    """
    ent = _entity(outputs=False, webrtc=False)
    ent._schedule_stream_recovery(ent.stream, 240.0)
    assert ent._stream_recovery_task is None
    assert capture.infos() == []


def test_a_stale_webrtc_session_is_not_a_viewer(capture) -> None:
    """A browser that died never sends the close; age retires the session."""
    ent = _entity(outputs=False, webrtc=False)
    ent._webrtc_sessions["dead"] = time.monotonic() - camera._WEBRTC_SESSION_MAX_AGE - 1
    ent._schedule_stream_recovery(ent.stream, 240.0)
    assert ent._stream_recovery_task is None
    assert capture.infos() == []
