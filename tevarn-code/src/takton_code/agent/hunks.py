"""Unified-diff hunk parse + selective apply (partial file restore)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_HUNK_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s@@(.*)$")


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header_extra: str
    lines: list[str] = field(default_factory=list)  # includes leading ' ','+','-' 

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_start": self.old_start,
            "old_count": self.old_count,
            "new_start": self.new_start,
            "new_count": self.new_count,
            "header": self.header_line(),
            "body_preview": "".join(self.lines[:12]),
            "line_count": len(self.lines),
        }

    def header_line(self) -> str:
        oc = f",{self.old_count}" if self.old_count != 1 else ""
        nc = f",{self.new_count}" if self.new_count != 1 else ""
        extra = self.header_extra or ""
        return f"@@ -{self.old_start}{oc} +{self.new_start}{nc} @@{extra}"


def parse_unified_hunks(patch: str) -> list[Hunk]:
    """Parse a unified diff (possibly with ---/+++ headers) into hunks."""
    hunks: list[Hunk] = []
    cur: Hunk | None = None
    for raw in (patch or "").splitlines(keepends=True):
        line = raw
        # normalize to keepends for storage
        if not line.endswith("\n") and line:
            line = line + "\n"
        m = _HUNK_RE.match(line.rstrip("\n"))
        if m:
            if cur:
                hunks.append(cur)
            old_start = int(m.group(1))
            old_count = int(m.group(2) or "1")
            new_start = int(m.group(3))
            new_count = int(m.group(4) or "1")
            extra = m.group(5) or ""
            cur = Hunk(old_start, old_count, new_start, new_count, extra, [])
            continue
        if cur is None:
            continue
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("\\"):  # \ No newline at end of file
            cur.lines.append(line)
            continue
        if line[:1] in (" ", "+", "-", "\n"):
            # bare newline treat as context empty? skip
            if line == "\n":
                cur.lines.append(" \n")
            else:
                cur.lines.append(line if line[0] in " +-" else " " + line)
    if cur:
        hunks.append(cur)
    return hunks


def apply_selected_hunks(
    original: str,
    hunks: list[Hunk],
    indices: list[int],
) -> tuple[str, list[str]]:
    """
    Apply selected hunks (by index) to original text.
    Hunks describe original → target (disk → checkpoint in our rewind diffs).
    Returns (new_text, errors).
    """
    if not indices:
        return original, ["no hunks selected"]
    # sort indices ascending by old_start position
    valid = []
    errs: list[str] = []
    for i in indices:
        if i < 0 or i >= len(hunks):
            errs.append(f"bad hunk index {i}")
            continue
        valid.append(i)
    valid.sort(key=lambda i: hunks[i].old_start)

    # Work line-based without keepends for simpler index math, then rejoin
    src_lines = original.splitlines(keepends=True)
    # Ensure lines end with \n for consistency except possibly last
    out: list[str] = []
    cursor = 0  # 0-based index into src_lines
    for hi in valid:
        h = hunks[hi]
        # unified old_start is 1-based; 0 means empty file
        start = max(0, h.old_start - 1) if h.old_start > 0 else 0
        if start < cursor:
            errs.append(f"hunk {hi} overlaps previous (start={start} cursor={cursor})")
            continue
        # copy unchanged prefix
        out.extend(src_lines[cursor:start])
        # consume old side from hunk
        old_consumed = 0
        new_bits: list[str] = []
        for ln in h.lines:
            if not ln:
                continue
            tag = ln[0]
            body = ln[1:] if tag in " +-\\" else ln
            if tag == "\\":
                continue
            if tag == " ":
                new_bits.append(body if body.endswith("\n") or body == "" else body + "\n")
                old_consumed += 1
            elif tag == "-":
                old_consumed += 1
            elif tag == "+":
                new_bits.append(body if body.endswith("\n") or body == "" else body + "\n")
        # verify old side roughly matches (soft)
        expected_old = []
        for ln in h.lines:
            if ln[:1] in (" ", "-"):
                expected_old.append(ln[1:] if ln[:1] in " -" else ln)
        actual = src_lines[start : start + old_consumed]
        # normalize compare without being too strict on final newline
        def _n(xs: list[str]) -> list[str]:
            return [x.replace("\r\n", "\n") for x in xs]

        if _n(actual) != _n(expected_old) and expected_old:
            # try fuzzy: still apply but warn
            errs.append(f"hunk {hi} context mismatch at line {h.old_start} (applied anyway)")
        out.extend(new_bits)
        cursor = start + old_consumed
    out.extend(src_lines[cursor:])
    return "".join(out), errs


def hunks_summary(hunks: list[Hunk], path: str = "") -> str:
    lines = [f"hunks for {path or '?'}: {len(hunks)}"]
    for i, h in enumerate(hunks):
        plus = sum(1 for ln in h.lines if ln.startswith("+"))
        minus = sum(1 for ln in h.lines if ln.startswith("-"))
        lines.append(f"  [{i}] {h.header_line()}  +{plus} -{minus}")
        # small preview
        for ln in h.lines[:4]:
            lines.append(f"      {ln.rstrip()}"[:100])
        if len(h.lines) > 4:
            lines.append(f"      … +{len(h.lines) - 4}")
    return "\n".join(lines)
