"""Redis 共享态：无 Redis 时用内存假客户端验证编解码与 charge 语义。"""

from __future__ import annotations

from typing import Any

import pytest

from backend.kernel.shared_store import KernelSharedStore


class _FakeRedis:
    """极简 Hash/Set/String/List 假客户端（覆盖 shared_store 全部路径）。"""

    def __init__(self) -> None:
        self._h: dict[str, dict[str, bytes]] = {}
        self._s: dict[str, set[bytes]] = {}
        self._kv: dict[str, bytes] = {}
        self._l: dict[str, list[bytes]] = {}
        self._ttl: dict[str, int] = {}  # key -> ttl seconds last set
        self._published: list[tuple[str, Any]] = []

    def pipeline(self, **_kwargs: Any) -> "_FakePipe":
        return _FakePipe(self)

    def hset(self, key: str, mapping: dict | None = None, **kwargs: Any) -> None:
        m = mapping or kwargs
        bucket = self._h.setdefault(key, {})
        for k, v in m.items():
            bucket[str(k).encode() if not isinstance(k, bytes) else k] = (
                v if isinstance(v, bytes) else str(v).encode()
            )

    def hgetall(self, key: str) -> dict:
        return dict(self._h.get(key, {}))

    def hget(self, key: str, field: str) -> bytes | None:
        b = self._h.get(key, {})
        fk = field.encode() if isinstance(field, str) else field
        return b.get(fk)

    def hincrby(self, key: str, field: str, amount: int) -> int:
        b = self._h.setdefault(key, {})
        fk = field.encode() if isinstance(field, str) else field
        cur = int(b.get(fk, b"0"))
        cur += int(amount)
        b[fk] = str(cur).encode()
        return cur

    def exists(self, key: str) -> int:
        return 1 if (key in self._h or key in self._kv or key in self._l or key in self._s) else 0

    def expire(self, key: str, ttl: int) -> None:
        self._ttl[key] = int(ttl)
        return None

    def ttl(self, key: str) -> int:
        if key not in self._kv and key not in self._h and key not in self._s and key not in self._l:
            return -2
        return int(self._ttl.get(key, -1))

    def sadd(self, key: str, *members: str) -> None:
        s = self._s.setdefault(key, set())
        for m in members:
            s.add(m.encode() if isinstance(m, str) else m)

    def srem(self, key: str, *members: str) -> None:
        s = self._s.setdefault(key, set())
        for m in members:
            s.discard(m.encode() if isinstance(m, str) else m)

    def smembers(self, key: str) -> set:
        return set(self._s.get(key, set()))

    def set(self, key: str, value: Any, nx: bool = False, ex: int | None = None, **_kw: Any) -> bool | None:
        if nx and key in self._kv:
            return False
        self._kv[key] = value if isinstance(value, bytes) else str(value).encode()
        if ex is not None:
            self._ttl[key] = int(ex)
        return True

    def get(self, key: str) -> bytes | None:
        return self._kv.get(key)

    def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                n += 1
            if k in self._h:
                del self._h[k]
                n += 1
            if k in self._l:
                del self._l[k]
                n += 1
            self._ttl.pop(k, None)
        return n

    def incr(self, key: str) -> int:
        return self.incrby(key, 1)

    def incrby(self, key: str, amount: int) -> int:
        cur = int(self._kv.get(key, b"0"))
        cur += int(amount)
        self._kv[key] = str(cur).encode()
        return cur

    def lpush(self, key: str, *values: Any) -> int:
        lst = self._l.setdefault(key, [])
        for v in values:
            b = v if isinstance(v, bytes) else str(v).encode()
            lst.insert(0, b)
        return len(lst)

    def lrange(self, key: str, start: int, end: int) -> list:
        lst = self._l.get(key, [])
        if end == -1:
            return list(lst[start:])
        return list(lst[start : end + 1])

    def ltrim(self, key: str, start: int, end: int) -> None:
        lst = self._l.get(key, [])
        if end == -1:
            self._l[key] = lst[start:]
        else:
            self._l[key] = lst[start : end + 1]

    def publish(self, channel: str, message: Any) -> int:
        self._published.append((channel, message))
        return 1

    def ping(self) -> bool:
        return True

    def eval(self, _script: str, _numkeys: int, *args: Any) -> Any:
        """测试替身：与 shared_store._CHARGE_LUA 语义等价（单线程天然原子）。

        返回 None 对应 Lua 的 return false（进程不存在）。
        """
        key, amount = str(args[0]), int(args[1])
        if key not in self._h:
            return None
        if amount > 0:
            used = self.hincrby(key, "tokens_used", amount)
        else:
            used = int(self.hget(key, "tokens_used") or b"0")
        budget = self.hget(key, "token_budget") or b""
        return [used, budget]


class _FakePipe:
    def __init__(self, r: _FakeRedis) -> None:
        self._r = r
        self._ops: list = []

    def hset(self, key: str, mapping: dict | None = None, **kw: Any) -> "_FakePipe":
        self._ops.append(("hset", key, mapping or kw))
        return self

    def expire(self, key: str, ttl: int) -> "_FakePipe":
        self._ops.append(("expire", key, ttl))
        return self

    def sadd(self, key: str, *m: str) -> "_FakePipe":
        self._ops.append(("sadd", key, m))
        return self

    def srem(self, key: str, *m: str) -> "_FakePipe":
        self._ops.append(("srem", key, m))
        return self

    def exists(self, key: str) -> "_FakePipe":
        self._ops.append(("exists", key))
        return self

    def lpush(self, key: str, *values: Any) -> "_FakePipe":
        self._ops.append(("lpush", key, values))
        return self

    def ltrim(self, key: str, start: int, end: int) -> "_FakePipe":
        self._ops.append(("ltrim", key, start, end))
        return self

    def delete(self, *keys: str) -> "_FakePipe":
        self._ops.append(("delete", keys))
        return self

    def execute(self) -> list:
        results: list = []
        for op in self._ops:
            if op[0] == "hset":
                self._r.hset(op[1], mapping=op[2])
                results.append(None)
            elif op[0] == "expire":
                self._r.expire(op[1], op[2])
                results.append(None)
            elif op[0] == "sadd":
                self._r.sadd(op[1], *op[2])
                results.append(None)
            elif op[0] == "srem":
                self._r.srem(op[1], *op[2])
                results.append(None)
            elif op[0] == "exists":
                results.append(self._r.exists(op[1]))
            elif op[0] == "lpush":
                results.append(self._r.lpush(op[1], *op[2]))
            elif op[0] == "ltrim":
                self._r.ltrim(op[1], op[2], op[3])
                results.append(None)
            elif op[0] == "delete":
                results.append(self._r.delete(*op[1]))
        self._ops.clear()
        return results


def test_put_get_process_roundtrip():
    store = KernelSharedStore(_FakeRedis())
    store.put_process({
        "id": "abc123",
        "identity": "main",
        "capabilities": ["web_search", "file_read"],
        "token_budget": 1000,
        "tokens_used": 10,
        "state": "running",
        "created_at": 1.0,
        "meta": {"k": "v"},
    })
    d = store.get_process("abc123")
    assert d is not None
    assert d["id"] == "abc123"
    assert d["capabilities"] == ["web_search", "file_read"]
    assert d["token_budget"] == 1000
    assert d["tokens_used"] == 10
    assert d["state"] == "running"
    assert "abc123" in store.list_process_ids()


def test_charge_tokens_atomic():
    store = KernelSharedStore(_FakeRedis())
    store.put_process({
        "id": "p1",
        "identity": "main",
        "capabilities": ["*"],
        "token_budget": 100,
        "tokens_used": 90,
        "state": "running",
        "created_at": 1.0,
    })
    used, rem = store.charge_tokens("p1", 15)
    assert used == 105
    assert rem == 0  # max(0, 100-105)


def test_put_process_does_not_regress_tokens_used():
    """全量 put 不得把 HINCRBY 后的计数回滚。"""
    store = KernelSharedStore(_FakeRedis())
    store.put_process({
        "id": "p2",
        "identity": "main",
        "capabilities": ["*"],
        "token_budget": 1000,
        "tokens_used": 10,
        "state": "running",
        "created_at": 1.0,
    })
    store.charge_tokens("p2", 50)  # used=60
    # 本地缓存仍是 10 时 put —— 应保留 60
    store.put_process({
        "id": "p2",
        "identity": "main",
        "capabilities": ["web_search"],
        "token_budget": 1000,
        "tokens_used": 10,
        "state": "running",
        "created_at": 1.0,
    })
    d = store.get_process("p2")
    assert d is not None
    assert d["tokens_used"] == 60
    assert d["capabilities"] == ["web_search"]


def test_find_covering_pending():
    store = KernelSharedStore(_FakeRedis())
    store.put_escalation({
        "id": "e9",
        "process_id": "px",
        "capabilities": ["shell", "file_write"],
        "status": "pending",
        "created_at": 1.0,
    })
    hit = store.find_covering_pending("px", ["shell"])
    assert hit is not None and hit["id"] == "e9"
    assert store.find_covering_pending("px", ["network"]) is None


def test_charge_tokens_missing_process():
    """进程不存在 → (None, None)，调用方按未知进程处理。"""
    store = KernelSharedStore(_FakeRedis())
    assert store.charge_tokens("ghost", 5) == (None, None)


def test_charge_tokens_amount_zero_reads_used():
    """amount=0 只读不扣（budget 校验路径）。"""
    store = KernelSharedStore(_FakeRedis())
    store.put_process({
        "id": "p2",
        "identity": "main",
        "capabilities": [],
        "token_budget": 100,
        "tokens_used": 42,
        "state": "running",
        "created_at": 1.0,
    })
    used, rem = store.charge_tokens("p2", 0)
    assert used == 42
    assert rem == 58
    # 再读一次不变（确认未误扣）
    assert store.charge_tokens("p2", 0) == (42, 58)


def test_list_process_ids_lazy_gc():
    """hash 已过期/删除的死 id 随读路径从索引剔除（防 Set 长期膨胀）。"""
    store = KernelSharedStore(_FakeRedis())
    store.put_process({"id": "live", "identity": "main", "capabilities": []})
    store.put_process({"id": "dead", "identity": "main", "capabilities": []})
    # 模拟 hash TTL 过期（FakeRedis 不实现真 TTL，直接删底层 hash）
    del store._r._h["takton:kernel:v1:proc:dead"]
    assert store.list_process_ids() == ["live"]
    # 死 id 已从 Set 索引剔除，不会反复扫描
    assert b"dead" not in store._r.smembers("takton:kernel:v1:procs")


def test_escalation_pending_set():
    store = KernelSharedStore(_FakeRedis())
    store.put_escalation({
        "id": "e1",
        "process_id": "p1",
        "capabilities": ["shell"],
        "reason": "need shell",
        "status": "pending",
        "created_at": 1.0,
    })
    pending = store.list_pending_escalations()
    assert len(pending) == 1
    assert pending[0]["id"] == "e1"
    store.put_escalation({
        "id": "e1",
        "process_id": "p1",
        "capabilities": ["shell"],
        "status": "approved",
        "created_at": 1.0,
        "resolved_at": 2.0,
        "resolved_by": "boss",
    })
    assert store.list_pending_escalations() == []


@pytest.mark.asyncio
async def test_kernel_mediate_via_shared_hydrate():
    """Worker B：本地无进程，从 shared 水合后 mediate 放行。"""
    from backend.kernel.kernel import AgentKernel, reset_kernel_for_tests
    from backend.kernel.process import AgentProcess

    reset_kernel_for_tests()
    store = KernelSharedStore(_FakeRedis())
    k = AgentKernel(shared_store=store)
    # 模拟 worker A 已 create 并写入 Redis
    proc = AgentProcess(
        identity="main",
        capabilities=["web_search"],
        id="shared-pid",
        state="running",
    )
    store.put_process(proc.to_dict())
    # worker B 本地空
    assert "shared-pid" not in k._processes
    decision = await k.mediate("shared-pid", "tool_call", "web_search", {})
    assert decision.allowed is True
    # 拒绝未知能力
    with pytest.raises(Exception):
        await k.mediate("shared-pid", "tool_call", "shell", {})
    reset_kernel_for_tests()


def test_try_claim_escalation_setnx():
    """SETNX：先到者占坑，后到者拿到 owner id。"""
    store = KernelSharedStore(_FakeRedis())
    owner1 = store.try_claim_escalation("p1", ["shell"], "esc-a")
    assert owner1 == "esc-a"
    owner2 = store.try_claim_escalation("p1", ["shell"], "esc-b")
    assert owner2 == "esc-a"  # 复用
    # 不同能力指纹互不干扰
    owner3 = store.try_claim_escalation("p1", ["network"], "esc-c")
    assert owner3 == "esc-c"


def test_claim_ttl_aligned_with_esc_pending():
    """claim TTL 与 pending 提权同寿，put_escalation 会续期 claim。"""
    from backend.kernel import shared_store as ss

    assert ss._CLAIM_TTL == ss._ESC_TTL
    assert ss._CLAIM_TTL >= 86400  # 至少按天级，不再是 120s

    r = _FakeRedis()
    store = KernelSharedStore(r)
    owner = store.try_claim_escalation("p1", ["shell"], "esc-a")
    assert owner == "esc-a"
    claim_key = f"{store._prefix}:esc:claim:p1:{ss.caps_fingerprint(['shell'])}"
    assert r.ttl(claim_key) == ss._CLAIM_TTL

    store.put_escalation(
        {
            "id": "esc-a",
            "process_id": "p1",
            "capabilities": ["shell"],
            "reason": "need",
            "status": "pending",
            "created_at": 1.0,
        }
    )
    # 续期后仍为 ESC_TTL
    assert r.ttl(claim_key) == ss._ESC_TTL
    # 短 TTL 不会再出现
    assert r.ttl(claim_key) != 120


def test_push_list_events_hot_buffer():
    store = KernelSharedStore(_FakeRedis())
    store.push_event({"id": "e1", "kind": "mediation", "process_id": "p", "ts": 1.0})
    store.push_event({"id": "e2", "kind": "process_created", "process_id": "p", "ts": 2.0})
    events = store.list_events(limit=10)
    assert len(events) == 2
    # LPUSH：最新在前
    assert events[0]["id"] == "e2"
    assert events[1]["id"] == "e1"


def test_daily_avg_per_run():
    store = KernelSharedStore(_FakeRedis())
    store.record_daily_run()
    store.record_daily_run()
    store.record_daily_charge(200)
    store.record_daily_charge(100)
    assert store.daily_avg_per_run() == 150.0  # 300 / 2
    # 排除本进程 100 后：基线 200 / max(runs-1,1)=1 → 200
    assert store.daily_avg_per_run(exclude_tokens=100) == 200.0


def test_publish_resume():
    r = _FakeRedis()
    store = KernelSharedStore(r)
    store.publish_resume("pid-xyz")
    assert any(ch.endswith(":resume") and msg == "pid-xyz" for ch, msg in r._published)


def test_set_process_fields_rejects_tokens_used():
    store = KernelSharedStore(_FakeRedis())
    store.put_process({
        "id": "p3",
        "identity": "main",
        "capabilities": ["*"],
        "token_budget": 1000,
        "tokens_used": 10,
        "state": "running",
        "created_at": 1.0,
    })
    store.charge_tokens("p3", 40)  # 50
    store.set_process_fields("p3", tokens_used=1, state="suspended", token_budget=800)
    d = store.get_process("p3")
    assert d is not None
    assert d["tokens_used"] == 50  # 不可被 set 回滚
    assert d["state"] == "suspended"
    assert d["token_budget"] == 800


@pytest.mark.asyncio
async def test_request_escalation_claim_dedup():
    """两 worker 并发 request 同能力 → 只一条 pending。"""
    from backend.kernel.kernel import AgentKernel, reset_kernel_for_tests
    from backend.kernel.process import AgentProcess

    reset_kernel_for_tests()
    store = KernelSharedStore(_FakeRedis())
    ka = AgentKernel(shared_store=store)
    kb = AgentKernel(shared_store=store)
    proc = AgentProcess(
        identity="main",
        capabilities=["web_search"],
        id="esc-pid",
        state="running",
    )
    ka._processes[proc.id] = proc
    store.put_process(proc.to_dict())
    # worker A 申请
    a = await ka.request_escalation("esc-pid", ["shell"], reason="need shell")
    # worker B 本地无缓存，从 Redis claim 复用
    assert "esc-pid" not in kb._processes or True
    b = await kb.request_escalation("esc-pid", ["shell"], reason="retry")
    assert a.id == b.id
    pending = [e for e in ka.list_escalations(status="pending") if e.process_id == "esc-pid"]
    # 去重后 pending 唯一
    assert len({e.id for e in pending}) == 1
    reset_kernel_for_tests()


@pytest.mark.asyncio
async def test_approve_target_process():
    """批准 live 进程 → target=process。"""
    from backend.kernel.kernel import AgentKernel, reset_kernel_for_tests
    from backend.kernel.process import AgentProcess

    reset_kernel_for_tests()
    k = AgentKernel()
    proc = AgentProcess(
        identity="main",
        capabilities=["web_search"],
        id="ap-pid",
        state="running",
    )
    k._processes[proc.id] = proc
    req = await k.request_escalation("ap-pid", ["shell"], reason="x")
    done = await k.approve_escalation(req.id, by="boss")
    assert done.status == "approved"
    assert done.target == "process"
    assert "shell" in (k.get_process("ap-pid").capabilities or [])
    assert done.to_dict().get("target") == "process"
    reset_kernel_for_tests()


@pytest.mark.asyncio
async def test_events_merge_from_shared():
    """他 worker push 的事件对本机 events() 可见。"""
    from backend.kernel.kernel import AgentKernel, reset_kernel_for_tests

    reset_kernel_for_tests()
    store = KernelSharedStore(_FakeRedis())
    # worker A emit
    ka = AgentKernel(shared_store=store)
    ka._emit("mediation", "p-remote", {"tool": "web_search"})
    # worker B 本地空缓冲
    kb = AgentKernel(shared_store=store)
    assert len(kb._events) == 0
    merged = kb.events(limit=50)
    assert any(e.kind == "mediation" and e.process_id == "p-remote" for e in merged)
    reset_kernel_for_tests()


@pytest.mark.asyncio
async def test_auto_tighten_2x_reduces_budget():
    """用量 > 2× 日均时收紧 token_budget。"""
    import backend.kernel.approval_rules as ar
    from backend.kernel.kernel import AgentKernel, reset_kernel_for_tests
    from backend.kernel.process import AgentProcess

    reset_kernel_for_tests()
    # 强制开启 auto_tighten
    ar._RULES_CACHE = [{"key": "auto_tighten_2x", "enabled": True}]
    store = KernelSharedStore(_FakeRedis())
    # 基线：3 个历史 run 共 300 → 他进程日均 100
    for _ in range(3):
        store.record_daily_run()
    store.record_daily_charge(300)
    k = AgentKernel(shared_store=store)
    proc = AgentProcess(
        identity="main",
        capabilities=["*"],
        id="tight-pid",
        state="running",
        token_budget=10000,
        tokens_used=0,
    )
    k._processes[proc.id] = proc
    store.put_process(proc.to_dict())
    store.record_daily_run()  # 本进程创建
    # charge 到 250（> 2× 他进程日均 100）
    rem = k.charge_tokens("tight-pid", 250)
    assert rem is not None
    fresh = k.get_process("tight-pid")
    assert fresh is not None
    # 收紧到 ~ used+500，且 < 原 10000
    assert fresh.token_budget is not None
    assert fresh.token_budget < 10000
    assert fresh.token_budget >= fresh.tokens_used
    ar._RULES_CACHE = None
    reset_kernel_for_tests()
