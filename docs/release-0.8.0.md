# 0.8.0：通用 Agent 接入与指令自检

Skill 的核心指令、运行入口和令牌配置改为按通用能力描述。支持可读文件、可执行 Python 的 Agent；安装器可选择 Codex、Claude Code、Cursor、Gemini CLI 或自定义位置。同一套转换和整理程序供各宿主使用。

- `install.py --agent NAME` 选择宿主约定目录，`--target` 优先指定位置；更新与离线自检流程沿用。
- `agents/openai.yaml` 仅是可选的 OpenAI/Codex 展示信息，核心程序不读取它。
- 使用当前 Agent 的模型完成整理；文档不再把整理用量等同于 Codex 用量。
- 远程运行时，令牌配置在实际执行环境中；用户不需要把令牌发到聊天。
- 扫描页与图示的 AI 核对需要图像读取能力；纯聊天环境需要另接执行服务。受限环境按宿主权限运行。

本轮也完成 [writing-for-agents 逐项自检](writing-for-agents-self-check.md)，修正环境读取条件、基础 / 整理交付分支、共享邀请触发和重复规则来源。

具体安装与已验证范围见 [Agent 兼容说明](../skills/textferry/references/agent-compatibility.md)，验证结果见 [验证记录](verification.md)。宿主目录和通用执行验证不能替代各产品真实会话及真人新手试用。
