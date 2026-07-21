"""
Desktop 任务分解服务
使用 LLM 将自然语言任务分解为具体操作序列
"""

import json
import logging
from typing import Any

from backend.services.llm import LLMServiceFactory

logger = logging.getLogger(__name__)


class DesktopTaskPlanner:
    """桌面任务规划器"""
    
    def __init__(self):
        self._llm_service = None
    
    async def _get_llm_service(self):
        """获取 LLM 服务"""
        if self._llm_service is None:
            self._llm_service = LLMServiceFactory.get_service()
        return self._llm_service
    
    async def plan_task(
        self,
        task: str,
        screenshot_context: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        将自然语言任务分解为操作序列
        
        Args:
            task: 自然语言任务描述
            screenshot_context: 当前屏幕截图描述（可选）
        
        Returns:
            操作序列列表
        """
        llm = await self._get_llm_service()
        
        # 构建提示词
        prompt = self._build_planning_prompt(task, screenshot_context)
        
        try:
            # 调用 LLM（chat_complete 非流式；provider 签名不含 temperature，需 TypeError 兜底）
            try:
                resp = await llm.chat_complete(
                    [{"role": "user", "content": prompt}],
                    temperature=0.3,  # type: ignore[call-arg] 低温度确保输出稳定
                )
            except TypeError:
                resp = await llm.chat_complete([{"role": "user", "content": prompt}])
            response = getattr(resp, "content", None) or str(resp)

            # 解析 JSON 响应
            operations = self._parse_planning_response(response)
            
            logger.info(f"Task planned: {len(operations)} operations")
            return operations
            
        except Exception as e:
            logger.error(f"Task planning failed: {e}")
            # 返回空操作序列
            return []
    
    def _build_planning_prompt(self, task: str, screenshot_context: str | None) -> str:
        """构建规划提示词"""
        base_prompt = f"""你是一个桌面自动化助手。请将用户的自然语言任务分解为具体的桌面操作序列。

可用操作类型：
1. screenshot - 截取屏幕（获取当前界面状态）
2. click - 点击（参数：element_id 或 x,y 坐标）
3. type - 输入文本（参数：element_id, text）
4. open_app - 打开应用（参数：app_name）
5. scroll - 滚动（参数：direction, amount）
6. drag - 拖拽（参数：from_x, from_y, to_x, to_y）
7. read_file - 读取文件（参数：path）
8. write_file - 写入文件（参数：path, content）

用户任务：{task}

"""
        
        if screenshot_context:
            base_prompt += f"""
当前屏幕状态：
{screenshot_context}

"""
        
        base_prompt += """请以 JSON 格式返回操作序列，格式如下：
{
    "analysis": "任务分析",
    "operations": [
        {"type": "操作类型", "params": {...}, "description": "操作描述"},
        ...
    ]
}

注意：
1. 先执行 screenshot 获取当前屏幕状态
2. 根据屏幕元素选择 element_id 或坐标
3. 每个操作都要有明确的 description
4. 如果任务复杂，可以分多步执行
"""
        
        return base_prompt
    
    def _parse_planning_response(self, response: str) -> list[dict[str, Any]]:
        """解析规划响应"""
        try:
            # 提取 JSON 部分
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            
            if json_start == -1 or json_end == 0:
                logger.warning("No JSON found in response")
                return []
            
            json_str = response[json_start:json_end]
            data = json.loads(json_str)
            
            operations = data.get("operations", [])
            
            # 验证操作格式
            valid_operations = []
            for op in operations:
                if "type" in op and "params" in op:
                    valid_operations.append({
                        "type": op["type"],
                        "params": op["params"],
                        "description": op.get("description", ""),
                    })
            
            return valid_operations
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse planning response: {e}")
            return []
    
    async def analyze_screen(
        self,
        screenshot_base64: str,
        elements: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        分析屏幕内容，生成上下文描述
        
        Args:
            screenshot_base64: Base64 编码的截图
            elements: UIA 元素列表（可选）
        
        Returns:
            屏幕内容描述
        """
        llm = await self._get_llm_service()
        
        # 构建元素描述
        elements_desc = ""
        if elements:
            elements_desc = "\n检测到的界面元素：\n"
            for i, elem in enumerate(elements[:20]):  # 限制数量
                elem_type = elem.get("type", "unknown")
                elem_name = elem.get("name", "")
                elem_id = elem.get("id", "")
                elements_desc += f"- {elem_type}: {elem_name} (id: {elem_id})\n"
        
        prompt = f"""请分析这个屏幕截图，描述当前界面状态。

{elements_desc}

请描述：
1. 当前打开的应用
2. 主要界面元素
3. 用户可能需要操作的区域

用简洁的中文回答："""
        
        try:
            # 如果有视觉模型，可以传入图片
            # 目前只使用文本描述；chat_complete 非流式，TypeError 兜底 temperature
            try:
                resp = await llm.chat_complete(
                    [{"role": "user", "content": prompt}],
                    temperature=0.5,  # type: ignore[call-arg]
                )
            except TypeError:
                resp = await llm.chat_complete([{"role": "user", "content": prompt}])

            return getattr(resp, "content", None) or str(resp)
            
        except Exception as e:
            logger.error(f"Screen analysis failed: {e}")
            return "无法分析屏幕内容"


# 全局实例
_task_planner: DesktopTaskPlanner | None = None


def get_task_planner() -> DesktopTaskPlanner:
    """获取任务规划器单例"""
    global _task_planner
    if _task_planner is None:
        _task_planner = DesktopTaskPlanner()
    return _task_planner
