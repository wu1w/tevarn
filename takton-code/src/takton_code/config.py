"""Global configuration for Takton Code."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ModeName = Literal["plan", "build", "ask"]


def home_dir() -> Path:
    raw = os.environ.get("TAKTON_CODE_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".takton-code").resolve()


class LLMSettings(BaseModel):
    provider: str = "openai_compatible"
    base_url: str = "http://127.0.0.1:8088/v1"
    api_key: str = "no-key"
    model: str = "default"
    temperature: float = 0.2
    max_tokens: int = 4096
    context_window: int = 65536
    # compression
    compress_threshold: float = 0.55
    compress_keep_recent: int = 8
    compress_keep_tool_blocks: int = 4  # microcompact: full payloads for last N tool blocks
    max_tool_result_chars: int = 4000  # larger tool results trimmed/offloaded
    # context policy
    compact_mode: Literal["static", "balanced", "aggressive"] = "static"
    retain_turns: int = 24  # static: keep ~N user turns fully when possible
    thrashing_max_events: int = 3
    thrashing_window_sec: float = 180.0
    thrashing_cooldown_sec: float = 300.0
    rag_compact: bool = False  # advanced: inject Desktop RAG into compact summary
    # thinking models (Qwen3.5 etc.) may put text in reasoning_content
    strip_thinking_tags: bool = True


class AgentSettings(BaseModel):
    max_iterations: int = 40
    auto_plan_complex: bool = True
    simple_task_max_chars: int = 80
    allow_git_commit: bool = True
    allow_git_push: bool = False  # always confirm / disabled by default
    permission_profile: Literal[
        "cautious", "free", "acceptEdits", "always", "bypass", "dontAsk", "plan", "auto"
    ] = "cautious"
    permission_timeout_sec: float = 300.0
    checkpoint_every: int = 3
    test_command: str | None = None
    enable_subagents: bool = True
    stream: bool = True
    # A: closed-loop coding
    autoloop: bool = False
    autoloop_max_fix: int = 3
    autoloop_auto_approve: bool = False  # plan gate: require /approve unless true or --yes-build
    # B: file history
    file_checkpointing: bool = True
    # auto classifier rules path (optional override; else ~/.takton-code/auto_rules.toml)
    auto_rules_path: str | None = None
    doom_loop_threshold: int = 3  # same tool+args streak → ask/block
    allow_web_fetch: bool = True
    web_fetch_max_bytes: int = 500_000
    # vision: attach local image paths in user messages as multimodal parts
    allow_images: bool = True
    max_images_per_message: int = 4
    # opt-in local memory file ~/.takton-code/memory/MEMORY.md
    local_memory: bool = False


class UISettings(BaseModel):
    screen_mode: Literal["fullscreen", "minimal"] = "fullscreen"
    stream_flush_chars: int = 1
    stream_flush_ms: int = 16
    # client UX
    vim_keys: bool = True  # Esc → NORMAL navigation; i → INSERT
    command_palette: bool = True


class BridgeSettings(BaseModel):
    """Reserved: connect to Takton Desktop backend."""

    enabled: bool = False
    base_url: str = "http://127.0.0.1:8090/api"
    api_token: str = ""
    use_desktop_models: bool = True
    use_desktop_skills: bool = True
    use_desktop_tools: bool = True
    use_desktop_mcp: bool = True
    use_desktop_rag: bool = True
    timeout_sec: float = 60.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TAKTON_CODE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    home: Path = Field(default_factory=home_dir)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    bridge: BridgeSettings = Field(default_factory=BridgeSettings)
    ui: UISettings = Field(default_factory=UISettings)
    default_mode: ModeName = "build"
    locale: str = "zh"

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "sessions").mkdir(parents=True, exist_ok=True)
        (self.home / "logs").mkdir(parents=True, exist_ok=True)

    def sessions_dir(self) -> Path:
        return self.home / "sessions"

    def settings_path(self) -> Path:
        return self.home / "settings.json"

    def config_toml_path(self) -> Path:
        return self.home / "config.toml"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> Settings:
    """Load settings: defaults < config.toml < env."""
    home = home_dir()
    data: dict[str, Any] = {}
    toml_path = home / "config.toml"
    if toml_path.is_file():
        with toml_path.open("rb") as f:
            data = tomllib.load(f) or {}

    # env overrides for common flat keys
    env_map = {
        "TAKTON_CODE_BASE_URL": ("llm", "base_url"),
        "TAKTON_CODE_API_KEY": ("llm", "api_key"),
        "TAKTON_CODE_MODEL": ("llm", "model"),
        "TAKTON_CODE_CONTEXT_WINDOW": ("llm", "context_window"),
        "TAKTON_CODE_MAX_TOKENS": ("llm", "max_tokens"),
        "TAKTON_CODE_TEMPERATURE": ("llm", "temperature"),
        "TAKTON_CODE_COMPRESS_THRESHOLD": ("llm", "compress_threshold"),
        "TAKTON_CODE_BRIDGE_URL": ("bridge", "base_url"),
        "TAKTON_CODE_BRIDGE_TOKEN": ("bridge", "api_token"),
        "TAKTON_CODE_BRIDGE_ENABLED": ("bridge", "enabled"),
    }
    for env_k, (sec, key) in env_map.items():
        val = os.environ.get(env_k)
        if val is None or val == "":
            continue
        data.setdefault(sec, {})
        if key in ("context_window", "max_tokens"):
            data[sec][key] = int(val)
        elif key in ("temperature", "compress_threshold"):
            data[sec][key] = float(val)
        elif key == "enabled":
            data[sec][key] = val.lower() in ("1", "true", "yes", "on")
        else:
            data[sec][key] = val

    settings = Settings()
    if data:
        merged = settings.model_dump()
        merged = _deep_merge(merged, data)
        # home as path
        if "home" in merged and not isinstance(merged["home"], Path):
            merged["home"] = Path(str(merged["home"])).expanduser()
        settings = Settings.model_validate(merged)
    settings.home = home
    settings.ensure_dirs()
    return settings


def save_user_settings_patch(patch: dict[str, Any]) -> Settings:
    """Persist a subset of settings to settings.json and reload."""
    import json

    s = load_settings()
    path = s.settings_path()
    current: dict[str, Any] = {}
    if path.is_file():
        current = json.loads(path.read_text(encoding="utf-8"))
    current = _deep_merge(current, patch)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    # also merge into runtime via env-like structure for next load_settings from toml+env;
    # settings.json is applied here:
    base = s.model_dump()
    base = _deep_merge(base, current)
    out = Settings.model_validate(base)
    out.home = s.home
    out.ensure_dirs()
    return out


def apply_settings_json(settings: Settings) -> Settings:
    """Merge ~/.takton-code/settings.json onto settings."""
    import json

    path = settings.settings_path()
    if not path.is_file():
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    merged = _deep_merge(settings.model_dump(), data)
    out = Settings.model_validate(merged)
    out.home = settings.home
    return out
