"""Runtime Services 门面：编制 / 工单 / 调度（逻辑层 L3）。

实现仍在 ``backend.kernel`` 子模块；本门面固定 import 路径，便于测试与 CLI
不直接依赖 kernel 内部文件布局。
"""

from __future__ import annotations

from typing import Any


def get_kernel() -> Any:
    from backend.kernel import get_kernel as _gk

    return _gk()


def init_workforce(kernel: Any, session_factory: Any, settings: Any) -> tuple[Any, Any]:
    from backend.kernel.workforce import init_workforce as _init

    return _init(kernel, session_factory, settings)


def get_workforce_inbox() -> Any:
    from backend.kernel.workforce import get_workforce_inbox as _g

    return _g()


def get_workforce_dispatcher() -> Any:
    from backend.kernel.workforce import get_workforce_dispatcher as _g

    return _g()


def build_daily_report(kernel: Any, inbox: Any, **kwargs: Any) -> Any:
    from backend.kernel.workforce import build_daily_report as _b

    return _b(kernel, inbox, **kwargs)
