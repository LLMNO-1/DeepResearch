import os
from functools import lru_cache
from typing import Annotated

from pydantic import AnyUrl, BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_str_to_none(v: object) -> object:
    """将空字符串转换为 None，避免 AnyUrl 等类型校验失败。"""
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


#: 可选 URL 类型 —— 当环境变量为空字符串时自动转为 None
OptionalUrl = Annotated[AnyUrl | None, BeforeValidator(_empty_str_to_none)]


class Settings(BaseSettings):
    """系统配置对象，负责从环境变量读取后端运行所需的基础配置。

    输入来自默认值、环境变量和项目根目录的 ``.env`` 文件；输出为业务模块可
    直接读取的类型化配置。该类只维护基础设施和模型参数，不保存任何业务状态。

    所有字段均可通过大写环境变量覆盖，例如 ``MONGODB_URI`` 对应
    ``mongodb_uri``。``.env`` 文件中的空值会被安全地解析为 ``None``。
    """

    app_name: str = "AI 研究报告工作台"
    app_version: str = "0.1.0"
    environment: str = "local"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "deep_research"
    redis_url: str = "redis://localhost:6379/0"

    llm_provider: str = "openai"
    llm_model_name: str = "gpt-4.1-mini"
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    openai_api_base: OptionalUrl = None
    openai_api_key: str | None = None
    deepseek_api_base: OptionalUrl = None
    deepseek_api_key: str | None = None
    enable_ragflow:bool = False
    ragflow_base_url: OptionalUrl = None
    ragflow_api_key: str | None = None
    tavily_api_key: str | None = None

    object_storage_endpoint: str | None = None
    object_storage_bucket: str = "deep-research"
    object_storage_access_key: str | None = None
    object_storage_secret_key: str | None = None
    report_storage_backend: str = "local"
    report_storage_local_dir: str = "reports"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

#保证第一次调用后缓存，后续都返回同一个对象
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取系统配置，负责为应用生命周期提供缓存后的配置对象。

    该函数没有输入参数，返回当前进程内唯一的 Settings 实例，避免每次请求重复读取
    环境变量和 `.env` 文件。
    """

    settings = Settings()
    _export_to_env(settings)
    return settings


def _export_to_env(settings: Settings) -> None:
    """将 LLM 密钥等关键配置注入 os.environ，供 LangChain 等直接读取环境变量的库使用。"""

    # 强制 UTF-8，解决 Windows 下 asyncio/LangGraph 内部 ASCII 编码报错
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    for field_name, env_var in [
        ("openai_api_base", "OPENAI_API_BASE"),
        ("openai_api_key", "OPENAI_API_KEY"),
        ("deepseek_api_base", "DEEPSEEK_API_BASE"),
        ("deepseek_api_key", "DEEPSEEK_API_KEY"),
        ("tavily_api_key", "TAVILY_API_KEY"),
    ]:
        value = getattr(settings, field_name, None)
        if value is not None:
            os.environ.setdefault(env_var, str(value))
