"""Takton Evolution Engine (TEE) — config.

Default: enabled=False until user turns on.
Decision B + auto_apply: when enabled, drafts that pass gates become active automatically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


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
    auto_apply_skills: bool = True  # B decision: auto apply after gates
    max_skill_bytes: int = 32000
    ban_patterns: Sequence[str] = field(
        default_factory=lambda: (
            "ghp_",
            "sk-",
            "local-l1-token",
            "BEGIN RSA PRIVATE",
            "api_key=",
            "password=",
        )
    )
    defer: bool = True
    db_path: str | None = None

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
        )

    def resolve_db_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path)
        # Prefer user takton home, else local data
        home = os.getenv("TAKTON_HOME") or os.getenv("USERPROFILE") or os.getenv("HOME") or "."
        base = Path(home)
        if (base / ".takton").exists() or os.getenv("TAKTON_HOME"):
            root = Path(os.getenv("TAKTON_HOME", base / ".takton"))
        else:
            try:
                from backend.core.config import settings

                # settings may have uploads_dir parent
                up = getattr(settings, "uploads_dir", None)
                if up:
                    root = Path(up).resolve().parent
                else:
                    root = Path.cwd() / "data"
            except Exception:
                root = Path.cwd() / "data"
        root.mkdir(parents=True, exist_ok=True)
        return root / "evolution.db"


_config: EvolutionConfig | None = None


def get_evolution_config() -> EvolutionConfig:
    global _config
    if _config is None:
        _config = EvolutionConfig.from_env()
    return _config


def set_evolution_config(**kwargs) -> EvolutionConfig:
    """Runtime update (e.g. from configure_takton / API)."""
    global _config
    cfg = get_evolution_config()
    for k, v in kwargs.items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    _config = cfg
    return cfg
