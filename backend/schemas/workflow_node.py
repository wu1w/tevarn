"""
Workflow Node 类型定义 Schema
定义所有可用的工作流节点类型及其配置参数
"""

from typing import Any

from pydantic import BaseModel, Field


class PortSchema(BaseModel):
    """端口定义：描述节点的输入/输出端口"""

    name: str = Field(..., description="端口标识名")
    label: str = Field(..., description="端口显示名称")
    type: str = Field(default="any", description="数据类型: any/string/number/boolean/object/list")
    required: bool = Field(default=False, description="是否必填")
    description: str = Field(default="", description="端口描述")


class ConfigFieldSchema(BaseModel):
    """配置字段定义：用于前端动态生成配置表单"""

    key: str = Field(..., description="字段标识名")
    label: str = Field(..., description="字段显示名称")
    type: str = Field(..., description="UI类型: text/textarea/number/select/boolean/json/code/slider")
    default: Any = Field(default=None, description="默认值")
    required: bool = Field(default=False, description="是否必填")
    description: str = Field(default="", description="字段描述")
    options: list[dict[str, Any]] | None = Field(default=None, description="select类型选项 [{label, value}]")
    min: float | None = Field(default=None, description="slider/number 最小值")
    max: float | None = Field(default=None, description="slider/number 最大值")
    step: float | None = Field(default=None, description="slider 步长")


class NodeTypeDefinition(BaseModel):
    """节点类型定义"""

    type: str = Field(..., description="节点类型唯一标识")
    label: str = Field(..., description="显示名称")
    category: str = Field(..., description="分类: input/output/ai/utility/logic")
    description: str = Field(..., description="节点功能描述")
    icon: str = Field(default="square", description="图标标识")
    color: str = Field(default="#6366f1", description="主题色")
    inputs: list[PortSchema] = Field(default_factory=list, description="输入端口列表")
    outputs: list[PortSchema] = Field(default_factory=list, description="输出端口列表")
    config_schema: list[ConfigFieldSchema] = Field(
        default_factory=list, description="配置字段列表"
    )


# ─────────── 内置节点类型定义 ───────────

NODE_TYPE_DEFINITIONS: list[NodeTypeDefinition] = [
    # ── Input / Output ──
    NodeTypeDefinition(
        type="input",
        label="输入",
        category="input",
        description="接收用户输入或初始数据",
        icon="arrow-right-circle",
        color="#10b981",
        outputs=[
            PortSchema(name="value", label="值", type="any", required=False),
        ],
        config_schema=[
            ConfigFieldSchema(
                key="input_type",
                label="输入类型",
                type="select",
                default="text",
                options=[
                    {"label": "文本", "value": "text"},
                    {"label": "数字", "value": "number"},
                    {"label": "JSON", "value": "json"},
                ],
                description="输入数据的类型",
            ),
            ConfigFieldSchema(
                key="default_value",
                label="默认值",
                type="textarea",
                default="",
                description="当没有上游输入时的默认值",
            ),
        ],
    ),
    NodeTypeDefinition(
        type="output",
        label="输出",
        category="output",
        description="输出最终结果",
        icon="arrow-left-circle",
        color="#f59e0b",
        inputs=[
            PortSchema(name="value", label="值", type="any", required=True),
        ],
        config_schema=[
            ConfigFieldSchema(
                key="output_name",
                label="输出名称",
                type="text",
                default="result",
                description="此输出的标识名称",
            ),
        ],
    ),
    # ── AI ──
    NodeTypeDefinition(
        type="llm",
        label="LLM",
        category="ai",
        description="调用大语言模型进行推理",
        icon="brain",
        color="#8b5cf6",
        inputs=[
            PortSchema(name="prompt", label="提示词", type="string", required=True),
            PortSchema(name="context", label="上下文", type="string", required=False),
        ],
        outputs=[
            PortSchema(name="response", label="回复", type="string"),
            PortSchema(name="tokens_used", label="Token消耗", type="number"),
        ],
        config_schema=[
            ConfigFieldSchema(
                key="model",
                label="模型",
                type="select",
                default="default",
                options=[
                    {"label": "默认模型", "value": "default"},
                    {"label": "GPT-4", "value": "gpt-4"},
                    {"label": "GPT-3.5", "value": "gpt-3.5-turbo"},
                    {"label": "Claude", "value": "claude"},
                    {"label": "Llama", "value": "llama"},
                ],
            ),
            ConfigFieldSchema(
                key="temperature",
                label="温度",
                type="slider",
                default=0.7,
                min=0.0,
                max=2.0,
                step=0.1,
            ),
            ConfigFieldSchema(
                key="max_tokens",
                label="最大Token数",
                type="number",
                default=2048,
                min=1,
                max=8192,
            ),
            ConfigFieldSchema(
                key="system_prompt",
                label="系统提示词",
                type="textarea",
                default="",
                description="模型的系统角色设定",
            ),
        ],
    ),
    NodeTypeDefinition(
            type="agent",
            label="Agent",
            category="ai",
            description="调用智能体执行复杂任务",
            icon="bot",
            color="#6366f1",
            inputs=[
                PortSchema(name="task", label="任务描述", type="string", required=True),
                PortSchema(name="context", label="上下文", type="string", required=False),
            ],
            outputs=[
                PortSchema(name="result", label="结果", type="string"),
                PortSchema(name="actions", label="执行记录", type="list"),
            ],
            config_schema=[
                ConfigFieldSchema(
                    key="agent_profile",
                    label="Agent角色",
                    type="select",
                    default="default",
                    options=[
                        {"label": "默认", "value": "default"},
                        {"label": "研究员", "value": "researcher"},
                        {"label": "程序员", "value": "coder"},
                        {"label": "作家", "value": "writer"},
                    ],
                ),
                ConfigFieldSchema(
                    key="max_steps",
                    label="最大步数",
                    type="number",
                    default=10,
                    min=1,
                    max=50,
                ),
                ConfigFieldSchema(
                    key="enable_tools",
                    label="启用工具",
                    type="boolean",
                    default=True,
                ),
            ],
        ),
        NodeTypeDefinition(
            type="sub_agent",
            label="子代理",
            category="ai",
            description="调用已配置的子代理（/profiles）执行任务，可独立模型与系统提示词",
            icon="users",
            color="#a855f7",
            inputs=[
                PortSchema(name="task", label="任务描述", type="string", required=True),
                PortSchema(name="context", label="上下文", type="string", required=False),
            ],
            outputs=[
                PortSchema(name="result", label="结果", type="string"),
                PortSchema(name="agent_name", label="子代理名", type="string"),
                PortSchema(name="model_ref", label="模型引用", type="string"),
            ],
            config_schema=[
                ConfigFieldSchema(
                    key="sub_agent_id",
                    label="子代理 ID",
                    type="text",
                    default="",
                    required=True,
                    description="来自 /profiles 子代理列表的 UUID；画布左侧卡片拖入时自动填充",
                ),
                ConfigFieldSchema(
                    key="sub_agent_name",
                    label="子代理名称",
                    type="text",
                    default="",
                    description="展示用名称（可选，运行时以 ID 为准）",
                ),
                ConfigFieldSchema(
                    key="max_steps",
                    label="最大步数",
                    type="number",
                    default=5,
                    min=1,
                    max=50,
                ),
                ConfigFieldSchema(
                    key="append_system_prompt",
                    label="追加系统提示",
                    type="textarea",
                    default="",
                    description="拼在子代理 system_prompt 之后（可选）",
                ),
            ],
        ),
        NodeTypeDefinition(
            type="rag",
            label="RAG",
        category="ai",
        description="检索增强生成：从知识库检索相关内容后生成回答",
        icon="database",
        color="#06b6d4",
        inputs=[
            PortSchema(name="query", label="查询", type="string", required=True),
            PortSchema(name="filter", label="过滤条件", type="string", required=False),
        ],
        outputs=[
            PortSchema(name="answer", label="回答", type="string"),
            PortSchema(name="sources", label="来源", type="list"),
        ],
        config_schema=[
            ConfigFieldSchema(
                key="top_k",
                label="检索条数",
                type="number",
                default=5,
                min=1,
                max=20,
            ),
            ConfigFieldSchema(
                key="threshold",
                label="相似度阈值",
                type="slider",
                default=0.7,
                min=0.0,
                max=1.0,
                step=0.05,
            ),
            ConfigFieldSchema(
                key="rerank",
                label="重排序",
                type="boolean",
                default=True,
            ),
        ],
    ),
    # ── Utility ──
    NodeTypeDefinition(
        type="python",
        label="Python",
        category="utility",
        description="执行 Python 代码片段",
        icon="code",
        color="#3b82f6",
        inputs=[
            PortSchema(name="input_data", label="输入数据", type="any", required=False),
        ],
        outputs=[
            PortSchema(name="output", label="输出", type="any"),
            PortSchema(name="stdout", label="标准输出", type="string"),
            PortSchema(name="stderr", label="标准错误", type="string"),
        ],
        config_schema=[
            ConfigFieldSchema(
                key="code",
                label="Python 代码",
                type="code",
                default="# 变量 input_data 包含上游输入\nresult = input_data\n",
                required=True,
                description="可用变量: input_data, context。返回 dict 会映射到各输出端口",
            ),
        ],
    ),
    NodeTypeDefinition(
        type="http",
        label="HTTP 请求",
        category="utility",
        description="发送 HTTP 请求",
        icon="globe",
        color="#ec4899",
        inputs=[
            PortSchema(name="body", label="请求体", type="any", required=False),
            PortSchema(name="headers", label="请求头", type="object", required=False),
        ],
        outputs=[
            PortSchema(name="response", label="响应", type="any"),
            PortSchema(name="status", label="状态码", type="number"),
        ],
        config_schema=[
            ConfigFieldSchema(
                key="method",
                label="请求方法",
                type="select",
                default="GET",
                options=[
                    {"label": "GET", "value": "GET"},
                    {"label": "POST", "value": "POST"},
                    {"label": "PUT", "value": "PUT"},
                    {"label": "DELETE", "value": "DELETE"},
                ],
            ),
            ConfigFieldSchema(
                key="url",
                label="请求地址",
                type="text",
                default="",
                required=True,
            ),
            ConfigFieldSchema(
                key="timeout",
                label="超时(秒)",
                type="number",
                default=30,
                min=1,
                max=300,
            ),
        ],
    ),
    # ── Logic ──
    NodeTypeDefinition(
        type="condition",
        label="条件判断",
        category="logic",
        description="根据条件分支到不同路径",
        icon="git-branch",
        color="#f97316",
        inputs=[
            PortSchema(name="input", label="输入", type="any", required=True),
        ],
        outputs=[
            PortSchema(name="true", label="真", type="any"),
            PortSchema(name="false", label="假", type="any"),
        ],
        config_schema=[
            ConfigFieldSchema(
                key="condition",
                label="条件表达式",
                type="text",
                default="",
                required=True,
                description="支持变量: input, context。例如: input == 'yes' 或 len(input) > 0",
            ),
        ],
    ),
    NodeTypeDefinition(
        type="loop",
        label="循环",
        category="logic",
        description="对列表中的每个元素执行循环体",
        icon="refresh-cw",
        color="#ef4444",
        inputs=[
            PortSchema(name="items", label="列表", type="list", required=True),
        ],
        outputs=[
            PortSchema(name="item", label="当前项", type="any"),
            PortSchema(name="index", label="索引", type="number"),
            PortSchema(name="results", label="结果列表", type="list"),
        ],
        config_schema=[
            ConfigFieldSchema(
                key="batch_size",
                label="批量大小",
                type="number",
                default=1,
                min=1,
                max=100,
            ),
        ],
    ),
    NodeTypeDefinition(
        type="merge",
        label="合并",
        category="logic",
        description="合并多个输入为列表或对象",
        icon="combine",
        color="#84cc16",
        inputs=[
            PortSchema(name="a", label="A", type="any", required=False),
            PortSchema(name="b", label="B", type="any", required=False),
            PortSchema(name="c", label="C", type="any", required=False),
        ],
        outputs=[
            PortSchema(name="list", label="列表", type="list"),
            PortSchema(name="object", label="对象", type="object"),
        ],
        config_schema=[
            ConfigFieldSchema(
                key="mode",
                label="合并模式",
                type="select",
                default="list",
                options=[
                    {"label": "列表", "value": "list"},
                    {"label": "对象", "value": "object"},
                ],
            ),
        ],
    ),
    # ── Custom ──
    NodeTypeDefinition(
        type="custom",
        label="自定义",
        category="custom",
        description="用户自定义功能的节点",
        icon="settings",
        color="#6b7280",
        inputs=[
            PortSchema(name="input", label="输入", type="any", required=False),
        ],
        outputs=[
            PortSchema(name="output", label="输出", type="any"),
        ],
        config_schema=[
            ConfigFieldSchema(
                key="custom_type",
                label="自定义类型名",
                type="text",
                default="",
                required=True,
                description="此自定义节点的类型标识",
            ),
            ConfigFieldSchema(
                key="description",
                label="功能描述",
                type="textarea",
                default="",
            ),
            ConfigFieldSchema(
                key="code",
                label="执行代码",
                type="code",
                default="# 自定义逻辑\n# 输入: input, context\n# 输出: 返回 dict 映射到输出端口\nreturn {'output': input}\n",
                required=True,
            ),
            ConfigFieldSchema(
                key="inputs_json",
                label="输入端口定义(JSON)",
                type="json",
                default='[{"name":"input","label":"输入","type":"any"}]',
                description='JSON数组，每个元素为 {"name", "label", "type"}',
            ),
            ConfigFieldSchema(
                key="outputs_json",
                label="输出端口定义(JSON)",
                type="json",
                default='[{"name":"output","label":"输出","type":"any"}]',
                description='JSON数组，每个元素为 {"name", "label", "type"}',
            ),
        ],
    ),
]


# 节点类型索引
NODE_TYPE_MAP: dict[str, NodeTypeDefinition] = {n.type: n for n in NODE_TYPE_DEFINITIONS}


def get_node_type_definition(node_type: str) -> NodeTypeDefinition | None:
    """根据类型名获取节点定义"""
    return NODE_TYPE_MAP.get(node_type)


def get_all_node_type_definitions() -> list[NodeTypeDefinition]:
    """获取所有节点类型定义"""
    return NODE_TYPE_DEFINITIONS.copy()


def get_node_types_by_category() -> dict[str, list[NodeTypeDefinition]]:
    """按分类获取节点类型"""
    result: dict[str, list[NodeTypeDefinition]] = {}
    for nt in NODE_TYPE_DEFINITIONS:
        result.setdefault(nt.category, []).append(nt)
    return result
