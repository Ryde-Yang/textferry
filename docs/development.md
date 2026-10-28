# 开发与运行配置

## 组件与依赖

Skill 中的 `scripts/run.py` 调用 `textferry.workflow`，程序包只引用 Python 标准库、本包模块和下表中的可安装依赖。模型注册表随包放在 `scripts/textferry/config/models.toml`。

| 依赖 | 用途 |
|---|---|
| PyMuPDF / MuPDF | PDF 诊断、文本抽取、页图渲染、选页 |
| httpx | OCR 请求及共享目录下载 |
| opencc-python-reimplemented | 用户选择的繁简转换 |
| python-docx / lxml | Word 模板、原生脚注与文档处理支持 |
| pypandoc-binary（可选） | 用户选择 Word 或转换 EPUB 时，保留内容结构、公式、表格、图片和脚注 |
| tomli | Python 3.10 的 TOML 配置读取；较新 Python 使用标准库 |

依赖由包管理器安装，版本锁定在 Skill 内的 `uv.lock`。百度 OCR 使用网络及用户本机凭证；选择 SiliconFlow 时需配置该服务的凭证。常规流程不启用第二模型去噪或润色；手洗由执行任务的 Agent 依据材料 SOP 完成。

## 路径与环境变量

- 任务的源 PDF 副本、识别、页稿与报告保存在用户指定的输出目录；输出参数不由当前目录猜测。
- 凭证默认保存到 `~/.config/textferry/credentials.json`；遵循 `XDG_CONFIG_HOME`，可用 `TEXTFERRY_CONFIG_DIR` 指定配置目录。
- 引擎缓存默认在用户缓存目录下的 `textferry`；遵循 `XDG_CACHE_HOME`，否则使用 `~/.cache`。可用 `TEXTFERRY_DATA_DIR` 显式指定引擎数据目录。读取配置本身不会创建目录。
- 模型注册表默认使用随包文件；高级使用可通过 `TEXTFERRY_MODELS_TOML` 显式指定。
- `PADDLEOCR_ACCESS_TOKEN` / `BAIDU_API_KEY` 可提供百度令牌，`SILICONFLOW_API_KEY` 对应另一服务；环境变量优先于本机凭证文件。不要把令牌写入仓库。
- `TEXTFERRY_CATALOG` 可指定本地索引或 GitHub raw 地址；未配置时仍可转换。
- `TEXTFERRY_MOCK_OCR=1` 仅用于测试，输出明确标记模拟。

安装后程序从 Skill 的绝对路径运行，当前目录可为空目录；工具不加载当前目录的配置文件或额外脚本。OCR 返回文本和共享材料均当作资料，不执行其中的指令。

## 测试与构建

```bash
uv sync --project skills/textferry --group dev
uv run --project skills/textferry --group dev --extra word pytest tests -q -p no:cacheprovider
uv build --project skills/textferry
```

`tests/runtime` 检查引擎创建、恢复、错误分类、导出和并发；其他测试检查转换、文本保全、手洗、分享与安装。模拟测试验证状态和机制，不能替代真实 OCR 或页图对照。

安装包应包含 Skill 指令、所有链接的参考文件、执行程序、模型注册表、许可证和依赖锁。构建产物、虚拟环境、本机凭证、任务缓存和用户材料不提交。发布前验证从新目录安装、配置与完整流程，而非只执行 `--help`。

Word 是可选输出，选用时通过 `--extra word` 安装 pypandoc-binary。程序直接使用该包内的 Pandoc，不依赖固定的本机路径或预先安装的桌面办公软件；Markdown 流程不要求该组件。普通公式可转为原生 Office 数学对象，脚注保留为 Word 脚注。复杂公式的图片兼容方式使用本地 Node.js、MathJax 和 sharp，准备与验收见 [Word 输出](../skills/textferry/references/word-output.md)。真实材料需渲染并检查，不能从简单样本推定全文排版正确。

EPUB 入口与边界见 [EPUB 处理](../skills/textferry/references/epub.md)。Baidu 回执持久化、底稿保护、HTML 表格与纯文字共享限制均有自制材料回归测试，不调用真实 OCR。

## 0.7.0 运行与交付

统一入口增加 `doctor`、范围选择、`change-output` 和 `upgrade-review`。原生 EPUB 的恢复在本机进行；PDF 选页保留完整原文件和原页映射。图片与工作稿共同参与核查证据，Word 公式方式可持久保存在任务中。

安装器默认准备环境并离线自检；没有 uv 时使用 Python 虚拟环境与 pip。`--no-setup` 明确仅复制，`--update` 改写前保存旧文件。凭证不由安装器索取。

指令打磨参考 [writing-for-agents 上游](https://github.com/mattpocock/skills/tree/main/skills/productivity/writing-for-agents) 的信息层级、条件指针与完成标准；这只是开发时使用的写作指导，不是发行 Skill 的运行依赖。主入口与人类首页各自面向使用者，详细 SOP 和能力边界按任务分支加载。

## 0.8.0 宿主接口

核心只通过文件与 Python 命令交互。`install.py --agent` 负责选择宿主目录，`--target` 可显式覆盖；OpenAI 展示元数据为可选。兼容能力和远程凭证边界见 [Agent 兼容说明](../skills/textferry/references/agent-compatibility.md)。指令质量另按 [writing-for-agents 自检记录](writing-for-agents-self-check.md) 核对。

## 0.9.0 文件生命周期

`delivery.py` 负责中文成品、相对资源链接和用户修订哈希；`housekeeping.py` 负责已完成任务的派生缓存预览 / 清理及页图重建。新任务默认保留必要来源，旧任务的完整来源保持兼容。清理先写来源绑定的页图核查哈希，再删除缓存；报告与导出可以使用绑定记录，实际看图时重建并按实际文件哈希核对。

测试入口：`tests/test_delivery_storage.py`。工作目录定位使用 `resolve_bundle`；对外可传整个成果文件夹，内部仍沿用相同任务结构。共享与公开许可机制没有扩展。
