"""Takton Evolution Engine (TEE) v0.1.1 — HAEE-inspired config.

Phases:
  P1 from_tasks/from_cron  — task/cron outcomes → evolution assets
  P2 quality SKILL.md      — structured proposals + structure gates
  P3 auto_create_tools     — tool drafts (default draft-only)
  P4 auto_observe/curator  — session pattern nudge + archive unused
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

ENGINE_VERSION = "0.1.1"


def _truthy(v: str | None, default: bool = False) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class EvolutionConfig:
    enabled: bool = False
    mode: str = "on_failure"  # off | on_failure | always | manual
    max_iterations: int = 3
    llm_judge: bool = True
    auto_apply_skills: bool = True
    max_skill_bytes: int = 32000
    ban_patterns: Sequence[str] = field(
        default_factory=lambda: (
            "ghp_",
            "sk-",
            "bridge-token-",
            "BEGIN RSA PRIVATE",
            "api_key=",
            "password=",
        )
    )
    defer: bool = True
    db_path: str | None = None

    # --- v0.1.1 HAEE phases ---
    from_tasks: bool = True
    from_cron: bool = True
    write_skill_files: bool = True  # also drop SKILL.md under skills/evolved/
    auto_create_tools: bool = True  # propose tool playbooks
    auto_apply_tools: bool = False  # tools stay draft unless True or user enables
    auto_observe: bool = True  # P4 cluster/nudge
    observe_min_sessions: int = 3
    observe_nudge_level: str = "notify"  # silent | notify | approve | off
    curator_enabled: bool = True
    curator_stale_days: int = 14
    curator_archive_days: int = 30
    dedupe_similarity: float = 0.72
    max_skill_gen: int = 20

    @classmethod
    def from_env(cls) -> "EvolutionConfig":
        return cls(
            enabled=_truthy(os.getenv("TAKTON_EVOLUTION_ENABLED"), False),
            mode=(os.getenv("TAKTON_EVOLUTION_MODE") or "on_failure").strip(),
            max_iterations=int(os.getenv("TAKTON_EVOLUTION_MAX_ITERATIONS") or "3"),
            llm_judge=_truthy(os.getenv("TAKTON_EVOLUTION_LLM_JUDGE"), True),
            auto_apply_skills=_truthy(os.getenv("TAKTON_EVOLUTION_AUTO_APPLY"), True),
            max_skill_bytes=int(os.getenv("TAKTON_EVOLUTION_MAX_SKILL_BYTES") or "32000"),
            defer=_truthy(os.getenv("TAKTON_EVOLUTION_DEFER"), True),
            db_path=os.getenv("TAKTON_EVOLUTION_DB") or None,
            from_tasks=_truthy(os.getenv("TAKTON_EVOLUTION_FROM_TASKS"), True),
            from_cron=_truthy(os.getenv("TAKTON_EVOLUTION_FROM_CRON"), True),
            write_skill_files=_truthy(os.getenv("TAKTON_EVOLUTION_WRITE_FILES"), True),
            auto_create_tools=_truthy(os.getenv("TAKTON_EVOLUTION_CREATE_TOOLS"), True),
            auto_apply_tools=_truthy(os.getenv("TAKTON_EVOLUTION_APPLY_TOOLS"), False),
            auto_observe=_truthy(os.getenv("TAKTON_EVOLUTION_AUTO_OBSERVE"), True),
            observe_min_sessions=int(os.getenv("TAKTON_EVOLUTION_OBSERVE_MIN") or "3"),
            observe_nudge_level=(os.getenv("TAKTON_EVOLUTION_NUDGE") or "notify").strip(),
            curator_enabled=_truthy(os.getenv("TAKTON_EVOLUTION_CURATOR"), True),
            curator_stale_days=int(os.getenv("TAKTON_EVOLUTION_STALE_DAYS") or "14"),
            curator_archive_days=int(os.getenv("TAKTON_EVOLUTION_ARCHIVE_DAYS") or "30"),
            dedupe_similarity=float(os.getenv("TAKTON_EVOLUTION_DEDUPE") or "0.72"),
            max_skill_gen=int(os.getenv("TAKTON_EVOLUTION_MAX_GEN") or "20"),
        )

    def resolve_db_path(self) -> Path:
        if self.db_path:
            return _norm(self.db_path)

        # Prefer same data dir as main takton.db: %APPDATA%/takton/data
        candidates: list[Path] = []
        appdata = os.getenv("APPDATA") or os.getenv("LOCALAPPDATA")
        if appdata:
            candidates.append(_norm(appdata) / "takton" / "data")
        if os.getenv("TAKTON_HOME"):
            th = _norm(os.getenv("TAKTON_HOME"))
            candidates.append(th if th.name == "data" else th / "data")
        try:
            from backend.core.config import settings

            up = getattr(settings, "uploads_dir", None)
            if up:
                candidates.append(_norm(up).resolve().parent)
        except Exception:
            pass
        home = os.getenv("USERPROFILE") or os.getenv("HOME")
        if home:
            candidates.append(_norm(home) / "AppData" / "Roaming" / "takton" / "data")
            candidates.append(_norm(home) / ".takton")  # legacy

        root = None
        for c in candidates:
            try:
                c.mkdir(parents=True, exist_ok=True)
                root = c
                break
            except Exception:
                continue
        if root is None:
            root = Path.cwd() / "data"
            root.mkdir(parents=True, exist_ok=True)

        path = root / "evolution.db"
        # One-time migrate from legacy ~/.takton/evolution.db
        if not path.exists() and home:
            legacy = _norm(home) / ".takton" / "evolution.db"
            if legacy.exists() and legacy.resolve() != path.resolve():
                try:
                    import shutil

                    shutil.copy2(legacy, path)
                    logger = __import__("logging").getLogger(__name__)
                    logger.info("Migrated evolution.db %s → %s", legacy, path)
                except Exception:
                    pass
        return path

    def resolve_skills_dir(self) -> Path:
        """User-writable evolved skills directory."""
        try:
            dbp = self.resolve_db_path().parent
            d = dbp / "evolved_skills"
        except Exception:
            d = Path.cwd() / "data" / "evolved_skills"
        d.mkdir(parents=True, exist_ok=True)
        return d


def _norm(p) -> Path:
    import re

    s = str(p).strip()
    if os.name == "nt":
        m = re.match(r"^/([A-Za-z])(/.*)?$", s.replace("\\", "/"))
        if m:
            drive = m.group(1).upper()
            rest = (m.group(2) or "").replace("/", "\\")
            return Path(f"{drive}:{rest}")
    return Path(s)


_config: EvolutionConfig | None = None

# 需跨重启保留的运行时开关字段（其余字段仍以 env / 默认为准）。
_PERSIST_KEYS = (
    "enabled",
    "mode",
    "auto_apply_skills",
    "auto_apply_tools",
    "from_cron",
    "from_tasks",
    "auto_observe",
    "auto_create_tools",
    "curator_enabled",
)


def _config_file() -> Path:
    """与 evolution.db 同目录的持久化配置文件。"""
    try:
        base = get_evolution_config().resolve_db_path().parent
    except Exception:
        base = Path.cwd() / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "evolution_config.json"


def _load_persisted() -> dict:
    import json

    try:
        f = _config_file()
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_persisted(cfg: EvolutionConfig) -> None:
    import json

    try:
        payload = {k: getattr(cfg, k) for k in _PERSIST_KEYS if hasattr(cfg, k)}
        _config_file().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def get_evolution_config() -> EvolutionConfig:
    global _config
    if _config is None:
        cfg = EvolutionConfig.from_env()
        # 持久化的运行时开关优先于 env 默认（用户手点的状态要跨重启保留）。
        for k, v in _load_persisted().items():
            if k in _PERSIST_KEYS and hasattr(cfg, k) and v is not None:
                setattr(cfg, k, v)
        _config = cfg
    return _config


def set_evolution_config(**kwargs) -> EvolutionConfig:
    global _config
    cfg = get_evolution_config()
    for k, v in kwargs.items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    _config = cfg
    _save_persisted(cfg)
    return cfg
