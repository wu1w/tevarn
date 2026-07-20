"""
统一工具加载器（异步）

在 FastAPI startup 事件中调用，加载所有来源工具到 ToolRegistry。
"""

from __future__ import annotations

import logging

from backend.repositories.skill_repo import AsyncSkillRepository
from backend.repositories.tool_repo import AsyncToolRepository
from backend.skills import SkillRegistry
from backend.tools.builtins import BUILTIN_TOOL_CLASSES
from backend.tools.adapters.db_tool_adapter import DbToolAdapter
from backend.tools.adapters.dynamic_adapter import DynamicSkillAdapter
from backend.tools.adapters.skill_adapter import SkillToolAdapter
from backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


async def load_builtin_tools() -> None:
    """加载内置 Skill 到统一工具注册表"""
    from backend.skills import builtins

    _ = builtins

    for skill in SkillRegistry.get_all_skills():
        adapter = SkillToolAdapter(skill)
        ToolRegistry.register(adapter)
    logger.info(f"Loaded {len(SkillRegistry.get_all_skills())} builtin skills as tools")


async def load_dynamic_skills() -> None:
    """从数据库加载用户自定义 Skill"""
    try:
        repo = AsyncSkillRepository()
        active = await repo.get_active_skills()
        for skill in active:
            if skill.is_builtin:
                continue
            from backend.skills.dynamic import DynamicSkill

            dynamic = DynamicSkill.from_db(skill)
            adapter = DynamicSkillAdapter(dynamic)
            ToolRegistry.register(adapter)
        logger.info(f"Loaded {len(active)} dynamic skills as tools")
    except Exception as e:
        logger.warning(f"Failed to load dynamic skills: {e}")


async def load_db_tools() -> None:
    """从数据库加载用户配置的 Tool"""
    try:
        repo = AsyncToolRepository()
        active = await repo.get_active_tools()
        for tool in active:
            adapter = DbToolAdapter(tool)
            ToolRegistry.register(adapter)
        logger.info(f"Loaded {len(active)} db tools")
    except Exception as e:
        logger.warning(f"Failed to load db tools: {e}")


async def load_all_tools(include_db: bool = False) -> None:
    """加载所有来源工具

    include_db: 是否加载数据库中的旧版工具。v3.0 阶段默认 False，
    避免旧工具覆盖新的统一 BaseTool；等 Week 1 Day 3 迁移完后再打开。
    """
    ToolRegistry.clear()
    await load_builtin_tools()
    await load_dynamic_skills()
    # TEE: 进化 playbook 挂入 ToolRegistry（可被模型直接调用）
    try:
        from backend.evolution.runtime_tools import load_active_evolution_tools

        n = load_active_evolution_tools()
        logger.info(f"Loaded {n} evolution playbook tools")
    except Exception as e:
        logger.warning(f"Failed to load evolution tools: {e}")
    # v3.0: 加载新的 BUILTIN 工具实现，覆盖数据库中同名的旧工具
    for cls in BUILTIN_TOOL_CLASSES:
        ToolRegistry.register(cls())
    # v3.1: 加载 Agent 自配置工具
    try:
        from backend.tools.builtins import SELF_CONFIG_TOOLS

        for cls in SELF_CONFIG_TOOLS:
            ToolRegistry.register(cls())
        logger.info(f"Loaded {len(SELF_CONFIG_TOOLS)} self-config tools")
    except ImportError:
        logger.debug("Self-config tools not available")
    # v3.2: 工作流自然语言生成 / 保存工具
    try:
        from backend.tools.builtins.workflow_tools import (
            GenerateWorkflow,
            UpdateDag,
            ValidateDag,
            SaveWorkflow,
        )

        workflow_tool_classes = [GenerateWorkflow, UpdateDag, ValidateDag, SaveWorkflow]
        for cls in workflow_tool_classes:
            ToolRegistry.register(cls())
        logger.info(f"Loaded {len(workflow_tool_classes)} workflow tools")
    except Exception as e:
        logger.warning(f"Failed to load workflow tools: {e}")
    if include_db:
        await load_db_tools()

    # v0.2: 加载 Desktop 工具
    try:
        from backend.services.desktop.tools import register_desktop_tools

        n = register_desktop_tools(ToolRegistry)
        logger.info(f"Loaded {n} desktop tools")
    except Exception as e:
        logger.warning(f"Failed to load desktop tools: {e}")

    # agent-ops: shell_session / device_onboard / clarify / session_search / apply_patch / ...
    try:
        from backend.tools.builtins.agent_ops_tools import AGENT_OPS_TOOL_CLASSES

        for cls in AGENT_OPS_TOOL_CLASSES:
            ToolRegistry.register(cls())
        logger.info(f"Loaded {len(AGENT_OPS_TOOL_CLASSES)} agent-ops tools")
    except Exception as e:
        logger.warning(f"Failed to load agent-ops tools: {e}")

    # capability pack: autopilot / memory_pref / github / desktop_observe / ...
    try:
        from backend.tools.builtins.capability_tools import CAPABILITY_TOOL_CLASSES

        for cls in CAPABILITY_TOOL_CLASSES:
            ToolRegistry.register(cls())
        logger.info(f"Loaded {len(CAPABILITY_TOOL_CLASSES)} capability tools")
    except Exception as e:
        logger.warning(f"Failed to load capability tools: {e}")

    # Wave A: doc_read/write, image_generate, calendar, tts, capability_status
    try:
        from backend.tools.builtins.wave_a_tools import WAVE_A_TOOL_CLASSES

        for cls in WAVE_A_TOOL_CLASSES:
            ToolRegistry.register(cls())
        logger.info(f"Loaded {len(WAVE_A_TOOL_CLASSES)} wave-A tools")
    except Exception as e:
        logger.warning(f"Failed to load wave-A tools: {e}")

    logger.info(f"Unified ToolRegistry loaded: {len(ToolRegistry.get_all())} tools")
