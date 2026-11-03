# term:系统配置  doc:doc/功能/会话网关/说明.md
"""Web 系统配置：产品 FAWAI_* 读写仓库根 .env，并热更新进程环境。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def env_file_path() -> Path:
    """与 ``config.load_dotenv`` 一致：包上一级仓库根 ``.env``。"""
    return Path(__file__).resolve().parents[2] / ".env"


@dataclass(frozen=True)
class SettingField:
    key: str
    group: str
    label: str
    kind: str  # str | bool | int
    default: str = ""
    secret: bool = False
    needs_restart: bool = False
    hint: str = ""
    options: tuple[str, ...] = ()


GROUPS: tuple[tuple[str, str], ...] = (
    ("llm", "LLM"),
    ("runtime", "运行与大脑"),
    ("gateway", "网关与保活"),
    ("frp", "frp 穿透"),
    ("relay", "云端中继"),
    ("coding", "编码闭环"),
    ("tool_obs", "工具观察压缩"),
)

FIELDS: tuple[SettingField, ...] = (
    SettingField(
        "FAWAI_LLM_BASE_URL",
        "llm",
        "API Base URL",
        "str",
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        hint="OpenAI 兼容接口根路径",
    ),
    SettingField(
        "FAWAI_LLM_API_KEY",
        "llm",
        "API Key",
        "str",
        secret=True,
        hint="留空保存表示不改；可点清空",
    ),
    SettingField(
        "FAWAI_LLM_TEXT_MODEL",
        "llm",
        "文本模型",
        "str",
        default="qwen3.7-plus",
    ),
    SettingField(
        "FAWAI_LLM_VLM_MODEL",
        "llm",
        "多模态模型",
        "str",
        default="qwen3-vl-plus",
    ),
    SettingField(
        "FAWAI_LLM_ASR_MODEL",
        "llm",
        "语音转写模型",
        "str",
        default="qwen3-asr-flash",
        hint="对话框语音输入；OpenAI 兼容 input_audio",
    ),
    SettingField(
        "FAWAI_LLM_MODEL",
        "llm",
        "强制模型（可选）",
        "str",
        hint="有值时覆盖智能路由",
    ),
    SettingField("FAWAI_DRY_RUN", "runtime", "干跑", "bool", default="0", hint="1=不真点键鼠/shell"),
    SettingField("FAWAI_DATA_DIR", "runtime", "数据目录", "str", default="data"),
    SettingField("FAWAI_WORK_ROOT", "runtime", "工作区根", "str", default="."),
    SettingField("FAWAI_AUTO_VERSION", "runtime", "写入前自动快照", "bool", default="1"),
    SettingField(
        "FAWAI_HEADLESS",
        "runtime",
        "无界面",
        "str",
        hint="空=自动探测；1=强制无界面；0=强制按有桌面",
        needs_restart=True,
        options=("","0","1"),
    ),
    SettingField("FAWAI_MAX_STEPS", "runtime", "最大步数", "int", default="0", hint="0=不限制"),
    SettingField("FAWAI_BRAIN_MOCK", "runtime", "大脑 Mock", "bool", default="0"),
    SettingField("FAWAI_STEP_REPEAT", "runtime", "步骤重复守卫", "bool", default="1"),
    SettingField("FAWAI_STEP_REPEAT_N", "runtime", "重复触发次数 N", "int", default="2", hint="最小 2"),
    SettingField(
        "FAWAI_EXPLORE_ONLY_GATE",
        "runtime",
        "探索进度闸门",
        "int",
        default="8",
        hint="连续只读探索注入约束；0=关",
    ),
    SettingField(
        "FAWAI_EXPLORE_ONLY_ABORT",
        "runtime",
        "探索闸门中止",
        "str",
        default="",
        hint="空=2×GATE；0=只注入不止损；填整数覆盖",
    ),
    SettingField(
        "FAWAI_GATEWAY_HOST",
        "gateway",
        "监听地址",
        "str",
        default="0.0.0.0",
        needs_restart=True,
    ),
    SettingField(
        "FAWAI_GATEWAY_PORT",
        "gateway",
        "端口",
        "int",
        default="7980",
        needs_restart=True,
    ),
    SettingField(
        "FAWAI_GATEWAY_PASSCODE",
        "gateway",
        "会话口令",
        "str",
        secret=True,
        needs_restart=True,
        hint="留空保存不改；清空后下次启动随机",
    ),
    SettingField("FAWAI_GATEWAY_CAPTURE", "gateway", "任务截屏观察", "bool", default="0"),
    SettingField(
        "FAWAI_NO_KEEPER",
        "gateway",
        "关闭保活外层",
        "bool",
        default="0",
        needs_restart=True,
        hint="1=调试时不套 keeper",
    ),
    SettingField(
        "FAWAI_CODE_RELOAD",
        "gateway",
        "代码热重载",
        "bool",
        default="1",
        needs_restart=True,
    ),
    SettingField(
        "FAWAI_PORT_RECLAIM",
        "gateway",
        "端口回收",
        "bool",
        default="1",
        needs_restart=True,
    ),
    SettingField(
        "FAWAI_FRP_SERVER_ADDR",
        "frp",
        "frps 地址",
        "str",
        needs_restart=True,
    ),
    SettingField(
        "FAWAI_FRP_SERVER_PORT",
        "frp",
        "frps 端口",
        "int",
        default="37000",
        needs_restart=True,
    ),
    SettingField(
        "FAWAI_FRP_REMOTE_PORT",
        "frp",
        "远端映射端口",
        "int",
        needs_restart=True,
    ),
    SettingField(
        "FAWAI_FRP_ENABLE",
        "frp",
        "启用穿透",
        "str",
        hint="空=配齐地址则启用；0=强制关",
        needs_restart=True,
        options=("","0","1"),
    ),
    SettingField(
        "FAWAI_FRPC_BIN",
        "frp",
        "frpc 二进制路径",
        "str",
        needs_restart=True,
        hint="Linux 勿用仓库 darwin 版",
    ),
    SettingField(
        "FAWAI_FRP_LOCAL_IP",
        "frp",
        "本地 IP",
        "str",
        default="127.0.0.1",
        needs_restart=True,
    ),
    SettingField("FAWAI_RELAY_URL", "relay", "中继 URL", "str", needs_restart=True),
    SettingField(
        "FAWAI_RELAY_DEVICE_TOKEN",
        "relay",
        "设备令牌",
        "str",
        secret=True,
        needs_restart=True,
    ),
    SettingField("FAWAI_RELAY_PAIR_CODE", "relay", "配对码", "str", needs_restart=True),
    SettingField(
        "FAWAI_DIFF_MODE",
        "coding",
        "Diff 模式",
        "str",
        default="auto",
        options=("auto", "confirm"),
    ),
    SettingField("FAWAI_NATIVE_TOOLS", "coding", "原生 tool_calls", "bool", default="1"),
    SettingField(
        "FAWAI_FULL_WRITE_LINE_LIMIT",
        "coding",
        "整写行数上限",
        "int",
        default="80",
    ),
    SettingField("FAWAI_TOOL_OBS_SOFT_CHARS", "tool_obs", "软压缩字符", "int", default="4000"),
    SettingField("FAWAI_TOOL_OBS_HARD_CHARS", "tool_obs", "硬压缩字符", "int", default="12000"),
    SettingField("FAWAI_TOOL_OBS_HEAD_LINES", "tool_obs", "头行数", "int", default="40"),
    SettingField("FAWAI_TOOL_OBS_TAIL_LINES", "tool_obs", "尾行数", "int", default="80"),
    SettingField("FAWAI_TOOL_OBS_KEYWORD_HITS", "tool_obs", "关键字命中数", "int", default="40"),
)

_FIELD_BY_KEY = {f.key: f for f in FIELDS}
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def parse_dotenv_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("#"):
            continue
        value = value.strip().strip("'").strip('"')
        if key:
            out[key] = value
    return out


def read_dotenv_map(path: Path | None = None) -> dict[str, str]:
    path = path or env_file_path()
    if not path.is_file():
        return {}
    return parse_dotenv_text(path.read_text(encoding="utf-8"))


def effective_value(key: str, *, disk: dict[str, str] | None = None) -> str:
    """进程环境优先，其次磁盘 .env，再次 schema 默认。"""
    if key in os.environ:
        return os.environ.get(key, "")
    disk = disk if disk is not None else read_dotenv_map()
    if key in disk:
        return disk[key]
    field = _FIELD_BY_KEY.get(key)
    return field.default if field else ""


def _quote_env_value(value: str) -> str:
    if value == "":
        return ""
    if any(ch in value for ch in ' \t#"\'\\'):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def merge_dotenv_file(path: Path, updates: dict[str, str | None]) -> None:
    """更新 .env：保留注释与无关行；``None`` 表示删除该键行。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()

    pending = dict(updates)
    out: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(raw)
            continue
        key, _ = stripped.split("=", 1)
        key = key.strip()
        if key not in pending:
            out.append(raw)
            continue
        val = pending.pop(key)
        if val is None:
            continue
        out.append(f"{key}={_quote_env_value(val)}")

    for key, val in pending.items():
        if val is None:
            continue
        out.append(f"{key}={_quote_env_value(val)}")

    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def apply_process_env(updates: dict[str, str | None]) -> None:
    for key, val in updates.items():
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val


def _normalize_bool(raw: str) -> str:
    v = (raw or "").strip().lower()
    if v in {"1", "true", "yes", "on"}:
        return "1"
    if v in {"0", "false", "no", "off", ""}:
        return "0"
    raise ValueError(f"invalid bool: {raw!r}")


def _normalize_value(field: SettingField, raw: str) -> str:
    if field.kind == "bool":
        return _normalize_bool(raw)
    if field.kind == "int":
        s = (raw or "").strip()
        if s == "" and field.default != "":
            return field.default
        try:
            return str(int(s))
        except ValueError as exc:
            raise ValueError(f"{field.key} must be int") from exc
    if field.options and raw.strip() not in field.options and raw.strip() != "":
        # allow empty when empty is in options
        if "" in field.options and raw.strip() == "":
            return ""
        if raw.strip() not in field.options:
            raise ValueError(f"{field.key} must be one of {list(field.options)}")
    return raw if isinstance(raw, str) else str(raw)


def get_settings_payload() -> dict[str, Any]:
    disk = read_dotenv_map()
    groups = [{"id": gid, "label": label} for gid, label in GROUPS]
    values: dict[str, Any] = {}
    fields_out: list[dict[str, Any]] = []
    for field in FIELDS:
        eff = effective_value(field.key, disk=disk)
        set_flag = bool(str(eff).strip()) if field.secret else True
        item: dict[str, Any] = {
            "key": field.key,
            "group": field.group,
            "label": field.label,
            "kind": field.kind,
            "default": field.default,
            "secret": field.secret,
            "needs_restart": field.needs_restart,
            "hint": field.hint,
            "options": list(field.options),
        }
        fields_out.append(item)
        if field.secret:
            values[field.key] = {
                "set": bool(str(eff).strip()),
                "value": "",
                "display": "••••••••" if str(eff).strip() else "",
            }
        else:
            values[field.key] = {
                "set": set_flag,
                "value": eff,
                "display": eff,
            }
    return {
        "ok": True,
        "env_path": str(env_file_path()),
        "groups": groups,
        "fields": fields_out,
        "values": values,
    }


def update_settings(
    values: dict[str, Any] | None = None,
    clear: list[str] | None = None,
) -> dict[str, Any]:
    """
    写入 .env 并热更进程环境。

    - ``values``：键 → 字符串；secret 键仅在提供非空串时更新
    - ``clear``：显式清空的键（从 .env 删除并 pop 进程环境）
    """
    values = values or {}
    clear_set = {str(k).strip() for k in (clear or []) if str(k).strip()}
    updates: dict[str, str | None] = {}
    changed_restart = False
    errors: list[str] = []

    disk = read_dotenv_map()

    def _current(key: str) -> str:
        return effective_value(key, disk=disk)

    for key in clear_set:
        field = _FIELD_BY_KEY.get(key)
        if field is None:
            errors.append(f"unknown key: {key}")
            continue
        if _current(key) == "" and key not in os.environ and key not in disk:
            continue
        updates[key] = None
        if field.needs_restart:
            changed_restart = True

    for key, raw in values.items():
        key = str(key).strip()
        if key in clear_set:
            continue
        field = _FIELD_BY_KEY.get(key)
        if field is None:
            errors.append(f"unknown key: {key}")
            continue
        if not _KEY_RE.match(key):
            errors.append(f"invalid key: {key}")
            continue
        if raw is None:
            if _current(key) == "" and key not in os.environ and key not in disk:
                continue
            updates[key] = None
            if field.needs_restart:
                changed_restart = True
            continue
        text = raw if isinstance(raw, str) else str(raw)
        if field.secret and text.strip() == "":
            # 空提交：不改
            continue
        try:
            norm = _normalize_value(field, text)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        # HEADLESS / FRP_ENABLE：空串表示删除强制项，回自动
        if field.key in {"FAWAI_HEADLESS", "FAWAI_FRP_ENABLE"} and norm == "":
            new_val: str | None = None
        else:
            new_val = norm
        cur = _current(key)
        if new_val is None:
            if cur == "" and key not in os.environ and key not in disk:
                continue
        elif new_val == cur:
            # 与生效值相同：不必写盘（避免无改仍 needs_restart）
            continue
        updates[key] = new_val
        if field.needs_restart:
            changed_restart = True

    if errors:
        return {"ok": False, "errors": errors, "needs_restart": False}

    if not updates:
        payload = get_settings_payload()
        payload["needs_restart"] = False
        payload["updated"] = []
        return payload

    path = env_file_path()
    merge_dotenv_file(path, updates)
    apply_process_env(updates)

    if "FAWAI_HEADLESS" in updates:
        try:
            from fawai_avatar.platform_info import clear_platform_cache

            clear_platform_cache()
        except Exception:
            pass

    payload = get_settings_payload()
    payload["needs_restart"] = changed_restart
    payload["updated"] = sorted(updates.keys())
    return payload
