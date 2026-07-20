"""
Cluster Protocol - JSON 任务分发协议
协调者 LLM 输出 JSON 任务卡，实时派活给子代理
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TaskPriority(str, Enum):
    """任务优先级"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class AgentRole(str, Enum):
    """代理角色"""
    COORDINATOR = "coordinator"  # 协调者
    WORKER = "worker"            # 工作者
    SPECIALIST = "specialist"    # 专家
    VALIDATOR = "validator"      # 验证者


@dataclass
class TaskCard:
    """
    任务卡 - 协调者 LLM 输出的 JSON 格式任务定义
    """
    # 基础信息
    id: str
    name: str
    description: str
    
    # 执行配置
    prompt: str                          # 给子代理的提示词
    agent_role: AgentRole = AgentRole.WORKER
    agent_config: dict = field(default_factory=dict)
    
    # 调度配置
    priority: TaskPriority = TaskPriority.NORMAL
    depends_on: list[str] = field(default_factory=list)
    timeout_seconds: int = 300
    retry_count: int = 0
    max_retries: int = 3
    
    # 输入输出
    input_data: dict = field(default_factory=dict)
    expected_output: str | None = None   # 期望的输出格式
    
    # 元数据
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "agent_role": self.agent_role.value,
            "agent_config": self.agent_config,
            "priority": self.priority.value,
            "depends_on": self.depends_on,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "input_data": self.input_data,
            "expected_output": self.expected_output,
            "tags": self.tags,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "TaskCard":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            prompt=data.get("prompt", ""),
            agent_role=AgentRole(data.get("agent_role", "worker")),
            agent_config=data.get("agent_config", {}),
            priority=TaskPriority(data.get("priority", "normal")),
            depends_on=data.get("depends_on", []),
            timeout_seconds=data.get("timeout_seconds", 300),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            input_data=data.get("input_data", {}),
            expected_output=data.get("expected_output"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ClusterPlan:
    """
    集群计划 - 完整的任务分发计划
    """
    id: str
    name: str
    description: str
    
    # 任务列表
    tasks: list[TaskCard]
    
    # 全局配置
    max_parallel: int = 5
    timeout_seconds: int = 600
    aggregation_strategy: str = "synthesize"
    
    # 上下文
    context: dict = field(default_factory=dict)
    
    # 元数据
    created_by: str = "coordinator"
    metadata: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tasks": [t.to_dict() for t in self.tasks],
            "max_parallel": self.max_parallel,
            "timeout_seconds": self.timeout_seconds,
            "aggregation_strategy": self.aggregation_strategy,
            "context": self.context,
            "created_by": self.created_by,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ClusterPlan":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            tasks=[TaskCard.from_dict(t) for t in data.get("tasks", [])],
            max_parallel=data.get("max_parallel", 5),
            timeout_seconds=data.get("timeout_seconds", 600),
            aggregation_strategy=data.get("aggregation_strategy", "synthesize"),
            context=data.get("context", {}),
            created_by=data.get("created_by", "coordinator"),
            metadata=data.get("metadata", {}),
        )


class ClusterProtocol:
    """
    集群协议 - 协调者 LLM 的任务分发协议
    """
    
    # 协调者系统提示词
    COORDINATOR_SYSTEM_PROMPT = """你是一个任务协调者。你的职责是将复杂任务分解为多个子任务，并分发给不同的子代理执行。

请按照以下 JSON 格式输出任务计划：

```json
{
  "plan_id": "unique-plan-id",
  "name": "计划名称",
  "description": "计划描述",
  "max_parallel": 5,
  "aggregation_strategy": "synthesize",
  "tasks": [
    {
      "id": "task-1",
      "name": "子任务1",
      "description": "子任务描述",
      "prompt": "给子代理的具体指令",
      "agent_role": "worker",
      "priority": "high",
      "depends_on": [],
      "tags": ["tag1", "tag2"]
    },
    {
      "id": "task-2",
      "name": "子任务2",
      "description": "子任务描述",
      "prompt": "给子代理的具体指令",
      "agent_role": "specialist",
      "priority": "normal",
      "depends_on": ["task-1"],
      "tags": ["tag3"]
    }
  ]
}
```

规则：
1. 每个任务必须有唯一的 id
2. depends_on 中的任务 id 必须存在于 tasks 中
3. 不要有循环依赖
4. priority 可以是: low, normal, high, urgent
5. agent_role 可以是: coordinator, worker, specialist, validator
6. aggregation_strategy 可以是: vote, merge, chain, synthesize, weighted, best
"""
    
    # 任务分解提示词模板
    TASK_DECOMPOSITION_TEMPLATE = """请将以下任务分解为多个子任务：

任务描述：{task_description}

可用代理类型：
{available_agents}

约束条件：
- 最大并行数：{max_parallel}
- 超时时间：{timeout_seconds}秒
- 聚合策略：{aggregation_strategy}

请输出 JSON 格式的任务计划。"""
    
    @classmethod
    def create_coordinator_prompt(
        cls,
        task_description: str,
        available_agents: list[dict] | None = None,
        max_parallel: int = 5,
        timeout_seconds: int = 600,
        aggregation_strategy: str = "synthesize",
    ) -> str:
        """
        创建协调者提示词
        
        Args:
            task_description: 任务描述
            available_agents: 可用代理列表
            max_parallel: 最大并行数
            timeout_seconds: 超时时间
            aggregation_strategy: 聚合策略
            
        Returns:
            协调者提示词
        """
        agents_text = "默认代理"
        if available_agents:
            agents_text = json.dumps(available_agents, indent=2, ensure_ascii=False)
        
        return cls.TASK_DECOMPOSITION_TEMPLATE.format(
            task_description=task_description,
            available_agents=agents_text,
            max_parallel=max_parallel,
            timeout_seconds=timeout_seconds,
            aggregation_strategy=aggregation_strategy,
        )
    
    @classmethod
    def parse_plan(cls, json_str: str) -> ClusterPlan | None:
        """
        解析任务计划 JSON
        
        Args:
            json_str: JSON 字符串
            
        Returns:
            ClusterPlan 或 None（解析失败）
        """
        try:
            # 提取 JSON 代码块
            if "```json" in json_str:
                start = json_str.find("```json") + 7
                end = json_str.find("```", start)
                json_str = json_str[start:end].strip()
            elif "```" in json_str:
                start = json_str.find("```") + 3
                end = json_str.find("```", start)
                json_str = json_str[start:end].strip()
            
            data = json.loads(json_str)
            return ClusterPlan.from_dict(data)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse plan JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to create plan from dict: {e}")
            return None
    
    @classmethod
    def validate_plan(cls, plan: ClusterPlan) -> tuple[bool, list[str]]:
        """
        验证任务计划
        
        Returns:
            (是否有效, 错误信息列表)
        """
        errors = []
        
        # 检查任务 ID 唯一性
        task_ids = [t.id for t in plan.tasks]
        if len(task_ids) != len(set(task_ids)):
            errors.append("Duplicate task IDs found")
        
        # 检查依赖关系
        for task in plan.tasks:
            for dep_id in task.depends_on:
                if dep_id not in task_ids:
                    errors.append(f"Task {task.id} depends on unknown task {dep_id}")
        
        # 检查循环依赖
        if cls._has_cycle(plan.tasks):
            errors.append("Circular dependency detected")
        
        return len(errors) == 0, errors
    
    @classmethod
    def _has_cycle(cls, tasks: list[TaskCard]) -> bool:
        """检查循环依赖"""
        visited = set()
        rec_stack = set()
        
        def has_cycle(task_id: str) -> bool:
            visited.add(task_id)
            rec_stack.add(task_id)
            
            task = next((t for t in tasks if t.id == task_id), None)
            if task:
                for dep_id in task.depends_on:
                    if dep_id not in visited:
                        if has_cycle(dep_id):
                            return True
                    elif dep_id in rec_stack:
                        return True
            
            rec_stack.remove(task_id)
            return False
        
        for task in tasks:
            if task.id not in visited:
                if has_cycle(task.id):
                    return True
        
        return False
    
    @classmethod
    def plan_to_executor_format(cls, plan: ClusterPlan) -> list[dict]:
        """
        将计划转换为执行器格式
        
        Returns:
            子任务字典列表
        """
        return [
            {
                "id": task.id,
                "name": task.name,
                "description": task.description,
                "prompt": task.prompt,
                "agent_config": {
                    **task.agent_config,
                    "role": task.agent_role.value,
                    "priority": task.priority.value,
                },
                "depends_on": task.depends_on,
                "metadata": {
                    "tags": task.tags,
                    "timeout_seconds": task.timeout_seconds,
                    "max_retries": task.max_retries,
                    **task.metadata,
                },
            }
            for task in plan.tasks
        ]


# 便捷函数
def create_task_card(
    name: str,
    prompt: str,
    description: str = "",
    agent_role: str = "worker",
    priority: str = "normal",
    depends_on: list[str] | None = None,
    **kwargs,
) -> TaskCard:
    """
    创建任务卡（便捷函数）
    
    Args:
        name: 任务名称
        prompt: 提示词
        description: 描述
        agent_role: 代理角色
        priority: 优先级
        depends_on: 依赖任务 ID 列表
        **kwargs: 其他参数
        
    Returns:
        TaskCard
    """
    import uuid
    
    return TaskCard(
        id=str(uuid.uuid4()),
        name=name,
        description=description,
        prompt=prompt,
        agent_role=AgentRole(agent_role),
        priority=TaskPriority(priority),
        depends_on=depends_on or [],
        **kwargs,
    )


def create_cluster_plan(
    name: str,
    tasks: list[TaskCard],
    description: str = "",
    max_parallel: int = 5,
    aggregation_strategy: str = "synthesize",
    **kwargs,
) -> ClusterPlan:
    """
    创建集群计划（便捷函数）
    
    Args:
        name: 计划名称
        tasks: 任务卡列表
        description: 描述
        max_parallel: 最大并行数
        aggregation_strategy: 聚合策略
        **kwargs: 其他参数
        
    Returns:
        ClusterPlan
    """
    import uuid
    
    return ClusterPlan(
        id=str(uuid.uuid4()),
        name=name,
        description=description,
        tasks=tasks,
        max_parallel=max_parallel,
        aggregation_strategy=aggregation_strategy,
        **kwargs,
    )
