"""Native Visio (.vsdx) preview + binary .vsd fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.visio_preview import (
    build_minimal_vsdx_bytes,
    converter_status,
    extract_ole_strings,
    is_visio_path,
    pages_as_text,
    preview_visio,
)


def test_is_visio_path():
    assert is_visio_path("flow.vsd")
    assert is_visio_path(Path("a/b.VSDX"))
    assert is_visio_path("stencil.vsdm")
    assert not is_visio_path("flow.pptx")


def test_native_vsdx_renders_svg_and_text(tmp_path: Path):
    dest = tmp_path / "hello.vsdx"
    dest.write_bytes(
        build_minimal_vsdx_bytes(
            "Flow",
            shapes=[
                {
                    "name": "Process",
                    "text": "HelloVisio",
                    "pin_x": 2.0,
                    "pin_y": 6.0,
                    "width": 1.8,
                    "height": 0.7,
                },
                {
                    "name": "Decision",
                    "text": "OK?",
                    "pin_x": 5.0,
                    "pin_y": 6.0,
                    "width": 1.4,
                    "height": 1.0,
                },
            ],
        )
    )
    out = preview_visio(dest)
    assert out["ok"] is True
    assert out["converter"] == "native"
    assert out["pages"]
    page = out["pages"][0]
    assert page["name"] == "Flow"
    assert "HelloVisio" in page["text"]
    assert "OK?" in page["text"]
    svg = page["svg"] or ""
    assert svg.startswith("<svg")
    assert "HelloVisio" in svg
    assert "OK?" in svg
    flat = pages_as_text(out)
    assert "HelloVisio" in flat
    assert "--- Flow ---" in flat


def test_vdx_xml_preview(tmp_path: Path):
    xml = """<?xml version="1.0"?>
    <VisioDocument xmlns="http://schemas.microsoft.com/visio/2003/core">
      <Pages>
        <Page Name="P1">
          <PageSheet>
            <Cell N="PageWidth" V="8"/>
            <Cell N="PageHeight" V="11"/>
          </PageSheet>
          <Shapes>
            <Shape ID="1" NameU="Process">
              <Cell N="PinX" V="1"/>
              <Cell N="PinY" V="2"/>
              <Cell N="Width" V="1"/>
              <Cell N="Height" V="0.5"/>
              <Text>VDXBox</Text>
            </Shape>
          </Shapes>
        </Page>
      </Pages>
    </VisioDocument>
    """
    dest = tmp_path / "old.vdx"
    dest.write_text(xml, encoding="utf-8")
    out = preview_visio(dest)
    assert out["converter"] == "native"
    assert "VDXBox" in out["pages"][0]["text"]
    assert "<svg" in (out["pages"][0]["svg"] or "")


def test_binary_vsd_without_converter_returns_hint(tmp_path: Path, monkeypatch):
    dest = tmp_path / "legacy.vsd"
    # OLE magic + a UTF-16LE string
    payload = b"\xd0\xcf\x11\xe0" + ("NetworkZone".encode("utf-16le")) + b"\x00\x00"
    dest.write_bytes(payload)

    monkeypatch.setattr("backend.services.visio_preview.find_soffice", lambda: None)
    monkeypatch.setattr("backend.services.visio_preview.find_vsd2svg", lambda: None)

    out = preview_visio(dest)
    assert out["converter"] == "strings"
    assert out["hint"]
    assert "LibreOffice" in out["hint"] or "libreoffice" in out["hint"].lower()
    blob = extract_ole_strings(dest)
    assert "NetworkZone" in blob


def test_preview_rejects_missing_and_wrong_type(tmp_path: Path):
    missing = tmp_path / "nope.vsdx"
    try:
        preview_visio(missing)
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError:
        pass
    other = tmp_path / "note.txt"
    other.write_text("hi", encoding="utf-8")
    try:
        preview_visio(other)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_converter_status_keys():
    st = converter_status()
    assert "native_vsdx" in st and st["native_vsdx"] is True
    assert "install_hint" in st
    assert "binary_vsd_ready" in st


@pytest.mark.asyncio
async def test_doc_read_visio_vsdx(tmp_path: Path):
    from backend.tools.builtins.wave_a_tools import DocReadTool

    dest = tmp_path / "flow.vsdx"
    dest.write_bytes(build_minimal_vsdx_bytes("Page-1", [{"name": "Process", "text": "DocReadVisio"}]))
    tool = DocReadTool()
    out = await tool.execute(path=str(dest))
    assert "DocReadVisio" in str(out)
    assert "Page-1" in str(out)
