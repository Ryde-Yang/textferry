# EPUB 转换与整理

支持无保护的 EPUB 2 / 3，以包内 spine 顺序处理 XHTML 章节。先用 `inspect input.epub` 检查。用户已选的档位、格式和范围不再重复问；EPUB 没有固定印刷页，按章节 / 内容单元定位，不报告虚构 PDF 页数。

## 安装和执行

EPUB 需要可选转换组件，由 Agent 执行：

```bash
uv run --project "$SKILL_DIR" --extra epub python "$SKILL_DIR/scripts/run.py" convert "/path/input.epub" --output "/path/new-bundle" --mode review --kind book --format markdown
```

选择 Word 时同时加 `--extra word` 并使用 `--format word`。这里两项共用同一个转换程序，不重复下载。原生 EPUB 文本全程在本机提取，不调用百度 OCR，不索取 OCR 令牌；AI 校正仍由当前 Agent 完成。

## 保全与核查

- 原文件保存在任务中，哈希用于确认底本；`source/chapters/NNNN.xhtml` 是移除样式与活动内容的本地对照副本，`assets/` 保存图片，`review/pages/NNNN.md` 是按章节编号的工作稿。
- 保留标题、段落、列表、可解析表格、图片、MathML 公式和具备注释语义的引用。跨章注释会跟随引用恢复为脚注；全书使用不同编号，防止合并冲突。
- 实际对照章节来源及关联图片，不把已生成工作稿当成已校正。`review-page --page N` 的 N 在 EPUB 中指第 N 个 spine 内容单元。内部兼容字段 `total_pages` / `pdf_page` 也指单元编号，`source_format=epub`、`source_unit=chapter` 用于区分；用户看到的文本使用 EPUB 节 / 章节。
- `prepare-handwash`、四项核查、注释配对及导出流程与 PDF 一致；按章节核定 `separate` / `join`。选用“显示页码”时显示 EPUB 节号，不能填造书页。底本、章节来源或图片改变后，相关核查失效。
- 书目资料按原文件核实，不从文件名猜作者或版本。样式可能改变，内容摘要与翻译另行处理。

## 当前边界

纯扫描 EPUB 不自动 OCR，需另给 PDF / 图片材料。文字书中的纯图片封面或空白单元保留并明确列出：基础档返回部分完成，不能声称已识别图中文字；整理档须实际核查并处理后才登记。缺失图片、断裂的语义注释链接、远程媒体、脚本或音视频会停止转换并说明原因。

带加密声明、字体混淆声明、多根文档、SVG 章节、畸形 XHTML 暂不支持。不破解保护。固定版式只能保留可提取内容，不复刻竖排、分栏或版面；普通链接形式、未标注语义的旧式尾注仍可能需要人工配对。CSS、嵌入字体、动画与音视频不属于成稿输出范围。

用 `--chapters 2-5` 选择原 EPUB 内容单元，先按章名核对目录；保留完整原文件和原编号映射，实际只提取所选内容。中断后 `resume` 继续本地提取，保留已完成文字及工作稿。公开共享目录目前仅支持纯文字 PDF 的同底本复用；EPUB 成果先保留本地，不伪装成 PDF 共享条目。

格式依据：[W3C EPUB 3.3](https://www.w3.org/TR/epub-33/)；结构转换使用 [Pandoc](https://pandoc.org/MANUAL.html)。本功能使用自制 EPUB 验证；没有用新真实书籍宣称普遍准确率。

内嵌和独立 SVG 在核对安全引用后保留原文件，并生成本地 PNG 供 Markdown / Word 一致显示。语义脚注在结构层编号，代码中的字面语法保持原样；返回正文链接保留引用锚点。
