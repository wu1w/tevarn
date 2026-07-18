"""
Desktop Agent 服务
桌面自动化核心：截图、键鼠控制、权限管理
"""

import asyncio
import base64
import logging
import platform
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# 平台检测
_CURRENT_PLATFORM = platform.system().lower()


class PermissionLevel(str, Enum):
    """权限级别"""
    ASK = "ask"                    # 每次询问
    ALLOW_ONCE = "allow_once"      # 允许一次
    ALLOW_SESSION = "allow_session"  # 本会话允许
    ALWAYS_ALLOW = "always_allow"  # 始终允许


class OperationType(str, Enum):
    """操作类型"""
    SCREENSHOT = "screenshot"
    CLICK = "click"
    TYPE = "type"
    OPEN_APP = "open_app"
    SCROLL = "scroll"
    DRAG = "drag"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"


class DesktopOperationResult:
    """桌面操作结果"""
    
    def __init__(
        self,
        success: bool,
        message: str = "",
        data: dict[str, Any] | None = None,
        error: str | None = None,
    ):
        self.success = success
        self.message = message
        self.data = data or {}
        self.error = error
        self.timestamp = datetime.now(timezone.utc)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "error": self.error,
            "timestamp": self.timestamp.isoformat(),
        }


class DesktopAgentService:
    """桌面代理服务"""
    
    def __init__(self):
        self._platform_adapter = None
        self._initialized = False
        self._session_permissions: dict[str, PermissionLevel] = {}
    
    async def initialize(self) -> None:
        """初始化平台适配器"""
        if self._initialized:
            return
        
        try:
            if _CURRENT_PLATFORM == "windows":
                from backend.services.desktop.adapters.windows_adapter import WindowsAdapter
                self._platform_adapter = WindowsAdapter()
            elif _CURRENT_PLATFORM == "darwin":
                from backend.services.desktop.adapters.mac_adapter import MacAdapter
                self._platform_adapter = MacAdapter()
            else:
                from backend.services.desktop.adapters.linux_adapter import LinuxAdapter
                self._platform_adapter = LinuxAdapter()
            
            await self._platform_adapter.initialize()
            self._initialized = True
            logger.info(f"Desktop agent initialized for platform: {_CURRENT_PLATFORM}")
            
        except Exception as e:
            logger.error(f"Failed to initialize desktop agent: {e}")
            raise
    
    async def check_permission(
        self,
        user_id: uuid.UUID,
        operation: OperationType,
        app_name: str | None = None,
    ) -> tuple[bool, PermissionLevel]:
        """
        检查权限
        
        Returns:
            (是否允许, 当前权限级别)
        """
        # 1. 检查会话级缓存
        cache_key = f"{user_id}:{operation}:{app_name or '*'}"
        if cache_key in self._session_permissions:
            level = self._session_permissions[cache_key]
            if level in (PermissionLevel.ALLOW_SESSION, PermissionLevel.ALWAYS_ALLOW):
                return True, level
        
        # 2. 检查数据库持久化权限
        try:
            from backend.repositories.desktop_permission_repo import AsyncDesktopPermissionRepository
            repo = AsyncDesktopPermissionRepository()
            perm = await repo.get_permission(user_id, operation.value, app_name)
            if perm and perm.level == PermissionLevel.ALWAYS_ALLOW:
                return True, PermissionLevel.ALWAYS_ALLOW
        except Exception as e:
            logger.warning(f"Failed to check permission from db: {e}")
        
        # 3. 需要询问
        return False, PermissionLevel.ASK
    
    async def set_permission(
        self,
        user_id: uuid.UUID,
        operation: OperationType,
        level: PermissionLevel,
        app_name: str | None = None,
    ) -> None:
        """设置权限"""
        cache_key = f"{user_id}:{operation}:{app_name or '*'}"
        
        if level == PermissionLevel.ALLOW_ONCE:
            # 仅本次有效，不存储
            pass
        elif level == PermissionLevel.ALLOW_SESSION:
            # 会话级，存内存
            self._session_permissions[cache_key] = level
        elif level == PermissionLevel.ALWAYS_ALLOW:
            # 持久化到数据库
            try:
                from backend.repositories.desktop_permission_repo import AsyncDesktopPermissionRepository
                repo = AsyncDesktopPermissionRepository()
                await repo.save_permission(
                    user_id=user_id,
                    operation=operation.value,
                    app_name=app_name,
                    level=level.value,
                )
            except Exception as e:
                logger.error(f"Failed to save permission: {e}")
    
    async def execute_operation(
        self,
        user_id: uuid.UUID,
        operation: OperationType,
        params: dict[str, Any],
        permission: PermissionLevel = PermissionLevel.ASK,
    ) -> DesktopOperationResult:
        """
        执行桌面操作
        
        Args:
            user_id: 用户ID
            operation: 操作类型
            params: 操作参数
            permission: 用户已授予的权限级别
        
        Returns:
            DesktopOperationResult
        """
        if not self._initialized:
            await self.initialize()
        
        # 检查权限
        allowed, current_level = await self.check_permission(
            user_id, operation, params.get("app_name")
        )
        
        if not allowed and permission == PermissionLevel.ASK:
            return DesktopOperationResult(
                success=False,
                message="需要用户授权",
                data={"requires_permission": True, "operation": operation.value},
            )
        
        # 记录已授予的权限
        if permission != PermissionLevel.ASK:
            await self.set_permission(
                user_id, operation, permission, params.get("app_name")
            )
        
        # 执行操作
        try:
            result = await self._execute_platform_operation(operation, params)
            return result
            
        except Exception as e:
            logger.error(f"Desktop operation failed: {operation} - {e}")
            return DesktopOperationResult(
                success=False,
                message=f"操作失败: {str(e)}",
                error=str(e),
            )
    
    async def _execute_platform_operation(
        self,
        operation: OperationType,
        params: dict[str, Any],
    ) -> DesktopOperationResult:
        """执行平台相关操作"""
        if not self._platform_adapter:
            raise RuntimeError("Platform adapter not initialized")
        
        handler = getattr(self._platform_adapter, operation.value, None)
        if not handler:
            return DesktopOperationResult(
                success=False,
                message=f"平台不支持操作: {operation.value}",
            )
        
        return await handler(**params)
    
    async def get_screen_stream(self, user_id: uuid.UUID):
        """获取屏幕流（WebSocket）"""
        if not self._initialized:
            await self.initialize()
        
        # 检查截图权限
        allowed, _ = await self.check_permission(
            user_id, OperationType.SCREENSHOT
        )
        if not allowed:
            raise PermissionError("Screenshot permission required")
        
        return self._platform_adapter.get_screen_stream()
    
    async def execute_task(
        self,
        user_id: uuid.UUID,
        task: str,
        permission: PermissionLevel = PermissionLevel.ASK,
    ) -> DesktopOperationResult:
        """
        执行自然语言桌面任务
        
        Args:
            user_id: 用户ID
            task: 自然语言任务描述
            permission: 用户已授予的权限级别
        
        Returns:
            DesktopOperationResult
        """
        if not self._initialized:
            await self.initialize()
        
        try:
            # 1. 先截图获取当前屏幕状态
            screenshot_result = await self._execute_platform_operation(
                OperationType.SCREENSHOT, {}
            )
            
            if not screenshot_result.success:
                return DesktopOperationResult(
                    success=False,
                    message="无法获取屏幕截图",
                    error=screenshot_result.error,
                )
            
            # 2. 分析屏幕内容
            from backend.services.desktop.task_planner import get_task_planner
            planner = get_task_planner()
            screen_context = await planner.analyze_screen(
                screenshot_base64=screenshot_result.data.get("image"),
                elements=screenshot_result.data.get("elements", []),
            )
            
            # 3. 分解任务为操作序列
            operations = await planner.plan_task(task, screen_context)
            
            if not operations:
                return DesktopOperationResult(
                    success=False,
                    message="无法分解任务",
                    error="Task planning failed",
                )
            
            # 4. 执行操作序列
            results = []
            for i, op in enumerate(operations):
                op_type = OperationType(op["type"])
                op_params = op["params"]
                
                # 检查权限
                allowed, _ = await self.check_permission(
                    user_id, op_type, op_params.get("app_name")
                )
                
                if not allowed and permission == PermissionLevel.ASK:
                    return DesktopOperationResult(
                        success=False,
                        message=f"操作 {i+1}/{len(operations)} 需要授权: {op['description']}",
                        data={
                            "requires_permission": True,
                            "operation": op_type.value,
                            "step": i + 1,
                            "total_steps": len(operations),
                            "description": op["description"],
                        },
                    )
                
                # 执行操作
                result = await self._execute_platform_operation(op_type, op_params)
                results.append({
                    "step": i + 1,
                    "operation": op_type.value,
                    "description": op["description"],
                    "success": result.success,
                    "message": result.message,
                })
                
                # 如果操作失败，停止执行
                if not result.success:
                    return DesktopOperationResult(
                        success=False,
                        message=f"操作 {i+1}/{len(operations)} 失败: {result.message}",
                        data={
                            "completed_steps": results,
                            "failed_step": i + 1,
                        },
                        error=result.error,
                    )
            
            return DesktopOperationResult(
                success=True,
                message=f"任务完成，共执行 {len(operations)} 个操作",
                data={
                    "operations": results,
                    "total_steps": len(operations),
                },
            )
            
        except Exception as e:
            logger.error(f"Desktop task execution failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"任务执行失败: {str(e)}",
                error=str(e),
            )
    
    def clear_session_permissions(self, user_id: uuid.UUID) -> None:
        """清除用户会话权限"""
        keys_to_remove = [
            k for k in self._session_permissions.keys()
            if k.startswith(f"{user_id}:")
        ]
        for key in keys_to_remove:
            del self._session_permissions[key]


# 全局服务实例
_desktop_service: DesktopAgentService | None = None


def get_desktop_service() -> DesktopAgentService:
    """获取桌面服务单例"""
    global _desktop_service
    if _desktop_service is None:
        _desktop_service = DesktopAgentService()
    return _desktop_service
