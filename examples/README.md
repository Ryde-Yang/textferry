# 可重建示例

执行 `skills/textferry/scripts/make_samples.py --output samples` 可生成自制中文论文、图书章节与竖排扫描样本；通过 Skill 的正常转换与校核流程运行。

本目录放置本次实际对照页图后的整理稿节选。示例不是完整书籍的质量基准，不包含私人材料。

- `classical-reviewed.md`：实际百度 OCR 后核查原文、注、疏三列，合并竖排逐字断行。
- `paper-reviewed.md`：保留繁体、公式、链接，配对有对应标记的脚注；没有对应标记的页底注保留并注明待核。

[双产物完整示例](dual-output/README.md)：`machine.md` 为按页机器底稿，`handwashed.md` 为无页码手洗稿，`handwashed-with-pages.md` 展示同一手洗稿的有页码版本。`content.md` 为共享复用所需的逐页中间文字。仅一页自制竖排样本，不能据此推断整本古籍质量。
