# GitHub 成品共享：首版设计与边界

## 能省什么

转换前先查一份小索引，找到适用于同一 PDF 的已审核成品后直接导入，省去重复 OCR 和部分重复整理。仍需版本确认和用户要求的核查，不能承诺 Agent tokens 完全为零。代码、索引和纯 Markdown 均可先放同一 GitHub 项目，客户端只下载所需成品。

首版实现：本地贡献包、按具体版本和目标仓库绑定的同意/拒绝记录、已审核索引检索、带清单/正文/页段哈希验证的复用。不含自动上传；仓库地址未确定，线上索引尚未绑定，`library/index.json` 为空。本地材料默认保留本地，公开贡献须逐份确认。

## 公开的前提

用户同意只能授权本次操作，不能代替第三方著作权。中国著作权法的数字化复制、网络传播、具有独创性的整理与注释等规则都相关；个人研究例外不能简单推导出向 GitHub 全文公开。古籍原文、古注与现代整理、校勘、译文、序跋等要按具体版本分开判断。扫描是否产生独立新权利、个别点校是否具有独创性，也需具体判断，不能一概断言。[国家版权局公布的著作权法](https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html)

代码采用 AGPL，不自动改变输入或普通格式转换输出的权利状态。底本权利和贡献者新增内容的许可逐份记录，同仓库也明确排除“正文全部 AGPL”的理解。[GNU：程序输出](https://www.gnu.org/licenses/gpl-faq.en.html#GPLOutput)、[GitHub 内容及贡献条款](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service#d-user-generated-content)

未查清权利的成果完全保留本地。公开前展示实际文件、书名版本、全本或节选范围、核查范围、许可和目标仓库；同意绑定整个具体内容版本，拒绝不影响使用。原 PDF、页图、个人笔记、密钥、日志和私人路径不进入包。

## GitHub 风险

纯 Markdown 适合作为起点，但 GitHub 不消除侵权、隐私或内容治理责任。公开内容可能被复制、fork、索引及依平台条款处理；当前文件撤下不保证收回历史或其他人的副本，已有内容许可也可能不可撤销。[GitHub 删除敏感信息的边界](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)、[Creative Commons 许可前说明](https://creativecommons.org/cc-license-your-work/)

常规 Git 单文件超过 50 MiB 会警告，超过 100 MiB 会阻止；仓库理想小于 1 GB、强烈建议小于 5 GB 是建议值。首版工具另设正文 20 MiB 上限；小章节更利于查看和审核。先不把 PDF、页图或全量运行缓存放 Git，也不默认使用 LFS。[GitHub 官方大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)

共享稿含错误或恶意指令时，哈希只能保证取得的是某个版本，不能保证内容真实、作者可信或可合法公开。外部 Markdown 始终当资料处理，官方目录的接收审核与学术核稿分别标注。随着资料增多，需要管理维护者审稿、争议撤下和 Git 历史体量；若未来超出同仓库可维护范围，仍可在 GitHub 内拆资料库，索引保持统一。

## 复用规则

同一 PDF SHA-256、同一繁简目标、覆盖输入全部页且校验通过，才直接复用。不同扫描或不同版次只按书名列候选。完整输入 PDF 可能只是节选，须用 `scope_description` 明示；首版没有跨扫描自动映射或自动拼全书能力。后来的用户可直接读底稿，也可继续本地核查，既有核查声明不会自动变为本人的核查记录。

没有本地 PDF 时仍可按书名查询、选择版本并下载已有 Markdown；这能满足“我需要这本书，直接取已完成稿”的用途。此时报告明确尚无本地 PDF 对应关系。要按页回查自己的底本，再采用精确匹配或另作版本核对。

依据核对日期：2026-10-27。单份材料的实际权利、维护者身份、复杂许可条件及跨法域状态，仍需在接收时核实。
