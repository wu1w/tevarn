"""新手帮助 — 返回固定、安全、可理解的使用说明。"""

from ..base import BaseSkill

_TOPICS = {
    "start": """# 第一次用 Takton

1. 首页输入框直接打字提问，点「发送」（未开会话会自动连上）。
2. 想改电脑上的文件/命令：点侧栏「设备」，配对后说：`@设备名 你的命令`。
3. 想让它用知识库：先去「知识」上传文档，再说「根据知识库回答…」。
4. 工具条上的「深度思考 / 联网搜索」按需点；不懂可以先不点。
5. 黄条/未连接：看右上角是否「服务就绪」；有会话才需要 WebSocket。
""",
    "safety": """# 安全小白须知

1. 不要让 Agent 执行「删除整盘 / 格式化 / 改密码」除非你完全清楚后果。
2. 远程设备默认有危险命令拦截，但仍请用「只读目录」作 root。
3. 配对 token 不要发到公开群；泄露了就换一个。
4. 邮件/支付类操作务必人工确认。
""",
    "devices": """# 远程设备（@设备）

1. 在目标电脑运行 takton-agent（端口默认 19876）。
2. 本机「设备」页填写 host、端口、token → 配对。
3. 对话：`@remote-pc 磁盘还剩多少` 或 `@win-local dir`。
4. 设备页可看延迟、目录、跑命令。
""",
    "tools": """# 工具 vs Skill（通俗版）

- **工具 Tools**：Agent 的「手」——读文件、跑命令、打开网页。
- **Skill 技能**：包装好的常用动作——搜知识库、查天气、做 PPT。
- 你一般**不用手动选**；直接说人话，Agent 会自己挑。
- 要关闭某个能力：去「工具 / 技能」页关掉开关。
""",
    "examples": """# 可以直接复制的说法

- 帮我总结一下桌面上的会议纪要（先配对/指定路径）
- 北京明天天气怎么样，要带伞吗？
- @remote-pc 看看磁盘空间
- 根据知识库解释一下我们公司的请假流程
- 把下面这段话改成邮件语气：…
- 搜索一下 2026 年 OpenAI Agent 是什么
""",
}


class BeginnerHelpSkill(BaseSkill):
    name = "beginner_help"
    description = (
        "输出 Takton 新手说明（上手、安全、设备、工具区别、示例说法）。"
        "当用户说「我不会用」「怎么开始」「有什么功能」「小白教程」时调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": ["start", "safety", "devices", "tools", "examples", "all"],
                "description": "主题，默认 all",
                "default": "all",
            },
        },
        "required": [],
    }

    async def execute(self, topic: str = "all", **kwargs) -> str:
        topic = (topic or "all").strip().lower()
        if topic == "all":
            return "\n\n".join(_TOPICS[k] for k in ("start", "safety", "devices", "tools", "examples"))
        return _TOPICS.get(topic, _TOPICS["start"])
