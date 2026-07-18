"""
Cluster Aggregator - 结果聚合器
支持多种聚合策略：投票、合并、链式、LLM 综合
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class AggregationStrategy(str, Enum):
    """聚合策略"""
    VOTE = "vote"           # 投票（多数决）
    MERGE = "merge"         # 合并结果
    CHAIN = "chain"         # 链式传递
    SYNTHESIZE = "synthesize"  # 主 LLM 综合
    WEIGHTED = "weighted"   # 加权投票
    BEST = "best"           # 最佳结果（按评分）


@dataclass
class SubTaskResult:
    """子任务结果"""
    task_id: str
    task_name: str
    result: Any
    error: str | None = None
    confidence: float = 1.0  # 置信度（用于加权）
    metadata: dict = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "result": self.result,
            "error": self.error,
            "confidence": self.confidence,
            "metadata": self.metadata or {},
        }


class BaseAggregator(ABC):
    """聚合器基类"""
    
    @abstractmethod
    async def aggregate(self, results: list[SubTaskResult]) -> Any:
        """聚合结果"""
        pass


class VoteAggregator(BaseAggregator):
    """投票聚合器（多数决）"""
    
    async def aggregate(self, results: list[SubTaskResult]) -> Any:
        if not results:
            return None
        
        # 过滤错误结果
        valid_results = [r for r in results if r.error is None]
        if not valid_results:
            return {"error": "All tasks failed", "errors": [r.error for r in results]}
        
        # 统计投票
        from collections import Counter
        votes = Counter(str(r.result) for r in valid_results)
        
        # 返回多数结果
        most_common = votes.most_common(1)
        if most_common:
            winner, count = most_common[0]
            return {
                "result": winner,
                "votes": dict(votes),
                "winner_votes": count,
                "total_votes": len(valid_results),
                "confidence": count / len(valid_results),
            }
        
        return None


class WeightedVoteAggregator(BaseAggregator):
    """加权投票聚合器"""
    
    async def aggregate(self, results: list[SubTaskResult]) -> Any:
        if not results:
            return None
        
        valid_results = [r for r in results if r.error is None]
        if not valid_results:
            return {"error": "All tasks failed"}
        
        # 按置信度加权
        weighted_votes: dict[str, float] = {}
        for r in valid_results:
            key = str(r.result)
            weighted_votes[key] = weighted_votes.get(key, 0) + r.confidence
        
        # 找最大权重
        winner = max(weighted_votes.items(), key=lambda x: x[1])
        total_weight = sum(weighted_votes.values())
        
        return {
            "result": winner[0],
            "weighted_votes": weighted_votes,
            "winner_weight": winner[1],
            "total_weight": total_weight,
            "confidence": winner[1] / total_weight if total_weight > 0 else 0,
        }


class MergeAggregator(BaseAggregator):
    """合并聚合器"""
    
    async def aggregate(self, results: list[SubTaskResult]) -> Any:
        if not results:
            return None
        
        valid_results = [r for r in results if r.error is None]
        if not valid_results:
            return {"error": "All tasks failed"}
        
        # 智能合并
        merged = {
            "items": [],
            "summary": {},
            "sources": [],
        }
        
        for r in valid_results:
            merged["sources"].append({
                "task_id": r.task_id,
                "task_name": r.task_name,
                "confidence": r.confidence,
            })
            
            if isinstance(r.result, list):
                merged["items"].extend(r.result)
            elif isinstance(r.result, dict):
                # 合并字典
                for k, v in r.result.items():
                    if k not in merged["summary"]:
                        merged["summary"][k] = []
                    merged["summary"][k].append(v)
            else:
                merged["items"].append(r.result)
        
        # 去重
        if merged["items"]:
            seen = set()
            unique_items = []
            for item in merged["items"]:
                key = str(item)
                if key not in seen:
                    seen.add(key)
                    unique_items.append(item)
            merged["items"] = unique_items
        
        merged["count"] = len(merged["items"])
        return merged


class ChainAggregator(BaseAggregator):
    """链式聚合器"""
    
    async def aggregate(self, results: list[SubTaskResult]) -> Any:
        if not results:
            return None
        
        # 按顺序连接结果
        chain = []
        for r in results:
            chain.append({
                "step": r.task_name,
                "task_id": r.task_id,
                "result": r.result,
                "error": r.error,
            })
        
        # 提取最终结果（最后一个成功的）
        final_result = None
        for r in reversed(results):
            if r.error is None:
                final_result = r.result
                break
        
        return {
            "chain": chain,
            "final_result": final_result,
            "steps": len(chain),
            "successful_steps": len([r for r in results if r.error is None]),
        }


class SynthesizeAggregator(BaseAggregator):
    """LLM 综合聚合器"""
    
    def __init__(self, llm_service: Any = None):
        self.llm_service = llm_service
    
    async def aggregate(self, results: list[SubTaskResult]) -> Any:
        if not results:
            return None
        
        valid_results = [r for r in results if r.error is None]
        if not valid_results:
            return {"error": "All tasks failed", "errors": [r.error for r in results]}
        
        # 构建综合提示
        results_text = json.dumps(
            [r.to_dict() for r in valid_results],
            indent=2,
            ensure_ascii=False,
        )
        
        prompt = f"""请综合以下多个子任务的结果，给出一个统一的答案：

子任务结果：
{results_text}

请分析所有结果，给出综合后的最终答案。如果有冲突，请说明并给出你的判断。
"""
        
        try:
            if self.llm_service is None:
                from backend.services.llm import LLMServiceFactory
                self.llm_service = LLMServiceFactory.get_default_service()
            
            response = await self.llm_service.chat([
                {"role": "user", "content": prompt}
            ])
            
            return {
                "synthesized_result": response,
                "raw_results": [r.to_dict() for r in valid_results],
                "method": "llm_synthesis",
            }
            
        except Exception as e:
            logger.error(f"LLM synthesis failed: {e}")
            # 降级到合并
            fallback = MergeAggregator()
            return await fallback.aggregate(results)


class BestResultAggregator(BaseAggregator):
    """最佳结果聚合器（按评分）"""
    
    async def aggregate(self, results: list[SubTaskResult]) -> Any:
        if not results:
            return None
        
        valid_results = [r for r in results if r.error is None]
        if not valid_results:
            return {"error": "All tasks failed"}
        
        # 按置信度排序
        sorted_results = sorted(valid_results, key=lambda r: r.confidence, reverse=True)
        
        best = sorted_results[0]
        
        return {
            "best_result": best.result,
            "best_task": {
                "task_id": best.task_id,
                "task_name": best.task_name,
                "confidence": best.confidence,
            },
            "all_results": [r.to_dict() for r in sorted_results],
            "method": "best_confidence",
        }


class ClusterAggregator:
    """
    集群聚合器 - 统一管理多种聚合策略
    """
    
    _aggregators: dict[AggregationStrategy, BaseAggregator] = {
        AggregationStrategy.VOTE: VoteAggregator(),
        AggregationStrategy.WEIGHTED: WeightedVoteAggregator(),
        AggregationStrategy.MERGE: MergeAggregator(),
        AggregationStrategy.CHAIN: ChainAggregator(),
        AggregationStrategy.SYNTHESIZE: SynthesizeAggregator(),
        AggregationStrategy.BEST: BestResultAggregator(),
    }
    
    @classmethod
    def register_aggregator(
        cls,
        strategy: AggregationStrategy,
        aggregator: BaseAggregator,
    ) -> None:
        """注册自定义聚合器"""
        cls._aggregators[strategy] = aggregator
    
    @classmethod
    async def aggregate(
        cls,
        results: list[SubTaskResult],
        strategy: AggregationStrategy = AggregationStrategy.SYNTHESIZE,
    ) -> Any:
        """
        聚合结果
        
        Args:
            results: 子任务结果列表
            strategy: 聚合策略
            
        Returns:
            聚合后的结果
        """
        aggregator = cls._aggregators.get(strategy)
        if aggregator is None:
            logger.warning(f"Unknown aggregation strategy: {strategy}, using SYNTHESIZE")
            aggregator = cls._aggregators[AggregationStrategy.SYNTHESIZE]
        
        return await aggregator.aggregate(results)
    
    @classmethod
    def get_available_strategies(cls) -> list[str]:
        """获取可用的聚合策略"""
        return [s.value for s in cls._aggregators.keys()]


# 便捷函数
async def aggregate_results(
    results: list[dict],
    strategy: str = "synthesize",
) -> Any:
    """
    聚合结果（便捷函数）
    
    Args:
        results: 子任务结果字典列表
        strategy: 聚合策略字符串
        
    Returns:
        聚合后的结果
    """
    # 转换字典为 SubTaskResult
    sub_results = [
        SubTaskResult(
            task_id=r.get("task_id", ""),
            task_name=r.get("task_name", ""),
            result=r.get("result"),
            error=r.get("error"),
            confidence=r.get("confidence", 1.0),
            metadata=r.get("metadata"),
        )
        for r in results
    ]
    
    strategy_enum = AggregationStrategy(strategy)
    return await ClusterAggregator.aggregate(sub_results, strategy_enum)
