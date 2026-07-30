"""回归测试：session.config 键级合并（并发写不互相覆盖）。

背景（Phase 1.2 / 审计 L2-M2、L2-M3 的实质根因）：
checkpoint(_agent_checkpoint)、goal(_goal)、llm 快照等多条路径此前各自
「读 config → dict 拷贝 → 整体写回」，在 await 间隙交错时后写者会抹掉
先写者刚落库的键——长任务丢 checkpoint / 丢 goal。

修复：AsyncSessionRepository.merge_config_keys（单事务 + 每会话进程锁），
checkpoint.py / goal_state.py / routes(sessions|settings) 全部改走键级合并。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.agent.checkpoint import (
    CHECKPOINT_KEY,
    clear_checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from backend.agent.goal_state import clear_goal, ensure_goal, save_goal_to_db
from backend.repositories.session_repo import AsyncSessionRepository
from backend.repositories.user_repo import AsyncUserRepository


async def _make_session() -> uuid.UUID:
    """建真实 user + session 行（checkpoint/goal 走各自的 repo 连接，需要真数据）。"""
    users = AsyncUserRepository()
    uname = f"cfg_race_{uuid.uuid4().hex[:8]}"
    user = await users.create(
        {
            "email": f"{uname}@example.com",
            "username": uname,
            "hashed_password": "x",
        }
    )
    sessions = AsyncSessionRepository()
    session = await sessions.create(
        {
            "user_id": user.id,
            "config": {"identity": "test", "sys_prompt": "", "agent_md": "", "skills": []},
        }
    )
    return session.id


@pytest.mark.asyncio
async def test_merge_config_keys_preserves_other_keys():
    """merge 只动指定键，不碰其他键。"""
    sid = await _make_session()
    repo = AsyncSessionRepository()

    await repo.merge_config_keys(sid, {"_goal": {"title": "g"}})
    await repo.merge_config_keys(sid, {CHECKPOINT_KEY: {"segment": 1}})

    cfg = await repo.get_config(sid)
    assert cfg["_goal"] == {"title": "g"}, "写 checkpoint 不应覆盖 _goal"
    assert cfg[CHECKPOINT_KEY] == {"segment": 1}
    assert cfg["identity"] == "test", "原有用户键必须保留"

    # remove 只删指定键
    await repo.merge_config_keys(sid, remove=[CHECKPOINT_KEY])
    cfg = await repo.get_config(sid)
    assert CHECKPOINT_KEY not in cfg
    assert cfg["_goal"] == {"title": "g"}


@pytest.mark.asyncio
async def test_concurrent_checkpoint_and_goal_no_clobber():
    """并发混写 checkpoint 与 goal，两者最终都必须在库里。

    旧实现（整体读改写）下本测试会随机丢键失败。
    """
    sid = await _make_session()
    key = str(sid)
    clear_goal(key)
    ensure_goal(key, title="race goal", description="")

    async def write_checkpoints() -> None:
        for i in range(10):
            await save_checkpoint(sid, segment=1, iteration=i, mode="chat")

    async def write_goals() -> None:
        for _ in range(10):
            await save_goal_to_db(sid)

    await asyncio.gather(
        write_checkpoints(), write_goals(), write_checkpoints(), write_goals()
    )

    repo = AsyncSessionRepository()
    cfg = await repo.get_config(sid)
    assert cfg.get("_goal", {}).get("title") == "race goal", "goal 被并发 checkpoint 覆盖丢失"
    cp = await load_checkpoint(sid)
    assert cp is not None and cp["iteration"] == 9, "checkpoint 被并发 goal 覆盖丢失"

    clear_goal(key)
    await clear_checkpoint(sid)


@pytest.mark.asyncio
async def test_checkpoint_roundtrip_still_works():
    """行为冻结：save/load/clear 语义与改造前一致。"""
    sid = await _make_session()

    await save_checkpoint(
        sid, segment=2, iteration=5, mode="inbox", note="n", run_id="r1", extra={"k": 1}
    )
    cp = await load_checkpoint(sid)
    assert cp == {
        "segment": 2,
        "iteration": 5,
        "mode": "inbox",
        "note": "n",
        "run_id": "r1",
        "extra": {"k": 1},
        "updated_at": cp["updated_at"],
    }

    await clear_checkpoint(sid)
    assert await load_checkpoint(sid) is None
