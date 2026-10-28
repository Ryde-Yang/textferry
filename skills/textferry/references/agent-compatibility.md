# Agent 兼容与安装

本 Skill 采用 [Agent Skills 格式](https://agentskills.io/specification)，核心由 `SKILL.md`、参考文件和 Python 命令组成。可用能力取决于执行环境，而不是模型品牌。

## 运行能力

- **基础转换**：Agent 能读取文件、执行命令，并能使用 Python 3.10+。依赖安装需要包下载条件；原生文字提取可以离线，扫描 OCR 需要网络及用户令牌。
- **AI 整理校正**：Agent 能读取完整来源；扫描页、图片和复杂版式的核对还需要图像读取能力。图像能力不足时，完成可核实的文本工作，明确保留未核查范围和具体问题。
- **Word 显示验收**：使用当前环境可用的预览或渲染方式。只有生成文件而未完成显示检查时，直接说明实际完成程度。

没有命令执行或文件访问能力的纯聊天环境，需要另接执行服务。受限云端环境能否安装依赖、访问网络和保留任务，由宿主权限决定；本 Skill 不改动宿主权限。

## 安装位置

让当前 Agent 运行项目根目录的安装器并完成自检。`--agent` 选择宿主约定的用户级目录，`--target` 可覆盖为自定义或项目级位置。具体目录以 `install.py --help` 的输出为准。

```bash
python3 install.py --agent codex
python3 install.py --agent claude-code
python3 install.py --agent cursor
python3 install.py --agent gemini-cli
python3 install.py --agent generic --target "/path/to/skills/textferry"
```

用于宿主自动发现时，目标 Skill 文件夹使用 `textferry` 名称；放在其他目录时可显式读取 `SKILL.md` 使用。默认 `generic` 使用共享的 `.agents/skills` 位置。不同宿主的发现方式并不完全相同，不能把“文件已复制”当作宿主已经加载；安装后确认当前 Agent 已发现本 Skill，再执行 `doctor`。更新时沿用同一宿主或目标位置，并加 `--update`。

宿主未原生支持 Skill 时，可让它读取安装目录的 `SKILL.md`，再按相对路径读取需要的参考文件并执行命令。正文不依赖某个宿主的工具名称或聊天指令前缀。`agents/openai.yaml` 仅供 OpenAI/Codex 展示，删除该文件不影响核心执行。

## 凭证与远程执行

令牌应配置在实际执行命令的环境中。本机可以由用户在终端隐藏输入，或用自己的编辑器填写配置；远程环境可使用宿主的凭证注入能力或用户可操作的配置文件。本机存在配置，不代表远程环境已配置。

程序只返回配置状态。Agent 无需读取令牌正文，也不需要把令牌作为聊天消息或普通命令参数传递。具体步骤见 [令牌配置](onboarding.md)。

## 已验证与未逐一试用

已核对各宿主官方安装位置，并测试目录适配、独立命令执行及缺少 OpenAI 元数据时的运行。核心转换沿用同一程序。Claude Code、Cursor、Gemini CLI 的真实对话体验尚未逐一验收；不能据此声称所有宿主和权限配置都已实测。

目录依据（2026-10-28）：[Codex / Agent Skills](https://learn.chatgpt.com/docs/build-skills)、[Claude Code](https://code.claude.com/docs/en/skills)、[Cursor](https://cursor.com/docs/skills)、[Gemini CLI](https://geminicli.com/docs/cli/skills/)。
