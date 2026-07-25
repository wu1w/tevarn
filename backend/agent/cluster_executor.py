"""
Cluster Executor - 真·并行执行器
使用 asyncio.gather 实现子代理并行执行
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AggregationStrategy(str, Enum):
    """聚合策略"""
    VOTE = "vote"           # 投票（多数决）
    MERGE = "merge"         # 合并结果
    CHAIN = "chain"         # 链式传递
    SYNTHESIZE = "synthesize"  # 主 LLM 综合


@dataclass
class SubTask:
    """子任务定义"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    prompt: str = ""                    # 子代理提示词
    agent_config: dict = field(default_factory=dict)  # 子代理配置
    depends_on: list[str] = field(default_factory=list)  # 依赖的任务 ID
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "agent_config": self.agent_config,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.metadata,
        }


@dataclass
class ClusterResult:
    """集群执行结果"""
    task_id: str
    status: TaskStatus
    sub_tasks: list[SubTask]
    aggregated_result: Any = None
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "sub_tasks": [st.to_dict() for st in self.sub_tasks],
            "aggregated_result": self.aggregated_result,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.metadata,
        }


class ClusterExecutor:
    """
    集群执行器
    
    功能：
    1. 解析 JSON 任务分发协议
    2. 并行执行子任务（asyncio.gather）
    3. 处理任务依赖关系
    4. 聚合结果
    """
    
    def __init__(
        self,
        max_parallel: int = 5,
        timeout_seconds: int = 300,
        aggregation_strategy: AggregationStrategy = AggregationStrategy.SYNTHESIZE,
    ):
        self.max_parallel = max_parallel
        self.timeout_seconds = timeout_seconds
        self.aggregation_strategy = aggregation_strategy
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._sub_agents: dict[str, Any] = {}  # agent_id -> agent_instance
        
    async def execute(
        self,
        task_description: str,
        sub_tasks: list[dict],
        aggregation_strategy: AggregationStrategy | None = None,
        progress_callback: Callable[[str, int, str], None] | None = None,
    ) -> ClusterResult:
        """
        执行集群任务
        
        Args:
            task_description: 任务描述
            sub_tasks: 子任务列表（JSON 格式）
            aggregation_strategy: 聚合策略（覆盖默认）
            progress_callback: 进度回调 (task_id, progress, message)
            
        Returns:
            ClusterResult: 执行结果
        """
        task_id = str(uuid.uuid4())
        strategy = aggregation_strategy or self.aggregation_strategy
        
        logger.info(f"Starting cluster execution: {task_id}, {len(sub_tasks)} sub-tasks")
        
        # 解析子任务
        tasks = [self._parse_sub_task(st) for st in sub_tasks]
        
        # 检查依赖关系
        if not self._validate_dependencies(tasks):
            return ClusterResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                sub_tasks=tasks,
                error="Invalid task dependencies",
            )
        
        result = ClusterResult(
            task_id=task_id,
            status=TaskStatus.RUNNING,
            sub_tasks=tasks,
        )
        
        try:
            # 并行执行
            await self._execute_parallel(tasks, progress_callback)

            # Phase 2：交付契约解析 + reviewer 复核（settings.cluster_review_enabled）
            await self._run_review_loop(tasks, task_description)

            # 聚合结果
            aggregated = await self._aggregate_results(tasks, strategy)

            result.status = TaskStatus.COMPLETED
            result.aggregated_result = aggregated
            result.completed_at = datetime.now(timezone.utc)
            result.metadata["review"] = {
                "reviewed": sum(1 for t in tasks if t.metadata.get("review")),
                "rejected": sum(1 for t in tasks if t.metadata.get("rejected")),
            }
            
        except Exception as e:
            logger.error(f"Cluster execution failed: {e}")
            result.status = TaskStatus.FAILED
            result.error = str(e)
            result.completed_at = datetime.now(timezone.utc)
        
        return result
    
    def _parse_sub_task(self, data: dict) -> SubTask:
        """解析子任务定义"""
        return SubTask(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", "unnamed"),
            description=data.get("description", ""),
            prompt=data.get("prompt", ""),
            agent_config=data.get("agent_config", {}),
            depends_on=data.get("depends_on", []),
            metadata=data.get("metadata", {}),
        )
    
    def _validate_dependencies(self, tasks: list[SubTask]) -> bool:
        """验证依赖关系（无循环依赖）"""
        task_ids = {t.id for t in tasks}
        
        for task in tasks:
            for dep_id in task.depends_on:
                if dep_id not in task_ids:
                    logger.error(f"Task {task.id} depends on unknown task {dep_id}")
                    return False
        
        # 检查循环依赖（DFS）
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
                    logger.error(f"Circular dependency detected in task {task.id}")
                    return False
        
        return True
    
    async def _execute_parallel(
        self,
        tasks: list[SubTask],
        progress_callback: Callable[[str, int, str], None] | None,
    ) -> None:
        """并行执行任务（考虑依赖关系）"""
        # 按依赖关系分层
        layers = self._topological_sort(tasks)
        
        for layer_idx, layer in enumerate(layers):
            logger.info(f"Executing layer {layer_idx + 1}/{len(layers)}: {len(layer)} tasks")
            
            # 并行执行当前层
            coroutines = [
                self._execute_single_task(task, progress_callback)
                for task in layer
            ]
            
            await asyncio.gather(*coroutines)
    
    def _topological_sort(self, tasks: list[SubTask]) -> list[list[SubTask]]:
        """拓扑排序（分层）"""
        # 计算入度
        in_degree = {t.id: len(t.depends_on) for t in tasks}
        task_map = {t.id: t for t in tasks}
        
        layers = []
        current_layer = [t for t in tasks if in_degree[t.id] == 0]
        
        while current_layer:
            layers.append(current_layer)
            next_layer = []
            
            for task in current_layer:
                # 减少依赖当前任务的任务的入度
                for other in tasks:
                    if task.id in other.depends_on:
                        in_degree[other.id] -= 1
                        if in_degree[other.id] == 0:
                            next_layer.append(other)
            
            current_layer = next_layer
        
        return layers
    
    async def _execute_single_task(
        self,
        task: SubTask,
        progress_callback: Callable[[str, int, str], None] | None,
    ) -> None:
        """执行单个任务"""
        async with self._semaphore:
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(timezone.utc)
            
            if progress_callback:
                progress_callback(task.id, 0, f"Starting {task.name}")
            
            try:
                # 获取或创建子代理
                agent = await self._get_or_create_agent(task)
                
                # 执行任务
                result = await self._run_agent_task(agent, task)
                
                task.result = result
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(timezone.utc)
                
                if progress_callback:
                    progress_callback(task.id, 100, f"Completed {task.name}")
                
            except asyncio.TimeoutError:
                task.status = TaskStatus.FAILED
                task.error = "Task timeout"
                task.completed_at = datetime.now(timezone.utc)
                logger.error(f"Task {task.id} timeout")
                
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                task.completed_at = datetime.now(timezone.utc)
                logger.error(f"Task {task.id} failed: {e}")
    
    async def _get_or_create_agent(self, task: SubTask) -> Any:
        """获取子代理配置 - 透传 persona（model_ref / system_prompt / name）"""
        cfg = dict(task.agent_config or {})
        cfg.setdefault("agent_id", "default")
        return cfg
    
    def _resolve_llm(self, agent: Any) -> Any:
        """按 agent_config.model_ref（provider_id/model）解析专属 LLM 服务。

        解析失败或缺失时降级到全局默认服务，保证 cluster 可用。
        """
        from backend.services.llm import LLMServiceFactory

        model_ref = str((agent or {}).get("model_ref") or "").strip()
        if model_ref and "/" in model_ref:
            provider_id, model = model_ref.split("/", 1)
            try:
                return LLMServiceFactory.get_service_for_snapshot({
                    "provider": provider_id,
                    "provider_id": provider_id,
                    "model": model,
                })
            except Exception as e:
                logger.warning(
                    "resolve model_ref %s failed, fallback to default LLM: %s",
                    model_ref, e,
                )
        return LLMServiceFactory.get_service()
    
    async def _run_agent_task(self, agent: Any, task: SubTask) -> Any:
        """运行代理任务"""
        # 使用 asyncio.wait_for 实现超时
        return await asyncio.wait_for(
            self._execute_agent_prompt(agent, task.prompt, task.metadata),
            timeout=self.timeout_seconds,
        )
    
    async def _execute_agent_prompt(self, agent: Any, prompt: str, metadata: dict) -> Any:
        """执行子代理提示 - 真实调用 LLM（按 persona 解析模型与系统提示词）"""
        from backend.agent.agent_contract import CONTRACT_INSTRUCTION

        llm = self._resolve_llm(agent)
        system_prompt = (
            str((agent or {}).get("system_prompt") or "").strip()
            or "你是一个专业的 AI 助手，请认真完成分配给你的子任务。"
        )
        model_ref = str((agent or {}).get("model_ref") or "").strip() or None
        try:
            # 调用 LLM 生成回复（使用非流式接口；附加交付契约格式要求）
            response = await llm.chat_complete([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt + CONTRACT_INSTRUCTION}
            ])

            return {
                "status": "success",
                "prompt": prompt,
                "result": response.content if hasattr(response, 'content') else str(response),
                "model": model_ref,
                "metadata": metadata,
            }
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            # 禁止以「模拟结果」冒充成功：向上抛出，由调用方标记 TaskStatus.FAILED
            raise RuntimeError(f"sub-agent LLM call failed: {e}") from e

    async def _run_review_loop(self, tasks: list[SubTask], task_description: str) -> None:
        """Phase 2 Review Loop：契约解析交付物 → reviewer 复核 → revise 返工 / reject 剔除

        诚实降级：reviewer 不可用或输出不可解析 → 全部默认 pass，不阻塞交付。
        """
        from backend.core.config import settings

        if not getattr(settings, "cluster_review_enabled", True):
            return

        import copy

        from backend.agent.agent_contract import (
            REVIEWER_SYSTEM_PROMPT,
            AgentDeliverable,
            build_review_prompt,
            parse_deliverable,
            parse_review_verdicts,
        )
        from backend.services.llm import LLMServiceFactory

        done = [
            t for t in tasks
            if t.status == TaskStatus.COMPLETED and isinstance(t.result, dict)
        ]
        if not done:
            return

        def _mk_deliverable(t: SubTask) -> AgentDeliverable:
            return parse_deliverable(
                str((t.result or {}).get("result") or ""),
                agent_id=str(t.agent_config.get("agent_id") or "default"),
                task_id=t.id,
                model=str(t.agent_config.get("model_ref") or "").strip() or None,
            )

        # 1) 契约解析
        dmap: dict[str, AgentDeliverable] = {}
        for t in done:
            d = _mk_deliverable(t)
            dmap[t.id] = d
            t.metadata["deliverable"] = d.to_dict()

        # 2) reviewer 复核（失败不阻塞）
        try:
            reviewer = LLMServiceFactory.get_service()
        except Exception as e:
            logger.warning("reviewer LLM unavailable, skip review: %s", e)
            return

        max_revise = int(getattr(settings, "cluster_review_max_revise", 1) or 0)
        rounds = 0
        while True:
            try:
                resp = await reviewer.chat_complete([
                    {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
                    {"role": "user", "content": build_review_prompt(
                        task_description, list(dmap.values())
                    )},
                ])
                raw = resp.content if hasattr(resp, "content") else str(resp)
            except Exception as e:
                logger.warning("reviewer call failed, treat all as pass: %s", e)
                return

            verdicts = parse_review_verdicts(raw, [t.id for t in done])
            for t in done:
                v = verdicts[t.id]
                t.metadata["review"] = v.to_dict()
                if v.verdict == "reject":
                    t.metadata["rejected"] = True

            revise_tasks = [t for t in done if verdicts[t.id].verdict == "revise"]
            if not revise_tasks or rounds >= max_revise:
                break
            rounds += 1
            for t in revise_tasks:
                v = verdicts[t.id]
                try:
                    agent = await self._get_or_create_agent(t)
                    t2 = copy.copy(t)
                    t2.prompt = (
                        t.prompt
                        + "\n\n---\n【复核返工要求】复核发现的问题："
                        + ("；".join(v.issues) or "无")
                        + f"。修改建议：{v.suggestion or '无'}。请修正后重新交付。"
                    )
                    t.result = await self._run_agent_task(agent, t2)
                    t.metadata["rejected"] = False
                    t.metadata["review_rounds"] = rounds
                    d = _mk_deliverable(t)
                    dmap[t.id] = d
                    t.metadata["deliverable"] = d.to_dict()
                except Exception as e:
                    logger.warning("revise re-run failed for %s: %s", t.id, e)
                    t.metadata["rejected"] = True
    
    async def _aggregate_results(
        self,
        tasks: list[SubTask],
        strategy: AggregationStrategy,
    ) -> Any:
        """聚合结果"""
        results = [t.result for t in tasks if t.status == TaskStatus.COMPLETED]
        errors = [t.error for t in tasks if t.status == TaskStatus.FAILED]
        
        if strategy == AggregationStrategy.VOTE:
            return self._aggregate_vote(results)
        elif strategy == AggregationStrategy.MERGE:
            return self._aggregate_merge(results)
        elif strategy == AggregationStrategy.CHAIN:
            return self._aggregate_chain(tasks)
        elif strategy == AggregationStrategy.SYNTHESIZE:
            return await self._aggregate_synthesize(tasks, errors)
        else:
            return {"results": results, "errors": errors}
    
    def _aggregate_vote(self, results: list[Any]) -> Any:
        """投票聚合（多数决）"""
        if not results:
            return None
        
        # 简单实现：返回出现次数最多的结果
        from collections import Counter
        counter = Counter(str(r) for r in results)
        most_common = counter.most_common(1)
        return most_common[0][0] if most_common else None
    
    def _aggregate_merge(self, results: list[Any]) -> Any:
        """合并聚合"""
        if not results:
            return None
        
        # 合并所有结果
        merged = []
        for r in results:
            if isinstance(r, list):
                merged.extend(r)
            else:
                merged.append(r)
        return merged
    
    def _aggregate_chain(self, tasks: list[SubTask]) -> Any:
        """链式聚合（按依赖顺序传递）"""
        # 按拓扑顺序收集结果
        layers = self._topological_sort(tasks)
        chain_result = None
        
        for layer in layers:
            for task in layer:
                if task.status == TaskStatus.COMPLETED:
                    if chain_result is None:
                        chain_result = task.result
                    else:
                        # 将前一个结果作为下一个任务的输入
                        chain_result = {
                            "previous": chain_result,
                            "current": task.result,
                        }
        
        return chain_result
    
    async def _aggregate_synthesize(
        self,
        tasks: list[SubTask],
        errors: list[str | None],
    ) -> Any:
        """主 LLM 综合聚合（Phase 2：剔除 reject，携带契约/复核元数据）"""
        # reject 的交付物不参与综合
        used = [
            t for t in tasks
            if t.status == TaskStatus.COMPLETED and not t.metadata.get("rejected")
        ]
        results = [t.result for t in used]
        rejected_names = [t.name for t in tasks if t.metadata.get("rejected")]
        review_notes = [
            {
                "task": t.name,
                "verdict": (t.metadata.get("review") or {}).get("verdict"),
                "score": (t.metadata.get("review") or {}).get("score"),
                "confidence": (t.metadata.get("deliverable") or {}).get("confidence"),
                "signature": (t.metadata.get("deliverable") or {}).get("signature"),
                "issues": (t.metadata.get("review") or {}).get("issues"),
            }
            for t in used
            if t.metadata.get("review") or t.metadata.get("deliverable")
        ]
        # 使用 LLM 综合所有结果
        try:
            from backend.services.llm import LLMServiceFactory

            llm = LLMServiceFactory.get_service()

            prompt = f"""请综合以下子任务结果，给出最终答案：

子任务结果：
{json.dumps(results, indent=2, ensure_ascii=False)}

交付元数据（置信度/复核结论，低分结果请谨慎采纳）：
{json.dumps(review_notes, indent=2, ensure_ascii=False)}

错误信息：
{json.dumps([e for e in errors if e], indent=2, ensure_ascii=False)}

请给出综合后的结果："""

            response = await llm.chat_complete([
                {"role": "user", "content": prompt}
            ])

            return {
                "synthesized": response.content if hasattr(response, 'content') else str(response),
                "raw_results": results,
                "errors": [e for e in errors if e],
                "review_notes": review_notes,
                "rejected": rejected_names,
            }

        except Exception as e:
            logger.error(f"LLM synthesis failed: {e}")
            # 降级到简单合并
            merged = self._aggregate_merge(results)
            if isinstance(merged, dict):
                merged["review_notes"] = review_notes
                merged["rejected"] = rejected_names
            return merged


# 全局实例
_cluster_executor: ClusterExecutor | None = None


def get_cluster_executor() -> ClusterExecutor:
    """获取集群执行器实例"""
    global _cluster_executor
    if _cluster_executor is None:
        _cluster_executor = ClusterExecutor()
    return _cluster_executor
