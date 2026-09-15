"""HA's per-stream log lines must never carry the Nanit access token (#27).

WHAT THESE PROVE, AND WHAT THEY DO NOT. They run the filter against records
shaped exactly like the live line (journal, 2026-09-14 11:10:09) on a real
`logging.Logger`, and the entity's add/remove hooks against a stubbed HA
(see ha_stubs). LAW.md §16: green on a stub authorises nothing about a
running instance -- that HA's stream component really names its logger
`homeassistant.components.stream.stream.<entity_id>` is a fact about core
(stream/__init__.py at 2026.9.2), verified on the running instance by
reading a redacted line back off the journal, not here.

EVERY TEST HERE ENCODES THE DELTA: against master the filter, the logger
prefix and the hook do not exist, so the file cannot even fix. Verified
2026-09-14 by running it against master's camera.py: 2 failed, 3 errors.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import nanit.camera as camera
import nanit.entity as entity

_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXNpZ25hdHVyZQ"
_URL = f"rtmps://media-secured.nanit.com/nanit/9c6d37df.{_TOKEN}"
# The live line, verbatim in shape: worker error text carrying the URL as
# the %s argument, on the per-stream logger.
_LIVE_MSG = "Error from stream worker: %s"
_LIVE_ARG = f"Error opening stream (I/O error, {_URL})"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def stream_logger():
    """A fresh per-stream logger with a capturing handler, torn down after."""
    logger = logging.getLogger(f"{camera._STREAM_LOGGER_PREFIX}camera.test_baby")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _Capture()
    logger.addHandler(handler)
    yield logger, handler
    logger.removeHandler(handler)
    logger.removeFilter(camera._STREAM_TOKEN_REDACTOR)


def _entity(entity_id: str = "camera.test_baby") -> camera.NanitCameraEntity:
    ent = camera.NanitCameraEntity(SimpleNamespace(data=None), SimpleNamespace(uid="cam-uid"))
    ent.entity_id = entity_id
    return ent


def test_error_line_keeps_the_dropout_and_loses_the_token(stream_logger) -> None:
    """The subject of #27: the line survives, the token does not."""
    logger, handler = stream_logger
    logger.addFilter(camera._STREAM_TOKEN_REDACTOR)
    logger.error(_LIVE_MSG, _LIVE_ARG)
    assert len(handler.lines) == 1, handler.lines
    line = handler.lines[0]
    assert _TOKEN not in line
    assert line == (
        "Error from stream worker: Error opening stream "
        "(I/O error, rtmps://media-secured.nanit.com/nanit/9c6d37df.<redacted>)"
    )


def test_debug_sites_are_covered_too(stream_logger) -> None:
    """Started/Updating/Restarting/Stopped lines carry the same URL."""
    logger, handler = stream_logger
    logger.addFilter(camera._STREAM_TOKEN_REDACTOR)
    logger.debug("Started stream: %s", _URL)
    logger.debug("Restarting stream worker in %d seconds: %s", 10, _URL)
    assert len(handler.lines) == 2
    assert all(_TOKEN not in line for line in handler.lines)
    assert handler.lines[1] == (
        "Restarting stream worker in 10 seconds: "
        "rtmps://media-secured.nanit.com/nanit/9c6d37df.<redacted>"
    )


def test_a_line_without_the_url_is_untouched(stream_logger) -> None:
    """The benign case the same filter sees: no URL, no rewrite, no drop."""
    logger, handler = stream_logger
    logger.addFilter(camera._STREAM_TOKEN_REDACTOR)
    record_args = ("x", 3)
    logger.warning("Immediate exit requested %s %d", *record_args)
    assert handler.lines == ["Immediate exit requested x 3"]


def test_filter_can_fail() -> None:
    """The assertion set's own self-test (LAW §4): an unfiltered logger leaks."""
    logger = logging.getLogger(f"{camera._STREAM_LOGGER_PREFIX}camera.unfiltered")
    logger.propagate = False
    handler = _Capture()
    logger.addHandler(handler)
    try:
        logger.error(_LIVE_MSG, _LIVE_ARG)
    finally:
        logger.removeHandler(handler)
    assert _TOKEN in handler.lines[0]


def test_binding_delta(monkeypatch) -> None:
    """Added binds the filter to the logger named after the entity; removed unbinds."""
    assert "async_added_to_hass" in vars(camera.NanitCameraEntity)

    async def _noop(self) -> None:
        return None

    monkeypatch.setattr(entity.CoordinatorEntity, "async_added_to_hass", _noop)
    monkeypatch.setattr(entity.CoordinatorEntity, "async_will_remove_from_hass", _noop)

    ent = _entity("camera.bound_baby")
    logger = logging.getLogger(f"{camera._STREAM_LOGGER_PREFIX}camera.bound_baby")
    assert camera._STREAM_TOKEN_REDACTOR not in logger.filters

    asyncio.run(ent.async_added_to_hass())
    assert logger.filters == [camera._STREAM_TOKEN_REDACTOR]
    # A second add (reload under the same id) does not stack a copy.
    asyncio.run(ent.async_added_to_hass())
    assert logger.filters == [camera._STREAM_TOKEN_REDACTOR]

    ent.stream = None  # nothing to tear down under the stub
    asyncio.run(ent.async_will_remove_from_hass())
    assert camera._STREAM_TOKEN_REDACTOR not in logger.filters
