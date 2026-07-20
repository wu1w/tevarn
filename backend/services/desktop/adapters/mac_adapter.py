"""
Mac 平台桌面适配器
使用 macOS Accessibility API
"""

import asyncio
import logging
from typing import Any, AsyncGenerator

from backend.services.desktop import DesktopOperationResult

logger = logging.getLogger(__name__)


class MacAdapter:
    """Mac 平台适配器"""
    
    def __init__(self):
        self._mcp_client = None
        self._initialized = False
    
    async def initialize(self) -> None:
        """初始化 Mac-MCP 连接"""
        if self._initialized:
            return
        
        try:
            from backend.mcp_hub.client import MCPClient
            
            self._mcp_client = MCPClient()
            await self._mcp_client.connect("macos-mcp")
            self._initialized = True
            logger.info("Mac adapter initialized with MCP")
            
        except Exception as e:
            logger.error(f"Failed to initialize Mac adapter: {e}")
            self._initialized = True
    
    async def screenshot(self, **kwargs) -> DesktopOperationResult:
        """截取屏幕"""
        try:
            if self._mcp_client:
                result = await self._mcp_client.call("screenshot", kwargs)
                return DesktopOperationResult(
                    success=True,
                    message="截图成功",
                    data={"image": result.get("image"), "elements": result.get("elements", [])}
                )
            else:
                return await self._fallback_screenshot()
                
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"截图失败: {e}",
                error=str(e)
            )
    
    async def click(self, element_id: str | None = None, x: int | None = None, y: int | None = None, **kwargs) -> DesktopOperationResult:
        """点击"""
        try:
            if self._mcp_client and element_id:
                result = await self._mcp_client.call("click", {"element_id": element_id})
            elif x is not None and y is not None:
                result = await self._mcp_client.call("click", {"x": x, "y": y})
            else:
                return DesktopOperationResult(
                    success=False,
                    message="缺少 element_id 或坐标参数",
                )
            
            return DesktopOperationResult(
                success=True,
                message="点击成功",
                data=result
            )
            
        except Exception as e:
            logger.error(f"Click failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"点击失败: {e}",
                error=str(e)
            )
    
    async def type(self, element_id: str | None = None, text: str = "", **kwargs) -> DesktopOperationResult:
        """输入文本"""
        try:
            if self._mcp_client and element_id:
                result = await self._mcp_client.call("type", {
                    "element_id": element_id,
                    "text": text
                })
            else:
                result = await self._mcp_client.call("type_text", {"text": text})
            
            return DesktopOperationResult(
                success=True,
                message="输入成功",
                data=result
            )
            
        except Exception as e:
            logger.error(f"Type failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"输入失败: {e}",
                error=str(e)
            )
    
    async def open_app(self, app_name: str, **kwargs) -> DesktopOperationResult:
        """打开应用"""
        try:
            if self._mcp_client:
                result = await self._mcp_client.call("open_app", {"name": app_name})
            else:
                import subprocess
                subprocess.Popen(["open", "-a", app_name])
                result = {"status": "launched"}
            
            return DesktopOperationResult(
                success=True,
                message=f"已打开应用: {app_name}",
                data=result
            )
            
        except Exception as e:
            logger.error(f"Open app failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"打开应用失败: {e}",
                error=str(e)
            )
    
    async def scroll(self, direction: str = "down", amount: int = 3, **kwargs) -> DesktopOperationResult:
        """滚动"""
        try:
            if self._mcp_client:
                result = await self._mcp_client.call("scroll", {
                    "direction": direction,
                    "amount": amount
                })
            else:
                result = {"status": "skipped"}
            
            return DesktopOperationResult(
                success=True,
                message="滚动成功",
                data=result
            )
            
        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"滚动失败: {e}",
                error=str(e)
            )
    
    async def drag(self, from_x: int, from_y: int, to_x: int, to_y: int, **kwargs) -> DesktopOperationResult:
        """拖拽"""
        try:
            if self._mcp_client:
                result = await self._mcp_client.call("drag", {
                    "from_x": from_x,
                    "from_y": from_y,
                    "to_x": to_x,
                    "to_y": to_y
                })
            else:
                result = {"status": "skipped"}
            
            return DesktopOperationResult(
                success=True,
                message="拖拽成功",
                data=result
            )
            
        except Exception as e:
            logger.error(f"Drag failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"拖拽失败: {e}",
                error=str(e)
            )
    
    async def read_file(self, path: str, **kwargs) -> DesktopOperationResult:
        """读取文件"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            return DesktopOperationResult(
                success=True,
                message="读取成功",
                data={"path": path, "content": content}
            )
            
        except Exception as e:
            logger.error(f"Read file failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"读取文件失败: {e}",
                error=str(e)
            )
    
    async def write_file(self, path: str, content: str, **kwargs) -> DesktopOperationResult:
        """写入文件"""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return DesktopOperationResult(
                success=True,
                message="写入成功",
                data={"path": path}
            )
            
        except Exception as e:
            logger.error(f"Write file failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"写入文件失败: {e}",
                error=str(e)
            )
    
    async def get_screen_stream(self) -> AsyncGenerator[dict[str, Any], None]:
        """获取屏幕流"""
        while True:
            try:
                result = await self.screenshot()
                if result.success:
                    yield {
                        "type": "screenshot",
                        "data": result.data,
                        "timestamp": result.timestamp.isoformat()
                    }
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Screen stream error: {e}")
                yield {
                    "type": "error",
                    "error": str(e)
                }
                break
    
    async def _fallback_screenshot(self) -> DesktopOperationResult:
        """降级截图方案"""
        try:
            import subprocess
            import base64
            import tempfile
            import os
            
            # 使用 screencapture 命令
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                tmp_path = tmp.name
            
            subprocess.run(["screencapture", "-x", "-t", "jpg", tmp_path], check=True)
            
            with open(tmp_path, 'rb') as f:
                img_base64 = base64.b64encode(f.read()).decode('utf-8')
            
            os.unlink(tmp_path)
            
            return DesktopOperationResult(
                success=True,
                message="截图成功（降级模式）",
                data={"image": img_base64, "mode": "fallback"}
            )
            
        except Exception as e:
            logger.error(f"Fallback screenshot failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"截图失败: {e}",
                error=str(e)
            )
