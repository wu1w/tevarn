"""统一启动安全自检（安全加固 2026-07-26）

把分散在各处的安全检查集中成一份清单：
- 启动时由 main.py 调用 run_startup_security_check()：
  - 任一 fail 级 → 打印全部问题后拒绝启动（RuntimeError）
  - warn 级 → 醒目日志，继续启动
- GET /api/settings/security-audit 复用 collect_security_report() 给前端展示
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from typing import Literal

from backend.core.config import settings

logger = logging.getLogger(__name__)

Level = Literal["ok", "warn", "fail"]


@dataclass
class CheckResult:
    id: str
    level: Level
    message: str
    hint: str = ""


@dataclass
class SecurityReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def worst(self) -> Level:
        if any(r.level == "fail" for r in self.results):
            return "fail"
        if any(r.level == "warn" for r in self.results):
            return "warn"
        return "ok"


def _is_loopback_bind(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # 主机名无法解析时按非 loopback 处理（保守）
        return False


def collect_security_report() -> SecurityReport:
    """收集全部安全检查项（纯读，无副作用）。"""
    report = SecurityReport()
    loopback_bind = _is_loopback_bind(settings.app_host)

    # 1. host 绑定 × single_user_mode 组合（P0-1 场景）
    if not loopback_bind and settings.single_user_mode:
        report.results.append(
            CheckResult(
                id="host_single_user_combo",
                level="fail",
                message=(
                    f"服务绑定 {settings.app_host}（非 loopback）且 single_user_mode 开启"
                ),
                hint="非本机部署请设置 TEVARN_SINGLE_USER_MODE=false，使用真实账号登录",
            )
        )
    else:
        report.results.append(
            CheckResult(
                id="host_single_user_combo",
                level="ok",
                message=(
                    f"绑定 {settings.app_host} + "
                    f"{'单用户模式（仅 loopback 放行）' if settings.single_user_mode else '多用户认证模式'}"
                ),
            )
        )

    # 2. JWT 密钥强度
    if len(settings.jwt_secret) < 16:
        report.results.append(
            CheckResult(
                id="jwt_secret_strength",
                level="fail",
                message="jwt_secret 长度过短（<16 字符）",
                hint="设置 TEVARN_JWT_SECRET 为强随机值，或删除 ~/.tevarn/secrets.json 重新生成",
            )
        )
    else:
        report.results.append(
            CheckResult(id="jwt_secret_strength", level="ok", message="jwt_secret 强度合格")
        )

    # 3. bridge_token × 非 loopback（P0-6）
    if not loopback_bind and not (settings.bridge_token or "").strip():
        report.results.append(
            CheckResult(
                id="bridge_token",
                level="warn",
                message="非 loopback 部署且未设置 bridge_token",
                hint="/bridge/v1/* 将回落用户鉴权；共享机场景建议设置 TEVARN_BRIDGE_TOKEN",
            )
        )
    else:
        report.results.append(
            CheckResult(
                id="bridge_token",
                level="ok",
                message=(
                    "bridge_token 已设置" if (settings.bridge_token or "").strip() else "loopback 部署，bridge 回落用户鉴权"
                ),
            )
        )

    # 4. 管理员初始密码来源
    if (settings.default_admin_password or "").strip() == "admin":
        report.results.append(
            CheckResult(
                id="admin_password",
                level="warn",
                message="default_admin_password 显式设置为弱值 'admin'",
                hint="留空则首启自动生成随机密码（写入 ~/.tevarn/initial_admin_password）",
            )
        )
    else:
        report.results.append(
            CheckResult(
                id="admin_password",
                level="ok",
                message=(
                    "管理员初始密码由环境注入" if (settings.default_admin_password or "").strip() else "管理员初始密码将首启随机生成"
                ),
            )
        )

    # 5. 命令执行沙箱（跨平台能力探测）
    if settings.agent_computer_enabled:
        from backend.computer.detect import detect_sandbox_capability

        cap = detect_sandbox_capability()
        if cap.level == "full":
            report.results.append(
                CheckResult(
                    id="command_sandbox",
                    level="ok",
                    message=f"命令执行沙箱已启用（{cap.label}）",
                    hint=cap.note,
                )
            )
        elif cap.level == "restricted":
            report.results.append(
                CheckResult(
                    id="command_sandbox",
                    level="warn",
                    message=f"命令执行沙箱已启用（{cap.label}）",
                    hint=cap.note,
                )
            )
        else:
            report.results.append(
                CheckResult(
                    id="command_sandbox",
                    level="warn",
                    message="沙箱已开启但当前平台无可用方案，将退回无沙箱执行",
                    hint=cap.note or "Linux 安装 bubblewrap；Windows/macOS 见文档",
                )
            )
    else:
        from backend.computer.detect import detect_sandbox_capability

        cap = detect_sandbox_capability()
        report.results.append(
            CheckResult(
                id="command_sandbox",
                level="warn",
                message="命令执行沙箱未启用，agent 命令直接在本机 shell 执行",
                hint=f"权限控制台 → 打开「沙箱模式」（本机能力：{cap.label}）",
            )
        )

    # 6. 设置加密盐
    if not (settings.settings_encryption_salt or "").strip():
        report.results.append(
            CheckResult(
                id="encryption_salt",
                level="warn",
                message="settings_encryption_salt 未设置，敏感设置（API Key 等）无法加密落盘",
                hint="设置 TEVARN_SETTINGS_ENCRYPTION_SALT 为强随机值",
            )
        )
    else:
        report.results.append(
            CheckResult(id="encryption_salt", level="ok", message="设置加密盐已配置")
        )

    return report


def run_startup_security_check() -> None:
    """启动自检：fail 级拒绝启动，warn 级醒目日志。"""
    report = collect_security_report()
    for r in report.results:
        if r.level == "fail":
            logger.error("[SECURITY][FAIL] %s — %s", r.message, r.hint)
        elif r.level == "warn":
            logger.warning("[SECURITY][WARN] %s — %s", r.message, r.hint)
        else:
            logger.info("[SECURITY][OK] %s", r.message)
    if report.worst == "fail":
        raise RuntimeError(
            "Security startup check failed; fix the [SECURITY][FAIL] items above and restart"
        )
