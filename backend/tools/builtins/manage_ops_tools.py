"""Ops/package tools: audit, tasks, package, git, evolution."""
from __future__ import annotations

import logging
from typing import Any

from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource
from backend.tools.builtins.manage_common import _iso, _parse_uuid
from backend.tools.builtins.self_config import ToolResult

logger = logging.getLogger(__name__)


class QueryAuditLog(BaseTool):
    """审计日志查询工具（只读）"""

    def __init__(self):
        super().__init__(
            name="query_audit_log",
            description="查询安全审计日志，支持按 action/资源/用户过滤，按时间倒序返回",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "按操作类型过滤，如 login/update_config"},
                    "resource_type": {"type": "string", "description": "按资源类型过滤，如 sub_agent/skill"},
                    "resource_id": {"type": "string", "description": "按资源 ID 过滤"},
                    "user_id": {"type": "string", "description": "按用户 UUID 过滤"},
                    "limit": {"type": "integer", "description": "返回条数，默认 50，最大 500"},
                    "offset": {"type": "integer", "description": "分页偏移，默认 0"},
                },
                "required": [],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    def _to_dict(self, obj: Any) -> dict[str, Any]:
        return {
            "id": str(obj.id),
            "action": obj.action,
            "resource_type": obj.resource_type,
            "resource_id": obj.resource_id,
            "user_id": str(obj.user_id) if obj.user_id else None,
            "success": bool(obj.success),
            "details": obj.details or {},
            "ip_address": obj.ip_address,
            "created_at": _iso(getattr(obj, "created_at", None)),
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        from sqlalchemy import desc, select

        from backend.database import get_db_context
        from backend.models.audit_log import AuditLog

        try:
            limit = int(kwargs.get("limit", 50) or 50)
            offset = int(kwargs.get("offset", 0) or 0)
        except (TypeError, ValueError):
            return ToolResult(success=False, data={}, message="limit/offset 必须是整数")
        limit = max(1, min(limit, 500))
        offset = max(0, offset)

        try:
            stmt = select(AuditLog)
            action = (kwargs.get("action") or "").strip()
            resource_type = (kwargs.get("resource_type") or "").strip()
            resource_id = (kwargs.get("resource_id") or "").strip()
            user_id = (kwargs.get("user_id") or "").strip()
            if action:
                stmt = stmt.where(AuditLog.action == action)
            if resource_type:
                stmt = stmt.where(AuditLog.resource_type == resource_type)
            if resource_id:
                stmt = stmt.where(AuditLog.resource_id == resource_id)
            if user_id:
                try:
                    stmt = stmt.where(AuditLog.user_id == _parse_uuid(user_id, "user_id"))
                except ValueError as e:
                    return ToolResult(success=False, data={}, message=str(e))
            stmt = stmt.order_by(desc(AuditLog.created_at)).offset(offset).limit(limit)

            async with get_db_context() as db:
                result = await db.execute(stmt)
                logs = result.scalars().all()
            data = [self._to_dict(log) for log in logs]
            return ToolResult(success=True, data={"logs": data, "count": len(data)}, message=f"查询到 {len(data)} 条审计日志")
        except Exception as e:
            return ToolResult(success=False, data={}, message=f"❌ 查询失败: {e}")


# ── 任务查询（只读） ──

class ListTasks(BaseTool):
    """任务查询工具（只读）— 列出会话任务或最近任务"""

    def __init__(self):
        super().__init__(
            name="list_tasks",
            description="查询异步任务执行记录。可按 session_id 列出会话任务，或不带参数列出最近任务；active_only=true 只看进行中任务",
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "可选，会话 UUID；不提供则列出全局最近任务"},
                    "active_only": {"type": "boolean", "description": "仅看 pending/running 中的任务，默认 false"},
                    "limit": {"type": "integer", "description": "返回条数，默认 20，最大 100"},
                    "offset": {"type": "integer", "description": "分页偏移，默认 0"},
                },
                "required": [],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    def _to_dict(self, t: Any) -> dict[str, Any]:
        if hasattr(t, "model_dump"):
            return t.model_dump(mode="json")
        return {
            "id": str(t.id),
            "session_id": str(t.session_id),
            "name": t.name,
            "status": str(t.status),
            "progress": t.progress,
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        from backend.repositories.task_repo import AsyncTaskRepository

        repo = AsyncTaskRepository()

        try:
            limit = int(kwargs.get("limit", 20) or 20)
            offset = int(kwargs.get("offset", 0) or 0)
        except (TypeError, ValueError):
            return ToolResult(success=False, data={}, message="limit/offset 必须是整数")
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        session_id = (kwargs.get("session_id") or "").strip()
        active_only = bool(kwargs.get("active_only", False))

        try:
            if session_id:
                sid = _parse_uuid(session_id, "session_id")
                if active_only:
                    tasks = await repo.get_active_tasks_by_session(sid)
                else:
                    tasks = await repo.get_tasks_by_session(sid, limit=limit, offset=offset)
            else:
                from sqlalchemy import desc, select

                from backend.database import get_db_context
                from backend.models.task import Task, TaskStatus

                stmt = select(Task)
                if active_only:
                    stmt = stmt.where(Task.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]))
                stmt = stmt.order_by(desc(Task.created_at)).offset(offset).limit(limit)
                async with get_db_context() as db:
                    result = await db.execute(stmt)
                    tasks = result.scalars().all()

            data = [self._to_dict(t) for t in tasks]
            scope = f"会话 {session_id}" if session_id else "全部会话"
            return ToolResult(success=True, data={"tasks": data, "count": len(data)}, message=f"{scope} 共 {len(data)} 个任务")
        except ValueError as e:
            return ToolResult(success=False, data={}, message=str(e))
        except Exception as e:
            return ToolResult(success=False, data={}, message=f"❌ 查询失败: {e}")


# ── Agent 角色画像 ──

class ManagePackage(BaseTool):
    """包管理工具（对齐 packages 路由：包来自工作区目录发现 + skill/sub_agent/workflow 虚拟投影）"""

    def __init__(self):
        super().__init__(
            name="manage_package",
            description=(
                "管理包（Package：工作区包 + skill/sub_agent/workflow 的虚拟包投影）。"
                "action: list(列出)/get(详情)/attach(挂载到会话)/detach(从会话卸载)/set_attached(整体设置会话挂载)。"
                "包本身是只读发现的，写操作仅作用于会话挂载状态"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "attach", "detach", "set_attached"],
                        "description": "操作类型",
                    },
                    "name": {"type": "string", "description": "get/attach/detach 时: 包名，如 skill:xxx / sub_agent:uuid / 工作区包名"},
                    "session_id": {"type": "string", "description": "attach/detach/set_attached 必填；list 可选（用于标记已挂载）"},
                    "source": {"type": "string", "description": "list 时: 按来源过滤 workspace|skill|sub_agent|workflow"},
                    "packages": {"type": "array", "items": {"type": "string"}, "description": "set_attached 时: 挂载包名列表（整体替换）"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        from backend.packages.loader import (
            get_package_by_name,
            list_all_packages,
            package_to_detail,
            package_to_list_item,
            resolve_attached_snippets,
        )
        from backend.packages.session_packages import (
            attach_package,
            detach_package,
            get_session_attached_packages,
            set_session_attached_packages,
        )

        session_id = (kwargs.get("session_id") or "").strip()
        name = (kwargs.get("name") or "").strip()

        if action == "list":
            try:
                pkgs = await list_all_packages()
                attached: list[str] = []
                if session_id:
                    attached = await get_session_attached_packages(session_id)
                att_set = set(attached)
                source = (kwargs.get("source") or "").strip()
                items = []
                for p in pkgs:
                    if source and p.source != source:
                        continue
                    items.append(package_to_list_item(p, attached=p.name in att_set).model_dump())
                return ToolResult(
                    success=True,
                    data={"packages": items, "attached": attached, "count": len(items)},
                    message=f"共 {len(items)} 个包" + (f"，会话已挂载 {len(attached)} 个" if session_id else ""),
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "get":
            if not name:
                return ToolResult(success=False, data={}, message="get 需要提供 name")
            try:
                pkgs = await list_all_packages()
                p = get_package_by_name(pkgs, name)
                if not p:
                    return ToolResult(success=False, data={}, message=f"包 `{name}` 不存在")
                attached = False
                if session_id:
                    attached = name in await get_session_attached_packages(session_id)
                detail = package_to_detail(p, attached=attached).model_dump()
                return ToolResult(success=True, data=detail, message=f"包 `{name}`（{p.source}{'·虚拟' if p.virtual else ''}）")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取失败: {e}")

        elif action in ("attach", "detach"):
            if not session_id or not name:
                return ToolResult(success=False, data={}, message=f"{action} 需要提供 session_id 和 name")
            try:
                if action == "attach":
                    pkgs = await list_all_packages()
                    if not get_package_by_name(pkgs, name):
                        return ToolResult(success=False, data={}, message=f"包 `{name}` 不存在")
                    attached = await attach_package(session_id, name)
                    snippets = await resolve_attached_snippets(attached)
                    return ToolResult(
                        success=True,
                        data={"attached": attached, "snippets": snippets},
                        message=f"✅ 已挂载包 `{name}`",
                    )
                attached = await detach_package(session_id, name)
                return ToolResult(
                    success=True,
                    data={"attached": attached},
                    message=f"✅ 已卸载包 `{name}`",
                )
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 操作失败: {e}")

        elif action == "set_attached":
            packages = kwargs.get("packages")
            if not session_id:
                return ToolResult(success=False, data={}, message="set_attached 需要提供 session_id")
            if packages is None or not isinstance(packages, list):
                return ToolResult(success=False, data={}, message="set_attached 需要提供 packages 数组")
            try:
                pkgs = await list_all_packages()
                known = {p.name for p in pkgs}
                unknown = [str(n) for n in packages if str(n) not in known]
                if unknown:
                    return ToolResult(success=False, data={}, message=f"未知包: {unknown}")
                attached = await set_session_attached_packages(session_id, [str(n) for n in packages])
                snippets = await resolve_attached_snippets(attached)
                return ToolResult(
                    success=True,
                    data={"attached": attached, "snippets": snippets},
                    message=f"✅ 会话挂载已更新（{len(attached)} 个包）",
                )
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 设置失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── 自进化资产查询（只读） ──

class QueryEvolution(BaseTool):
    """自进化资产查询工具（只读，对齐 evolution 路由的读端点）"""

    def __init__(self):
        super().__init__(
            name="query_evolution",
            description=(
                "查询自进化（Evolution）系统状态与资产。action: status(运行状态)/stats(统计)/"
                "list(资产列表，可按 kind/status/source 过滤)/get(单个资产)/tasks(演化任务)/clusters(聚类)/version(引擎版本)。只读"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "stats", "list", "get", "tasks", "clusters", "version"],
                        "description": "操作类型",
                    },
                    "asset_id": {"type": "string", "description": "get 时: 资产 ID"},
                    "kind": {"type": "string", "description": "list 时: 按资产类型过滤，如 skill/tool/playbook"},
                    "status": {"type": "string", "description": "list 时: 按状态过滤，如 active/disabled/draft"},
                    "source": {"type": "string", "description": "list 时: 按来源过滤，如 seed/auto"},
                    "unused_only": {"type": "boolean", "description": "list 时: 仅看未被使用的资产"},
                    "limit": {"type": "integer", "description": "list 时: 返回条数，默认 200，最大 500"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        try:
            from backend.evolution import store
            from backend.evolution.manager import get_evolution_manager
        except Exception as e:
            return ToolResult(success=False, data={}, message=f"❌ 进化模块不可用: {e}")

        if action == "status":
            try:
                mgr = get_evolution_manager()
                data = mgr.status()
                return ToolResult(success=True, data=data, message="进化系统状态已获取")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取状态失败: {e}")

        elif action == "stats":
            try:
                get_evolution_manager().ensure_seeded()
                data = store.stats()
                return ToolResult(success=True, data=data, message="进化资产统计已获取")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取统计失败: {e}")

        elif action == "list":
            try:
                get_evolution_manager().ensure_seeded()
                try:
                    limit = int(kwargs.get("limit", 200) or 200)
                except (TypeError, ValueError):
                    return ToolResult(success=False, data={}, message="limit 必须是整数")
                limit = max(1, min(limit, 500))
                assets = store.list_assets(
                    kind=(kwargs.get("kind") or "").strip() or None,
                    status=(kwargs.get("status") or "").strip() or None,
                    source=(kwargs.get("source") or "").strip() or None,
                    unused_only=bool(kwargs.get("unused_only", False)),
                    limit=limit,
                )
                return ToolResult(
                    success=True,
                    data={"assets": assets, "count": len(assets)},
                    message=f"共 {len(assets)} 个进化资产",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "get":
            asset_id = (kwargs.get("asset_id") or "").strip()
            if not asset_id:
                return ToolResult(success=False, data={}, message="get 需要提供 asset_id")
            try:
                a = store.get_asset(asset_id)
                if not a:
                    return ToolResult(success=False, data={}, message="资产不存在")
                return ToolResult(success=True, data=a, message=f"资产 `{a.get('name', asset_id)}`")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取失败: {e}")

        elif action == "tasks":
            try:
                get_evolution_manager().ensure_seeded()
                tasks = store.list_tasks()
                return ToolResult(
                    success=True,
                    data={"tasks": tasks, "count": len(tasks)},
                    message=f"共 {len(tasks)} 个演化任务",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "clusters":
            try:
                get_evolution_manager().ensure_seeded()
                clusters = store.list_clusters(50)
                return ToolResult(
                    success=True,
                    data={"clusters": clusters, "count": len(clusters)},
                    message=f"共 {len(clusters)} 个聚类",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "version":
            try:
                from backend.evolution.config import (
                    ENGINE_VERSION,
                    get_evolution_config,
                )

                cfg = get_evolution_config()
                return ToolResult(
                    success=True,
                    data={
                        "engine_version": ENGINE_VERSION,
                        "phases": ["P1_tasks", "P2_skill_md", "P3_tool_draft", "P4_observe_curator"],
                        "enabled": cfg.enabled,
                    },
                    message=f"进化引擎版本 {ENGINE_VERSION}",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取版本失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── Git 协作（只读） ──

class ManageGit(BaseTool):
    """Git 协作工具（复用 git 路由的 _resolve_repo_path/_run_git，不直接 subprocess）"""

    def __init__(self):
        super().__init__(
            name="manage_git",
            description=(
                "查看 Git 仓库状态与变更。action: status(分支/ahead/behind/改动文件概览)、"
                "branches(分支列表)、diff(未暂存+已暂存 diff，可按 file 过滤)、log(最近提交)。"
                "全部为只读操作，不提供 commit/push/reset 等写操作；工作区不是 git 仓库时返回不可用提示"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "branches", "diff", "log"],
                        "description": "操作类型",
                    },
                    "file": {"type": "string", "description": "diff 时: 可选，仅看指定文件的 diff（仓库内相对路径）"},
                    "limit": {"type": "integer", "description": "log 时: 提交条数，默认 20，最大 100"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        # 复用 git 路由的仓库解析与命令执行（失败返回空串，不抛 500）
        from backend.api.routes.git import _resolve_repo_path, _run_git

        repo = _resolve_repo_path()
        if repo is None:
            return ToolResult(
                success=False,
                data={"is_repo": False, "reason": "no_git_repo"},
                message="当前工作区不是 git 仓库（也未配置 TEVARN_GIT_REPO），Git 功能不可用",
            )

        if action == "status":
            try:
                branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
            except FileNotFoundError:
                return ToolResult(success=False, data={"is_repo": False, "reason": "git_not_installed"}, message="系统未安装 git")
            if not branch:
                return ToolResult(
                    success=False,
                    data={"is_repo": False, "reason": "not_a_repo", "repo_path": str(repo)},
                    message=f"{repo} 不是有效的 git 仓库",
                )
            status_output = _run_git(["status", "--short"], repo)
            changed_files = []
            if status_output:
                for line in status_output.split("\n"):
                    if line.strip():
                        changed_files.append({"status": line[:2].strip(), "file": line[3:].strip()})
            ahead = behind = 0
            ahead_behind = _run_git(["rev-list", "--count", "--left-right", f"origin/{branch}...HEAD"], repo)
            if ahead_behind and "\t" in ahead_behind:
                parts = ahead_behind.split("\t")
                behind = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
                ahead = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            total_commits = _run_git(["rev-list", "--count", "HEAD"], repo)
            data = {
                "branch": branch,
                "ahead": ahead,
                "behind": behind,
                "total_commits": int(total_commits) if total_commits and total_commits.isdigit() else 0,
                "changed_files": changed_files,
                "has_changes": len(changed_files) > 0,
                "is_dirty": any(f.get("status", "") not in ("", "??") for f in changed_files),
                "is_repo": True,
                "repo_path": str(repo),
            }
            return ToolResult(
                success=True,
                data=data,
                message=f"分支 `{branch}`，{len(changed_files)} 个改动文件，共 {data['total_commits']} 次提交",
            )

        elif action == "branches":
            try:
                output = _run_git(["branch", "--list"], repo)
            except FileNotFoundError:
                return ToolResult(success=False, data={"is_repo": False, "reason": "git_not_installed"}, message="系统未安装 git")
            branches = []
            if output:
                for line in output.split("\n"):
                    line = line.strip()
                    if line:
                        branches.append({"name": line.lstrip("* ").strip(), "current": line.startswith("* ")})
            return ToolResult(success=True, data={"branches": branches, "count": len(branches)}, message=f"共 {len(branches)} 个分支")

        elif action == "diff":
            file_arg = (kwargs.get("file") or "").strip()
            args = ["diff"]
            staged_args = ["diff", "--cached"]
            if file_arg:
                # 与路由一致：仅允许仓库内相对路径
                safe = file_arg.replace("\\", "/").lstrip("/")
                if ".." in safe.split("/"):
                    return ToolResult(success=False, data={}, message="非法文件路径")
                args.extend(["--", safe])
                staged_args.extend(["--", safe])
            try:
                diff_output = _run_git(args, repo)
                staged_output = _run_git(staged_args, repo)
            except FileNotFoundError:
                return ToolResult(success=False, data={"is_repo": False, "reason": "git_not_installed"}, message="系统未安装 git")
            data = {
                "unstaged": diff_output,
                "staged": staged_output,
                "has_changes": bool(diff_output or staged_output),
                "is_repo": True,
            }
            scope = f"文件 `{file_arg}`" if file_arg else "工作区"
            return ToolResult(
                success=True,
                data=data,
                message=f"{scope} {'有' if data['has_changes'] else '无'}变更",
            )

        elif action == "log":
            try:
                limit = int(kwargs.get("limit", 20) or 20)
            except (TypeError, ValueError):
                return ToolResult(success=False, data={}, message="limit 必须是整数")
            limit = max(1, min(limit, 100))
            try:
                output = _run_git(["log", "--oneline", "-n", str(limit)], repo)
            except FileNotFoundError:
                return ToolResult(success=False, data={"is_repo": False, "reason": "git_not_installed"}, message="系统未安装 git")
            commits = []
            if output:
                for line in output.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    sha, _, subject = line.partition(" ")
                    commits.append({"sha": sha, "subject": subject})
            return ToolResult(success=True, data={"commits": commits, "count": len(commits)}, message=f"最近 {len(commits)} 次提交")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── 自进化写操作 ──

class ManageEvolution(BaseTool):
    """自进化管理工具（对齐 evolution 路由的写端点，调用相同的 manager/store/skill_sync 函数）"""

    def __init__(self):
        super().__init__(
            name="manage_evolution",
            description=(
                "管理自进化（Evolution）系统。action: "
                "config(启用/禁用进化引擎及开关)、"
                "enable_asset(启用资产并同步为技能)/disable_asset(禁用资产)、"
                "apply_draft(草稿过安全门后转正)/reject_draft(弃用草稿)、"
                "delete_asset(删除单个资产，预置 seed 不可删)、"
                "bulk_delete_unused(清理未使用的 auto 资产)、"
                "run_task(运行指定演化任务)、"
                "curator_run(运行聚类整理器，建议先 dry_run=true 预览)"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "config", "enable_asset", "disable_asset",
                            "apply_draft", "reject_draft", "delete_asset",
                            "bulk_delete_unused", "run_task", "curator_run",
                        ],
                        "description": "操作类型",
                    },
                    "asset_id": {"type": "string", "description": "enable_asset/disable_asset/apply_draft/reject_draft/delete_asset 时: 资产 ID"},
                    "enabled": {"type": "boolean", "description": "config 时: 是否启用进化引擎"},
                    "auto_apply_skills": {"type": "boolean", "description": "config 时: 自动应用演化技能"},
                    "auto_apply_tools": {"type": "boolean", "description": "config 时: 自动应用演化工具"},
                    "auto_observe": {"type": "boolean", "description": "config 时: 自动观察"},
                    "auto_create_tools": {"type": "boolean", "description": "config 时: 自动创建工具"},
                    "curator_enabled": {"type": "boolean", "description": "config 时: 启用整理器"},
                    "mode": {"type": "string", "description": "config 时: 运行模式"},
                    "task_name": {"type": "string", "description": "run_task 时: 演化任务名"},
                    "dry_run": {"type": "boolean", "description": "curator_run 时: 仅预览不落地，默认 true（安全）"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def _sync_skill(self, asset: dict[str, Any], *, enabled: bool) -> None:
        """与路由一致：资产状态变更后同步技能表（失败不阻断）"""
        try:
            from backend.evolution.skill_sync import upsert_skill_from_asset

            await upsert_skill_from_asset(
                name=asset["name"],
                summary=asset.get("summary") or asset["name"],
                content=asset.get("content") or "",
                asset_id=asset.get("id"),
                kind=asset.get("kind") or "skill",
                enabled=enabled,
            )
        except Exception as e:
            logger.warning("evolution skill sync failed: %s", e)

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        try:
            from backend.evolution import store
            from backend.evolution.manager import get_evolution_manager
        except Exception as e:
            return ToolResult(success=False, data={}, message=f"❌ 进化模块不可用: {e}")

        if action == "config":
            try:
                from backend.evolution.config import set_evolution_config

                cfg_kwargs: dict[str, Any] = {}
                for key in ("enabled", "auto_apply_skills", "auto_apply_tools",
                            "auto_observe", "auto_create_tools", "curator_enabled"):
                    if kwargs.get(key) is not None:
                        cfg_kwargs[key] = bool(kwargs[key])
                if kwargs.get("mode") is not None:
                    cfg_kwargs["mode"] = str(kwargs["mode"])
                if kwargs.get("from_cron") is not None:
                    cfg_kwargs["from_cron"] = bool(kwargs["from_cron"])
                if kwargs.get("from_tasks") is not None:
                    cfg_kwargs["from_tasks"] = bool(kwargs["from_tasks"])
                if not cfg_kwargs:
                    return ToolResult(success=False, data={}, message="config 至少需要提供一项配置（如 enabled）")
                set_evolution_config(**cfg_kwargs)
                mgr = get_evolution_manager()
                return ToolResult(success=True, data=mgr.status(), message=f"✅ 进化配置已更新: {sorted(cfg_kwargs.keys())}")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 配置失败: {e}")

        elif action in ("enable_asset", "disable_asset"):
            asset_id = (kwargs.get("asset_id") or "").strip()
            if not asset_id:
                return ToolResult(success=False, data={}, message=f"{action} 需要提供 asset_id")
            try:
                new_status = "active" if action == "enable_asset" else "disabled"
                a = store.update_asset_status(asset_id, new_status)
                if not a:
                    return ToolResult(success=False, data={}, message="资产不存在")
                await self._sync_skill(a, enabled=(action == "enable_asset"))
                return ToolResult(success=True, data=a, message=f"✅ 资产 `{a.get('name', asset_id)}` 已{'启用' if action == 'enable_asset' else '禁用'}")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 操作失败: {e}")

        elif action == "apply_draft":
            asset_id = (kwargs.get("asset_id") or "").strip()
            if not asset_id:
                return ToolResult(success=False, data={}, message="apply_draft 需要提供 asset_id")
            try:
                a = store.get_asset(asset_id)
                if not a:
                    return ToolResult(success=False, data={}, message="资产不存在")
                from backend.evolution.gates import run_gates

                gate = run_gates(
                    name=a["name"],
                    content=a.get("content") or "",
                    summary=a.get("summary") or "",
                    score=a.get("last_score"),
                    baseline_score=0.5,
                )
                if not gate["ok"]:
                    return ToolResult(success=False, data={"gates": gate}, message="❌ 未过安全门，草稿未转正")
                updated = store.update_asset_status(asset_id, "active")
                if updated:
                    await self._sync_skill(updated, enabled=True)
                return ToolResult(success=True, data={"asset": updated, "gate": gate}, message=f"✅ 草稿 `{a.get('name', asset_id)}` 已转正")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 转正失败: {e}")

        elif action == "reject_draft":
            asset_id = (kwargs.get("asset_id") or "").strip()
            if not asset_id:
                return ToolResult(success=False, data={}, message="reject_draft 需要提供 asset_id")
            try:
                a = store.get_asset(asset_id)
                if not a:
                    return ToolResult(success=False, data={}, message="资产不存在")
                updated = store.update_asset_status(asset_id, "rejected")
                try:
                    from backend.evolution.skill_sync import delete_skill_by_name

                    await delete_skill_by_name(a["name"], only_evolved=True)
                except Exception as e:
                    logger.warning("evolution reject skill cleanup failed: %s", e)
                return ToolResult(success=True, data=updated or {}, message=f"✅ 草稿 `{a.get('name', asset_id)}` 已弃用")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 弃用失败: {e}")

        elif action == "delete_asset":
            asset_id = (kwargs.get("asset_id") or "").strip()
            if not asset_id:
                return ToolResult(success=False, data={}, message="delete_asset 需要提供 asset_id")
            try:
                a = store.get_asset(asset_id)
                if not a:
                    return ToolResult(success=False, data={}, message="资产不存在")
                if a.get("source") == "seed":
                    return ToolResult(success=False, data={}, message="预置（seed）资产不可删除")
                ok = store.delete_asset(asset_id)
                if not ok:
                    return ToolResult(success=False, data={}, message="删除失败")
                try:
                    from backend.evolution.skill_sync import purge_asset

                    await purge_asset(a)
                except Exception as e:
                    logger.warning("evolution purge skill failed: %s", e)
                return ToolResult(success=True, data={"id": asset_id, "name": a.get("name")}, message=f"✅ 资产 `{a.get('name', asset_id)}` 已删除")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        elif action == "bulk_delete_unused":
            try:
                to_purge = [a for a in store.list_assets(source="auto", unused_only=True, limit=500) if a.get("source") != "seed"]
                store.bulk_delete_unused_auto()
                try:
                    from backend.evolution.skill_sync import purge_asset

                    for a in to_purge:
                        try:
                            await purge_asset(a)
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning("evolution bulk purge failed: %s", e)
                names = [a.get("name") for a in to_purge if a.get("name")]
                return ToolResult(
                    success=True,
                    data={"deleted": len(to_purge), "skill_names": names},
                    message=f"✅ 已清理 {len(to_purge)} 个未使用的 auto 资产",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 清理失败: {e}")

        elif action == "run_task":
            task_name = (kwargs.get("task_name") or "").strip()
            if not task_name:
                return ToolResult(success=False, data={}, message="run_task 需要提供 task_name")
            try:
                mgr = get_evolution_manager()
                res = await mgr.run_task(task_name, improve=True)
                return ToolResult(success=True, data=res or {}, message=f"✅ 演化任务 `{task_name}` 已执行")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 任务执行失败: {e}")

        elif action == "curator_run":
            dry_run = bool(kwargs.get("dry_run", True))
            try:
                mgr = get_evolution_manager()
                res = await mgr.run_curator(dry_run=dry_run)
                return ToolResult(
                    success=True,
                    data=res or {},
                    message=f"✅ 整理器已运行（{'预览模式，未落地' if dry_run else '已落地'}）",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 整理器运行失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")

