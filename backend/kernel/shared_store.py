"""Kernel 多 worker 共享态（Redis）——**可选热层，非业务权威**。

权威数据（Identity / Inbox / 进程档案 / 审批）在 **SQLite**（见 docs/internal/STORAGE.md）。
本模块只解决：多 uvicorn worker 时 mediate / charge_tokens / 能力集 只活在本进程内存，
A 上 create 的进程 B 上 mediate 会「未知进程」。

设计：
- **同步** redis-py 客户端（符合 kernel 零 await 红线）
- 进程元数据 HSET；**tokens_used 仅 HINCRBY 权威**，put 更新不写回计数
- charge_tokens Lua 原子扣减
- 提权 SETNX 占坑防并发双 pending
- 事件 LPUSH 热缓冲（多 worker 观测）
- 未配置 / 默认关闭 → ``create_shared_store_from_settings()`` 返回 **None**（单进程）

Key：
  tevarn:kernel:v1:proc:{id}
  tevarn:kernel:v1:procs
  tevarn:kernel:v1:esc:{id}
  tevarn:kernel:v1:esc:pending
  tevarn:kernel:v1:esc:claim:{process}:{fp}
  tevarn:kernel:v1:events
  tevarn:kernel:v1:daily:{YYYY-MM-DD}
  tevarn:kernel:v1:daily_runs:{YYYY-MM-DD}
  tevarn:kernel:v1:wf_busy:{identity_id}   # 编制分布式 busy（SETNX）
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "tevarn:kernel:v1"
_PROC_TTL = 86400 * 2
_ESC_TTL = 86400 * 7
# claim 与 pending 提权同寿：120s 过期会在人审窗口内释放，同 caps 可再开第二单
_CLAIM_TTL = _ESC_TTL
_EVENT_MAX = 1000
_EVENT_TTL = 86400
# 编制 identity busy：默认 ≥ 工单超时，防止长任务中途锁过期被双派
_WF_BUSY_TTL = 720

_CHARGE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return false
end
local amount = tonumber(ARGV[1])
local used = tonumber(redis.call('HGET', KEYS[1], 'tokens_used') or '0')
local budget_s = redis.call('HGET', KEYS[1], 'token_budget') or ''
if amount > 0 then
    if budget_s ~= '' and budget_s ~= false then
        local budget = tonumber(budget_s)
        if budget ~= nil and (used + amount) > budget then
            return {'exceeded', used, budget_s}
        end
    end
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


def caps_fingerprint(capabilities: list[str] | tuple[str, ...] | set[str]) -> str:
    raw = ",".join(sorted({str(c) for c in capabilities}))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


class KernelSharedStore:
    """Redis 共享进程/提权/事件。所有 public 方法同步、无 await。"""

    def __init__(self, client: Any, *, prefix: str = _PREFIX) -> None:
        self._r = client
        self._prefix = prefix

    # ── 进程 ────────────────────────────────────────────────

    def put_process(self, data: dict[str, Any], *, force_tokens_used: bool = False) -> None:
        """写入进程元数据。

        **默认不覆盖 tokens_used**（已存在 key 时省略该 field）——
        计数只由 charge_tokens / HINCRBY 维护，杜绝 put 回滚。
        新建进程或 force_tokens_used=True 时才写入 tokens_used。
        """
        pid = str(data.get("id") or "")
        if not pid:
            return
        key = _proc_key(pid)
        exists = bool(self._r.exists(key))
        payload: dict[str, str] = {
            "id": pid,
            "identity": str(data.get("identity") or ""),
            "session_id": data.get("session_id") or "",
            "parent_id": data.get("parent_id") or "",
            "capabilities": json.dumps(data.get("capabilities"), ensure_ascii=False),
            "token_budget": "" if data.get("token_budget") is None else str(data["token_budget"]),
            "state": str(data.get("state") or "created"),
            "created_at": str(float(data.get("created_at") or time.time())),
            "started_at": "" if data.get("started_at") is None else str(data["started_at"]),
            "ended_at": "" if data.get("ended_at") is None else str(data["ended_at"]),
            "exit_reason": data.get("exit_reason") or "",
            "meta": json.dumps(data.get("meta") or {}, ensure_ascii=False),
            "token_json": json.dumps(data.get("token") or None, ensure_ascii=False),
            "updated_at": str(time.time()),
        }
        if not exists or force_tokens_used:
            payload["tokens_used"] = str(int(data.get("tokens_used") or 0))
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
        pipe = self._r.pipeline(transaction=False)
        for pid in members:
            pipe.exists(_proc_key(pid))
        flags = pipe.execute()
        alive = [pid for pid, ok in zip(members, flags, strict=True) if ok]
        dead = [pid for pid, ok in zip(members, flags, strict=True) if not ok]
        if dead:
            self._r.srem(key, *dead)
        return alive

    def charge_tokens(
        self,
        process_id: str,
        amount: int,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[int | None, int | None]:
        """Lua 原子扣减。返回 (tokens_used, budget_remaining)。

        超预算拒绝写入时返回 used=None 且 remaining 语义由调用方识别——
        实际用 raising via special: 若 res[0]=='exceeded' 则抛给上层。
        idempotency_key：同一 key 5 分钟内只扣一次（RPC 超时重试防双写）。
        """
        if idempotency_key:
            idem_k = f"{self._prefix}:charge_idem:{process_id}:{idempotency_key}"
            try:
                cached = self._r.get(idem_k)
                if cached:
                    # format: used|remaining
                    parts = self._s(cached).split("|", 1)
                    used = int(parts[0]) if parts[0] not in ("", "None") else None
                    rem = (
                        int(parts[1])
                        if len(parts) > 1 and parts[1] not in ("", "None")
                        else None
                    )
                    return used, rem
            except Exception:
                pass
        key = _proc_key(process_id)
        res = self._r.eval(_CHARGE_LUA, 1, key, int(amount), _PROC_TTL)
        if res is None or res is False:
            return None, None
        # redis 可能返回 bytes
        head = self._s(res[0]) if isinstance(res, (list, tuple)) else self._s(res)
        if head == "exceeded":
            raise RuntimeError(
                f"budget exceeded for {process_id}: used={self._s(res[1])} budget={self._s(res[2])}"
            )
        used = int(res[0])
        budget_s = self._s(res[1])
        if budget_s == "" or budget_s is None:
            remaining: int | None = None
            if amount > 0:
                try:
                    self.record_daily_charge(amount)
                except Exception:
                    pass
            if idempotency_key:
                try:
                    self._r.setex(
                        f"{self._prefix}:charge_idem:{process_id}:{idempotency_key}",
                        300,
                        f"{used}|None",
                    )
                except Exception:
                    pass
            return used, remaining
        budget = int(budget_s)
        remaining = max(0, budget - used)
        if amount > 0:
            try:
                self.record_daily_charge(amount)
            except Exception:
                pass
        if idempotency_key:
            try:
                self._r.setex(
                    f"{self._prefix}:charge_idem:{process_id}:{idempotency_key}",
                    300,
                    f"{used}|{remaining}",
                )
            except Exception:
                pass
        return used, remaining

    def set_process_fields(self, process_id: str, **fields: Any) -> None:
        """更新元数据字段；默认**拒绝**写 tokens_used（请用 charge_tokens）。"""
        key = _proc_key(process_id)
        if not self._r.exists(key):
            return
        mapping: dict[str, str] = {"updated_at": str(time.time())}
        for k, v in fields.items():
            if k == "tokens_used":
                continue  # 计数权威只在 HINCRBY
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

    def publish_resume(self, process_id: str) -> None:
        """通知其他 worker：进程已恢复（best-effort）。"""
        try:
            self._r.publish(f"{self._prefix}:resume", process_id)
        except Exception:
            pass

    # ── 编制 identity busy（多 worker 防双派）────────────────

    def _wf_busy_key(self, identity_id: str) -> str:
        return f"{self._prefix}:wf_busy:{identity_id}"

    def try_acquire_identity_busy(
        self,
        identity_id: str,
        item_id: str,
        *,
        ttl: int = _WF_BUSY_TTL,
    ) -> bool:
        """SETNX 占坑。True=本 worker 占有；False=他 worker 已占该身份。"""
        iid = str(identity_id or "").strip()
        oid = str(item_id or "").strip()
        if not iid or not oid:
            return False
        try:
            ok = self._r.set(self._wf_busy_key(iid), oid, nx=True, ex=max(60, int(ttl)))
            return bool(ok)
        except Exception as e:
            logger.warning("try_acquire_identity_busy redis fail: %s", e)
            return True  # fail-open 到单机路径（仍有 DB claimed 兜底）

    def refresh_identity_busy(
        self,
        identity_id: str,
        item_id: str,
        *,
        ttl: int = _WF_BUSY_TTL,
    ) -> bool:
        """续期：仅当 value==item_id 时 EXPIRE。"""
        iid = str(identity_id or "").strip()
        oid = str(item_id or "").strip()
        if not iid or not oid:
            return False
        try:
            key = self._wf_busy_key(iid)
            cur = self._s(self._r.get(key))
            if cur != oid:
                return False
            self._r.expire(key, max(60, int(ttl)))
            return True
        except Exception as e:
            logger.debug("refresh_identity_busy: %s", e)
            return False

    def release_identity_busy(
        self,
        identity_id: str,
        item_id: str | None = None,
    ) -> None:
        """释放 busy。若传 item_id，仅当持有者匹配时删除（防误删他 worker）。"""
        iid = str(identity_id or "").strip()
        if not iid:
            return
        try:
            key = self._wf_busy_key(iid)
            if item_id:
                cur = self._s(self._r.get(key))
                if cur and cur != str(item_id):
                    return
            self._r.delete(key)
        except Exception as e:
            logger.debug("release_identity_busy: %s", e)

    def get_identity_busy_owner(self, identity_id: str) -> str | None:
        """返回占有该身份的 item_id，无则 None。"""
        try:
            return self._s(self._r.get(self._wf_busy_key(str(identity_id)))) or None
        except Exception:
            return None

    def list_busy_identity_ids(self) -> set[str]:
        """扫描当前所有 busy 身份（best-effort，供 claim 合并 busy 集合）。"""
        out: set[str] = set()
        try:
            pattern = f"{self._prefix}:wf_busy:*"
            cursor = 0
            prefix_len = len(f"{self._prefix}:wf_busy:")
            while True:
                cursor, keys = self._r.scan(cursor=cursor, match=pattern, count=100)
                for k in keys or []:
                    ks = self._s(k) if not isinstance(k, str) else k
                    if ks and len(ks) > prefix_len:
                        out.add(ks[prefix_len:])
                if cursor == 0:
                    break
        except Exception as e:
            logger.debug("list_busy_identity_ids: %s", e)
        return out

    # ── 日用量（auto_tighten_2x）────────────────────────────

    def record_daily_charge(self, amount: int) -> None:
        day = time.strftime("%Y-%m-%d")
        k = f"{self._prefix}:daily:{day}"
        self._r.incrby(k, max(0, int(amount)))
        self._r.expire(k, 86400 * 4)

    def record_daily_run(self) -> None:
        day = time.strftime("%Y-%m-%d")
        k = f"{self._prefix}:daily_runs:{day}"
        self._r.incr(k)
        self._r.expire(k, 86400 * 4)

    def daily_stats(self) -> tuple[int, int]:
        """返回 (今日累计 charge tokens, 今日进程创建次数)。"""
        day = time.strftime("%Y-%m-%d")
        total = int(self._s(self._r.get(f"{self._prefix}:daily:{day}")) or 0)
        runs = int(self._s(self._r.get(f"{self._prefix}:daily_runs:{day}")) or 0)
        return total, runs

    def daily_avg_per_run(self, *, exclude_tokens: int = 0) -> float:
        """今日累计 charge / 今日进程创建次数。

        exclude_tokens：排除当前进程已用量，避免「刚 charge 抬高日均」
        导致 auto_tighten 永远触发不了。
        """
        total, runs = self.daily_stats()
        base = max(0, total - max(0, int(exclude_tokens)))
        # 有其他 run 时用 runs-1 更贴近「他进程均值」；仅 1 run 时用 1
        denom = max(runs - 1, 1) if runs > 1 and exclude_tokens > 0 else max(runs, 1)
        return float(base) / float(denom)

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
            "target": data.get("target") or "",
            "identity_id": data.get("identity_id") or "",
        }
        key = _esc_key(eid)
        pipe = self._r.pipeline()
        pipe.hset(key, mapping=payload)
        pipe.expire(key, _ESC_TTL)
        if payload["status"] == "pending":
            pipe.sadd(f"{self._prefix}:esc:pending", eid)
            # claim 续期与 pending 同寿，防止人审窗口内 claim 先过期
            pid = payload["process_id"]
            if pid:
                try:
                    caps = json.loads(payload["capabilities"] or "[]")
                    fp = caps_fingerprint(caps)
                    claim_key = f"{self._prefix}:esc:claim:{pid}:{fp}"
                    cur = self._s(self._r.get(claim_key))
                    if cur == eid:
                        pipe.expire(claim_key, _ESC_TTL)
                except Exception:
                    pass
        else:
            pipe.srem(f"{self._prefix}:esc:pending", eid)
            # 释放 claim
            pid = payload["process_id"]
            try:
                caps = json.loads(payload["capabilities"])
                fp = caps_fingerprint(caps)
                pipe.delete(f"{self._prefix}:esc:claim:{pid}:{fp}")
            except Exception:
                pass
        pipe.execute()

    def try_claim_escalation(
        self,
        process_id: str,
        capabilities: list[str] | tuple[str, ...] | set[str],
        escalation_id: str,
        *,
        ttl: int = _CLAIM_TTL,
    ) -> str:
        """SETNX 占坑。返回我们拥有的 id，或已有 claim 的 id。

        调用方：若返回值 != escalation_id，应水合并复用已有申请。
        """
        fp = caps_fingerprint(capabilities)
        key = f"{self._prefix}:esc:claim:{process_id}:{fp}"
        ok = self._r.set(key, escalation_id, nx=True, ex=int(ttl))
        if ok:
            return escalation_id
        owner = self._s(self._r.get(key))
        return owner or escalation_id

    def get_escalation(self, escalation_id: str) -> dict[str, Any] | None:
        raw = self._r.hgetall(_esc_key(escalation_id))
        if not raw:
            return None
        return self._decode_escalation(raw)

    def list_pending_escalations(self) -> list[dict[str, Any]]:
        ids = self._r.smembers(f"{self._prefix}:esc:pending") or set()
        out: list[dict[str, Any]] = []
        dead: list[str] = []
        for eid in ids:
            sid = self._s(eid)
            d = self.get_escalation(sid)
            if d and d.get("status") == "pending":
                out.append(d)
            else:
                dead.append(sid)
        if dead:
            try:
                self._r.srem(f"{self._prefix}:esc:pending", *dead)
            except Exception:
                pass
        return out

    def find_covering_pending(
        self, process_id: str, capabilities: list[str] | tuple[str, ...] | set[str]
    ) -> dict[str, Any] | None:
        want = set(capabilities)
        for d in self.list_pending_escalations():
            if d.get("process_id") != process_id:
                continue
            have = set(d.get("capabilities") or [])
            if want <= have:
                return d
        return None

    # ── 事件热缓冲 ──────────────────────────────────────────

    def push_event(self, event: dict[str, Any]) -> None:
        key = f"{self._prefix}:events"
        try:
            line = json.dumps(event, ensure_ascii=False, default=str)
            pipe = self._r.pipeline()
            pipe.lpush(key, line)
            pipe.ltrim(key, 0, _EVENT_MAX - 1)
            pipe.expire(key, _EVENT_TTL)
            pipe.execute()
        except Exception as e:
            logger.debug("push_event failed: %s", e)

    def list_events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        key = f"{self._prefix}:events"
        try:
            raw = self._r.lrange(key, 0, max(0, int(limit) - 1)) or []
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for item in raw:
            try:
                out.append(json.loads(self._s(item)))
            except json.JSONDecodeError:
                continue
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
        ua = g("updated_at")
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
            "updated_at": float(ua) if ua else 0.0,
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
        target = g("target") or None
        identity_id = g("identity_id") or None
        return {
            "id": g("id"),
            "process_id": g("process_id"),
            "capabilities": caps,
            "reason": g("reason"),
            "status": g("status") or "pending",
            "created_at": float(g("created_at") or 0),
            "resolved_at": float(ra) if ra else None,
            "resolved_by": g("resolved_by") or None,
            "target": target or None,
            "identity_id": identity_id or None,
        }


def create_shared_store_from_settings() -> KernelSharedStore | None:
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
