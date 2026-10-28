# TextFerry 0.9.1

2026-10-28。修正 GitHub 自动测试的公式渲染器配置，让五项可选集成测试在两个平台上实际运行。转换、校核、交付和缓存行为沿用 0.9.0。

首轮 0.9.0 工作流实际为每个平台 200 passed、5 skipped；本机为 205 passed、无跳过。0.9.1 的自动测试及构建结果以当前发布说明和 GitHub 工作流为准，不把准备了组件当作测试已经执行。

安装包为 `textferry-0.9.1.zip`。已有 TextFerry 安装在解压目录运行 `python3 install.py --update`，也可以让当前 Agent 完成更新和离线自检。
