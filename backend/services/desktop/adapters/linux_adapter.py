"""
Linux 平台桌面适配器
使用 xdotool + scrot/import 实现基础桌面控制
"""

import asyncio
import base64
import logging
import shutil
import subprocess
import tempfile
import os
from typing import Any, AsyncGenerator

from backend.services.desktop import DesktopOperationResult

logger = logging.getLogger(__name__)


class LinuxAdapter:
    """Linux 平台适配器"""
    
    def __init__(self):
        self._initialized = False
        self._has_xdotool = False
        self._has_scrot = False
        self._has_import = False
    
    async def initialize(self) -> None:
        """检测可用工具"""
        if self._initialized:
            return
        
        # 检测 xdotool
        self._has_xdotool = shutil.which("xdotool") is not None
        
        # 检测截图工具
        self._has_scrot = shutil.which("scrot") is not None
        self._has_import = shutil.which("import") is not None
        
        self._initialized = True
        logger.info(
            f"Linux adapter initialized: "
            f"xdotool={self._has_xdotool}, "
            f"scrot={self._has_scrot}, "
            f"import={self._has_import}"
        )
    
    async def screenshot(self, **kwargs) -> DesktopOperationResult:
        """截取屏幕"""
        try:
            if self._has_scrot:
                return await self._screenshot_scrot()
            elif self._has_import:
                return await self._screenshot_import()
            else:
                return DesktopOperationResult(
                    success=False,
                    message="无可用截图工具，请安装 scrot 或 imagemagick",
                )
                
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return DesktopOperationResult(
                success=False,
                message=f"截图失败: {e}",
                error=str(e)
            )
    
    async def click(self, element_id: str | None = None, x: int | None = None, y: int | None = None, **kwargs) -> DesktopOperationResult:
        """点击"""
        if not self._has_xdotool:
            return DesktopOperationResult(
                success=False,
                message="xdotool 未安装，无法执行点击",
            )
        
        try:
            if x is not None and y is not None:
                # 移动鼠标并点击
                subprocess.run(["xdotool", "mousemove", str(x), str(y)], check=True)
                subprocess.run(["xdotool", "click", "1"], check=True)
            else:
                return DesktopOperationResult(
                    success=False,
                    message="Linux 平台需要坐标参数",
                )
            
            return DesktopOperationResult(
                success=True,
                message="点击成功",
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
        if not self._has_xdotool:
            return DesktopOperationResult(
                success=False,
                message="xdotool 未安装，无法输入文本",
            )
        
        try:
            subprocess.run(["xdotool", "type", "--", text], check=True)
            
            return DesktopOperationResult(
                success=True,
                message="输入成功",
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
            # 尝试直接执行
            subprocess.Popen([app_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            return DesktopOperationResult(
                success=True,
                message=f"已打开应用: {app_name}",
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
        if not self._has_xdotool:
            return DesktopOperationResult(
                success=False,
                message="xdotool 未安装，无法滚动",
            )
        
        try:
            # 4 = 上滚，5 = 下滚
            button = "4" if direction == "up" else "5"
            for _ in range(amount):
                subprocess.run(["xdotool", "click", button], check=True)
            
            return DesktopOperationResult(
                success=True,
                message="滚动成功",
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
        if not self._has_xdotool:
            return DesktopOperationResult(
                success=False,
                message="xdotool 未安装，无法拖拽",
            )
        
        try:
            # 移动鼠标到起始位置
            subprocess.run(["xdotool", "mousemove", str(from_x), str(from_y)], check=True)
            # 按下鼠标左键
            subprocess.run(["xdotool", "mousedown", "1"], check=True)
            # 移动到目标位置
            subprocess.run(["xdotool", "mousemove", str(to_x), str(to_y)], check=True)
            # 释放鼠标左键
            subprocess.run(["xdotool", "mouseup", "1"], check=True)
            
            return DesktopOperationResult(
                success=True,
                message="拖拽成功",
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
    

    def _finalize_screenshot(self, raw: bytes, tool: str, tmp_path: str) -> DesktopOperationResult:
        """Persist screenshot and return path (avoid megabyte base64 in tool loop)."""
        out_dir = os.environ.get("TAKTON_DESKTOP_SHOT_DIR") or os.path.join(
            tempfile.gettempdir(), "takton_desktop_shots"
        )
        os.makedirs(out_dir, exist_ok=True)
        import time
        out_path = os.path.join(out_dir, f"shot_{int(time.time()*1000)}_{tool}.jpg")
        with open(out_path, "wb") as f:
            f.write(raw)
        # tiny base64 head for debug only (optional empty)
        return DesktopOperationResult(
            success=True,
            message=f"截图成功 ({len(raw)} bytes)",
            data={
                "path": out_path,
                "bytes": len(raw),
                "tool": tool,
                "image": "",  # full image on disk; path is canonical
            },
        )

    async def _screenshot_scrot(self) -> DesktopOperationResult:
        """使用 scrot 截图"""
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        # scrot 不会覆盖已存在的空文件，必须先删掉占位
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        try:
            env = os.environ.copy()
            # 确保子进程带上 DISPLAY
            proc = subprocess.run(
                ["scrot", "-q", "70", tmp_path],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            if proc.returncode != 0:
                return DesktopOperationResult(
                    success=False,
                    message=f"scrot 失败: {proc.stderr or proc.stdout or proc.returncode}",
                    error=proc.stderr,
                )
            with open(tmp_path, "rb") as f:
                raw = f.read()
            if len(raw) < 100:
                return DesktopOperationResult(
                    success=False,
                    message=f"scrot 输出过小 ({len(raw)} bytes)，检查 DISPLAY={env.get('DISPLAY')}",
                )
            img_base64 = base64.b64encode(raw).decode("utf-8")
            return DesktopOperationResult(
                success=True,
                message="截图成功",
                data={
                    "image": img_base64,
                    "tool": "scrot",
                    "bytes": len(raw),
                    "path": tmp_path,
                },
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    
    async def _screenshot_import(self) -> DesktopOperationResult:
        """使用 imagemagick import 截图"""
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        try:
            env = os.environ.copy()
            proc = subprocess.run(
                ["import", "-window", "root", "-quality", "70", tmp_path],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            if proc.returncode != 0:
                return DesktopOperationResult(
                    success=False,
                    message=f"import 失败: {proc.stderr or proc.returncode}",
                    error=proc.stderr,
                )
            with open(tmp_path, "rb") as f:
                raw = f.read()
            if len(raw) < 100:
                return DesktopOperationResult(
                    success=False,
                    message=f"import 输出过小 ({len(raw)} bytes)",
                )
            return self._finalize_screenshot(raw, "import", tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
