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


def dotenv_path(path: str | Path | None = None) -> Path:
    """工程根 ``.env`` 路径（与 ``channel.system_settings.env_file_path`` 一致）。"""
    if path is None:
        return Path(__file__).resolve().parents[1] / ".env"
    return Path(path)


def parse_dotenv_pairs(path: Path) -> list[tuple[str, str]]:
    """解析 ``.env`` 为 ``(key, value)``；忽略空行与注释。"""
    if not path.is_file():
        return []
    out: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key.startswith("#"):
            continue
        value = value.strip().strip("'").strip('"')
        out.append((key, value))
    return out


def overlay_dotenv(
    env: dict[str, str],
    path: str | Path | None = None,
) -> dict[str, str]:
    """把磁盘 ``.env`` 覆盖写入 ``env``（Web 已写盘的键以磁盘为准）。

    保活外层长期存活时，父进程 ``os.environ`` 可能仍是启动时的旧值；
    拉起子进程前须用本函数叠加磁盘，避免模型等配置「重启回退」。
    """
    for key, value in parse_dotenv_pairs(dotenv_path(path)):
        env[key] = value
    return env


def load_dotenv(
    path: str | Path | None = None,
    *,
    override: bool = False,
) -> Path | None:
    """加载工程根 ``.env``。

    - ``override=False``（默认）：不覆盖已有环境变量（导入时软加载）。
    - ``override=True``：磁盘键覆盖进程环境（网关/保活入口启动用，与 Web 写盘一致）。
    """
    resolved = dotenv_path(path)
    if not resolved.is_file():
        return None
    for key, value in parse_dotenv_pairs(resolved):
        if override or key not in os.environ:
            os.environ[key] = value
    return resolved


# 导入配置时自动尝试加载一次（不覆盖已有环境；入口再 override）
load_dotenv()


def _env_float(name: str) -> float | None:
    raw = _env(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_int(name: str, *, default: int | None = None) -> int | None:
    raw = _env(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class LlmConfig:
    """接入凭证 + 路由模型名。真源为 ``llm_settings.sqlite``（见 ``llm.settings_store``）。"""

    api_key: str | None
    base_url: str | None
    text_model: str
    vlm_model: str
    asr_model: str
    fast_model: str | None = None
    force_model: str | None = None
    timeout_sec: float | None = None

    @property
    def model(self) -> str:
        """兼容旧字段：强制模型优先，否则默认文本模型。"""
        return self.force_model or self.text_model

    @classmethod
    def from_store(cls) -> "LlmConfig":
        """从本机 LLM 设置库组装兼容视图（单供应商聚合仅作兜底展示）。"""
        from fawai_avatar.llm.settings_store import get_store

        store = get_store()
        text = store.resolve_route("text")
        vlm = store.resolve_route("vlm")
        asr = store.resolve_route("asr")
        fast = store.resolve_route("fast")
        force = store.resolve_route("force")
        embed = store.resolve_route("embed")
        # 默认凭证取文本路由；无则退到任一已配置 Key 的供应商
        primary = text if text.has_key else None
        if primary is None:
            for role in ("vlm", "fast", "asr", "embed", "force"):
                ep = store.resolve_route(role)
                if ep.has_key:
                    primary = ep
                    break
        if primary is None:
            primary = text
        return cls(
            api_key=(primary.api_key or None),
            base_url=(primary.base_url or None),
            text_model=text.model or "qwen3.7-plus",
            vlm_model=vlm.model or "qwen3-vl-plus",
            asr_model=asr.model or "",
            fast_model=fast.model or None,
            force_model=force.model or None,
            timeout_sec=primary.timeout_sec,
        )

    @classmethod
    def from_env(cls) -> "LlmConfig":
        """兼容旧名：实际读 SQLite 设置库（含首次从 .env 迁移）。"""
        return cls.from_store()

    def chat_sampling_kwargs(self, model: str | None = None) -> dict[str, float | int]:
        """按选定模型读取档案；无档案则不传采样参数。"""
        from fawai_avatar.llm.model_profiles import sampling_kwargs_for_model

        mid = (model or self.model or "").strip()
        if not mid:
            return {}
        return sampling_kwargs_for_model(mid)


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


def lore_auto_p1_kv(*, default: bool = False) -> bool:
    """
    P1 键值是否自动入库：`FAWAI_LORE_AUTO_P1_KV`。
    默认关 → 进缓冲待确认；P0 仍直写。开：1/true。
    """
    load_dotenv()
    raw = _env("FAWAI_LORE_AUTO_P1_KV")
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
    # auto：任一供应商已配置 Key 则开
    try:
        from fawai_avatar.llm.settings_store import get_store

        return any(p.get("has_key") for p in get_store().list_providers())
    except Exception:
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
    from fawai_avatar.llm.settings_store import get_store

    mid = get_store().resolve_route("embed").model.strip()
    return mid or "text-embedding-v3"


@dataclass(frozen=True)
class RuntimeConfig:
    dry_run: bool
    data_dir: str
    lore_dir: str
    lore_sidecar: bool
    lore_llm_extract: bool
    lore_vector: bool
    work_root: str
    read_roots: tuple[str, ...]
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
        from fawai_avatar.workspace.paths import parse_read_roots

        dry = (_env("FAWAI_DRY_RUN") or "0").lower() in {"1", "true", "yes", "on"}
        data_dir = _env("FAWAI_DATA_DIR") or "data"
        lore_dir = resolve_lore_dir(data_dir=data_dir)
        work_root = _env("FAWAI_WORK_ROOT") or "."
        # 未设置时默认 `/`：启动即申请整盘只读；设 FAWAI_READ_ROOTS=work 可收回仅 work_root
        read_root_paths = parse_read_roots(_env("FAWAI_READ_ROOTS"))
        read_roots = tuple(str(p) for p in read_root_paths)
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
            read_roots=read_roots,
            auto_version=auto_version,
            diff_mode=diff_mode,
            native_tools=native_tools,
            full_write_line_limit=max(1, full_limit),
            max_steps=max_steps_from_env(default=0),
            headless=is_headless(),
        )
