# 首次使用引导

普通 Skill 安装器可能只复制文件，不执行安装后脚本。仓库安装脚本会返回引导；手工安装时，让用户说“使用 textferry 开始首次配置”。首次任务在确实需要 OCR 时检查配置，不依赖安装钩子；原生 EPUB 与可直接提取的 PDF 可以跳过令牌配置。

1. 转换任务先确认版本与输出格式；单独配置时只处理配置。已有配置用 `setup` 检查状态，新配置才用 `setup --prepare` 创建空白文件（已有文件不覆盖），返回配置状态和官方入口，不发出 OCR 请求。
2. 使用返回的 `configured`、`credential_source`、`execution_config_dir` 判断实际执行环境。`environment:变量名` 表示环境变量生效，`file:路径` 表示文件生效，`none` 表示未配置。已有环境变量即使文件为空也可继续；`configured_unverified` 仅表示存在凭证，尚未验证可用性。状态由程序读取，Agent 无需打开或打印凭证文件。
3. 未配置时告诉用户：“我们需要你自己的百度 AI Studio Access Token。打开官方页面登录账号，获取并复制访问令牌。我会打开本机配置文件，你把它填入引号内并保存，不需要发给我。”
4. 优先在用户可操作的本机终端打开 `configure` 隐藏输入；也可用文件面板打开返回的 `credentials_file`，让用户只填写 `baidu_async` 的引号内容。配置由程序读取，Agent 不把内容显示回聊天。
5. 保存后执行 `setup` 检查状态。让用户在另一个终端运行 `configure` 时，显式使用返回的同一 `execution_config_dir` 设置 `TEXTFERRY_CONFIG_DIR`；远程环境使用宿主凭证配置入口。这样填写位置与程序读取位置一致。只描述当前状态，不推测剪贴板或用户保存操作出了什么问题。
6. 已有密钥时用用户选定的少量页开始验证；无材料时可用自制样本。实际成功后继续约定范围；认证失败再核对令牌，服务拥堵则保留任务并说明恢复方式。不要把本机已配置说成已联网验证。

## 官方入口与凭证

- [百度 AI Studio Access Token](https://aistudio.baidu.com/account/accessToken)：未登录会转到百度登录页。
- [PaddleOCR 官方 API Python SDK](https://www.paddleocr.ai/main/version3.x/inference_deployment/serving/paddleocr_official_api/python.html)：安装与认证章节指向以上页面。
- 本链路用 Bearer 令牌调用 PaddleOCR 官方异步服务。`baidu_async` 配置和兼容的 `BAIDU_API_KEY` 存放上述 Access Token；也支持官方环境变量名称 `PADDLEOCR_ACCESS_TOKEN`。
- 无需创建传统 OCR 应用的另一套 AppID / API Key / Secret Key。
- 账号页面按钮、额度和收费可能变化，以官网为准；不承诺固定免费额度。已有令牌可直接使用。

官方入口核对日期：2026-10-27。链接或界面变化时重新核对官方文档，不猜按钮位置。

## 保存位置与执行环境

在实际运行命令的环境配置。本机终端、远程工作区和云端容器可能各有独立配置；远程环境可使用宿主的凭证注入或用户可操作的配置文件。只输出存在与否，不读取令牌正文回显。


默认 `~/.config/textferry/credentials.json`，遵循 `XDG_CONFIG_HOME`；测试可用 `TEXTFERRY_CONFIG_DIR` 指定独立目录。macOS / Linux 文件权限为 `600`。这是本机明文配置，不宣称使用钥匙串或加密保存。

环境变量优先于文件。换了文件令牌仍认证失败时，检查是否有旧环境变量，只输出存在与否。实际 OCR 成功才说明该次调用可用。

`configure --key-file /path/token.txt` 可从已有本机文件导入。密钥不放入命令行参数、项目、报告或示例。
