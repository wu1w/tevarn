"""Package 发现与加载。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.packages.manifest import PackageDetail, PackageListItem, PackageManifest

logger = logging.getLogger(__name__)

# 包目录候选（按优先级）
PACKAGE_DIR_CANDIDATES = (
    "workspace/packages",
    "packages",
    "backend/packages/examples",
)


def _project_root() -> Path:
    # backend/packages/loader.py -> backend/packages -> backend -> root
    return Path(__file__).resolve().parents[2]


def package_search_roots() -> list[Path]:
    root = _project_root()
    roots: list[Path] = []
    for rel in PACKAGE_DIR_CANDIDATES:
        p = root / rel
        if p.is_dir():
            roots.append(p)
    # 用户数据目录
    try:
        import os

        appdata = os.environ.get("APPDATA") or os.environ.get("HOME") or ""
        if appdata:
            user_pkg = Path(appdata) / "takton" / "packages"
            if user_pkg.is_dir():
                roots.append(user_pkg)
            user_ws = Path(appdata) / "takton" / "data" / "workspace" / "packages"
            if user_ws.is_dir():
                roots.append(user_ws)
    except Exception:
        pass
    return roots


def _read_manifest_file(path: Path) -> dict[str, Any] | None:
    try:
        if path.name in {"package.json", "takton.package.json"}:
            return json.loads(path.read_text(encoding="utf-8"))
        if path.name.lower() in {"package.yaml", "package.yml", "takton.package.yaml"}:
            try:
                import yaml  # type: ignore

                return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                return None
    except Exception as e:
        logger.warning("read package manifest %s failed: %s", path, e)
    return None


def _manifest_from_dict(data: dict[str, Any], *, name_fallback: str, path: str, source: str, virtual: bool = False) -> PackageManifest:
    payload = dict(data or {})
    payload.setdefault("name", name_fallback)
    payload["path"] = path
    payload["source"] = source
    payload["virtual"] = virtual
    # 兼容字段别名
    if "system" in payload and not payload.get("system_snippet"):
        payload["system_snippet"] = str(payload.get("system") or "")
    if "skills" in payload and not payload.get("skill_names"):
        sk = payload.get("skills")
        if isinstance(sk, list):
            payload["skill_names"] = [str(x) for x in sk if isinstance(x, str)]
    return PackageManifest.model_validate(payload)


def load_workspace_packages() -> list[PackageManifest]:
    found: list[PackageManifest] = []
    seen: set[str] = set()
    for root in package_search_roots():
        try:
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                man_path = None
                for candidate in (
                    child / "takton.package.json",
                    child / "package.json",
                    child / "PACKAGE.yaml",
                    child / "package.yaml",
                ):
                    if candidate.is_file():
                        man_path = candidate
                        break
                if not man_path:
                    # 允许仅有 SYSTEM.md 的轻量包
                    system_md = child / "SYSTEM.md"
                    if system_md.is_file():
                        m = PackageManifest(
                            name=child.name,
                            description=f"workspace package {child.name}",
                            system_snippet=system_md.read_text(encoding="utf-8", errors="replace")[:4000],
                            source="workspace",
                            path=str(child),
                            virtual=False,
                        )
                        if m.name not in seen:
                            seen.add(m.name)
                            found.append(m)
                    continue
                raw = _read_manifest_file(man_path)
                if not raw:
                    continue
                m = _manifest_from_dict(
                    raw,
                    name_fallback=child.name,
                    path=str(child),
                    source="workspace",
                    virtual=False,
                )
                if m.name in seen:
                    continue
                # 若 system_snippet 空且有 SYSTEM.md
                if not m.system_snippet:
                    sm = child / "SYSTEM.md"
                    if sm.is_file():
                        m.system_snippet = sm.read_text(encoding="utf-8", errors="replace")[:4000]
                seen.add(m.name)
                found.append(m)
        except Exception as e:
            logger.warning("scan packages root %s failed: %s", root, e)
    return found


async def load_virtual_packages_from_existing() -> list[PackageManifest]:
    """把现有 skill / sub_agent / workflow 投影为 virtual package，便于统一挂载。"""
    out: list[PackageManifest] = []
    # skills
    try:
        from backend.skills import SkillRegistry
        from backend.skills import builtins as _b  # noqa: F401

        _ = _b
        for skill in SkillRegistry.get_all_skills():
            name = getattr(skill, "name", None) or getattr(skill, "skill_name", None)
            if not name:
                continue
            desc = getattr(skill, "description", "") or ""
            out.append(
                PackageManifest(
                    name=f"skill:{name}",
                    type="skill",
                    description=str(desc)[:256],
                    icon="🧩",
                    system_snippet=f"# Skill package: {name}\n{desc}".strip()[:1500],
                    skill_names=[str(name)],
                    tools=[],
                    source="skill",
                    virtual=True,
                    tags=["skill"],
                )
            )
    except Exception as e:
        logger.debug("virtual skills packages skipped: %s", e)

    # sub agents
    try:
        from backend.repositories.sub_agent_repo import AsyncSubAgentRepository

        repo = AsyncSubAgentRepository()
        rows = []
        if hasattr(repo, "list_enabled"):
            rows = await repo.list_enabled()
        elif hasattr(repo, "list_all"):
            rows = await repo.list_all()
        for r in rows or []:
            if hasattr(r, "enabled") and not getattr(r, "enabled", True):
                continue
            sid = str(getattr(r, "id", ""))
            sname = getattr(r, "name", "") or sid
            icon = getattr(r, "icon", "🤖") or "🤖"
            prompt = (getattr(r, "system_prompt", "") or "").strip()
            desc = getattr(r, "description", "") or ""
            snippet = (
                f"# Sub-agent: {icon} {sname}\n"
                f"{desc}\n\n"
                f"{prompt[:1200]}"
            ).strip()
            out.append(
                PackageManifest(
                    name=f"sub_agent:{sid}",
                    type="sub_agent",
                    description=desc[:256],
                    icon=icon,
                    system_snippet=snippet[:2000],
                    sub_agent_ids=[sid],
                    tools=list(getattr(r, "enabled_toolsets", None) or []),
                    source="sub_agent",
                    virtual=True,
                    tags=["sub_agent"],
                )
            )
    except Exception as e:
        logger.debug("virtual sub_agent packages skipped: %s", e)

    # workflows
    try:
        from backend.repositories.workflow_repo import AsyncWorkflowRepository

        wrepo = AsyncWorkflowRepository()
        wfs = []
        if hasattr(wrepo, "list_all"):
            wfs = await wrepo.list_all()
        elif hasattr(wrepo, "list"):
            try:
                wfs = await wrepo.list()
            except TypeError:
                wfs = []
        for w in wfs or []:
            wid = str(getattr(w, "id", ""))
            wname = getattr(w, "name", "") or wid
            wdesc = getattr(w, "description", "") or ""
            dag = getattr(w, "dag", None) or {}
            ncount = len((dag or {}).get("nodes") or []) if isinstance(dag, dict) else 0
            out.append(
                PackageManifest(
                    name=f"workflow:{wid}",
                    type="workflow",
                    description=(wdesc or f"workflow with {ncount} nodes")[:256],
                    icon="⚡",
                    system_snippet=(
                        f"# Workflow package: {wname}\n"
                        f"{wdesc}\n"
                        f"Nodes: {ncount}. Prefer generate_workflow/save_workflow tools when editing."
                    )[:1500],
                    workflow_ids=[wid],
                    source="workflow",
                    virtual=True,
                    tags=["workflow"],
                )
            )
    except Exception as e:
        logger.debug("virtual workflow packages skipped: %s", e)

    return out


async def list_all_packages() -> list[PackageManifest]:
    pkgs = load_workspace_packages()
    names = {p.name for p in pkgs}
    for vp in await load_virtual_packages_from_existing():
        if vp.name not in names:
            pkgs.append(vp)
            names.add(vp.name)
    return pkgs


def get_package_by_name(packages: list[PackageManifest], name: str) -> PackageManifest | None:
    for p in packages:
        if p.name == name:
            return p
    return None


def package_to_list_item(p: PackageManifest, attached: bool = False) -> PackageListItem:
    preview = (p.system_snippet or "").strip().replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:120] + "…"
    return PackageListItem(
        name=p.name,
        version=p.version,
        type=p.type,
        description=p.description,
        icon=p.icon,
        source=p.source,
        virtual=p.virtual,
        path=p.path,
        system_snippet_preview=preview,
        tools=list(p.tools or []),
        tags=list(p.tags or []),
        attached=attached,
    )


def package_to_detail(p: PackageManifest, attached: bool = False) -> PackageDetail:
    base = package_to_list_item(p, attached=attached)
    return PackageDetail(
        **base.model_dump(),
        system_snippet=p.system_snippet or "",
        skill_names=list(p.skill_names or []),
        sub_agent_ids=list(p.sub_agent_ids or []),
        workflow_ids=list(p.workflow_ids or []),
        manifest=p.model_dump(),
    )


async def resolve_attached_snippets(attached_names: list[str]) -> list[dict[str, str]]:
    """返回会话已挂载包的 system snippets。"""
    if not attached_names:
        return []
    pkgs = await list_all_packages()
    by_name = {p.name: p for p in pkgs}
    out: list[dict[str, str]] = []
    for name in attached_names:
        p = by_name.get(name)
        if not p:
            continue
        snip = (p.system_snippet or "").strip()
        if not snip:
            continue
        out.append({"name": p.name, "icon": p.icon or "📦", "content": snip})
    return out
