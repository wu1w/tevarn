"""Kernel 多 worker 共享态（Redis）。

解决：多 uvicorn worker 时 mediate / charge_tokens / 能力集 只活在本进程内存，
A 上 create 的进程 B 上 mediate 会「未知进程」。

设计：
- **同步** redis-py 客户端（符合 kernel 零 await 红线；不在 mediate 路径里 await）
- 进程 / 提权 以 Redis Hash 为权威；本进程内存作缓存
- charge_tokens 用 Lua 脚本原子扣减（EXISTS+HINCRBY+HGET+EXPIRE 单脚本，
  避免 HINCRBY 与 budget 读取两步之间的并发窗口）
- 进程 id 索引 Set 在读路径惰性 GC（hash TTL 过期后死 id 随 list 剔除，
  防长期运行 Set 无限膨胀——与 Redis 自身惰性过期同构）
- 未配置 redis_url 或 redis 包缺失 → 返回 None，行为与单 worker 一致

Key 约定：
  takton:kernel:v1:proc:{id}   Hash 进程档案
  takton:kernel:v1:procs       Set  进程 id 索引
  takton:kernel:v1:esc:{id}    Hash 提权
  takton:kernel:v1:esc:pending Set  pending 提权 id
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "takton:kernel:v1"
_PROC_TTL = 86400 * 2  # 进程档案 2 天过期（终态后仍可观测一阵）
_ESC_TTL = 86400 * 7

# charge_tokens 原子脚本：存在性检查、扣减、budget 读取、TTL 续期单脚本完成。
# 返回 false → 进程不存在；否则返回 {tokens_used, token_budget}（budget 空串 = 不限）。
_CHARGE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return false
end
local amount = tonumber(ARGV[1])
local used
if amount > 0 then
    used = redis.call('HINCRBY', KEYS[1], 'tokens_used', amount)
else
    used = tonumber(redis.call('HGET', KEYS[1], 'tokens_used') or '0')
end
local budget = redis.call('HGET', KEYS[1], 'token_budget') or ''
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return {used, budget}
"""


def _proc_key(pid: str) -> str:
    return f"{_PREFIX}:proc:{pid}"


def _esc_key(eid: str) -> str:
    return f"{_PREFIX}:esc:{eid}"


class KernelSharedStore:
    """Redis 共享进程/提权。所有 public 方法同步、无 await。"""

    def __init__(self, client: Any, *, prefix: str = _PREFIX) -> None:
        self._r = client
        self._prefix = prefix

    # ── 进程 ────────────────────────────────────────────────

    def put_process(self, data: dict[str, Any]) -> None:
        pid = str(data.get("id") or "")
        if not pid:
            return
        payload = {
            "id": pid,
            "identity": str(data.get("identity") or ""),
            "session_id": data.get("session_id") or "",
            "parent_id": data.get("parent_id") or "",
            "capabilities": json.dumps(data.get("capabilities"), ensure_ascii=False),
            "token_budget": "" if data.get("token_budget") is None else str(data["token_budget"]),
            "tokens_used": str(int(data.get("tokens_used") or 0)),
            "state": str(data.get("state") or "created"),
            "created_at": str(float(data.get("created_at") or time.time())),
            "started_at": "" if data.get("started_at") is None else str(data["started_at"]),
            "ended_at": "" if data.get("ended_at") is None else str(data["ended_at"]),
            "exit_reason": data.get("exit_reason") or "",
            "meta": json.dumps(data.get("meta") or {}, ensure_ascii=False),
            "token_json": json.dumps(data.get("token") or None, ensure_ascii=False),
            "updated_at": str(time.time()),
        }
        key = _proc_key(pid)
        pipe = self._r.pipeline()
        pipe.hset(key, mapping=payload)
        pipe.expire(key, _PROC_TTL)
        pipe.sadd(f"{self._prefix}:procs", pid)
        pipe.execute()

    def get_process(self, process_id: str) -> dict[str, Any] | None:
        raw = self._r.hgetall(_proc_key(process_id))
        if not raw:
            return None
        return self._decode_process(raw)

    def list_process_ids(self) -> list[str]:
        key = f"{self._prefix}:procs"
        members = [self._s(m) for m in (self._r.smembers(key) or set())]
        if not members:
            return []
        # 惰性 GC：hash 已 TTL 过期的死 id 随读路径从索引剔除。
        # 不清理的话 Set 成员比 hash 活得久，长期运行索引无限膨胀。
        pipe = self._r.pipeline(transaction=False)
        for pid in members:
            pipe.exists(_proc_key(pid))
        flags = pipe.execute()
        alive = [pid for pid, ok in zip(members, flags) if ok]
        dead = [pid for pid, ok in zip(members, flags) if not ok]
        if dead:
            self._r.srem(key, *dead)
        return alive

    def charge_tokens(self, process_id: str, amount: int) -> tuple[int | None, int | None]:
        """Lua 单脚本原子扣减。返回 (tokens_used, budget_remaining)。

        budget_remaining None = 不限；超限时仍返回 used，调用方抛 BudgetExceeded。
        单脚本消除「HINCRBY 与 budget 读取」两步之间的并发窗口。
        """
        key = _proc_key(process_id)
        res = self._r.eval(_CHARGE_LUA, 1, key, int(amount), _PROC_TTL)
        if res is None:  # Lua return false → 进程不存在
            return None, None
        used = int(res[0])
        budget_s = self._s(res[1])
        if budget_s == "" or budget_s is None:
            return used, None
        budget = int(budget_s)
        return used, max(0, budget - used)

    def set_process_fields(self, process_id: str, **fields: Any) -> None:
        key = _proc_key(process_id)
        if not self._r.exists(key):
            return
        mapping: dict[str, str] = {"updated_at": str(time.time())}
        for k, v in fields.items():
            if k == "capabilities":
                mapping[k] = json.dumps(v, ensure_ascii=False)
            elif k == "meta":
                mapping[k] = json.dumps(v or {}, ensure_ascii=False)
            elif k == "token":
                mapping["token_json"] = json.dumps(v, ensure_ascii=False)
            elif v is None:
                mapping[k] = ""
            else:
                mapping[k] = str(v)
        self._r.hset(key, mapping=mapping)
        self._r.expire(key, _PROC_TTL)

    # ── 提权 ────────────────────────────────────────────────

    def put_escalation(self, data: dict[str, Any]) -> None:
        eid = str(data.get("id") or "")
        if not eid:
            return
        payload = {
            "id": eid,
            "process_id": str(data.get("process_id") or ""),
            "capabilities": json.dumps(data.get("capabilities") or [], ensure_ascii=False),
            "reason": str(data.get("reason") or ""),
            "status": str(data.get("status") or "pending"),
            "created_at": str(float(data.get("created_at") or 0)),
            "resolved_at": "" if data.get("resolved_at") is None else str(data["resolved_at"]),
            "resolved_by": data.get("resolved_by") or "",
        }
        key = _esc_key(eid)
        pipe = self._r.pipeline()
        pipe.hset(key, mapping=payload)
        pipe.expire(key, _ESC_TTL)
        if payload["status"] == "pending":
            pipe.sadd(f"{self._prefix}:esc:pending", eid)
        else:
            pipe.srem(f"{self._prefix}:esc:pending", eid)
        pipe.execute()

    def get_escalation(self, escalation_id: str) -> dict[str, Any] | None:
        raw = self._r.hgetall(_esc_key(escalation_id))
        if not raw:
            return None
        return self._decode_escalation(raw)

    def list_pending_escalations(self) -> list[dict[str, Any]]:
        ids = self._r.smembers(f"{self._prefix}:esc:pending") or set()
        out: list[dict[str, Any]] = []
        for eid in ids:
            d = self.get_escalation(self._s(eid))
            if d and d.get("status") == "pending":
                out.append(d)
        return out

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:
            return False

    # ── 编解码 ──────────────────────────────────────────────

    @staticmethod
    def _s(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, bytes):
            return v.decode("utf-8", errors="replace")
        return str(v)

    def _decode_process(self, raw: dict[Any, Any]) -> dict[str, Any]:
        def g(k: str) -> str:
            # redis-py may return bytes keys
            for kk, vv in raw.items():
                if self._s(kk) == k:
                    return self._s(vv)
            return ""

        caps_raw = g("capabilities")
        try:
            caps = json.loads(caps_raw) if caps_raw and caps_raw != "null" else None
        except json.JSONDecodeError:
            caps = None
        tb = g("token_budget")
        token_budget = int(tb) if tb not in ("", "None") else None
        meta_raw = g("meta") or "{}"
        try:
            meta = json.loads(meta_raw)
        except json.JSONDecodeError:
            meta = {}
        token_raw = g("token_json")
        token = None
        if token_raw and token_raw not in ("", "null", "None"):
            try:
                token = json.loads(token_raw)
            except json.JSONDecodeError:
                token = None
        sa, ea = g("started_at"), g("ended_at")
        return {
            "id": g("id"),
            "identity": g("identity"),
            "session_id": g("session_id") or None,
            "parent_id": g("parent_id") or None,
            "capabilities": caps,
            "token_budget": token_budget,
            "tokens_used": int(g("tokens_used") or 0),
            "state": g("state") or "created",
            "created_at": float(g("created_at") or 0),
            "started_at": float(sa) if sa else None,
            "ended_at": float(ea) if ea else None,
            "exit_reason": g("exit_reason") or None,
            "meta": meta,
            "token": token,
        }

    def _decode_escalation(self, raw: dict[Any, Any]) -> dict[str, Any]:
        def g(k: str) -> str:
            for kk, vv in raw.items():
                if self._s(kk) == k:
                    return self._s(vv)
            return ""

        try:
            caps = json.loads(g("capabilities") or "[]")
        except json.JSONDecodeError:
            caps = []
        ra = g("resolved_at")
        return {
            "id": g("id"),
            "process_id": g("process_id"),
            "capabilities": caps,
            "reason": g("reason"),
            "status": g("status") or "pending",
            "created_at": float(g("created_at") or 0),
            "resolved_at": float(ra) if ra else None,
            "resolved_by": g("resolved_by") or None,
        }


def create_shared_store_from_settings() -> KernelSharedStore | None:
    """读配置；失败/未配置返回 None（单 worker 内存态）。"""
    try:
        from backend.core.config import settings

        if not bool(getattr(settings, "agent_kernel_redis_shared", False)):
            return None
        url = str(getattr(settings, "redis_url", "") or "").strip()
        if not url:
            logger.info("agent_kernel_redis_shared=true 但 redis_url 为空，跳过 Redis 共享")
            return None
        try:
            import redis  # type: ignore
        except ImportError:
            logger.warning("redis 包未安装，无法启用 kernel Redis 共享（pip install redis）")
            return None
        client = redis.Redis.from_url(
            url,
            decode_responses=False,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
            health_check_interval=30,
        )
        store = KernelSharedStore(client)
        if not store.ping():
            logger.warning("Redis ping 失败（%s），kernel 回退单进程内存", url)
            return None
        logger.info("kernel Redis 共享态已启用：%s", url.split("@")[-1])
        return store
    except Exception as e:
        logger.warning("kernel Redis 共享初始化失败：%s", e)
        return None
