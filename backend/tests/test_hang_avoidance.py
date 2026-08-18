"""Hang-avoidance: image HTTP timeout + emit_run_event off the event loop."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_image_services_set_aiohttp_timeout():
    for rel in (
        "backend/services/image/openai.py",
        "backend/services/image/local.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "ClientTimeout" in src
        assert "total=120" in src
        assert "connect=10" in src


def test_emit_run_event_uses_to_thread():
    src = (ROOT / "backend" / "agent" / "run_events.py").read_text(encoding="utf-8")
    assert "await asyncio.to_thread(_atomic_emit" in src


def test_file_write_edit_use_to_thread():
    src = (ROOT / "backend" / "services" / "tools" / "executors.py").read_text(
        encoding="utf-8"
    )
    assert "await asyncio.to_thread(_write)" in src
    assert "await asyncio.to_thread(_edit)" in src
