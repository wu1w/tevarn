from app.auth import login


def test_login_ok():
    assert login("alice", "password")["ok"] is True


def test_login_wrong_password():
    assert login("alice", "nope")["ok"] is False


def test_unknown_user_does_not_crash():
    # 未知用户应返回 ok=False，而不是抛 KeyError
    assert login("mallory", "whatever")["ok"] is False
