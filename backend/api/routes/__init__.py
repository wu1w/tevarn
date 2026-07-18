"""
API 路由统一注册入口
"""

from fastapi import APIRouter

from . import (
    agent_profiles,
    audit,
    auth,
    channels,
    chat,
    cluster,
    context,
    cron,
    cron_hook,
    desktop,
    devices,
    evolution,
    files,
    git,
    images,
    knowledge,
    mcp,
    mcp_store,
    messages,
    notifications,
    packages,
    sessions,
    settings,
    skills,
    skill_store,
    smoke_test,
    sub_agents,
    tasks,
    tools,
    traces,
    entities,
    upload,
    webhook,
    wiki,
    workflows,
    workflow_templates,
    workspace,
)


def register_routes(app, prefix: str = "") -> None:
    """
    注册所有 REST 路由到 FastAPI app。

    当 main.py 传入 prefix="/api" 时，注册带 /api 前缀的路由。
    当 prefix 为空时，注册无前缀路由（用于测试等场景）。
    不再双重注册，消除路由匹配歧义。
    """
    p = prefix  # 简写
    app.include_router(auth.router, prefix=p)
    app.include_router(channels.router, prefix=p)
    app.include_router(chat.router, prefix=p)
    app.include_router(sessions.router, prefix=p)
    app.include_router(messages.router, prefix=p)
    app.include_router(tasks.router, prefix=p)
    app.include_router(traces.router, prefix=p)
    app.include_router(entities.router, prefix=p)
    app.include_router(skills.router, prefix=p)
    app.include_router(skill_store.router, prefix=f"{p}/skills")
    app.include_router(evolution.router, prefix=p)
    app.include_router(tools.router, prefix=p)
    app.include_router(context.router, prefix=p)
    app.include_router(packages.router, prefix=p)
    app.include_router(devices.router, prefix=p)
    app.include_router(workflows.router, prefix=p)
    app.include_router(cron.router, prefix=p)
    app.include_router(cron_hook.router, prefix=p)
    app.include_router(knowledge.router, prefix=p)
    app.include_router(wiki.router, prefix=p)
    app.include_router(settings.router, prefix=p)
    app.include_router(agent_profiles.router, prefix=p)
    app.include_router(audit.router, prefix=p)
    app.include_router(notifications.router, prefix=p)
    app.include_router(files.router, prefix=p)
    app.include_router(git.router, prefix=p)
    app.include_router(images.router, prefix=p)
    app.include_router(upload.router, prefix=p)
    app.include_router(mcp.router, prefix=p)
    app.include_router(mcp_store.router, prefix=p)
    app.include_router(desktop.router, prefix=p)
    app.include_router(cluster.router, prefix=p)
    app.include_router(workspace.router, prefix=p)
    app.include_router(webhook.router, prefix=p)
    app.include_router(workflow_templates.router, prefix=p)
    app.include_router(smoke_test.router, prefix=p)
    app.include_router(sub_agents.router, prefix=p)

    # 健康检查路由：单独注册，不使用 router 自带的 prefix（避免 /api/health/health 双重前缀）
    from .health import _health_router
    app.include_router(_health_router, prefix=p)


# 向后兼容的旧入口：允许测试直接调用 register_routes(app) 而不传 prefix
_register_routes = register_routes
