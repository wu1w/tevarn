"""backend.core.timezone 统一时间源测试"""

from datetime import datetime, timezone

from backend.core.timezone import ensure_aware, iso_utc, local_now, now, utc_now


def test_now_returns_aware_utc():
    """now() 必须返回带 UTC 时区的 aware datetime"""
    dt = now()
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timezone.utc.utcoffset(None)


def test_utc_now_semantics():
    """utc_now() 与 now() 同义，用于 ORM default"""
    dt = utc_now()
    assert dt.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - dt).total_seconds()) < 1


def test_local_now_follows_system_tz():
    """local_now() 返回本地时区 aware datetime"""
    dt = local_now()
    assert dt.tzinfo is not None
    # 本地时间与 UTC 差值应在 ±14h 内（合法时区范围）
    delta = dt.utcoffset()
    assert delta is not None
    assert -14 * 3600 <= delta.total_seconds() <= 14 * 3600


def test_ensure_aware_naive_treated_as_utc():
    """naive datetime 一律视为 UTC"""
    naive = datetime(2026, 7, 23, 12, 0, 0)
    aware = ensure_aware(naive)
    assert aware.tzinfo == timezone.utc
    assert aware.isoformat() == "2026-07-23T12:00:00+00:00"


def test_ensure_aware_keeps_existing_tz():
    """已 aware 的 datetime 保持原时区"""
    from datetime import timedelta

    tz8 = timezone(timedelta(hours=8))
    dt = datetime(2026, 7, 23, 20, 0, 0, tzinfo=tz8)
    assert ensure_aware(dt) is dt


def test_iso_utc_format():
    """iso_utc() 输出 Z 后缀 ISO 字符串"""
    dt = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)
    assert iso_utc(dt) == "2026-07-23T12:00:00Z"


def test_iso_utc_naive_fallback():
    """naive datetime 经 iso_utc 视为 UTC"""
    naive = datetime(2026, 7, 23, 12, 0, 0)
    assert iso_utc(naive) == "2026-07-23T12:00:00Z"
