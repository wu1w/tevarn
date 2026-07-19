"""
Windows 平台桌面适配器
使用 Windows-MCP (UIA API) 实现精准控件识别
"""

import asyncio
import base64
import io
import logging
from typing import Any, AsyncGenerator

from backend.services.desktop import DesktopOperationResult

logger = logging.getLogger(__name__)


class WindowsAdapter:
    """Windows 平台适配器"""
    
    def __init__(self):
        self._mcp_client = None
        self._initialized = False
        self._use_fallback = False
    
    async def initialize(self) -> None:
        """初始化 Windows-MCP 连接"""
        if self._initialized:
            return
        
        try:
            # 尝试连接 Windows-MCP
            from backend.mcp_hub.client import MCPClient, MCPServerConfig
            
            # Windows-MCP 配置（可通过环境变量覆盖）
            import os
            mcp_command = os.environ.get("WINDOWS_MCP_COMMAND", "windows-mcp")
            mcp_args = os.environ.get("WINDOWS_MCP_ARGS", "").split() if os.environ.get("WINDOWS_MCP_ARGS") else []
            
            config = MCPServerConfig(
                name="windows-mcp",
                transport="stdio",
                command=mcp_command,
                args=mcp_args,
            )
            
            self._mcp_client = MCPClient(config)
            await self._mcp_client.connect()
            
            # 测试连接
            tools = await self._mcp_client.list_tools()
            logger.info(f"Windows-MCP connected, available tools: {[t.name for t in tools.tools]}")
            
        except Exception as e:
            logger.warning(f"Windows-MCP not available, using fallback mode: {e}")
            self._use_fallback = True
        
        self._initialized = True
    
    async def screenshot(self, **kwargs) -> DesktopOperationResult:
        """截取屏幕"""
        try:
            if self._mcp_client and not self._use_fallback:
                # 使用 MCP 截图
                result = await self._mcp_client.call_tool("screenshot", {})
                
                # 解析 MCP 返回结果
                data = self._parse_mcp_result(result)
                
                return DesktopOperationResult(
                    success=True,
                    message="截图成功",
                    data={
                        "image": data.get("image"),
                        "elements": data.get("elements", []),
                        "mode": "mcp",
                    }
                )
            else:
                # 降级：使用 PIL 截图
                return await self._fallback_screenshot()
                
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"截图失败: {e}",
                error=str(e)
            )
    
    async def click(self, element_id: str | None = None, x: int | None = None, y: int | None = None, **kwargs) -> DesktopOperationResult:
        """点击元素或坐标"""
        try:
            if self._use_fallback:
                if x is None or y is None:
                    return DesktopOperationResult(
                        success=False,
                        message="降级模式需要 x/y 坐标点击（无 UIA element_id）",
                    )
                return await self._fallback_click(int(x), int(y))

            if element_id:
                result = await self._mcp_client.call_tool("click", {"element_id": element_id})
            elif x is not None and y is not None:
                result = await self._mcp_client.call_tool("click", {"x": x, "y": y})
            else:
                return DesktopOperationResult(
                    success=False,
                    message="缺少 element_id 或坐标参数",
                )

            return DesktopOperationResult(
                success=True,
                message="点击成功",
                data=self._parse_mcp_result(result),
            )

        except Exception as e:
            logger.error(f"Click failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"点击失败: {e}",
                error=str(e),
            )

    async def type(self, element_id: str | None = None, text: str = "", **kwargs) -> DesktopOperationResult:
        """输入文本"""
        try:
            if self._use_fallback:
                return await self._fallback_type(str(text or ""))

            if element_id:
                result = await self._mcp_client.call_tool(
                    "type", {"element_id": element_id, "text": text}
                )
            else:
                result = await self._mcp_client.call_tool("type_text", {"text": text})

            return DesktopOperationResult(
                success=True,
                message="输入成功",
                data=self._parse_mcp_result(result),
            )

        except Exception as e:
            logger.error(f"Type failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"输入失败: {e}",
                error=str(e),
            )
    
    async def open_app(self, app_name: str, **kwargs) -> DesktopOperationResult:
        """打开应用"""
        try:
            if self._use_fallback:
                # 使用 subprocess 打开
                import subprocess
                subprocess.Popen(f"start {app_name}", shell=True)
                result = {"status": "launched", "mode": "fallback"}
            else:
                result = await self._mcp_client.call_tool("open_app", {"name": app_name})
                result = self._parse_mcp_result(result)
            
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
            if self._use_fallback:
                return await self._fallback_scroll(direction, int(amount or 3))

            result = await self._mcp_client.call_tool(
                "scroll", {"direction": direction, "amount": amount}
            )
            return DesktopOperationResult(
                success=True,
                message="滚动成功",
                data=self._parse_mcp_result(result),
            )
        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"滚动失败: {e}",
                error=str(e),
            )

    async def drag(self, from_x: int, from_y: int, to_x: int, to_y: int, **kwargs) -> DesktopOperationResult:
        """拖拽"""
        try:
            if self._use_fallback:
                return DesktopOperationResult(
                    success=False,
                    message="降级模式不支持拖拽操作",
                )
            
            result = await self._mcp_client.call_tool("drag", {
                "from_x": from_x,
                "from_y": from_y,
                "to_x": to_x,
                "to_y": to_y
            })
            
            return DesktopOperationResult(
                success=True,
                message="拖拽成功",
                data=self._parse_mcp_result(result)
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
                await asyncio.sleep(0.5)  # 500ms 间隔
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
            from PIL import ImageGrab
            
            # 截取全屏
            screenshot = ImageGrab.grab()
            
            # 转换为 base64
            buffer = io.BytesIO()
            screenshot.save(buffer, format='JPEG', quality=70)
            img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
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
    
    def _parse_mcp_result(self, result: str) -> dict[str, Any]:
        """解析 MCP 返回结果"""
        # MCP 返回的是字符串，尝试解析为 JSON
        try:
            import json
            return json.loads(result)
        except:
            # 如果不是 JSON，返回原始字符串
            return {"raw": result}
    
    async def close(self) -> None:
        """关闭连接"""
        if self._mcp_client:
            await self._mcp_client.close()
            self._mcp_client = None
        self._initialized = False
