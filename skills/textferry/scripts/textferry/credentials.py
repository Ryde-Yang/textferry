# SPDX-License-Identifier: AGPL-3.0-only
"""Local, user-owned OCR credentials; never return token values in status output."""

import json
import os
from pathlib import Path

TOKEN_URL = "https://aistudio.baidu.com/account/accessToken"
DOC_URL = "https://www.paddleocr.ai/main/version3.x/inference_deployment/serving/paddleocr_official_api/python.html"


def config_dir():
    explicit = os.getenv("TEXTFERRY_CONFIG_DIR")
    root = Path(explicit).expanduser() if explicit else Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "textferry"
    return root.resolve()


def credentials_path():
    return config_dir() / "credentials.json"


def load_api_key(provider):
    return effective_credential(provider)[0]


def effective_credential(provider):
    names = ("PADDLEOCR_ACCESS_TOKEN", "BAIDU_API_KEY") if provider == "baidu_async" else ("SILICONFLOW_API_KEY",)
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value, "environment:" + name
    path = credentials_path()
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get(provider, "")
        except (ValueError, AttributeError):
            raise ValueError("本机密钥配置格式有误，请重新打开配置文件检查。") from None
        if value is not None and not isinstance(value, str):
            raise ValueError("本机访问令牌应填写为引号内的文字，请检查配置格式。")
        value = (value or "").strip()
        return value, "file:" + str(path) if value else "none"
    return "", "none"


def save_api_key(key, provider="baidu_async"):
    key = key.strip()
    if not key or any(ch.isspace() for ch in key):
        raise ValueError("请粘贴完整的 Access Token，内容中不应包含空格或换行。")
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[provider] = key
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.chmod(temp, 0o600)
    temp.replace(path)
    return {"status": "configured_unverified", "credentials_file": str(path), "message": "密钥已保存在本机；尚未进行联网验证。"}


def onboarding(prepare=False):
    path = credentials_path()
    if prepare and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"baidu_async": ""}, stream, indent=2)
            stream.write("\n")
    key, credential_source = effective_credential("baidu_async")
    configured = bool(key)
    return {
        "status": "configured_unverified" if configured else "needs_setup",
        "configured": configured,
        "credential_source": credential_source,
        "execution_config_dir": str(config_dir()),
        "provider": "baidu_async",
        "token_url": TOKEN_URL,
        "official_docs": DOC_URL,
        "credentials_file": str(path),
        "steps": [
            "打开百度 AI Studio 的 Access Token 页面，登录你自己的百度账号。",
            "在账号页面获取或复制 Access Token；本工具使用这一访问令牌。",
            "在用户可操作的终端隐藏输入令牌，或用编辑器填写本机配置文件；无需发到聊天。",
            "运行单页自制示例验证，然后继续原任务。额度和收费以你账号页面的实时规则为准。",
        ],
    }
