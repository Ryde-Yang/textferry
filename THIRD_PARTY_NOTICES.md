# 许可与第三方依赖

TextFerry 代码采用 AGPL-3.0-only，完整条款见 LICENSE。材料全文和贡献内容按各自权利另行记录。

依赖通过包管理器安装，不把其二进制文件重新标记为本项目代码。以下为本轮实际安装版本的包元数据声明，完整条件以相应项目随包许可为准。

| 依赖 | 本轮版本 | 声明许可 |
|---|---|---|
| PyMuPDF / MuPDF | 1.28.2 | GNU AGPL 3.0 或 Artifex 商业许可 |
| httpx | 0.28.1 | BSD-3-Clause |
| opencc-python-reimplemented | 0.1.7 | Apache License |
| python-docx | 1.2.0 | MIT |
| lxml | 6.1.3 | BSD-3-Clause |
| anyio | 4.15.1 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |

Python 3.10 还使用 tomli（MIT）。测试工具为 pytest、PyYAML 及其开发依赖，不参与默认运行。精确版本及来源见 Skill 中的 `uv.lock`。

PyMuPDF 的许可来源：[官方 License and Copyright](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)。不捆绑 OCR 模型权重；百度或其他托管服务的使用仍遵循各自服务条款。

Word 与 EPUB 可选组件使用 [pypandoc-binary](https://github.com/JessicaTegner/pypandoc)（Python 包 MIT），其随包 Pandoc 为独立转换程序，保留 [Pandoc 许可](https://github.com/jgm/pandoc/blob/main/COPYING.md)。本项目发行源码不重新打包该二进制；由使用者通过可选依赖安装。原生公式导出行为见 [Pandoc 手册](https://pandoc.org/MANUAL.html)。

Word 的公式图片兼容方式另使用 [MathJax](https://github.com/mathjax/MathJax-src) 与 [sharp](https://github.com/lovell/sharp)，两者声明 Apache-2.0。它们由执行环境提供或安装到使用者缓存中，不随代码包分发二进制。sharp 所用图像库及其他间接依赖保留各自许可。
