"""P0: VPS HMAC vpt mint/verify + single_user relay gate."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from backend.api.dependencies import _is_via_relay, _may_single_user_free_login
from backend.services.vps_relay import mint_vpt, verify_vpt


def test_mint_verify_vpt_roundtrip():
    token = "relay-master-secret"
    tid = "pc-deadbeef"
    vpt = mint_vpt(token, tid, ttl_secs=120)
    assert verify_vpt(token, tid, vpt)
    assert not verify_vpt(token, "pc-other", vpt)
    assert not verify_vpt("wrong", tid, vpt)
    assert not verify_vpt(token, tid, "not-a-ticket")


def test_vpt_expiry():
    token = "t"
    tid = "pc-1"
    # craft expired
    import hashlib
    import hmac as hmac_mod

    exp = int(time.time()) - 120
    sig = hmac_mod.new(token.encode(), f"{tid}:{exp}".encode(), hashlib.sha256).hexdigest()[
        :32
    ]
    vpt = f"{exp}.{sig}"
    assert not verify_vpt(token, tid, vpt)


def test_via_relay_blocks_single_user_free_login():
    req = MagicMock()
    req.headers = {"x-takton-relay": "1"}
    req.client = MagicMock()
    req.client.host = "127.0.0.1"
    assert _is_via_relay(req) is True
    assert _may_single_user_free_login(req) is False


def test_true_loopback_allows_single_user():
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock()
    req.client.host = "127.0.0.1"
    assert _is_via_relay(req) is False
    assert _may_single_user_free_login(req) is True
