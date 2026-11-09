# term:系统配置  term:模型配置  doc:doc/功能/会话网关/说明.md
"""Web 系统/模型配置：产品 FAWAI_* 读写仓库根 .env，并热更新进程环境。"""
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
    kind: str  # str | bool | int | float
    default: str = ""
    secret: bool = False
    needs_restart: bool = False
    hint: str = ""
    options: tuple[str, ...] = ()
    step: str = ""  # float 输入步进（前端提示）
    model_kind: str = ""  # text|vlm|asr|embed；配合 model_pick 在线列表过滤
    supports_params: bool = False  # chat 类可挂生成参数


# 模型 → llm_settings.sqlite；记忆 → memory_settings.sqlite；其余系统项仍写 .env
MODEL_GROUP_IDS: frozenset[str] = frozenset()
MODEL_ROUTE_KEYS: frozenset[str] = frozenset()
# Web「记忆配置」页专用；不进系统配置页；路径类 sqlite，数据目录仍写 .env
MEMORY_GROUP_IDS: frozenset[str] = frozenset({"memory", "memory_data"})

GROUPS: tuple[tuple[str, str], ...] = (
    ("memory", "工作区与可读区"),
    ("memory_data", "数据区"),
    ("runtime", "运行与大脑"),
    ("gateway", "网关与保活"),
    ("frp", "frp 穿透"),
    ("relay", "云端中继"),
    ("coding", "编码闭环"),
    ("tool_obs", "工具观察压缩"),
)

FIELDS: tuple[SettingField, ...] = (
    SettingField("FAWAI_WORK_ROOT", "memory", "工作区根", "str", default="."),
    SettingField(
        "FAWAI_READ_ROOTS",
        "memory",
        "可读区目录",
        "str",
        default="/",
        hint="冒号分隔；写仍限工作区；work=仅工作区",
    ),
    SettingField(
        "FAWAI_LEDGER_GATE_THRESHOLD",
        "memory",
        "价值台账晋升阈值（%）",
        "int",
        default="80",
        hint="成功/失败台账：完备·通用·效率三维均须≥该百分比才写入 lore；默认 80（约 1～5 分制的 4 分）",
    ),
    SettingField(
        "FAWAI_TASK_DISTILL",
        "memory",
        "终态条件短卡蒸馏",
        "bool",
        default="1",
        hint="开=多法成功或已明确失败时自动蒸一张短卡进台账/lore；关=不自动蒸馏（仍可手写台账）",
    ),
    SettingField(
        "FAWAI_CODE_INDEX",
        "memory",
        "代码符号索引",
        "bool",
        default="1",
        hint="开=code_symbols/code_map/刷新可用（含 HTML/CSS）；关=拒建索，改用 fs_grep",
    ),
    # 旁路短 KV（FAWAI_LORE_SIDECAR）暂时不在 Web 记忆配置页展示；后端默认仍关
    SettingField(
        "FAWAI_DATA_DIR",
        "memory_data",
        "数据目录",
        "str",
        default="data",
        needs_restart=True,
        hint="改后需重启",
    ),
    SettingField("FAWAI_DRY_RUN", "runtime", "干跑", "bool", default="0", hint="1=不真点键鼠/shell"),
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
        default="0",
        hint="连续只读软提示阈值；默认 0=关（不因只读步数逼写）",
    ),
    SettingField(
        "FAWAI_EXPLORE_ONLY_ABORT",
        "runtime",
        "探索改写强制",
        "str",
        default="",
        hint="空/0=关；填整数才启用实验性强制改写轮",
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
        default="0",
        hint="默认关；服务与版本页可开关；有执行中任务时延后重启",
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
    SettingField(
        "FAWAI_TOOL_OBS_SOFT_CHARS",
        "tool_obs",
        "软压缩字符",
        "int",
        default="0",
    ),
    SettingField(
        "FAWAI_TOOL_OBS_MAX_CHARS",
        "tool_obs",
        "观察绝对上限",
        "int",
        default="48000",
    ),
    SettingField("FAWAI_TOOL_OBS_HARD_CHARS", "tool_obs", "压缩后硬顶", "int", default="12000"),
    SettingField(
        "FAWAI_TOOL_OBS_HITS_SOFT_CHARS",
        "tool_obs",
        "检索 hits 软压缩字符",
        "int",
        default="0",
    ),
    SettingField("FAWAI_TOOL_OBS_HEAD_LINES", "tool_obs", "头行数", "int", default="40"),
    SettingField("FAWAI_TOOL_OBS_TAIL_LINES", "tool_obs", "尾行数", "int", default="80"),
    SettingField("FAWAI_TOOL_OBS_KEYWORD_HITS", "tool_obs", "关键字命中数", "int", default="40"),
    SettingField(
        "FAWAI_SHELL_MAX_STDOUT_CHARS",
        "tool_obs",
        "shell 输出上限",
        "int",
        default="120000",
    ),
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
        if s == "":
            return ""
        try:
            return str(int(s))
        except ValueError as exc:
            raise ValueError(f"{field.key} must be int") from exc
    if field.kind == "float":
        s = (raw or "").strip()
        if s == "":
            return ""
        try:
            return str(float(s))
        except ValueError as exc:
            raise ValueError(f"{field.key} must be float") from exc
    if field.options and raw.strip() not in field.options and raw.strip() != "":
        # allow empty when empty is in options
        if "" in field.options and raw.strip() == "":
            return ""
        if raw.strip() not in field.options:
            raise ValueError(f"{field.key} must be one of {list(field.options)}")
    return raw if isinstance(raw, str) else str(raw)


def normalize_scope(scope: str | None) -> str:
    """``system`` | ``model`` | ``memory`` | ``all``。"""
    s = (scope or "all").strip().lower()
    if s in {"system", "model", "memory", "all"}:
        return s
    raise ValueError(f"invalid scope: {scope!r}")


def _group_in_scope(group_id: str, scope: str) -> bool:
    if scope == "all":
        return True
    is_model = group_id in MODEL_GROUP_IDS
    is_memory = group_id in MEMORY_GROUP_IDS
    if scope == "model":
        return is_model
    if scope == "memory":
        return is_memory
    # system：排除模型组与记忆配置组（后者只在记忆配置页）
    return not is_model and not is_memory


def _selected_route_models(disk: dict[str, str] | None = None) -> list[str]:
    del disk  # 兼容旧签名
    from fawai_avatar.llm.settings_store import get_store

    models: list[str] = []
    seen: set[str] = set()
    for role, r in get_store().list_routes().items():
        if role not in {"text", "vlm", "fast", "force"}:
            continue
        mid = str(r.get("model_id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        models.append(mid)
    return models


def get_settings_payload(*, scope: str = "all") -> dict[str, Any]:
    scope_n = normalize_scope(scope)
    disk = read_dotenv_map()
    mem_store = None
    mem_values: dict[str, Any] = {}
    if scope_n in {"memory", "all"}:
        from fawai_avatar.memory_settings import get_memory_store

        mem_store = get_memory_store()
        mem_values = mem_store.settings_payload_values()
    groups = [
        {"id": gid, "label": label}
        for gid, label in GROUPS
        if _group_in_scope(gid, scope_n)
    ]
    values: dict[str, Any] = {}
    fields_out: list[dict[str, Any]] = []
    from fawai_avatar.memory_settings import MEMORY_KEYS as _MEM_KEYS

    for field in FIELDS:
        if not _group_in_scope(field.group, scope_n):
            continue
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
            "step": field.step,
            "model_pick": False,
            "model_kind": field.model_kind,
            "supports_params": bool(field.supports_params),
        }
        if field.key in _MEM_KEYS:
            item["storage"] = "sqlite"
        elif field.group in MEMORY_GROUP_IDS:
            item["storage"] = "env"
        fields_out.append(item)
        if field.key in _MEM_KEYS:
            values[field.key] = mem_values.get(
                field.key,
                {"set": True, "value": field.default, "display": field.default},
            )
            continue
        eff = effective_value(field.key, disk=disk)
        set_flag = bool(str(eff).strip()) if field.secret else True
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
    payload: dict[str, Any] = {
        "ok": True,
        "scope": scope_n,
        "env_path": str(env_file_path()),
        "groups": groups,
        "fields": fields_out,
        "values": values,
    }
    if mem_store is not None and scope_n in {"memory", "all"}:
        payload["memory_db_path"] = mem_store.db_path
        payload["index_policy"] = mem_store.index_policy_payload()
        if scope_n == "memory":
            payload["env_path"] = mem_store.db_path
            payload["storage"] = "sqlite+env(data_dir)"
    if scope_n == "model":
        # 模型配置已迁 /api/llm/settings；此处仅占位避免旧前端报错
        from fawai_avatar.llm.settings_store import get_store

        payload["llm_settings"] = get_store().settings_payload()
        payload["hint"] = "use /api/llm/settings"
    return payload


def update_settings(
    values: dict[str, Any] | None = None,
    clear: list[str] | None = None,
    *,
    scope: str = "all",
    model_profiles: dict[str, Any] | None = None,
    index_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    系统字段写入 .env；记忆字段写入 ``memory_settings.sqlite``；模型页还可写档案。

    - ``values``：键 → 字符串；secret 键仅在提供非空串时更新
    - ``clear``：显式清空的键
    - ``scope``：只允许改对应页的字段（``system`` / ``model`` / ``memory`` / ``all``）
    - ``model_profiles``：``{ model_id: { params: {...} } }``，落盘到 ``data/llm_models/``
    - ``index_policy``：按区/业务多选索引方式（写入 memory_settings）
    """
    try:
        scope_n = normalize_scope(scope)
    except ValueError as exc:
        return {"ok": False, "errors": [str(exc)], "needs_restart": False}

    values = values or {}
    clear_set = {str(k).strip() for k in (clear or []) if str(k).strip()}
    updates: dict[str, str | None] = {}
    memory_updates: dict[str, str | None] = {}
    changed_restart = False
    errors: list[str] = []
    profile_saved: list[str] = []
    policy_changed: list[str] = []

    disk = read_dotenv_map()
    from fawai_avatar.memory_settings import MEMORY_KEYS, get_memory_store

    mem_store = get_memory_store()
    mem_map = mem_store.as_map()

    def _current(key: str) -> str:
        if key in MEMORY_KEYS:
            return mem_map.get(key, "")
        return effective_value(key, disk=disk)

    def _allow(field: SettingField) -> bool:
        return _group_in_scope(field.group, scope_n)

    for key in clear_set:
        field = _FIELD_BY_KEY.get(key)
        if field is None:
            errors.append(f"unknown key: {key}")
            continue
        if not _allow(field):
            errors.append(f"key not in scope {scope_n}: {key}")
            continue
        if key in MEMORY_KEYS:
            memory_updates[key] = None
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
        if not _allow(field):
            errors.append(f"key not in scope {scope_n}: {key}")
            continue
        if not _KEY_RE.match(key):
            errors.append(f"invalid key: {key}")
            continue
        if raw is None:
            if key in MEMORY_KEYS:
                memory_updates[key] = None
                continue
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
        # float 空串：删除键，回接口默认
        if field.key in {"FAWAI_HEADLESS", "FAWAI_FRP_ENABLE"} and norm == "":
            new_val: str | None = None
        elif field.kind == "float" and norm == "":
            new_val = None
        else:
            new_val = norm
        cur = _current(key)
        if new_val is None:
            if key not in MEMORY_KEYS and cur == "" and key not in os.environ and key not in disk:
                continue
        elif new_val == cur:
            # 与生效值相同：不必写盘（避免无改仍 needs_restart）
            continue
        if key in MEMORY_KEYS:
            memory_updates[key] = new_val
        else:
            updates[key] = new_val
            if field.needs_restart:
                changed_restart = True

    if model_profiles is not None:
        if scope_n not in {"model", "all"}:
            errors.append(f"model_profiles not allowed in scope {scope_n}")
        elif not isinstance(model_profiles, dict):
            errors.append("model_profiles must be an object")
        else:
            from fawai_avatar.llm.model_profiles import save_profile

            for mid, body in model_profiles.items():
                model_id = str(mid).strip()
                if not model_id:
                    errors.append("empty model id in model_profiles")
                    continue
                params = body.get("params") if isinstance(body, dict) else None
                if params is None and isinstance(body, dict):
                    params = body
                if not isinstance(params, dict):
                    errors.append(f"invalid params for model {model_id}")
                    continue
                try:
                    save_profile(model_id, params)
                    profile_saved.append(model_id)
                except ValueError as exc:
                    errors.append(str(exc))

    if index_policy is not None:
        if scope_n not in {"memory", "all"}:
            errors.append(f"index_policy not allowed in scope {scope_n}")
        elif not isinstance(index_policy, dict):
            errors.append("index_policy must be an object")
        else:
            try:
                from fawai_avatar.index_policy import parse_policy, policy_to_json
                from fawai_avatar.memory_settings import KEY_INDEX_POLICY

                text = policy_to_json(parse_policy(index_policy))
                memory_updates[KEY_INDEX_POLICY] = text
            except Exception as exc:
                errors.append(f"index_policy: {exc}")

    if errors:
        return {"ok": False, "errors": errors, "needs_restart": False}

    mem_changed = mem_store.set_many(memory_updates) if memory_updates else []
    if any(k == "FAWAI_INDEX_POLICY" for k in mem_changed):
        policy_changed = ["FAWAI_INDEX_POLICY"]

    if not updates and not profile_saved and not mem_changed:
        payload = get_settings_payload(scope=scope_n)
        payload["needs_restart"] = False
        payload["updated"] = []
        payload["profiles_saved"] = []
        return payload

    if updates:
        path = env_file_path()
        merge_dotenv_file(path, updates)
        apply_process_env(updates)

    if "FAWAI_HEADLESS" in updates:
        try:
            from fawai_avatar.platform_info import clear_platform_cache

            clear_platform_cache()
        except Exception:
            pass

    # 路由模型变更后，确保对应档案存在
    if scope_n in {"model", "all"}:
        from fawai_avatar.llm.model_profiles import ensure_profiles

        ensure_profiles(_selected_route_models())

    payload = get_settings_payload(scope=scope_n)
    payload["needs_restart"] = changed_restart
    payload["updated"] = sorted(set(list(updates.keys()) + mem_changed))
    payload["profiles_saved"] = sorted(set(profile_saved))
    return payload
