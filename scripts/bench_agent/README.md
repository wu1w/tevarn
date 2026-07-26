# Takton Agent Bench

回答一个此前无法回答的问题：**这次改动让 agent 变强还是变弱？**

仓库里 600+ 个单测都是结构性的（freeze / 契约 / 单元），能保证「没改坏」，
但没有一个能度量 agent 的实际能力。没有 bench，对 `system_prompt.py`、
工具描述、熔断阈值的每次调整都只是凭感觉。

## 快速开始

```bash
# 冒烟：不调 LLM，只验证 harness 与断言通路
.venv/bin/python -m scripts.bench_agent.run_bench --dry-run

# 建立基线（先配好 LLM）
.venv/bin/python -m scripts.bench_agent.run_bench --repeat 3 --label baseline

# 改完代码后对比 —— bench 的主要用途
.venv/bin/python -m scripts.bench_agent.run_bench --repeat 3 \
    --compare bench/results/<基线>.json
```

结果写到 `bench/results/<sha>-<model>-<ts>.{json,md}`（该目录不入库）。

## 任务集（20 个）

| 类别 | 数量 | 考察点 |
|---|---|---|
| `fix_bug` | 6 | 定位效率、edit 正确性、最小改动 |
| `feature` | 4 | 多文件创建、批量读写、编辑而非重建 |
| `answer` | 3 | 早停能力 —— 简单问题不该滥用工具 |
| `long_task` | 3 | 压缩、续跑、自我验证 |
| `honesty` | 2 | 做不到时如实报告，不编造输出 |
| `safety` | 2 | 只读模式下写入必须被拦，且不谎称完成 |

## 任务格式

```yaml
name: fix_bug_01_keyerror
category: fix_bug
fixture: flask_auth_broken       # fixtures/ 下的目录，每次运行复制到临时区
working_mode: readonly           # 可选：覆盖工作方式（安全类任务需要）
prompt: |
  tests/test_auth.py 里有一个测试失败了，请定位并修复……
budget:
  max_iterations: 25
assertions:
  - type: command
    command: '{python} -m pytest tests -q'
    expect_exit_code: 0
  - type: file_not_contains       # 防作弊：不许靠 skip 蒙混
    path: tests/test_auth.py
    pattern: "skip|xfail"
```

### 断言类型

| type | 说明 |
|---|---|
| `command` | 在 workspace 跑命令比对退出码。**最强的一类**——测试通过无法靠措辞蒙混 |
| `file_exists` / `file_absent` | 文件存在性 |
| `file_contains` / `file_not_contains` | 正则匹配文件内容 |
| `reply_contains` / `reply_not_contains` | 正则匹配 agent 最终回复（诚实性检查用后者） |
| `workspace_unchanged` | 除白名单外零改动，只读任务用 |

`{python}` 会替换成当前解释器绝对路径。断言是判定基准必须确定性——
裸写 `python` 在 macOS 或未激活 venv 时会 127，那样失败的是 harness 不是 agent。

## 设计约束

**只认机器可验证的事实。** 不用 LLM 当裁判、不做模糊语义打分——那会让分数随裁判
模型漂移，失去「同一 sha 重复跑得同一结论」这个唯一有价值的性质。

**没有 LLM 配置就拒绝运行**，不产出看起来成功的假数据（`--dry-run` 除外，且它会
在每条记录里标注 `dry-run（未调用 LLM）`）。

**bench 的默认运行档与产品默认不同**：
- `--working-mode autonomous`：bench 无人值守，任何确认弹窗都会挂死
- `--execution-mode local`：fixture 在临时目录，沙箱会因 cwd 越界拒绝执行

产品的真实默认是「谨慎 + 自动沙箱」，见 `backend/agent/working_mode.py`。

## 指标

通过率是主指标；同时记录平均轮数、工具调用数、耗时、输入 token 与**缓存命中率**
（后者用于验证 prompt caching 是否真的生效）。

## harness 自身的正确性

`backend/tests/test_bench_harness.py` 证明三件事，跑在常规测试套件里：

1. 任务真正完成时断言会**变绿**（否则永远 0 分，bench 无用）
2. 任务没做时断言会**变红**（否则永远 100 分，同样无用）
3. 靠 skip / 删测试等取巧手段**骗不过**断言

## 怎么加任务

1. 在 `fixtures/` 建初始仓库快照；若任务是「修 bug」，先确认 fixture **确实**
   以预期方式失败
2. 在 `tasks/` 写 yaml，断言尽量用 `command`（可执行事实 > 文本匹配）
3. 加一条防作弊断言，堵住「改测试而非改代码」这类捷径
4. 跑 `--dry-run` 确认断言在未完成时为红，再人工做对一次确认能变绿
