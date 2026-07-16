"""
Workflow 执行引擎
拓扑排序 + 节点执行 + 数据流传递
"""

import ast
import asyncio
import json
import logging
import os
import sys
import tempfile
from collections import deque
from typing import Any

from backend.core.net_safety import UnsafeURLError, validate_public_url
from backend.core.config import settings
from backend.repositories.workflow_execution_repo import AsyncWorkflowExecutionRepository
from backend.services.llm import LLMService, LLMServiceFactory

logger = logging.getLogger(__name__)

# Python/自定义代码节点的最大执行时间（秒），防止死循环阻塞事件循环
_CODE_EXEC_TIMEOUT = 10

# 子进程 stdout/stderr 最大解码长度（字节），防止恶意代码通过 PIPE 耗尽宿主内存
_MAX_SUBPROCESS_OUTPUT = 1_048_576

# 子进程内允许使用的 builtins（已移除 type/getattr/hasattr 等危险 introspection 函数）
_SAFE_BUILTIN_NAMES = (
    "True",
    "False",
    "None",
    "len",
    "range",
    "enumerate",
    "zip",
    "map",
    "filter",
    "sum",
    "min",
    "max",
    "abs",
    "round",
    "int",
    "float",
    "str",
    "list",
    "dict",
    "set",
    "tuple",
    "print",
    "isinstance",
    "Exception",
    "ValueError",
    "KeyError",
    "IndexError",
)

# 禁止访问的危险 dunder 属性名（用于阻断常见的 Python 沙箱逃逸手法，
# 如 ().__class__.__bases__[0].__subclasses__() 之类的对象内省攻击）
_BLOCKED_ATTR_NAMES = {
    "__class__", "__bases__", "__base__", "__subclasses__", "__mro__",
    "__globals__", "__code__", "__closure__", "__builtins__", "__import__",
    "__loader__", "__spec__", "__dict__", "__getattribute__", "__setattr__",
    "__reduce__", "__reduce_ex__", "__init_subclass__", "__subclasshook__",
}

# 安全修复：额外禁止危险函数调用，防止沙箱逃逸
_BLOCKED_FUNC_NAMES = {"getattr", "hasattr", "setattr", "delattr", "compile", "eval", "exec"}


# 子进程包装代码模板。父进程将 {BUILTINS} 替换为受限 builtins 字面量后写入临时文件执行。
_CHILD_WRAPPER_TEMPLATE = r'''
import json
import sys
from io import StringIO


def main():
    raw = sys.stdin.read()
    if not raw:
        payload = {}
    else:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            print(json.dumps({
                "result": None,
                "stdout": "",
                "stderr": f"Invalid payload: {e}",
                "error": f"Invalid payload: {e}",
            }))
            return

    code = payload.get("code", "")
    input_data = payload.get("input_data")
    input_val = payload.get("input", input_data)
    context_data = payload.get("context", {})

    safe_builtins = {{BUILTINS}}
    exec_globals = {
        "__builtins__": safe_builtins,
        "input": input_val,
        "input_data": input_data,
        "context": context_data,
        "json": json,
    }

    _MAX_CAPTURED_OUTPUT = 100_000

    def _truncate(text: str) -> str:
        if len(text) > _MAX_CAPTURED_OUTPUT:
            return text[:_MAX_CAPTURED_OUTPUT] + "\n[Output truncated]"
        return text

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    stdout_buf = StringIO()
    stderr_buf = StringIO()
    sys.stdout = stdout_buf
    sys.stderr = stderr_buf

    error = None
    try:
        exec(compile(code, "<workflow>", "exec"), exec_globals)
        result = exec_globals.get("result")
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        result = None
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    output = {
        "result": result,
        "stdout": _truncate(stdout_buf.getvalue()),
        "stderr": _truncate(stderr_buf.getvalue()),
        "error": error,
    }
    print(json.dumps(output, default=str))


if __name__ == "__main__":
    main()
'''


class WorkflowExecutionError(Exception):
    """工作流执行异常"""

    pass


class WorkflowContext:
    """工作流执行上下文，存储节点输出数据和全局变量"""

    def __init__(self, initial_inputs: dict[str, Any] | None = None):
        self._data: dict[str, Any] = {}  # node_id -> {port_name -> value}
        self._globals: dict[str, Any] = initial_inputs or {}
        self._logs: list[dict[str, Any]] = []

    def set_node_output(self, node_id: str, outputs: dict[str, Any]) -> None:
        """存储节点的输出数据"""
        self._data[node_id] = outputs

    def get_node_output(self, node_id: str, port: str = "output") -> Any:
        """获取指定节点的指定端口输出"""
        node_data = self._data.get(node_id, {})
        # 尝试精确匹配，也尝试 fallback 到默认输出名
        if port in node_data:
            return node_data[port]
        # 如果只有一个输出，直接返回
        if len(node_data) == 1:
            return next(iter(node_data.values()))
        if node_data:
            self.log(
                node_id,
                "warning",
                f"端口 '{port}' 在节点输出 {list(node_data.keys())} 中未找到匹配项，返回 None",
            )
        return None

    def set_global(self, key: str, value: Any) -> None:
        self._globals[key] = value

    def get_global(self, key: str) -> Any:
        return self._globals.get(key)

    @property
    def globals(self) -> dict[str, Any]:
        return self._globals.copy()

    def log(self, node_id: str, level: str, message: str) -> None:
        self._logs.append({"node_id": node_id, "level": level, "message": message})

    @property
    def logs(self) -> list[dict[str, Any]]:
        return self._logs.copy()


class WorkflowEngine:
    """工作流执行引擎"""

    def __init__(self) -> None:
        self._llm_service: LLMService | None = None
        self.execution_repo = AsyncWorkflowExecutionRepository()

    async def _get_llm_service(self) -> LLMService:
        if self._llm_service is None:
            self._llm_service = LLMServiceFactory.get_service()
        return self._llm_service

    def build_execution_order(self, dag: dict[str, Any]) -> list[str]:
        """拓扑排序计算节点执行顺序"""
        nodes = dag.get("nodes", [])
        edges = dag.get("edges", [])

        if not isinstance(nodes, list):
            raise WorkflowExecutionError("工作流 DAG 的 'nodes' 字段必须是数组")

        node_ids: set[str] = set()
        for n in nodes:
            if not isinstance(n, dict) or "id" not in n:
                raise WorkflowExecutionError(f"工作流节点缺少必填字段 'id': {n!r}")
            node_ids.add(n["id"])

        adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}

        for edge in edges:
            fr = edge.get("from")
            to = edge.get("to")
            if fr in node_ids and to in node_ids:
                adjacency[fr].append(to)
                in_degree[to] += 1
            else:
                logger.warning(f"工作流边引用了不存在的节点，已忽略: {edge!r}")

        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        order: list[str] = []

        while queue:
            current = queue.popleft()
            order.append(current)
            for neighbor in adjacency[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) != len(node_ids):
            raise WorkflowExecutionError("工作流 DAG 存在循环依赖，无法执行")

        return order

    async def execute(
        self,
        dag: dict[str, Any],
        inputs: dict[str, Any] | None = None,
        *,
        workflow_id: str | None = None,
        trigger: str = "manual",
        invoked_by: str | None = None,
    ) -> dict[str, Any]:
        """
        执行工作流

        Args:
            dag: { nodes: [...], edges: [...] }
            inputs: 初始输入数据
            workflow_id: 工作流 ID（用于记录执行历史）
            trigger: 触发方式
            invoked_by: 触发用户 ID

        Returns:
            { outputs: {...}, logs: [...], node_outputs: {...} }
        """
        execution_id = None
        if workflow_id:
            try:
                log = await self.execution_repo.create({
                    "workflow_id": workflow_id,
                    "trigger": trigger,
                    "status": "running",
                    "invoked_by": invoked_by,
                })
                execution_id = log.id
            except Exception as e:
                logger.error(f"Failed to create workflow execution log: {e}")

        try:
            result = await self._run_dag(dag, inputs)
            if execution_id:
                await self.execution_repo.finish(execution_id, "success", result)
            return result
        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            if execution_id:
                await self.execution_repo.finish(execution_id, "failed", error=str(e))
            raise

    async def _run_dag(self, dag: dict[str, Any], inputs: dict[str, Any] | None) -> dict[str, Any]:
        """内部：执行工作流 DAG"""
        nodes = dag.get("nodes", [])
        edges = dag.get("edges", [])
        node_map: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}

        # 构建边索引: target_node_id -> list of {from, fromPort, toPort}
        incoming_edges: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            to_node = edge.get("to")
            incoming_edges.setdefault(to_node, []).append(edge)

        ctx = WorkflowContext(initial_inputs=inputs or {})
        execution_order = self.build_execution_order(dag)

        for node_id in execution_order:
            node = node_map[node_id]
            node_type = node.get("type", "")
            config = node.get("config", {})

            # 收集输入数据
            node_inputs: dict[str, Any] = {}
            for edge in incoming_edges.get(node_id, []):
                src_id = edge.get("from")
                src_port = edge.get("fromPort", "output")
                dst_port = edge.get("toPort", "input")
                value = ctx.get_node_output(src_id, src_port)
                # 如果多个边连接到同一个端口，用列表收集
                if dst_port in node_inputs:
                    if not isinstance(node_inputs[dst_port], list):
                        node_inputs[dst_port] = [node_inputs[dst_port]]
                    node_inputs[dst_port].append(value)
                else:
                    node_inputs[dst_port] = value

            try:
                outputs = await self._execute_node(node_type, config, node_inputs, ctx)
                ctx.set_node_output(node_id, outputs)
                ctx.log(node_id, "info", f"节点 {node_type} 执行成功")
            except Exception as e:
                logger.exception(f"节点 {node_id}({node_type}) 执行失败")
                ctx.log(node_id, "error", str(e))
                raise WorkflowExecutionError(f"节点 {node_id}({node_type}) 执行失败: {e}")

        # 收集输出节点数据作为最终结果
        final_outputs: dict[str, Any] = {}
        for node_id in execution_order:
            node = node_map[node_id]
            if node.get("type") == "output":
                out_name = node.get("config", {}).get("output_name", node_id)
                val = ctx.get_node_output(node_id, "value")
                final_outputs[out_name] = val

        return {
            "outputs": final_outputs,
            "logs": ctx.logs,
            "node_outputs": ctx._data,
        }

    async def _execute_node(
        self,
        node_type: str,
        config: dict[str, Any],
        inputs: dict[str, Any],
        ctx: WorkflowContext,
    ) -> dict[str, Any]:
        """根据节点类型执行对应的逻辑"""
        handlers = {
            "input": self._exec_input,
            "output": self._exec_output,
            "llm": self._exec_llm,
            "agent": self._exec_agent,
            "sub_agent": self._exec_sub_agent,
            "rag": self._exec_rag,
            "http": self._exec_http,
            "condition": self._exec_condition,
            "loop": self._exec_loop,
            "merge": self._exec_merge,
            "custom": self._exec_custom,
        }
        # 安全：Python 代码执行节点默认禁用，需显式配置开启
        if settings.enable_python_execution:
            handlers["python"] = self._exec_python
        handler = handlers.get(node_type)
        if handler is None:
            ctx.log("_engine", "error", f"未知的节点类型: {node_type!r}")
            raise WorkflowExecutionError(f"未知的节点类型: {node_type!r}")
        return await handler(config, inputs, ctx)

    # ── Input / Output ──

    async def _exec_input(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext
    ) -> dict[str, Any]:
        default_value = config.get("default_value", "")
        input_type = config.get("input_type", "text")
        # 如果有上游输入，优先使用上游输入
        value = inputs.get("value", default_value)
        if input_type == "number" and isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                pass
        elif input_type == "json" and isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        return {"value": value}

    async def _exec_output(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext
    ) -> dict[str, Any]:
        return {"value": inputs.get("value")}

    # ── AI ──

    async def _exec_llm(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext
    ) -> dict[str, Any]:
        prompt = str(inputs.get("prompt", ""))
        context = inputs.get("context", "")
        system_prompt = config.get("system_prompt", "")

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if context:
            messages.append({"role": "user", "content": f"上下文:\n{context}"})
        messages.append({"role": "user", "content": prompt})

        try:
            llm = await self._get_llm_service()
            resp = await llm.chat_complete(messages)
            return {"response": resp.content, "tokens_used": resp.usage.get("total_tokens", 0)}
        except Exception as e:
            logger.warning(f"LLM 调用失败: {e}，返回模拟响应")
            return {"response": f"[LLM模拟响应] 提示词: {prompt[:100]}...", "tokens_used": 0}

    async def _exec_agent(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext
    ) -> dict[str, Any]:
        """调用真实 Agent 执行任务"""
        task = str(inputs.get("task", ""))
        if not task:
            return {"result": "", "actions": []}

        try:
            from backend.agent import NexusAgentLoop
            from backend.database import AsyncSessionLocal
            from backend.repositories.session_repo import AsyncSessionRepository
            from backend.repositories.message_repo import AsyncMessageRepository
            from backend.repositories.context_repo import AsyncCtxItemRepository

            # 本地执行 agent（简化版：创建临时会话）
            async with AsyncSessionLocal() as db:
                session_repo = AsyncSessionRepository(db)
                message_repo = AsyncMessageRepository(db)
                ctx_repo = AsyncCtxItemRepository(db)

                agent = NexusAgentLoop(
                    session_repo=session_repo,
                    message_repo=message_repo,
                    ctx_item_repo=ctx_repo,
                )
                from backend.core.config import settings as app_settings
                agent.max_iterations = int(
                    config.get(
                        "max_steps",
                        getattr(app_settings, "agent_max_iterations", 25) or 25,
                    )
                )


                # 创建临时 session 来执行
                import uuid
                temp_sid = uuid.uuid4()
                await session_repo.create({"id": temp_sid, "config": config})

                result = await agent.run(temp_sid, task)
                return {"result": result, "actions": []}
        except Exception as e:
            logger.warning(f"Agent 执行失败: {e}")
            return {"result": f"[Agent执行失败] {e}", "actions": []}

    async def _exec_sub_agent(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext
    ) -> dict[str, Any]:
        """执行已配置的子代理节点：按 sub_agent_id 加载配置，以独立 system_prompt 跑一轮 LLM/Agent。"""
        import uuid as _uuid

        task = str(inputs.get("task", "") or "")
        context = str(inputs.get("context", "") or "")
        sub_agent_id = str(config.get("sub_agent_id") or "").strip()
        display_name = str(config.get("sub_agent_name") or "").strip()

        if not task:
            return {"result": "", "agent_name": display_name, "model_ref": ""}

        agent_row = None
        if sub_agent_id:
            try:
                from backend.repositories.sub_agent_repo import AsyncSubAgentRepository

                repo = AsyncSubAgentRepository()
                agent_row = await repo.get_by_id(_uuid.UUID(sub_agent_id))
            except Exception as e:
                logger.warning("load sub_agent %s failed: %s", sub_agent_id, e)

        if agent_row is None:
            # 回退：当作通用 agent 节点
            fallback = await self._exec_agent(
                {
                    "max_steps": config.get("max_steps", 5),
                    "agent_profile": "default",
                    "enable_tools": True,
                },
                {"task": task, "context": context},
                ctx,
            )
            return {
                "result": fallback.get("result", ""),
                "agent_name": display_name or "unknown-sub-agent",
                "model_ref": "",
            }

        name = getattr(agent_row, "name", None) or display_name or "sub-agent"
        model_ref = getattr(agent_row, "model_ref", "") or ""
        system_prompt = (getattr(agent_row, "system_prompt", "") or "").strip()
        append = str(config.get("append_system_prompt") or "").strip()
        if append:
            system_prompt = (system_prompt + "\n\n" + append).strip()

        max_steps = int(
            config.get("max_steps")
            or getattr(agent_row, "max_iterations", 5)
            or 5
        )
        temperature = float(getattr(agent_row, "temperature", 0.3) or 0.3)

        user_content = task
        if context:
            user_content = f"上下文:\n{context}\n\n任务:\n{task}"

        # 优先：单次 LLM 调用（轻量、稳定）；失败再退回 agent loop
        try:
            llm = await self._get_llm_service()
            messages: list[dict[str, str]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": user_content})
            # 若 LLM 服务支持 temperature，尽量透传
            try:
                resp = await llm.chat_complete(messages, temperature=temperature)  # type: ignore[call-arg]
            except TypeError:
                resp = await llm.chat_complete(messages)
            content = getattr(resp, "content", None) or str(resp)
            ctx.log(
                "_engine",
                "info",
                f"sub_agent `{name}` ({model_ref}) done via LLM, max_steps={max_steps}",
            )
            return {"result": content, "agent_name": name, "model_ref": model_ref}
        except Exception as e:
            logger.warning("sub_agent LLM path failed (%s), fallback agent loop: %s", name, e)

        try:
            agent_result = await self._exec_agent(
                {
                    "max_steps": max_steps,
                    "agent_profile": "default",
                    "enable_tools": True,
                    "system_prompt": system_prompt,
                },
                {"task": user_content},
                ctx,
            )
            return {
                "result": agent_result.get("result", ""),
                "agent_name": name,
                "model_ref": model_ref,
            }
        except Exception as e2:
            logger.warning("sub_agent fallback failed: %s", e2)
            return {
                "result": f"[子代理执行失败] {name}: {e2}",
                "agent_name": name,
                "model_ref": model_ref,
            }

    async def _exec_rag(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext
    ) -> dict[str, Any]:
        """调用真实 RAG 服务检索知识库"""
        query = str(inputs.get("query", ""))
        if not query:
            return {"answer": "", "sources": []}

        try:
            from backend.services.rag.factory import RAGServiceFactory
            rag = RAGServiceFactory.get_service()
            top_k = int(config.get("top_k", 5))
            context = await rag.search_knowledge_base(query, top_k=top_k)

            return {
                "answer": context,
                "sources": [{"doc": "knowledge_base", "score": 1.0}],
            }
        except Exception as e:
            logger.warning(f"RAG 检索失败: {e}")
            return {"answer": "", "sources": []}

    # ── Utility ──

    _ALLOWED_AST_NODES = (
        ast.Expression,
        ast.Module,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Return,
        ast.Delete,
        ast.Assign,
        ast.AugAssign,
        ast.AnnAssign,
        ast.For,
        ast.While,
        ast.If,
        ast.With,
        ast.Raise,
        ast.Try,
        ast.Assert,
        ast.Global,
        ast.Nonlocal,
        ast.Expr,
        ast.Pass,
        ast.Break,
        ast.Continue,
        ast.BoolOp,
        ast.NamedExpr,
        ast.BinOp,
        ast.UnaryOp,
        ast.Lambda,
        ast.IfExp,
        ast.Dict,
        ast.Set,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.Await,
        ast.Yield,
        ast.YieldFrom,
        ast.Compare,
        ast.Call,
        ast.FormattedValue,
        ast.JoinedStr,
        ast.Constant,
        ast.Attribute,
        ast.Subscript,
        ast.Starred,
        ast.Name,
        ast.List,
        ast.Tuple,
        ast.Slice,
        ast.Load,
        ast.Store,
        ast.Del,
        ast.Param,
        ast.arguments,
        ast.arg,
        ast.keyword,
        ast.comprehension,
        ast.ExceptHandler,
        # 基础运算符
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.UAdd,
        ast.USub,
        ast.Not,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
    )

    @classmethod
    def _validate_code_ast(cls, code: str) -> ast.AST:
        """
        对用户提供的代码做 AST 白名单 + 危险属性名黑名单校验。

        注意：这是一种尽力而为的缓解措施，不能完全杜绝 Python 沙箱逃逸
        （理论上仍可能存在未知的对象内省手法）。如需强隔离，应改用
        独立进程 + 资源限制，或成熟的沙箱运行时（如 WASM）。
        """
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as e:
            raise WorkflowExecutionError(f"代码语法错误: {e}") from e

        for node in ast.walk(tree):
            if not isinstance(node, cls._ALLOWED_AST_NODES):
                # Python 3.10+ 的 match 语句节点类型可能不存在于旧版本，使用属性名判断
                if type(node).__name__.startswith("Match"):
                    continue
                if type(node).__name__ == "TryStar":
                    continue
                raise WorkflowExecutionError(f"不安全的 AST 节点: {type(node).__name__}")
            if isinstance(node, ast.Import):
                raise WorkflowExecutionError("代码中禁止使用 import 语句")
            if isinstance(node, ast.ImportFrom):
                raise WorkflowExecutionError("代码中禁止使用 import 语句")
            if isinstance(node, ast.Attribute) and node.attr in _BLOCKED_ATTR_NAMES:
                raise WorkflowExecutionError(f"禁止访问属性: {node.attr}")
            if isinstance(node, ast.Name) and node.id in _BLOCKED_ATTR_NAMES:
                raise WorkflowExecutionError(f"禁止引用标识符: {node.id}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_FUNC_NAMES:
                raise WorkflowExecutionError(f"禁止调用函数: {node.func.id}")

        return tree

    async def _run_code_in_subprocess(
        self,
        code: str,
        input_data: Any,
        context_data: dict[str, Any],
        timeout: int = _CODE_EXEC_TIMEOUT,
        label: str = "<workflow>",
    ) -> dict[str, Any]:
        """
        在独立子进程中执行用户代码。

        - 通过临时文件承载子进程脚本，避免 Windows 命令行长度/引号问题
        - 通过 stdin JSON 传入 code + input_data + context
        - stdout 最后一行作为结果 JSON 解析
        - 超时后强制 kill 子进程
        - stdout/stderr 超过上限时截断，防止 PIPE 耗尽宿主内存
        """
        builtins_str = ", ".join(f'"{name}": {name}' for name in _SAFE_BUILTIN_NAMES)
        wrapper_code = _CHILD_WRAPPER_TEMPLATE.replace("{BUILTINS}", builtins_str)

        script_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                f.write(wrapper_code)
                script_path = f.name

            payload = json.dumps(
                {"code": code, "input_data": input_data, "context": context_data},
                default=str,
            )
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                script_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(input=payload.encode("utf-8")),
                    timeout=timeout,
                )
            except asyncio.TimeoutError as e:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                raise WorkflowExecutionError(
                    f"代码执行超时（超过 {timeout}s），可能存在死循环"
                ) from e

            stdout_text = stdout_b.decode("utf-8", errors="replace")
            stderr_text = stderr_b.decode("utf-8", errors="replace")
            if len(stdout_text) > _MAX_SUBPROCESS_OUTPUT:
                stdout_text = stdout_text[:_MAX_SUBPROCESS_OUTPUT] + "\n[Output truncated]"
            if len(stderr_text) > _MAX_SUBPROCESS_OUTPUT:
                stderr_text = stderr_text[:_MAX_SUBPROCESS_OUTPUT] + "\n[Output truncated]"

            lines = [line for line in stdout_text.splitlines() if line.strip()]
            if not lines:
                raise WorkflowExecutionError("子进程未返回执行结果")

            try:
                output = json.loads(lines[-1])
            except json.JSONDecodeError as e:
                raise WorkflowExecutionError(
                    f"子进程输出无法解析为 JSON: {e}\n原始输出: {lines[-1][:200]}"
                ) from e

            return {
                "result": output.get("result"),
                "stdout": output.get("stdout", stdout_text),
                "stderr": (output.get("stderr", stderr_text) or "")
                + (f"\n{output.get('error')}" if output.get("error") else ""),
                "error": output.get("error"),
            }
        finally:
            if script_path:
                try:
                    os.unlink(script_path)
                except Exception:
                    pass

    async def _exec_python(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext | None
    ) -> dict[str, Any]:
        code = config.get("code", "")
        input_data = inputs.get("input_data")
        context_data = ctx.globals if ctx else {}

        try:
            # AST 校验仍在父进程执行，拒绝危险语法和 import
            self._validate_code_ast(code)
            output = await self._run_code_in_subprocess(
                code=code,
                input_data=input_data,
                context_data=context_data,
                timeout=_CODE_EXEC_TIMEOUT,
                label="<workflow_python>",
            )
            if output.get("error"):
                return {
                    "output": None,
                    "stdout": output.get("stdout", ""),
                    "stderr": output.get("stderr", ""),
                }
            return {
                "output": output.get("result"),
                "stdout": output.get("stdout", ""),
                "stderr": output.get("stderr", ""),
            }
        except WorkflowExecutionError as e:
            return {"output": None, "stdout": "", "stderr": str(e)}
        except Exception as e:
            return {"output": None, "stdout": "", "stderr": str(e)}

    async def _exec_http(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext
    ) -> dict[str, Any]:
        import httpx

        method = config.get("method", "GET")
        url = config.get("url", "")
        timeout = config.get("timeout", 30)
        body = inputs.get("body")
        headers = inputs.get("headers", {})

        if not url:
            return {"response": None, "status": 0}

        try:
            validate_public_url(url)
        except UnsafeURLError as e:
            return {"response": None, "status": 0, "error": f"[Security Blocked] {e}"}

        try:
            timeout_val = float(timeout)
        except (TypeError, ValueError):
            timeout_val = 30.0

        try:
            async with httpx.AsyncClient(timeout=timeout_val, follow_redirects=False) as client:
                kwargs: dict[str, Any] = {"headers": headers}
                if body is not None and method in ("POST", "PUT", "PATCH"):
                    if isinstance(body, (dict, list)):
                        kwargs["json"] = body
                    else:
                        kwargs["content"] = str(body).encode("utf-8")
                resp = await client.request(method, url, **kwargs)
                # 检查重定向状态码
                if resp.status_code in (301, 302, 303, 307, 308):
                    return {"response": None, "status": resp.status_code, "error": f"[Security Blocked] Redirects are not followed (status {resp.status_code}, location={resp.headers.get('Location', '')})"}
                # 限制响应体大小
                MAX_RESP_SIZE = 500_000
                resp_text = resp.text
                if len(resp_text) > MAX_RESP_SIZE:
                    resp_text = resp_text[:MAX_RESP_SIZE] + "\n\n[Response truncated at 500KB]"
                return {"response": resp_text, "status": resp.status_code}
        except Exception as e:
            return {"response": None, "status": 0, "error": str(e)}

    # ── Logic ──

    async def _exec_condition(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext | None
    ) -> dict[str, Any]:
        condition_expr = config.get("condition", "")
        input_val = inputs.get("input")

        try:
            result = self._eval_condition(condition_expr, input_val, ctx.globals if ctx else {})
            return {"true": input_val if result else None, "false": input_val if not result else None}
        except Exception:
            # 默认走 false
            return {"true": None, "false": input_val}

    # 条件表达式只允许出现的 AST 节点（不含 Call/Attribute/Subscript，杜绝沙箱逃逸）
    _CONDITION_ALLOWED_NODES = (
        ast.Expression,
        ast.BoolOp,
        ast.BinOp,
        ast.UnaryOp,
        ast.Compare,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.List,
        ast.Tuple,
        ast.Dict,
        ast.IfExp,
        ast.And,
        ast.Or,
        ast.Not,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.USub,
        ast.UAdd,
    )

    @classmethod
    def _eval_condition(cls, expr: str, input_val: Any, context: dict[str, Any]) -> bool:
        """安全地求值条件表达式：仅允许比较/布尔/算术运算，禁止函数调用与属性访问"""
        if not expr:
            return bool(input_val)
        tree = ast.parse(expr, mode="eval")
        for node in ast.walk(tree):
            if not isinstance(node, cls._CONDITION_ALLOWED_NODES):
                raise WorkflowExecutionError(f"条件表达式包含不安全的语法: {type(node).__name__}")
        code = compile(tree, "<condition>", "eval")
        return bool(eval(code, {"__builtins__": {}}, {"input": input_val, "context": context}))  # noqa: S307

    async def _exec_loop(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext
    ) -> dict[str, Any]:
        """
        循环节点（当前实现限制）：
        引擎按一次性拓扑排序执行整个 DAG，不支持对"循环体"子图进行多次重复调度。
        因此该节点目前只能将输入列表整体透传，并返回第一个元素供简单场景使用，
        不会真正对每个元素重复执行下游子图。如果工作流依赖"逐条处理并分别执行下游节点"
        的语义，请勿使用此节点，需等待后续实现真正的子图循环调度机制。
        """
        items = inputs.get("items", [])
        if not isinstance(items, list):
            items = [items]
        ctx.log(
            "loop",
            "warning",
            "循环节点当前不支持真正的子图重复执行，仅透传第一个元素，"
            "如需批处理请在 Python 节点中显式实现循环逻辑。",
        )
        return {
            "item": items[0] if items else None,
            "index": 0,
            "results": items,
        }

    async def _exec_merge(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext
    ) -> dict[str, Any]:
        mode = config.get("mode", "list")
        values = [v for v in [inputs.get("a"), inputs.get("b"), inputs.get("c")] if v is not None]
        if mode == "object":
            obj: dict[str, Any] = {}
            for i, v in enumerate(values):
                if isinstance(v, dict):
                    obj.update(v)
                else:
                    obj[f"value_{i}"] = v
            return {"list": values, "object": obj}
        return {"list": values, "object": {}}

    # ── Custom ──

    async def _exec_custom(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext | None
    ) -> dict[str, Any]:
        code = config.get("code", "")
        input_val = inputs.get("input")
        context_data = ctx.globals if ctx else {}

        try:
            self._validate_code_ast(code)
            output = await self._run_code_in_subprocess(
                code=code,
                input_data=input_val,
                context_data=context_data,
                timeout=_CODE_EXEC_TIMEOUT,
                label="<custom_node>",
            )
            if output.get("error"):
                return {
                    "output": None,
                    "error": output.get("stderr", output.get("error")),
                }
            result = output.get("result")
            if isinstance(result, dict):
                return result
            return {"output": result}
        except WorkflowExecutionError as e:
            return {"output": None, "error": str(e)}
        except Exception as e:
            return {"output": None, "error": str(e)}

    # ── Fallback ──

    async def _exec_unknown(
        self, config: dict[str, Any], inputs: dict[str, Any], ctx: WorkflowContext
    ) -> dict[str, Any]:
        return {"output": inputs.get("input")}
