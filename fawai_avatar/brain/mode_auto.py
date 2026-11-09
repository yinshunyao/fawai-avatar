# term:执行配置自动模式  tool:brain_mode_auto  doc:doc/功能/执行配置自动模式/说明.md
"""执行配置：四项各自三态 auto|on|off；自动项由首轮任务分析回写生效布尔。"""

from __future__ import annotations

from typing import Any, Literal

MODE_KEYS: tuple[str, ...] = ("fast", "effort", "step_tier", "plan_auto")
ModePref = Literal["auto", "on", "off"]
PREF_AUTO: ModePref = "auto"
PREF_ON: ModePref = "on"
PREF_OFF: ModePref = "off"


def coerce_mode_pref(value: Any) -> ModePref:
    """缺省 / 无法识别 → auto。"""
    if value is True:
        return PREF_ON
    if value is False:
        return PREF_OFF
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return PREF_ON if int(value) != 0 else PREF_OFF
    s = str(value or "").strip().lower()
    if not s:
        return PREF_AUTO
    if s in {"auto", "automatic", "a"}:
        return PREF_AUTO
    if s in {"1", "true", "yes", "on", "open"}:
        return PREF_ON
    if s in {"0", "false", "no", "off", "close", "closed"}:
        return PREF_OFF
    return PREF_AUTO


def normalize_mode_prefs(raw: Any) -> dict[str, ModePref]:
    out: dict[str, ModePref] = {k: PREF_AUTO for k in MODE_KEYS}
    if not isinstance(raw, dict):
        return out
    for k in MODE_KEYS:
        if k in raw:
            out[k] = coerce_mode_pref(raw.get(k))
    return out


def default_mode_prefs() -> dict[str, ModePref]:
    return {k: PREF_AUTO for k in MODE_KEYS}


def prefs_from_meta(meta: dict[str, Any] | None) -> dict[str, ModePref]:
    """读 mode_prefs；兼容旧 mode_auto + mode_auto_lock。"""
    if not isinstance(meta, dict):
        return default_mode_prefs()
    if "mode_prefs" in meta:
        return normalize_mode_prefs(meta.get("mode_prefs"))

    # 兼容：全局 mode_auto + 锁定表
    locks_raw = meta.get("mode_auto_lock")
    locks: dict[str, bool] = {}
    if isinstance(locks_raw, dict):
        for k in MODE_KEYS:
            if k in locks_raw and locks_raw.get(k):
                locks[k] = True
    elif isinstance(locks_raw, (list, tuple, set)):
        for item in locks_raw:
            k = str(item or "").strip()
            if k in MODE_KEYS:
                locks[k] = True

    mode_auto = True
    if "mode_auto" in meta:
        v = meta.get("mode_auto")
        if v is False or str(v).strip().lower() in {"0", "false", "off", "no", "manual"}:
            mode_auto = False

    out = default_mode_prefs()
    if not mode_auto:
        for k in MODE_KEYS:
            out[k] = PREF_ON if bool(meta.get(k)) else PREF_OFF
        return out
    for k in MODE_KEYS:
        if locks.get(k):
            out[k] = PREF_ON if bool(meta.get(k)) else PREF_OFF
        else:
            out[k] = PREF_AUTO
    return out


def any_pref_auto(meta: dict[str, Any] | None) -> bool:
    return any(p == PREF_AUTO for p in prefs_from_meta(meta).values())


def is_pref_auto(meta: dict[str, Any] | None, key: str) -> bool:
    if key not in MODE_KEYS:
        return False
    return prefs_from_meta(meta).get(key) == PREF_AUTO


def pref_to_bool(pref: ModePref) -> bool | None:
    """auto → None（待分析）；on/off → 布尔。"""
    if pref == PREF_ON:
        return True
    if pref == PREF_OFF:
        return False
    return None


def prefs_from_analysis(
    *,
    simple: bool,
    want_effort: bool,
    enter: bool,
) -> dict[str, bool]:
    """分析字段 → 四开关生效布尔。"""
    if simple:
        return {
            "fast": True,
            "effort": False,
            "step_tier": False,
            "plan_auto": False,
        }
    return {
        "fast": False,
        "effort": bool(want_effort),
        "step_tier": True,
        "plan_auto": bool(enter),
    }


def apply_auto_prefs(
    meta: dict[str, Any] | None,
    *,
    simple: bool,
    want_effort: bool,
    enter: bool,
) -> dict[str, Any]:
    """仅回写 pref=auto 的生效布尔；保留 mode_prefs。"""
    base = meta if isinstance(meta, dict) else {}
    mode_prefs = prefs_from_meta(base)
    analyzed = prefs_from_analysis(
        simple=bool(simple), want_effort=bool(want_effort), enter=bool(enter)
    )
    patch: dict[str, Any] = {"mode_prefs": dict(mode_prefs)}
    for key in MODE_KEYS:
        if mode_prefs.get(key) != PREF_AUTO:
            continue
        patch[key] = bool(analyzed[key])
    return patch


def inbound_prefs_patch(inbound_meta: dict[str, Any] | None) -> dict[str, Any]:
    """从入站整理 mode_prefs + 手工项的生效布尔。"""
    if not isinstance(inbound_meta, dict):
        return {}
    patch: dict[str, Any] = {}
    if "mode_prefs" in inbound_meta or "mode_auto" in inbound_meta or "mode_auto_lock" in inbound_meta:
        mode_prefs = prefs_from_meta(inbound_meta)
    elif any(k in inbound_meta for k in MODE_KEYS):
        # 旧客户端只传布尔：视为全手工
        mode_prefs = {
            k: (PREF_ON if bool(inbound_meta.get(k)) else PREF_OFF)
            if k in inbound_meta
            else PREF_AUTO
            for k in MODE_KEYS
        }
        # 若四项都显式带了，全手工；否则未带的保持 auto
        if not all(k in inbound_meta for k in MODE_KEYS):
            mode_prefs = {
                k: (
                    PREF_ON
                    if bool(inbound_meta.get(k))
                    else PREF_OFF
                    if k in inbound_meta
                    else PREF_AUTO
                )
                for k in MODE_KEYS
            }
    else:
        mode_prefs = default_mode_prefs()

    patch["mode_prefs"] = mode_prefs
    for key in MODE_KEYS:
        pref = mode_prefs[key]
        forced = pref_to_bool(pref)
        if forced is None:
            continue
        patch[key] = bool(forced)
    return patch


def summarize_prefs(meta: dict[str, Any] | None) -> str:
    """进度卡短文案。"""
    m = meta if isinstance(meta, dict) else {}
    mode_prefs = prefs_from_meta(m)
    label = {
        "fast": "极速",
        "effort": "全力",
        "step_tier": "分档",
        "plan_auto": "计划",
    }
    bits: list[str] = []
    for key in MODE_KEYS:
        pref = mode_prefs.get(key, PREF_AUTO)
        on = bool(m.get(key))
        if pref == PREF_AUTO:
            bits.append(f"{label[key]}自动·{'开' if on else '关'}")
        elif pref == PREF_ON:
            bits.append(f"{label[key]}开")
        else:
            bits.append(f"{label[key]}关")
    return "、".join(bits)


# --- 兼容旧名（调度/旧调用） ---


def coerce_mode_auto(value: Any) -> bool:
    """旧全局自动闸：缺省 True。"""
    pref = coerce_mode_pref(value)
    if pref == PREF_OFF:
        return False
    if value is False or str(value or "").strip().lower() in {
        "0",
        "false",
        "off",
        "no",
        "manual",
    }:
        return False
    return True


def mode_auto_from_meta(meta: dict[str, Any] | None) -> bool:
    """任一键为 auto 则视为仍走自动分析。"""
    return any_pref_auto(meta)


def locks_from_meta(meta: dict[str, Any] | None) -> dict[str, bool]:
    """旧锁表视图：非 auto 即锁定。"""
    prefs = prefs_from_meta(meta)
    return {k: True for k in MODE_KEYS if prefs.get(k) != PREF_AUTO}


def is_locked(meta: dict[str, Any] | None, key: str) -> bool:
    return not is_pref_auto(meta, key)


def normalize_mode_auto_lock(raw: Any) -> dict[str, bool]:
    """兼容旧入站锁表。"""
    out: dict[str, bool] = {}
    if isinstance(raw, dict):
        for k in MODE_KEYS:
            if k not in raw:
                continue
            v = raw.get(k)
            locked = False
            if v is True or v is False:
                locked = bool(v)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                locked = int(v) != 0
            else:
                s = str(v or "").strip().lower()
                locked = s in {"1", "true", "yes", "on", "lock", "locked", "manual"}
            if locked:
                out[k] = True
        return out
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            k = str(item or "").strip()
            if k in MODE_KEYS:
                out[k] = True
    return out
