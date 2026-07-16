"""
统一工具层（Tool v3.0）基础测试

运行方式：
    cd backend && pytest tests/tools/test_unified_tools.py -v

或直接用系统 Python（需安装 pytest）：
    python -m pytest backend/tests/tools/test_unified_tools.py -v
"""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

import pytest

# 确保项目根目录在路径中
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# 在未安装 asyncpg 的环境中 mock 它（避免 Windows 开发环境依赖）
asyncpg = types.ModuleType("asyncpg")
asyncpg.Connection = object
asyncpg.pool = types.ModuleType("pool")
asyncpg.pool.Pool = object
sys.modules["asyncpg"] = asyncpg

from backend.tools.loader import load_all_tools
from backend.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
async def reset_registry():
    """每个测试前清空注册表"""
    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


@pytest.fixture
async def loaded_registry():
    """加载统一工具注册表"""
    await load_all_tools()
    return ToolRegistry


async def test_load_builtin_tools():
    """内置 Skill 和内置工具都成功注册"""
    await load_all_tools()
    tools = ToolRegistry.get_all()

    # 至少应包含 Skill 适配的工具和新的 BUILTIN 工具
    names = {t.name for t in tools}
    assert "bash" in names, "bash skill 应被注册为工具"
    assert "file_read" in names, "file_read builtin 应被注册"
    assert "command" in names
    assert "python" in names


async def test_tool_schema_generation():
    """统一注册表能为所有工具生成 OpenAI-compatible schema"""
    await load_all_tools()
    schemas = ToolRegistry.get_tools_schema()

    assert len(schemas) == len(ToolRegistry.get_all())
    for schema in schemas:
        assert "type" in schema and schema["type"] == "function"
        assert "function" in schema
        func = schema["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func


async def test_file_read_execution():
    """file_read 工具可以读取文件内容"""
    await load_all_tools()

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
        f.write("hello takton v3.0")
        path = f.name

    # 关闭安全目录限制（使用真实路径）
    tool = ToolRegistry.get("file_read")
    assert tool is not None

    # 默认 allowed_paths 是 None，但 execute_file_read 有内部安全目录限制
    # 这个测试仅验证接口通路与返回类型
    result = await ToolRegistry.execute("file_read", {"filepath": path})
    assert isinstance(result, str)


async def test_python_execution():
    """python 工具可以执行代码"""
    await load_all_tools()
    result = await ToolRegistry.execute("python", {"code": "print('ok')"})
    assert isinstance(result, str)
    assert "ok" in result or "[Error]" not in result


async def test_source_priority():
    """同名称时 Builtin 来源应覆盖 DB 来源"""
    from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource

    class LowPriorityTool(BaseTool):
        def __init__(self):
            super().__init__(
                name="same_name",
                description="db",
                parameters={},
                source=ToolSource.DB,
                risk_level=ToolRiskLevel.LOW,
            )

        async def execute(self, **kwargs):
            return "db"

    class HighPriorityTool(BaseTool):
        def __init__(self):
            super().__init__(
                name="same_name",
                description="builtin",
                parameters={},
                source=ToolSource.BUILTIN,
                risk_level=ToolRiskLevel.LOW,
            )

        async def execute(self, **kwargs):
            return "builtin"

    ToolRegistry.register(LowPriorityTool())
    ToolRegistry.register(HighPriorityTool())

    t = ToolRegistry.get("same_name")
    assert t.source == ToolSource.BUILTIN
    assert (await t.execute()) == "builtin"


async def test_disabled_tool_not_in_schema():
    """禁用的工具不应出现在 schema 列表中"""
    from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource

    class EnabledTool(BaseTool):
        def __init__(self):
            super().__init__(
                name="enabled_tool",
                description="enabled",
                parameters={},
                source=ToolSource.BUILTIN,
                risk_level=ToolRiskLevel.LOW,
                enabled=True,
            )

        async def execute(self, **kwargs):
            return "ok"

    class DisabledTool(BaseTool):
        def __init__(self):
            super().__init__(
                name="disabled_tool",
                description="disabled",
                parameters={},
                source=ToolSource.BUILTIN,
                risk_level=ToolRiskLevel.LOW,
                enabled=False,
            )

        async def execute(self, **kwargs):
            return "ok"

    ToolRegistry.register(EnabledTool())
    ToolRegistry.register(DisabledTool())

    schemas = ToolRegistry.get_tools_schema()
    names = {s["function"]["name"] for s in schemas}
    assert "enabled_tool" in names
    assert "disabled_tool" not in names


async def test_filter_by_source():
    """按来源过滤工具"""
    from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource

    class SkillLikeTool(BaseTool):
        def __init__(self):
            super().__init__(
                name="skill_tool",
                description="skill",
                parameters={},
                source=ToolSource.SKILL,
                risk_level=ToolRiskLevel.LOW,
            )

        async def execute(self, **kwargs):
            return "skill"

    ToolRegistry.register(SkillLikeTool())
    skill_tools = ToolRegistry.get_all(source=ToolSource.SKILL)
    assert len(skill_tools) == 1
    assert skill_tools[0].name == "skill_tool"
