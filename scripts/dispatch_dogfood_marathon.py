#!/usr/bin/env python3
"""并行下发 3 类大型 dogfood 工单（目标 ≥2h 运行窗口）。

前提：
  - BE 已提高 agent_inbox_item_timeout（建议 10800s）
  - dispatcher 开启

用法:
  .venv/Scripts/python.exe scripts/dispatch_dogfood_marathon.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8090/api"
OUT = ROOT / "reports" / "DOGFOOD_MARATHON_DISPATCH.json"

# 编制员工（live）
BACKEND = "310adc13-96c2-4fdb-961f-f19bef9d08c5"
AGENT_ENG = "ea266dbb-1af0-4c34-a7b3-0d9243155fed"
QA = "c26e5ff2-874c-4306-a4ab-ce29b16166dc"
KERNEL = "bf29b574-d86a-4dc9-ada1-0f5d0798bbda"

TOKEN_BUDGET = 900_000
PRIORITY = 80


def req(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    url = BASE.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw[:500]
        return e.code, parsed


INSTR_OVERNIGHT = r"""
# DOGFOOD-A · 隔夜 Durable 大型审计马拉松（目标运行 ≥2 小时）

工作区: E:\项目\takton-alpha
输出目录: reports/dogfood_marathon/overnight_audit/
请创建输出目录；所有报告写入该目录（Markdown）。

## 硬性要求
1. 本工单是 **长任务**：请分 **至少 8 个阶段** 推进，每阶段结束都要把中间结果 **落盘** 到输出目录（不要只放在对话里）。
2. 每阶段开始前用 `glob`/`grep`/`file_read` **实际读代码**，禁止空谈；每阶段至少 5 次工具调用。
3. 预算约 90 万 token；**不要**为了省预算提前结束。若某阶段结论已够，扩展为「反例搜索 / 边界路径 / 测试对照」再写一节。
4. 最终交付 `00_EXECUTIVE_SUMMARY.md` + 各阶段报告；并在摘要中列出 **Run/工单可恢复点**（你写了哪些中间文件）。

## 阶段清单（必须全部做完）

### P1 仓库地图（≥15 分钟工作量）
- 统计 backend/ frontend/ docs/ scripts/ 顶层结构
- 输出 `01_repo_map.md`：目录职责、关键入口、启动路径

### P2 Run 统一与 Durable（Phase2）
- 精读 `run_lifecycle.py` `run_recovery.py` `run_recorder` 相关、`EXECUTION_MODEL.md`
- 对照 `test_durable_run_recovery.py` 与实现差异
- 输出 `02_run_durable.md`：origin 路径、checkpoint 权威、recovery 策略表

### P3 记忆总线 + 权限法院（Phase3）
- 精读 `memory_bus.py` `permission_court.py`；抽查 tools 是否绕过总线
- 验证 wiki 写入路径（代码阅读 + 如有工具可试写一条再废止）
- 输出 `03_memory_court.md`

### P4 进化回放与成长（Phase4）
- 精读 `replay_validator.py`、evolution 路由 apply/replay 门禁
- 输出 `04_evolution.md`：门禁条件、失败路径、与 FE 的对应关系

### P5 发行与安全表面（Phase5）
- 阅读 `PHASE5_EXECUTION_PLAN.md` `ZERO_DEPS` `INSTALL` `security_check`
- 输出 `05_release_security.md`

### P6 测试矩阵
- 列出 security / kernel / phase5 / durable 相关测试文件与覆盖空洞
- 本地可运行则用 **项目 .venv** 跑一小撮 pytest（不要全量拖死），记录结果
- 输出 `06_test_matrix.md`

### P7 风险与债
- 汇总未实现/半截项（Intent/wiki/dogfood/NSIS 等）与建议优先级
- 输出 `07_risks.md`

### P8 总控与可恢复
- 写 `00_EXECUTIVE_SUMMARY.md`：结论 1 页 + 文件索引 + 「若进程被 kill，从哪几个中间文件续跑」

## 质量条
- 每份报告含：证据路径（文件:行号或命令输出摘要）、结论、残留风险
- 禁止编造不存在的文件；不确定就写「未找到」
""".strip()


INSTR_PPT = r"""
# DOGFOOD-B · 公司 PPT 大型交付（偏好 + 多轮修订，目标 ≥2 小时）

工作区: E:\项目\takton-alpha
输出目录: reports/dogfood_marathon/ppt_delivery/
中间产物: reports/dogfood_marathon/ppt_delivery/workspace/

## 风格偏好（必须遵守，并先写入/确认记忆）
- 深蓝封面、少字多图、页脚含版本号
- 正文字号 ≥18、禁用花哨动画
- 受众：管理层 + 工程负责人
- 版本标记：0.4.10-alpha dogfood marathon

请先：
1. 用 memory / identity 相关能力 **写入或确认** preference（公司 PPT 风格）
2. recall 验证偏好可被读到
3. 再开始做 PPT

## 交付物（全部落盘）
1. `outline_v1.json` `outline_v2.json` `outline_final.json`（多轮大纲修订，禁止一轮定稿）
2. 完整幻灯片内容：至少 **28 页**，结构建议：
   - 封面/目录/执行摘要
   - Takton 定位（治理内核数字员工运行时）
   - Phase1–5 路线图（每 Phase ≥2 页）
   - 统一 Run / 权限法院 / 回放进化 深潜（各 ≥2 页）
   - 风险、资源、安装路径、Demo 三连
   - 附录：关键路径表、术语表
3. 若 `generate_ppt` / python-pptx 可用：产出 `.pptx`；否则产出等价 Markdown 幻灯片 `deck.md` + 每页要点
4. `style_compliance_checklist.md`：逐条对照偏好打勾
5. `speaker_notes.md`：演讲者备注（每页 2–4 句）
6. `revision_log.md`：至少 **3 轮** 自我评审与修改记录（每轮读一遍 outline 再改）

## 硬性要求
- 预算约 90 万 token；分阶段慢慢做，**不要** 10 分钟交卷
- 每轮修订必须基于文件 diff 思维（写清改了什么）
- 中间 JSON 必须可 `resume_from` 语义（路径写在 revision_log）
- 引用仓库真实能力时核对 docs/ 与代码，禁止胡编 API
""".strip()


INSTR_EVOLUTION = r"""
# DOGFOOD-C · Evolution 回放上岗 + 大型验收（目标 ≥2 小时）

工作区: E:\项目\takton-alpha
输出目录: reports/dogfood_marathon/evolution_qa/

## 背景
系统已有 evolution draft/replay/apply 机制。你的任务是 **端到端验证「回放门禁 → 上岗技能 → 真实使用」**，并做深度 QA。

## 阶段 1 · 盘点与回放（强制）
1. 用工具/命令行（项目 `.venv`）或读 `~/.takton` / evolution store 相关代码，搞清资产落盘位置
2. 列出若干 draft 技能名；对 **至少 3 个** draft 描述如何触发 replay（读路由 `evolution.py`）
3. 写 `01_inventory.md`

## 阶段 2 · 门禁对抗
1. 精读 `replay_validator.py` 与 apply 门禁
2. 构造/寻找「应 fail」与「应 pass」案例的代码级说明
3. 写 `02_gate_analysis.md`（含伪代码级条件表）

## 阶段 3 · 上岗后真实使用
1. 若环境允许：通过合理工具路径 **使用** 一个已 active/evolved 技能，或模拟挂载流程（读 skill_sync）
2. 设计一个 **真实仓库小任务** 并执行（例如：为 `scripts/dogfood_live_smoke.py` 增加一节 README 说明，或修一处文档交叉链接），要求：
   - 先读后改
   - 改完用 grep 自检
   - 记录前后 diff 摘要
3. 写 `03_real_use.md`

## 阶段 4 · 回归与长稳
1. 跑一组与 evolution/kernel/security 相关的 pytest（项目 venv，小范围，记录输出）
2. 检查 FE 页面路由 `/evolution` 依赖的 API 列表是否与后端一致
3. 写 `04_regression.md`

## 阶段 5 · 综合报告
1. `00_SUMMARY.md`：是否具备「回放失败不得上岗」护城河；残留风险；给老板 5 条建议
2. 附录：你调用过的关键文件路径列表（≥20 个）

## 硬性要求
- 预算约 90 万 token；阶段要慢、要深
- 每阶段落盘；禁止只输出对话不写文件
- 工具调用要密集；结论必须有证据
""".strip()


def enqueue(identity_id: str, instruction: str, *, title: str, priority: int = PRIORITY) -> dict:
    body = {
        "identity_id": identity_id,
        "instruction": instruction,
        "source": "manual",
        "priority": priority,
        "payload": {
            "dogfood": "marathon_2h",
            "title": title,
            "token_budget": TOKEN_BUDGET,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
            "min_runtime_hint_hours": 2,
        },
    }
    code, data = req("POST", "/kernel/inbox", body)
    return {"http": code, "response": data, "identity_id": identity_id, "title": title}


def try_apply_evolution() -> dict:
    """replay + apply 一个看起来可过的 draft。"""
    code, assets = req("GET", "/evolution/assets?limit=80")
    if code != 200 or not isinstance(assets, list):
        return {"ok": False, "error": f"list assets {code}"}
    drafts = [a for a in assets if isinstance(a, dict) and a.get("status") == "draft"]
    # 优先 good_apply / 非 bad
    drafts.sort(key=lambda a: (0 if "good" in str(a.get("name", "")).lower() else 1))
    tried = []
    for d in drafts[:8]:
        aid = str(d["id"])
        name = str(d.get("name"))
        c1, rep = req("POST", f"/evolution/drafts/{aid}/replay", {})
        passed = False
        if isinstance(rep, dict):
            r = rep.get("replay") if isinstance(rep.get("replay"), dict) else rep
            passed = bool(isinstance(r, dict) and r.get("pass"))
        tried.append({"id": aid, "name": name, "replay_http": c1, "pass": passed})
        if not passed:
            continue
        c2, applied = req("POST", f"/evolution/drafts/{aid}/apply", {})
        if c2 == 200:
            return {"ok": True, "applied": applied, "tried": tried}
        tried[-1]["apply_http"] = c2
        tried[-1]["apply_body"] = str(applied)[:200]
    return {"ok": False, "tried": tried}


def main() -> int:
    # health
    for i in range(30):
        code, _ = req("GET", "/health")
        if code == 200:
            break
        time.sleep(1)
    else:
        print("backend not healthy")
        return 1

    print("=== Evolution pre-step: replay+apply ===")
    evo = try_apply_evolution()
    print(json.dumps(evo, ensure_ascii=False, indent=2)[:1500])

    jobs = [
        ("A_overnight_durable", BACKEND, INSTR_OVERNIGHT, "隔夜 Durable 全库审计马拉松"),
        ("B_ppt_delivery", AGENT_ENG, INSTR_PPT, "公司 PPT 大型交付（偏好+多轮）"),
        ("C_evolution_qa", QA, INSTR_EVOLUTION, "Evolution 回放上岗+大型 QA"),
    ]
    # 并行语义：连续 enqueue，dispatcher 会按不同 identity 并行领单
    dispatched = []
    print("\n=== Dispatch 3 parallel jobs ===")
    for key, iid, instr, title in jobs:
        r = enqueue(iid, instr, title=title, priority=PRIORITY)
        dispatched.append({"key": key, **r})
        print(f"{key}: HTTP {r['http']} -> {r['response']}")

    report = {
        "when": datetime.now(timezone.utc).isoformat(),
        "token_budget_per_job": TOKEN_BUDGET,
        "evolution_prestep": evo,
        "jobs": dispatched,
        "note": "inbox timeout should be >= 10800s on BE; identities default_token_budget raised to 900k",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")

    # quick status
    time.sleep(3)
    code, ib = req("GET", "/kernel/inbox?limit=20")
    print("inbox snapshot HTTP", code)
    if isinstance(ib, dict):
        for it in (ib.get("items") or [])[:10]:
            print(
                " ",
                it.get("status"),
                (it.get("identity_name") or it.get("identity_id") or "")[:20],
                (it.get("instruction") or "")[:50].replace("\n", " "),
            )
    return 0 if all(j.get("http") == 200 for j in dispatched) else 1


if __name__ == "__main__":
    raise SystemExit(main())
