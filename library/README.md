# 共享成品目录

当前索引为空，尚未公开收录用户图书。这里只接收权利依据清楚、经贡献者明确同意并经维护者接收审核的纯 Markdown 成品。

```text
library/
  index.json
  artifacts/<内容版本ID>/
    manifest.json
    content.md
    README.md
    machine.md       # 有有效手洗成稿时提供
    handwashed.md    # 可选，清单绑定内容与手洗范围
```

`index.json` 中 `approved` 必须由维护者审核后写入，投稿清单自称已审核不生效。每条记录至少含 id、书名、版本、输入 PDF SHA-256、页数、繁简目标、质量标签、manifest 的相对路径和 SHA-256。索引不接收指向目录外部的路径。

同一输入 PDF、繁简目标相同、覆盖完整输入且正文及页段校验通过，才可直接复用。完整输入可能只是用户截取的一卷或几页，必须同时阅读 `scope_description`；同名书或同版不同扫描仅列候选，不自动替换底本。复用已核稿也不会冒充当前用户已亲自核查。

代码与正文采用不同许可范围，见 [本目录许可说明](LICENSE.md)。完整贡献流程见 [贡献指南](../CONTRIBUTING.md) 和 [共享设计及风险](../docs/sharing.md)。

仓库公开且地址确定后，可用 `TEXTFERRY_CATALOG` 指向本索引的 GitHub raw 地址。建议固定到已审核 commit；未绑定时 Skill 继续支持本地转换。客户端读取小索引、按需下载成品，不要求克隆全资料库。
