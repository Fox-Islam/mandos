"""stderr-bound structured logging: JSON to the configured stream, level filtering."""

from __future__ import annotations

import io
import json

from orchestrator.observability import configure_logging, get_logger


def test_logs_emit_json_to_configured_stream():
    buf = io.StringIO()
    configure_logging("INFO", stream=buf)
    get_logger("test").info("hello.event", foo="bar")
    record = json.loads(buf.getvalue().strip())
    assert record["event"] == "hello.event"
    assert record["foo"] == "bar"
    assert record["level"] == "info"


def test_debug_is_filtered_at_info_level():
    buf = io.StringIO()
    configure_logging("INFO", stream=buf)
    get_logger("test").debug("debug.event")
    assert buf.getvalue() == ""
