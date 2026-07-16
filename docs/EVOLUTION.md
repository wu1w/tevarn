# 自主进化（TEE）使用说明

Takton Evolution Engine：验收 + 安全门 + 可管理清单。  
决策：**方案 B**，**过门后 auto_apply**（可关）。

## 开关

环境变量（或 API）：

| 变量 | 默认 | 含义 |
|------|------|------|
| `TAKTON_EVOLUTION_ENABLED` | false | 总开关 |
| `TAKTON_EVOLUTION_AUTO_APPLY` | true | 过门自动 active |
| `TAKTON_EVOLUTION_MODE` | on_failure | off / on_failure / always / manual |

API：

```http
POST /api/evolution/enable
{ "enabled": true, "auto_apply_skills": true, "mode": "on_failure" }
```

UI：侧栏 **自主进化** → 点「开启进化」。

## 你能看见 / 管理

- 自主归纳了多少、待审、从未使用  
- 每项 **使用次数**  
- 筛选「仅未使用」、**删除**、批量清理  
- 预置 seed 不可删  

## 行为

1. 对话中工具调用记入轨迹；成功调用给 skill 资产 +use_count  
2. 轮次结束：失败启发式（多答案并列、工具错误等）→ 生成 playbook  
3. 五道安全门通过且 auto_apply → status=active，并 **注册进 ToolRegistry**（模型可直接调用）  
4. 未过门 → 留 draft，可在进化中心启用/删除  
5. 删除/停用时从 ToolRegistry **注销**

## 评估准则类型

| type | 说明 |
|------|------|
| `file_exists` / `content_match` | 本地文件 |
| `command_output` / `test_pass` | 本机命令/测试 |
| `http_ok` | HTTP 健康检查 |
| `remote_exec` | 已配对设备上 `exec.run`；`optional: true` 时无设备跳过 |
| `llm_judge` | LLM 打 0–1 分（`TAKTON_EVOLUTION_LLM_JUDGE=false` 可关） |

预置任务含：`smoke-health`、`evolution-module`、`remote-device-optional`、`llm-judge-sample` 等。

## 验收任务

```http
POST /api/evolution/run_task/smoke-health
POST /api/evolution/run_task/evolution-module
POST /api/evolution/run_task/remote-device-optional
POST /api/evolution/run_task/llm-judge-sample
```

## 数据

默认库：`%TAKTON_HOME%/evolution.db` 或项目 `data/evolution.db`。

## UI

侧栏 **自主进化**（`/evolution`）：清单、次数、删除、开关。  
**无角标数字**（按产品要求不加）。
