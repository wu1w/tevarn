"""SSE data-line splitting that is safe across TCP chunks."""

from __future__ import annotations


def split_sse_data_lines(text: str) -> tuple[str, list[str]]:
    """Split buffered SSE text into complete ``data:`` payloads.

    Returns (residual_without_trailing_newline, payload_strings).
    ``event:`` / comment lines are ignored. Incomplete last line stays in residual.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = text.split("\n")
    residual = parts.pop()
    out: list[str] = []
    for line in parts:
        line = line.strip()
        if not line:
            continue
        if line.startswith("data: "):
            out.append(line[6:])
        elif line.startswith("data:"):
            out.append(line[5:].strip())
    return residual, out
