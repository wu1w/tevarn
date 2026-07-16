# Takton 对话配置体系

目标：用户尽量通过对话框完成配置与使用。

## 交付

1. **Skill `configure_takton`**
   - guide / status / list_settings / set_setting / checklist / search / topics
   - 内置完整手册，不依赖向量 RAG

2. **知识库手册文档**（title 前缀 `[手册]`，source=`builtin-seed`）
   - 总览、模型、RAG、设备、通道、工具技能、Cron、工作流、MCP/Profiles、上下文、Wiki、安全、速查表、开箱清单

3. **首页示例**引导「按开箱清单配置」

## 用户话术

- 按开箱清单一步步带我配置 Takton
- 怎么配模型 / 设备 / 知识库
- 当前系统状态
- 把 temperature 设为 0.2
- configure_takton 讲通道

## 文件

- `backend/content/product_handbook.py`
- `backend/skills/builtins/configure_takton_skill.py`
- seed：`main._seed_beginner_knowledge`
