# TextFerry

*An agent skill for document conversion and AI-assisted proofreading.*

供可运行脚本的 Agent 使用的通用 Skill，把中文论文、图书和古籍的 **PDF / EPUB** 转成可回查的全文。你提供材料和选择，当前 Agent 负责准备环境、运行和整理。

## 第一次使用

1. 在 [Releases](https://github.com/Ryde-Yang/textferry/releases) 下载并解压安装包，把文件夹交给当前 Agent，说：**“安装这个 TextFerry Skill，检查能否正常使用。”**
2. 安装通过离线自检后，提供 PDF 或 EPUB，例如：**“处理这本书的前十页，先让我选处理方式和格式。”**
3. Agent 先解释两档区别，询问处理方式；再询问格式，优先推荐 Markdown，长材料再确认范围。选择 AI 整理校正时，开工前还会说明并询问是否显示来源页码（EPUB 为章节标记）。已经说清的选择直接沿用。
4. 等待结果；需要扫描识别时，Agent 才引导你配置自己的百度访问令牌，填在本机，无需发到聊天。

| 处理方式 | 你会拿到什么 | 适合什么时候 |
|---|---|---|
| 基础转换 | 机器底稿，保留文字、可提取图示和来源，列出未完成内容；未经逐页校正 | 先搜索、浏览材料 |
| AI 整理校正 | 机器底稿＋对照来源校正后的整理稿＋中文整理说明，检查正文、顺序、公式、注释和结构，耗时更多 | 需要连续阅读、继续整理或回查引用 |

输出可选 **Markdown** 或 **Word**，默认推荐 Markdown。Markdown 便于后续整理复用，公式需要支持数学显示的阅读器；Word 适合直接阅读编辑。复杂 Word 公式可能以清晰图片保留，交付时会说明。

整理稿是否显示来源页码，由你在开工前选择：显示便于定位和回查，隐藏便于连续阅读，两种都保留来源对应关系。这与 Word 重新排版后的页码不同。EPUB 以章节定位，可选择显示章节标记。未能确认的内容会保留位置，并在《整理说明》中直接解释。


## 成果文件

AI 整理档主要交付：**整理稿**（你选择的格式）、**机器底稿.md**、**整理说明.md**。基础转换直接给基础稿；Markdown 底稿内容相同时复用一份文件。正文图片、来源与恢复记录由工具收在工作目录中。

请保留、移动整个成果文件夹，方便回查和后续追加 Word。工具会清理已完成任务的上传副本和可重建页图，新任务只保留必要的来源 PDF；选页任务默认保留摘页与原页号对应。用户改过的交付稿会被保护，不会被重导出覆盖。

## 继续已有任务

直接告诉当前 Agent：

> 继续上次没完成的转换，保留已经处理的内容。

> 把现有结果再给我一份 Word。

> 在这份基础稿上做 AI 整理校正。

> 我改过这份稿，请保留修改后继续。

追加格式和升级整理会复用已有文字。遇到拥堵或中断时保留进度，说明下一步；用户直接修改的文件不会被静默覆盖。

## 安装方式

需要能读文件、运行命令的 Agent 执行环境和 Python 3.10+；本机或已配置的远程环境均可。环境准备交给当前 Agent 即可；也可在解压目录运行：

```bash
python3 install.py
```

安装器准备运行环境并执行离线自检，通过后才报告可用。默认使用共享的 `~/.agents/skills/textferry`；可用 `--agent` 选择宿主目录，例如 `python3 install.py --agent claude-code`；也可用 `--target` 指定位置。更新使用 `python3 install.py --update`，改写前保存旧文件副本；其他本地文件保留。只复制文件可用 `--no-setup`，此时尚未验证环境。

遇到环境问题可让当前 Agent 按 [运行与恢复说明](skills/textferry/references/runtime.md) 处理。当前主要验证环境是 macOS；Windows / Linux 尚未获得同等验证。

安装适配和所需能力见 [Agent 兼容说明](skills/textferry/references/agent-compatibility.md)。核心通过通用文件和 Python 命令运行；没有文件或执行能力的纯聊天环境需要另接执行服务。

## 能力与边界

- PDF 支持直接提取和扫描识别；带图文字页保留本地图示，复杂阅读顺序与图表内容由整理档对照处理。
- EPUB 支持无保护的文字书、图片、可解析的公式、表格和语义脚注。纯扫描、受保护或交互式 EPUB 有明确限制，见 [EPUB 说明](skills/textferry/references/epub.md)。
- 来源、机器底稿和整理稿分别保留。自动检查与 AI 校正的实际范围会说明，研究引用仍需作版本判断。
- 需要共享或复用时，见 [共享规则](docs/sharing.md)。公开权利与具体版本的分享意愿分别核对。

[整理示例](examples/dual-output/README.md) · [处理规范](skills/textferry/references/material-processing.md) · [验证记录](docs/verification.md) · [开发说明](docs/development.md) · [发布审核](docs/release-review-0.9.0.md)

代码采用 [AGPL-3.0-only](LICENSE)，第三方依赖见 [许可说明](THIRD_PARTY_NOTICES.md)。材料全文按各自权利与许可处理。
