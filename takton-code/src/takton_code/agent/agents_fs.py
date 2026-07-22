"""Local agents under .takton/agents/*.md"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AgentDef:
    name: str
    path: str
    body: str
    mode: str | None = None
    readonly: bool = False
    description: str = ""

    def prompt_append(self) -> str:
        bits = [f"# Active custom agent: {self.name}"]
        if self.description:
            bits.append(self.description)
        bits.append(self.body.strip())
        return "\n\n".join(bits)


_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = _FM.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip("\"'")
    return meta, m.group(2)


def load_agents(project_root: Path) -> list[AgentDef]:
    d = Path(project_root) / ".takton" / "agents"
    if not d.is_dir():
        return []
    out: list[AgentDef] = []
    for p in sorted(d.glob("*.md")):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = _parse_frontmatter(raw)
        name = meta.get("name") or p.stem
        mode = meta.get("mode")
        readonly = str(meta.get("readonly") or "").lower() in ("1", "true", "yes")
        desc = meta.get("description") or ""
        out.append(
            AgentDef(
                name=name,
                path=str(p),
                body=body.strip(),
                mode=mode,
                readonly=readonly,
                description=desc,
            )
        )
    return out


def get_agent(project_root: Path, name: str) -> AgentDef | None:
    key = name.strip().lower()
    for a in load_agents(project_root):
        if a.name.lower() == key or Path(a.path).stem.lower() == key:
            return a
    return None


def agents_summary(project_root: Path) -> str:
    agents = load_agents(project_root)
    if not agents:
        return "(no .takton/agents/*.md)"
    lines = ["custom agents:"]
    for a in agents:
        ro = " ro" if a.readonly else ""
        lines.append(f"  - {a.name}{ro}  {a.description[:60]}")
    return "\n".join(lines)
