"""观测 / Eval 周报（AIOS 债 #2）：cost · cache · marathon · eval · packages · wasm 打穿。

产物：
- ``data/eval/runs/<ts>.json`` — 单次 eval 结果
- ``data/eval/weekly/<ISO-WEEK>.json`` — 周快照
- ``data/eval/weekly/latest.json`` — 最新周报指针
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def eval_data_dir() -> Path:
    env = os.environ.get("TAKTON_EVAL_DATA_DIR", "").strip()
    if env:
        p = Path(env)
    else:
        p = _project_root() / "data" / "eval"
    p.mkdir(parents=True, exist_ok=True)
    (p / "runs").mkdir(exist_ok=True)
    (p / "weekly").mkdir(exist_ok=True)
    return p


def iso_week_id(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def persist_eval_run(result: dict[str, Any]) -> Path:
    """写入单次 eval 结果并返回路径。"""
    root = eval_data_dir()
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = root / "runs" / f"{ts}.json"
    payload = {
        **result,
        "recorded_at": time.time(),
        "iso_week": iso_week_id(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # pointer
    (root / "runs" / "latest.json").write_text(
        json.dumps({"path": str(path), "overall": payload.get("overall")}, indent=2),
        encoding="utf-8",
    )
    return path


def load_latest_eval() -> dict[str, Any] | None:
    root = eval_data_dir()
    latest = root / "runs" / "latest.json"
    if not latest.is_file():
        # fallback: newest run file
        runs = sorted((root / "runs").glob("*.json"), reverse=True)
        runs = [r for r in runs if r.name != "latest.json"]
        if not runs:
            return None
        try:
            return json.loads(runs[0].read_text(encoding="utf-8"))
        except Exception:
            return None
    try:
        meta = json.loads(latest.read_text(encoding="utf-8"))
        p = Path(meta.get("path") or "")
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _kernel_metrics(k: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "cost": {},
        "cache": {},
        "marathon": {},
        "pkg": {},
        "wasm": {},
        "scheduler": {},
        "run_gate": {},
    }
    if k is None:
        return out

    def call(method: str, params: dict | None = None) -> Any:
        try:
            if hasattr(k, "_call"):
                return k._call(method, params or {}) or {}
            fn = getattr(k, method, None)
            if callable(fn):
                return fn() if not params else fn(**params)
        except Exception as e:
            return {"error": str(e)}
        return {}

    out["cost"] = call("cost_panel")
    out["cache"] = call("cache_metrics")
    out["marathon"] = call("marathon_metrics")
    out["pkg"] = call("pkg_status")
    out["wasm"] = call("wasm_status")
    out["scheduler"] = call("scheduler_stats")
    out["run_gate"] = call("run_gate_status")
    try:
        if hasattr(k, "list_processes"):
            procs = k.list_processes(include_terminal=False) or []
            out["live_processes"] = {
                "count": len(procs),
                "tokens_used": sum(int(getattr(p, "tokens_used", 0) or 0) for p in procs),
            }
    except Exception:
        out["live_processes"] = {}

    # H-11：host 离线时回落磁盘上一次 cache 快照
    cache = out.get("cache") or {}
    if not cache or cache.get("error") or not (cache.get("totals") or cache.get("families")):
        snap = _load_cache_snapshot()
        if snap:
            out["cache"] = {**snap, "from_snapshot": True}
    else:
        _persist_cache_snapshot(cache)
    return out


def _cache_snapshot_path() -> Path:
    return eval_data_dir() / "cache_metrics_latest.json"


def _persist_cache_snapshot(cache: dict[str, Any]) -> None:
    try:
        path = _cache_snapshot_path()
        path.write_text(
            json.dumps(
                {
                    **cache,
                    "snapshotted_at": time.time(),
                    "iso_week": iso_week_id(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug("cache snapshot persist skip: %s", e)


def _load_cache_snapshot() -> dict[str, Any] | None:
    path = _cache_snapshot_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _score_health(metrics: dict[str, Any], eval_result: dict[str, Any] | None) -> dict[str, Any]:
    """合成周健康分 0–1（可解释子项）。"""
    parts: dict[str, float] = {}
    if eval_result and eval_result.get("overall") is not None:
        parts["eval"] = float(eval_result["overall"])
    cache = metrics.get("cache") or {}
    totals = cache.get("totals") or {}
    if totals.get("hit_rate") is not None:
        parts["cache_hit_rate"] = float(totals["hit_rate"])
    mar = metrics.get("marathon") or {}
    if mar.get("resume_success_rate") is not None:
        parts["marathon_resume"] = float(mar["resume_success_rate"])
    elif mar.get("resume_ok") is not None and mar.get("resume_total"):
        parts["marathon_resume"] = float(mar["resume_ok"]) / max(1, float(mar["resume_total"]))
    pkg = metrics.get("pkg") or {}
    q = int(pkg.get("quarantined") or 0)
    total_pkg = int(pkg.get("packages") or 0)
    if total_pkg > 0:
        parts["pkg_clean"] = max(0.0, 1.0 - (q / total_pkg))
    else:
        parts["pkg_clean"] = 1.0
    wasm = metrics.get("wasm") or {}
    # engine present
    parts["wasm_engine"] = 1.0 if wasm.get("engine") else 0.5

    if not parts:
        overall = 0.0
    else:
        overall = sum(parts.values()) / len(parts)
    return {
        "overall": round(overall, 4),
        "parts": {k: round(v, 4) for k, v in parts.items()},
    }


def previous_week_report(week_id: str | None = None) -> dict[str, Any] | None:
    root = eval_data_dir() / "weekly"
    weeks = sorted(
        [p for p in root.glob("*.json") if p.name not in ("latest.json",)],
        reverse=True,
    )
    if week_id:
        # find the one before week_id
        ids = [p.stem for p in weeks]
        if week_id in ids:
            i = ids.index(week_id)
            if i + 1 < len(ids):
                try:
                    return json.loads(weeks[i + 1].read_text(encoding="utf-8"))
                except Exception:
                    return None
        return None
    if len(weeks) >= 2:
        try:
            return json.loads(weeks[1].read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def collect_weekly_report(
    k: Any = None,
    *,
    eval_result: dict[str, Any] | None = None,
    run_eval: bool = False,
    persist: bool = True,
) -> dict[str, Any]:
    """聚合本周观测快照。

    Args:
        k: kernel client（RustAgentKernel / get_kernel）
        eval_result: 已有 eval 结果；None 时尝试 load_latest 或 run_eval
        run_eval: 为 True 时同步跑 scripts.takton_eval suites（需 host）
        persist: 写入 data/eval/weekly
    """
    if k is None:
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
        except Exception as e:
            logger.debug("get_kernel for weekly: %s", e)
            k = None

    if eval_result is None and run_eval:
        try:
            from scripts import takton_eval as te

            if k is None:
                k = te._connect()
            results = [
                te.suite_coding(k),
                te.suite_research(k),
                te.suite_long(k),
                te.suite_safety(k),
            ]
            overall = sum(r["score"] for r in results) / max(1, len(results))
            threshold = float(os.environ.get("TAKTON_EVAL_THRESHOLD", "0.75") or 0.75)
            eval_result = {
                "overall": round(overall, 4),
                "threshold": threshold,
                "suites": results,
                "pass": overall + 1e-9 >= threshold,
            }
            persist_eval_run(eval_result)
        except Exception as e:
            logger.warning("weekly run_eval failed: %s", e)
            eval_result = {"error": str(e)}
    if eval_result is None:
        eval_result = load_latest_eval()

    metrics = _kernel_metrics(k)
    week = iso_week_id()
    health = _score_health(metrics, eval_result if isinstance(eval_result, dict) else None)

    prev = previous_week_report(week)
    trend: dict[str, Any] = {}
    if prev and isinstance(prev.get("health"), dict):
        try:
            delta = health["overall"] - float(prev["health"].get("overall") or 0)
            trend = {
                "prev_week": prev.get("week"),
                "health_delta": round(delta, 4),
                "eval_delta": None,
            }
            if eval_result and prev.get("eval") and prev["eval"].get("overall") is not None:
                trend["eval_delta"] = round(
                    float(eval_result.get("overall") or 0)
                    - float(prev["eval"].get("overall") or 0),
                    4,
                )
        except Exception:
            trend = {}

    report = {
        "week": week,
        "generated_at": time.time(),
        "generated_at_iso": datetime.now(timezone.utc).isoformat(),
        "eval": eval_result,
        "metrics": metrics,
        "health": health,
        "trend": trend,
        "version": "weekly-report-v1",
    }

    if persist:
        root = eval_data_dir() / "weekly"
        path = root / f"{week}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (root / "latest.json").write_text(
            json.dumps(
                {
                    "week": week,
                    "path": str(path),
                    "health": health.get("overall"),
                    "eval_overall": (eval_result or {}).get("overall")
                    if isinstance(eval_result, dict)
                    else None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        report["path"] = str(path)
    return report


def load_weekly_report(week: str | None = None) -> dict[str, Any] | None:
    root = eval_data_dir() / "weekly"
    if week:
        p = root / f"{week}.json"
    else:
        latest = root / "latest.json"
        if not latest.is_file():
            return None
        try:
            meta = json.loads(latest.read_text(encoding="utf-8"))
            p = Path(meta.get("path") or "")
        except Exception:
            return None
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
