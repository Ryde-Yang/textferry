# SPDX-License-Identifier: AGPL-3.0-only
"""独立工具配置对象、用户缓存路径、模型注册表与默认文本保全开关。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Dict

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from .types import ModelOption


PROVIDER_DEFAULT_MODEL = MappingProxyType({
    "baidu_async": "paddleocr_vl",
    "siliconflow": "deepseek_ocr",
})

PROVIDER_SUPPORTED_MODELS = MappingProxyType({
    "baidu_async": frozenset({"paddleocr_vl", "paddleocr_vl_16"}),
    "siliconflow": frozenset({"deepseek_ocr", "paddleocr_vl"}),
})


@dataclass(frozen=True)
class TextFerrySettings:
    data_dir: Path
    models_toml_path: Path
    max_pdf_size_bytes: int
    max_pdf_pages: int
    batch_size: int
    max_concurrency: int
    max_retries: int
    ocr_base_url: str
    ocr_timeout_seconds: float
    ocr_provider: str
    baidu_ocr_base_url: str
    baidu_connect_timeout_seconds: float
    baidu_submit_read_timeout_seconds: float
    baidu_poll_read_timeout_seconds: float
    baidu_result_read_timeout_seconds: float
    baidu_ocr_poll_seconds: float
    baidu_ocr_job_timeout_seconds: float
    baidu_min_split_pages: int
    baidu_batch_size: int
    baidu_batch_concurrency: int
    default_model_option: str
    enable_model_denoise: bool
    denoise_provider: str
    denoise_model_option: str
    enable_agent_footnote_resolution: bool
    agent_footnote_model_option: str
    enable_flow_model_polish: bool
    flow_polish_model_option: str
    enable_reader_docx: bool
    preserve_source_text: bool = True


@dataclass(frozen=True)
class ModelRegistry:
    default_option: str
    options: Dict[str, ModelOption]


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_mock_ocr_enabled() -> bool:
    return _bool_env("TEXTFERRY_MOCK_OCR", False)


def _default_data_dir() -> Path:
    cache_home = os.getenv("XDG_CACHE_HOME")
    cache_root = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return (cache_root / "textferry").resolve()


@lru_cache(maxsize=1)
def get_textferry_settings() -> TextFerrySettings:
    module_dir = Path(__file__).resolve().parent

    explicit_data_dir = os.getenv("TEXTFERRY_DATA_DIR")
    data_dir = Path(explicit_data_dir).expanduser().resolve() if explicit_data_dir else _default_data_dir()
    models_toml_path = Path(
        os.getenv("TEXTFERRY_MODELS_TOML", str(module_dir / "config" / "models.toml"))
    ).expanduser().resolve()

    settings = TextFerrySettings(
        data_dir=data_dir,
        models_toml_path=models_toml_path,
        max_pdf_size_bytes=_int_env("TEXTFERRY_MAX_PDF_BYTES", 200 * 1024 * 1024),
        max_pdf_pages=_int_env("TEXTFERRY_MAX_PDF_PAGES", 10000),
        batch_size=_int_env("TEXTFERRY_BATCH_SIZE", 10),
        max_concurrency=_int_env("TEXTFERRY_MAX_CONCURRENCY", 6),
        max_retries=_int_env("TEXTFERRY_MAX_RETRIES", 5),
        ocr_base_url=os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1/chat/completions"),
        ocr_timeout_seconds=_float_env("TEXTFERRY_OCR_TIMEOUT_SECONDS", 120.0),
        ocr_provider=os.getenv("TEXTFERRY_OCR_PROVIDER", "baidu_async").strip().lower(),
        baidu_ocr_base_url=os.getenv("TEXTFERRY_BAIDU_OCR_BASE_URL", "https://paddleocr.aistudio-app.com").rstrip("/"),
        baidu_connect_timeout_seconds=_float_env("BAIDU_OCR_CONNECT_TIMEOUT_SECONDS", 30.0),
        baidu_submit_read_timeout_seconds=_float_env("BAIDU_OCR_SUBMIT_READ_TIMEOUT_SECONDS", 180.0),
        baidu_poll_read_timeout_seconds=_float_env("BAIDU_OCR_POLL_READ_TIMEOUT_SECONDS", 30.0),
        baidu_result_read_timeout_seconds=_float_env("BAIDU_OCR_RESULT_READ_TIMEOUT_SECONDS", 180.0),
        baidu_ocr_poll_seconds=_float_env("BAIDU_OCR_POLL_SECONDS", 2.0),
        baidu_ocr_job_timeout_seconds=_float_env("BAIDU_OCR_JOB_TIMEOUT_SECONDS", 600.0),
        baidu_min_split_pages=max(1, _int_env("TEXTFERRY_BAIDU_MIN_SPLIT_PAGES", 2)),
        baidu_batch_size=max(1, _int_env("TEXTFERRY_BAIDU_BATCH_SIZE", 10)),
        baidu_batch_concurrency=max(1, _int_env("TEXTFERRY_BAIDU_BATCH_CONCURRENCY", 1)),
        default_model_option="paddleocr_vl",
        enable_model_denoise=_bool_env("TEXTFERRY_ENABLE_MODEL_DENOISE", True),
        denoise_provider=os.getenv("TEXTFERRY_DENOISE_PROVIDER", "siliconflow").strip().lower(),
        denoise_model_option=os.getenv("TEXTFERRY_DENOISE_MODEL_OPTION", "deepseek_ocr").strip(),
        enable_agent_footnote_resolution=_bool_env("TEXTFERRY_ENABLE_AGENT_FOOTNOTE_RESOLUTION", True),
        agent_footnote_model_option=os.getenv("TEXTFERRY_AGENT_FOOTNOTE_MODEL_OPTION", "deepseek_ocr").strip(),
        enable_flow_model_polish=_bool_env("TEXTFERRY_ENABLE_FLOW_MODEL_POLISH", True),
        flow_polish_model_option=os.getenv("TEXTFERRY_FLOW_POLISH_MODEL_OPTION", "deepseek_ocr").strip(),
        enable_reader_docx=_bool_env("TEXTFERRY_ENABLE_READER_DOCX", False),
        preserve_source_text=_bool_env("TEXTFERRY_PRESERVE_SOURCE_TEXT", True),
    )
    return settings


@lru_cache(maxsize=1)
def get_model_registry() -> ModelRegistry:
    settings = get_textferry_settings()
    if not settings.models_toml_path.exists():
        raise FileNotFoundError(f"models.toml not found: {settings.models_toml_path}")

    raw = tomllib.loads(settings.models_toml_path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults") or {}
    default_option = str(defaults.get("model_option") or settings.default_model_option)

    options_raw = raw.get("model_options")
    if not isinstance(options_raw, dict) or not options_raw:
        raise ValueError("models.toml missing [model_options.*] definitions")

    options: Dict[str, ModelOption] = {}
    for key, value in options_raw.items():
        if not isinstance(value, dict):
            continue
        model_id = str(value.get("model_id") or "").strip()
        prompt_template = str(value.get("prompt_template") or "").strip()
        if not model_id or not prompt_template:
            raise ValueError(f"model option '{key}' missing model_id or prompt_template")
        display_name = str(value.get("display_name") or key)
        raw_stop_tokens = value.get("stop_tokens")
        stop_tokens = ()
        if raw_stop_tokens is not None:
            if not isinstance(raw_stop_tokens, list) or not all(isinstance(item, str) for item in raw_stop_tokens):
                raise ValueError(f"model option '{key}' stop_tokens must be a string list")
            stop_tokens = tuple(item for item in raw_stop_tokens if item.strip())
        options[key] = ModelOption(
            key=key,
            display_name=display_name,
            model_id=model_id,
            prompt_template=prompt_template,
            stop_tokens=stop_tokens,
        )

    if default_option not in options:
        default_option = sorted(options.keys())[0]
    return ModelRegistry(default_option=default_option, options=options)


def get_provider_default_model(provider: str) -> str:
    return PROVIDER_DEFAULT_MODEL.get(provider.strip().lower(), "deepseek_ocr")


def is_model_supported_for_provider(provider: str, model_key: str) -> bool:
    supported = PROVIDER_SUPPORTED_MODELS.get(provider.strip().lower())
    if supported is None:
        return True
    return model_key in supported
