"""Redis 共享态：无 Redis 时用内存假客户端验证编解码与 charge 语义。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.kernel.shared_store import KernelSharedStore


class _FakeRedis:
    """极简 Hash/Set 假客户端。"""

    def __init__(self) -> None:
        self._h: dict[str, dict[str, bytes]] = {}
        self._s: dict[str, set[bytes]] = {}

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
        return 1 if key in self._h else 0

    def expire(self, key: str, ttl: int) -> None:
        return None

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

    def ping(self) -> bool:
        return True

    def eval(self, _script: str, _numkeys: int, *args: Any) -> Any:
        """测试替身：与 shared_store._CHARGE_LUA 语义等价（单线程天然原子）。

        返回 None 对应 Lua 的 return false（进程不存在）。
        """
        key, amount = str(args[0]), int(args[1])
        if not self.exists(key):
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
