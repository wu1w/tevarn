"""Skill 契约（Phase 1）：包目录下 skill.yaml 的规范化定义与校验

skill.yaml 示例：
    name: code-review
    version: 0.2.0
    description: 按 checklist 审查 diff
    requires:
      bins: [git, rg]        # 必须存在的可执行文件
      python: [yaml]         # 必须可 import 的 python 模块
    tools: [file_read, shell]  # 挂载后会话工具白名单（并集；空=不过滤）
    permissions:
      fs: workspace          # none | workspace | any（声明式，执行仍走权限 gate）
      network: false
    workflow:                # 引导步骤，渲染进 system snippet
      - 先 git diff 看改动
      - 按 checklist 逐项审查

设计口径：
- 契约失败不炸加载：contract_errors 透出，包仍可用（降级为无契约）
- requires 仅检测+透出（API 可见），不硬阻断挂载（本地单用户场景）
- tools 白名单在执行边界（_execute_registered_tool）真实拦截
"""
from __future__ import annotations

import importlib.util
import logging
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

CONTRACT_FILENAMES = ("skill.yaml", "skill.yml", "takton.skill.yaml")


class SkillRequires(BaseModel):
    bins: list[str] = Field(default_factory=list)
    python: list[str] = Field(default_factory=list)


class SkillPermissions(BaseModel):
    fs: Literal["none", "workspace", "any"] = "workspace"
    network: bool = False


class SkillContract(BaseModel):
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    requires: SkillRequires = Field(default_factory=SkillRequires)
    tools: list[str] = Field(default_factory=list)
    permissions: SkillPermissions = Field(default_factory=SkillPermissions)
    workflow: list[str] = Field(default_factory=list)


def parse_contract(data: dict[str, Any]) -> tuple[SkillContract | None, list[str]]:
    """dict → SkillContract；返回 (contract, errors)。错误友好化，不 raise"""
    if not isinstance(data, dict):
        return None, ["contract must be a mapping"]
    try:
        return SkillContract.model_validate(data), []
    except ValidationError as e:
        errs = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()))
            errs.append(f"{loc}: {err.get('msg', 'invalid')}")
        return None, errs


def load_contract_for_dir(pkg_dir: str | Path) -> tuple[SkillContract | None, list[str]]:
    """在包目录找 skill.yaml 并解析；无文件返回 (None, [])"""
    d = Path(pkg_dir)
    for fn in CONTRACT_FILENAMES:
        p = d / fn
        if not p.is_file():
            continue
        try:
            import yaml  # type: ignore
        except Exception:
            return None, [f"{fn}: PyYAML 不可用，无法解析契约"]
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as e:
            return None, [f"{fn}: YAML 解析失败: {e}"]
        contract, errs = parse_contract(data)
        if contract is not None and not contract.name:
            contract.name = d.name
        return contract, errs
    return None, []


def check_requires(contract: SkillContract) -> list[str]:
    """检测 requires 缺失项，返回如 ['bin: rg', 'python: yaml']"""
    missing: list[str] = []
    for b in contract.requires.bins:
        if shutil.which(b) is None:
            missing.append(f"bin: {b}")
    for mod in contract.requires.python:
        if importlib.util.find_spec(mod) is None:
            missing.append(f"python: {mod}")
    return missing


def render_workflow_block(contract: SkillContract) -> str:
    """workflow 步骤渲染为 snippet 片段"""
    if not contract.workflow:
        return ""
    lines = [f"**Workflow（{contract.name or 'skill'}）**"]
    for i, step in enumerate(contract.workflow, 1):
        lines.append(f"{i}. {step}")
    return "\n".join(lines)
