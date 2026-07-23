"""
Desktop 工具
将桌面操作注册为 Agent 可调用的工具
"""

import logging
import uuid
from typing import Any

from backend.services.desktop import OperationType, PermissionLevel, get_desktop_service
from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource

logger = logging.getLogger(__name__)


class DesktopScreenshotTool(BaseTool):
    """截图工具"""
    
    def __init__(self):
        super().__init__(
            name="desktop_screenshot",
            description=(
                "截取当前屏幕。用于桌面 GUI 操作前的感知。"
                "成功后结合 uia_snapshot/desktop_observe 定位再 click/type；"
                "不要在未截图/快照时盲点坐标。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.LOW,
        )
    
    async def execute(self, user_id: uuid.UUID | None = None, **kwargs: Any) -> dict[str, Any]:
        service = get_desktop_service()
        await service.initialize()
        
        result = await service.execute_operation(
            user_id=user_id or uuid.UUID(int=0),
            operation=OperationType.SCREENSHOT,
            params={},
            permission=PermissionLevel.ALLOW_ONCE,
        )
        
        return result.to_dict()


class DesktopClickTool(BaseTool):
    """点击工具"""
    
    def __init__(self):
        super().__init__(
            name="desktop_click",
            description=(
                "点击桌面元素或坐标。优先 element_id（来自 uia_snapshot）；"
                "否则传 x,y 像素坐标。点击前应先 screenshot/observe。"
                "失败时检查权限与前台窗口是否正确。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "string",
                        "description": "UIA 元素 ID（优先使用）",
                    },
                    "x": {
                        "type": "integer",
                        "description": "X 坐标（无 element_id 时使用）",
                    },
                    "y": {
                        "type": "integer",
                        "description": "Y 坐标（无 element_id 时使用）",
                    },
                },
                "required": [],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )
    
    async def execute(
        self,
        user_id: uuid.UUID | None = None,
        element_id: str | None = None,
        x: int | None = None,
        y: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        service = get_desktop_service()
        await service.initialize()
        
        result = await service.execute_operation(
            user_id=user_id or uuid.UUID(int=0),
            operation=OperationType.CLICK,
            params={"element_id": element_id, "x": x, "y": y},
            permission=PermissionLevel.ALLOW_ONCE,
        )
        
        return result.to_dict()


class DesktopTypeTool(BaseTool):
    """输入工具"""
    
    def __init__(self):
        super().__init__(
            name="desktop_type",
            description=(
                "向桌面控件或当前焦点输入文本。text 必填；"
                "有 element_id 时先聚焦该控件。用于填表/搜索框，勿用于长文文件写入（用 file_write）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "string",
                        "description": "UIA 元素 ID（优先使用）",
                    },
                    "text": {
                        "type": "string",
                        "description": "要输入的文本",
                    },
                },
                "required": ["text"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )
    
    async def execute(
        self,
        user_id: uuid.UUID | None = None,
        element_id: str | None = None,
        text: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        service = get_desktop_service()
        await service.initialize()
        
        result = await service.execute_operation(
            user_id=user_id or uuid.UUID(int=0),
            operation=OperationType.TYPE,
            params={"element_id": element_id, "text": text},
            permission=PermissionLevel.ALLOW_ONCE,
        )
        
        return result.to_dict()


class DesktopOpenAppTool(BaseTool):
    """打开应用工具"""
    
    def __init__(self):
        super().__init__(
            name="desktop_open_app",
            description=(
                "启动应用程序。app_name 如 notepad.exe、chrome、code。"
                "打开后用 screenshot/uia_snapshot 确认窗口再操作。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "应用程序名称（如 notepad.exe、chrome.exe）",
                    },
                },
                "required": ["app_name"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.LOW,
        )
    
    async def execute(
        self,
        user_id: uuid.UUID | None = None,
        app_name: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        service = get_desktop_service()
        await service.initialize()
        
        result = await service.execute_operation(
            user_id=user_id or uuid.UUID(int=0),
            operation=OperationType.OPEN_APP,
            params={"app_name": app_name},
            permission=PermissionLevel.ALLOW_ONCE,
        )
        
        return result.to_dict()


class DesktopScrollTool(BaseTool):
    """滚动工具"""
    
    def __init__(self):
        super().__init__(
            name="desktop_scroll",
            description=(
                "滚动当前焦点区域。direction=up|down，amount 默认 3。"
                "列表找不到目标时滚动再 snapshot，避免重复盲点。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "滚动方向",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "滚动量（行数）",
                        "default": 3,
                    },
                },
                "required": ["direction"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.LOW,
        )
    
    async def execute(
        self,
        user_id: uuid.UUID | None = None,
        direction: str = "down",
        amount: int = 3,
        **kwargs: Any,
    ) -> dict[str, Any]:
        service = get_desktop_service()
        await service.initialize()
        
        result = await service.execute_operation(
            user_id=user_id or uuid.UUID(int=0),
            operation=OperationType.SCROLL,
            params={"direction": direction, "amount": amount},
            permission=PermissionLevel.ALLOW_ONCE,
        )
        
        return result.to_dict()


class DesktopReadFileTool(BaseTool):
    """读取文件工具"""
    
    def __init__(self):
        super().__init__(
            name="desktop_read_file",
            description="读取指定路径的文件内容",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                },
                "required": ["path"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )
    
    async def execute(
        self,
        user_id: uuid.UUID | None = None,
        path: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        service = get_desktop_service()
        await service.initialize()
        
        result = await service.execute_operation(
            user_id=user_id or uuid.UUID(int=0),
            operation=OperationType.READ_FILE,
            params={"path": path},
            permission=PermissionLevel.ALLOW_ONCE,
        )
        
        return result.to_dict()


class DesktopWriteFileTool(BaseTool):
    """写入文件工具"""
    
    def __init__(self):
        super().__init__(
            name="desktop_write_file",
            description="将内容写入指定路径的文件",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "文件内容",
                    },
                },
                "required": ["path", "content"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.HIGH,
        )
    
    async def execute(
        self,
        user_id: uuid.UUID | None = None,
        path: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        service = get_desktop_service()
        await service.initialize()
        
        result = await service.execute_operation(
            user_id=user_id or uuid.UUID(int=0),
            operation=OperationType.WRITE_FILE,
            params={"path": path, "content": content},
            permission=PermissionLevel.ALLOW_ONCE,
        )
        
        return result.to_dict()


def register_desktop_tools(registry) -> int:
    """
    注册桌面工具到 ToolRegistry
    
    Returns:
        注册的工具数量
    """
    tools = [
        DesktopScreenshotTool(),
        DesktopClickTool(),
        DesktopTypeTool(),
        DesktopOpenAppTool(),
        DesktopScrollTool(),
        DesktopReadFileTool(),
        DesktopWriteFileTool(),
    ]
    
    for tool in tools:
        registry.register(tool)
    
    logger.info(f"Registered {len(tools)} desktop tools")
    return len(tools)
