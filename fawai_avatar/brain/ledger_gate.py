# term:成功价值台账  term:失败价值台账  doc:doc/功能/成功价值台账/说明.md
"""价值台账三维门控：阈值（记忆配置）+ 分数归一 + 旁路通知。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

DEFAULT_THRESHOLD_PCT = 80.0
DIMS = ("completeness", "generality", "efficiency")

GateProgressFn = Callable[[str, dict[str, Any]], None]
GateUsageFn = Callable[[dict[str, Any] | None, int], None]


@dataclass
class GateNotify:
    """旁路门控：会话进度 + token/用时回写。"""

    on_progress: GateProgressFn | None = None
    on_usage: GateUsageFn | None = None
    step: int | None = None
    run_id: str | None = None
    phase: str = "success_ledger_gate"


def ledger_gate_threshold_pct(*, data_dir: str | None = None) -> float:
    """记忆配置 ``FAWAI_LEDGER_GATE_THRESHOLD``；默认 80（百分比）。"""
    try:
        from fawai_avatar.memory_settings import get_memory_store

        return float(get_memory_store(data_dir=data_dir).ledger_gate_threshold_pct())
    except Exception:
        return float(DEFAULT_THRESHOLD_PCT)


def score_to_pct(raw: Any) -> float | None:
    """把模型分归一到 0～100。支持 1～5、0～1、0～100。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return 100.0 if raw else 0.0
    if isinstance(raw, str):
        s = raw.strip().lower()
        if not s:
            return None
        # "4/5" "80%" "4"
        if s.endswith("%"):
            s = s[:-1].strip()
        if "/" in s:
            parts = s.split("/", 1)
            try:
                a, b = float(parts[0]), float(parts[1])
                if b > 0:
                    return max(0.0, min(100.0, (a / b) * 100.0))
            except ValueError:
                return None
        try:
            raw = float(s)
        except ValueError:
            return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if 0.0 <= v <= 1.0:
        return max(0.0, min(100.0, v * 100.0))
    if 1.0 < v <= 5.0:
        return max(0.0, min(100.0, (v / 5.0) * 100.0))
    if 0.0 <= v <= 100.0:
        return float(v)
    return None


def decide_gate_from_scores(
    parsed: dict[str, Any],
    *,
    threshold_pct: float | None = None,
) -> dict[str, Any]:
    """
    三维均 ≥ 阈值才过门；缺分则不过。
    模型显式 ``pass: false`` 时即使分够也拒绝（保守）。
    """
    thr = float(
        DEFAULT_THRESHOLD_PCT if threshold_pct is None else threshold_pct
    )
    thr = max(0.0, min(100.0, thr))
    scores_pct: dict[str, float | None] = {}
    scores_raw: dict[str, Any] = {}
    for dim in DIMS:
        scores_raw[dim] = parsed.get(dim)
        scores_pct[dim] = score_to_pct(parsed.get(dim))
    missing = [d for d in DIMS if scores_pct[d] is None]
    all_ok = not missing and all(
        float(scores_pct[d] or 0) >= thr for d in DIMS
    )
    model_pass = parsed.get("pass")
    if model_pass is False:
        passed = False
        reason_extra = "模型判定 pass=false"
    elif missing:
        passed = False
        reason_extra = f"缺三维分数: {','.join(missing)}"
    elif not all_ok:
        passed = False
        low = [
            f"{d}={scores_pct[d]:.0f}%<{thr:.0f}%"
            for d in DIMS
            if scores_pct[d] is not None and float(scores_pct[d]) < thr
        ]
        reason_extra = "未达阈值: " + (", ".join(low) if low else "unknown")
    else:
        passed = True
        reason_extra = f"三维均≥{thr:.0f}%"
    rationale = str(parsed.get("rationale") or "").strip()
    if reason_extra:
        rationale = (rationale + "；" + reason_extra).strip("；") if rationale else reason_extra
    return {
        "pass": passed,
        "status": "passed" if passed else "rejected",
        "completeness": scores_raw["completeness"],
        "generality": scores_raw["generality"],
        "efficiency": scores_raw["efficiency"],
        "scores_pct": {d: scores_pct[d] for d in DIMS},
        "threshold_pct": thr,
        "rationale": rationale[:800],
        "model_pass": model_pass,
    }


def parse_gate_json(content: str) -> dict[str, Any] | None:
    text = (content or "").strip()
    if not text:
        return None
    try:
        obj = json_loads_obj(text)
        if obj is not None:
            return obj
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    return json_loads_obj(m.group(0))


def json_loads_obj(text: str) -> dict[str, Any] | None:
    import json

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def gate_prompt_suffix(*, threshold_pct: float, polarity: str) -> str:
    thr = max(0.0, min(100.0, float(threshold_pct)))
    # 1～5 分制对照：80% ≈ 4 分
    min_star = thr / 20.0
    if polarity == "avoid":
        head = (
            "你是失败步骤价值台账的晋升评审。评估是否适合写入跨任务知识库 lore（avoid 极性，勿当正例）。\n"
            "三维须给 1～5 的数字分（也可 0～100）：\n"
            "1. completeness：错因、勿再试、证据、作用对象是否够避坑复用\n"
            "2. generality：换掉本案专名后是否仍应避免\n"
            "3. efficiency：短卡+指针是否够用，有无巨日志噪声\n"
        )
    else:
        head = (
            "你是成功步骤价值台账的晋升评审。评估是否适合写入跨任务知识库 lore。\n"
            "三维须给 1～5 的数字分（也可 0～100）：\n"
            "1. completeness 完备性：验收点、命令、落点、证据、材料来源等是否够复用\n"
            "2. generality 通用性：换掉本案专名（主机号、单文件名）后是否仍成立\n"
            "3. efficiency 效率：短卡+指针是否够用，有无巨日志噪声\n"
        )
    return (
        head
        + f"晋升门槛：三维折算百分比后均须 ≥ {thr:.0f}%（约 1～5 分制 ≥ {min_star:.1f}）。\n"
        "规则：口令/Token 明文不得晋升；仅 ok/fail 无证据则不通过。\n"
        "只输出一个 JSON 对象，字段：completeness, generality, efficiency, "
        "pass(boolean), rationale(string)。\n"
    )
