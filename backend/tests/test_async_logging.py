"""B3 异步日志测试（零 mock）

真实组件：真实 setup_logging（tmp 目录真实文件）+ 真实 QueueListener 线程 +
真实文件轮询验证落盘。保存/恢复 root logger handlers 避免污染其它测试。
"""

from __future__ import annotations

import logging
import logging.handlers
import time

import pytest


@pytest.fixture()
def root_logger_guard():
    """保存/恢复 root logger 现场（真实对象，非 mock）"""
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    yield
    from backend.core.logging_config import stop_async_logging

    stop_async_logging()
    root.handlers.clear()
    for h in old_handlers:
        root.addHandler(h)
    root.setLevel(old_level)


def _wait_file_contains(path, needle: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(path, encoding="utf-8") as f:
                if needle in f.read():
                    return True
        except OSError:
            pass
        time.sleep(0.05)
    return False


def test_logs_written_via_async_listener(tmp_path, root_logger_guard):
    """日志经 QueueListener 真实落盘（主文件 + error 文件分级）"""
    from backend.core.logging_config import setup_logging

    setup_logging(log_dir=str(tmp_path), log_level="INFO", json_output=True)

    logger = logging.getLogger("b3.test")
    logger.info("hello-async-info")
    logger.error("hello-async-error")

    assert _wait_file_contains(tmp_path / "takton.log", "hello-async-info"), \
        "takton.log 未收到 INFO（异步落盘失败）"
    assert _wait_file_contains(tmp_path / "error.log", "hello-async-error"), \
        "error.log 未收到 ERROR（respect_handler_level 失效）"


def test_setup_twice_no_duplicate_queue_handlers(tmp_path, root_logger_guard):
    """重复 setup_logging：旧 listener 停止、root 只挂一个 QueueHandler"""
    from backend.core.logging_config import _ASYNC_LISTENERS, setup_logging

    setup_logging(log_dir=str(tmp_path / "a"), log_level="INFO")
    setup_logging(log_dir=str(tmp_path / "b"), log_level="INFO")

    root = logging.getLogger()
    qhs = [h for h in root.handlers if isinstance(h, logging.handlers.QueueHandler)]
    assert len(qhs) == 1, f"QueueHandler 重复挂载: {len(qhs)}"
    alive = [l for l in _ASYNC_LISTENERS if getattr(l, "_thread", None) and l._thread.is_alive()]
    assert len(alive) == 1, f"活跃 listener 应恰为 1: {len(alive)}"

    # 第二次 setup 的目录真实可用
    logging.getLogger("b3.test2").info("second-setup-works")
    assert _wait_file_contains(tmp_path / "b" / "takton.log", "second-setup-works")


def test_stop_async_logging_drains_and_stops(tmp_path, root_logger_guard):
    """stop_async_logging：排空队列且线程终止（shutdown 语义）"""
    from backend.core.logging_config import _ASYNC_LISTENERS, setup_logging, stop_async_logging

    setup_logging(log_dir=str(tmp_path), log_level="INFO")
    logging.getLogger("b3.test3").info("before-stop")
    stop_async_logging()

    assert not _ASYNC_LISTENERS, "listener 注册表未清空"
    # 停止后队列已排空：日志一定已落盘（QueueListener.stop 会 join 并 flush）
    with open(tmp_path / "takton.log", encoding="utf-8") as f:
        assert "before-stop" in f.read()
