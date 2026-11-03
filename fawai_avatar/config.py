# term:LLM 接入  doc:doc/功能/LLM接入/说明.md
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def load_dotenv(path: str | Path | None = None) -> Path | None:
    """加载工程根 `.env`（不覆盖已有环境变量）。返回实际加载路径。"""
    if path is None:
        root = Path(__file__).resolve().parents[1]
        path = root / ".env"
    else:
        path = Path(path)
    if not path.is_file():
        return None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value
    return path


# 导入配置时自动尝试加载一次
load_dotenv()


@dataclass(frozen=True)
class LlmConfig:
    api_key: str | None
    base_url: str | None
    text_model: str
    vlm_model: str
    asr_model: str
    force_model: str | None = None

    @property
    def model(self) -> str:
        """兼容旧字段：强制模型优先，否则默认文本模型。"""
        return self.force_model or self.text_model

    @classmethod
    def from_env(cls) -> "LlmConfig":
        load_dotenv()
        return cls(
            api_key=_env("FAWAI_LLM_API_KEY") or _env("OPENAI_API_KEY"),
            base_url=_env("FAWAI_LLM_BASE_URL") or _env("OPENAI_BASE_URL"),
            text_model=_env("FAWAI_LLM_TEXT_MODEL") or "qwen3.7-plus",
            vlm_model=_env("FAWAI_LLM_VLM_MODEL") or "qwen3-vl-plus",
            asr_model=_env("FAWAI_LLM_ASR_MODEL") or "qwen3-asr-flash",
            force_model=_env("FAWAI_LLM_MODEL") or _env("OPENAI_MODEL"),
        )


def max_steps_from_env(*, default: int = 0) -> int:
    """工作任务最大步数：`FAWAI_MAX_STEPS`；**0（默认）= 不限制**。"""
    load_dotenv()
    raw = _env("FAWAI_MAX_STEPS")
    if raw is None or raw == "":
        return int(default)
    try:
        return max(0, int(raw))
    except ValueError:
        return int(default)


def resolve_lore_dir(
    *,
    data_dir: str | None = None,
    lore_dir: str | None = None,
) -> str:
    """
    个人知识库根目录：`FAWAI_LORE_DIR` 优先；否则 `{FAWAI_DATA_DIR}/lore`。
    代码在 `fawai_lore/`，数据默认落在项目 `data/lore/`。
    """
    load_dotenv()
    explicit = lore_dir if lore_dir is not None else _env("FAWAI_LORE_DIR")
    if explicit:
        return str(Path(explicit).expanduser())
    base = data_dir if data_dir is not None else (_env("FAWAI_DATA_DIR") or "data")
    return str(Path(base).expanduser() / "lore")


def lore_sidecar_enabled(*, default: bool = True) -> bool:
    """全程旁路长记忆：`FAWAI_LORE_SIDECAR`；默认开。"""
    load_dotenv()
    raw = _env("FAWAI_LORE_SIDECAR")
    if raw is None or raw == "":
        return bool(default)
    return raw.lower() in {"1", "true", "yes", "on"}


def lore_llm_extract_enabled() -> bool:
    """
    旁路是否调用 LLM 补抽：`FAWAI_LORE_LLM_EXTRACT`。
    默认 auto：有 API key 则开；0/1 强制关/开。
    """
    load_dotenv()
    raw = (_env("FAWAI_LORE_LLM_EXTRACT") or "auto").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    # auto
    key = _env("FAWAI_LLM_API_KEY") or _env("OPENAI_API_KEY")
    return bool(key)


def lore_vector_enabled(*, default: str = "auto") -> bool:
    """
    笔记向量检索（sqlite-vec）：`FAWAI_LORE_VECTOR`。
    默认 auto：已安装 sqlite-vec 则开；0/1 强制关/开。
    """
    load_dotenv()
    raw = (_env("FAWAI_LORE_VECTOR") or str(default)).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    # auto
    try:
        from fawai_lore.vector import sqlite_vec_available

        return bool(sqlite_vec_available())
    except Exception:
        return False


def lore_embed_model() -> str:
    load_dotenv()
    return _env("FAWAI_LORE_EMBED_MODEL") or "text-embedding-v3"


@dataclass(frozen=True)
class RuntimeConfig:
    dry_run: bool
    data_dir: str
    lore_dir: str
    lore_sidecar: bool
    lore_llm_extract: bool
    lore_vector: bool
    work_root: str
    auto_version: bool
    diff_mode: str
    native_tools: bool
    full_write_line_limit: int
    max_steps: int
    headless: bool

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        load_dotenv()
        from fawai_avatar.platform_info import is_headless

        dry = (_env("FAWAI_DRY_RUN") or "0").lower() in {"1", "true", "yes", "on"}
        data_dir = _env("FAWAI_DATA_DIR") or "data"
        lore_dir = resolve_lore_dir(data_dir=data_dir)
        work_root = _env("FAWAI_WORK_ROOT") or "."
        auto_version = (_env("FAWAI_AUTO_VERSION") or "1").lower() in {"1", "true", "yes", "on"}
        diff_mode = (_env("FAWAI_DIFF_MODE") or "auto").strip().lower()
        if diff_mode not in {"auto", "confirm"}:
            diff_mode = "auto"
        native_tools = (_env("FAWAI_NATIVE_TOOLS") or "1").lower() in {"1", "true", "yes", "on"}
        try:
            full_limit = int(_env("FAWAI_FULL_WRITE_LINE_LIMIT") or "80")
        except ValueError:
            full_limit = 80
        return cls(
            dry_run=dry,
            data_dir=data_dir,
            lore_dir=lore_dir,
            lore_sidecar=lore_sidecar_enabled(default=True),
            lore_llm_extract=lore_llm_extract_enabled(),
            lore_vector=lore_vector_enabled(default="auto"),
            work_root=work_root,
            auto_version=auto_version,
            diff_mode=diff_mode,
            native_tools=native_tools,
            full_write_line_limit=max(1, full_limit),
            max_steps=max_steps_from_env(default=0),
            headless=is_headless(),
        )
