# 按范围校核

本表用于登记实际检查，不替代 [材料处理 SOP](material-processing.md)。校核整理需按材料流程产出手洗稿，而非仅为转换稿添加记录。

来源 PDF 是裁决底本。机器输出、去噪结果和其他版本只用于帮助发现问题。保全字形、顺序、正文、引文、注释、题辞和参考文献；疑字挂待核，不凭语义猜补。实际查看页图，不只比较两份机器文本。

| 项目 | 完成标准 |
|---|---|
| body | 逐段检查漏字、重复、误识和段首段尾；保持原文 |
| order | 确认单栏、双栏或竖排方向，以及正文、跨页阅读顺序 |
| notes | 核对正文标记与脚注、尾注或注疏归属；不确定时保留并标待核 |
| structure | 标题、引文、原文和注释层次与底本对应，避免互相吞并 |

只核一项可以，但记录为部分核查；四项都做过仍有疑难，也要记录疑难。实际回图的页才能提交记录。

按论文、图书、古籍或教材校核时，采用 [材料处理 SOP](material-processing.md) 中对应类型的规则。这里统一定义核查项目和记录机制。

## 记录

编辑 `review/pages/NNNN.md`，通过 `review-page` 记录修复和疑难；`document.review.md` 是汇总输出。文字、来源或页图变化后，旧记录不能代表当前版本。`report` 重算范围。记录由执行者提交，不等于独立专家复核，也不自动授予学术引用准出。

说明含公式、美元符号、反引号或引号时，先用文件工具保存 UTF-8 说明，再用 `--note-file /path/note.txt`，避免命令行解释其内容。`review-page` 与 `export-handwash` 都支持该入口。

成稿导出：`prepare-handwash` 建立范围和边界记录，按页手洗、核查后 `export-handwash --page-numbers hide|show --reviewer NAME --note TEXT`。最终主文本是 `handwashed.md`，映射是 `handwashed.sources.json`；`document.review.md` 仍是中间工作稿。

EPUB 的裁决底本是原 EPUB；对照章节来源和关联图片。`--page N` 在此指第 N 个内容单元，具体存储与能力边界见 [EPUB](epub.md)。
