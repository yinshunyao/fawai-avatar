# term:大脑  tool:brain_plan  doc:doc/功能/大脑/说明.md
# term:规划提示档  tool:prompt_profile  doc:doc/功能/规划提示档/说明.md
# term:完成任务  doc:doc/功能/完成任务/说明.md
# term:执行反省  tool:brain_reflect  doc:doc/功能/执行反省/说明.md
# term:步骤重复  tool:brain_stagnation  doc:doc/功能/步骤重复/说明.md
# term:代码索引  doc:doc/功能/代码索引/说明.md
# term:自我进化  doc:doc/功能/自我进化/说明.md
# term:守规约束  doc:doc/功能/守规约束/说明.md
# term:执行中澄清  doc:doc/功能/执行中澄清/说明.md
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from fawai_avatar.brain.capabilities import (
    apply_capability_defaults,
    capabilities_prompt_block,
    looks_like_coding_goal,
    mock_plan_for_goal,
    validate_plan,
)
from fawai_avatar.brain.compliance import (
    apply_architecture_skip_fallback,
    ensure_architecture_decls,
    rules_prompt_block,
)
from fawai_avatar.brain.context_inject import coding_context_prompt_block
from fawai_avatar.brain.explore_progress import (
    history_requests_mutate_prefer,
    history_requests_verify_read,
    is_explore_only_actions,
    is_verify_read_only_actions,
)
from fawai_avatar.brain.history_window import compress_history_for_plan
from fawai_avatar.brain.native_tools import openai_tools_for_coding, plan_from_tool_calls
from fawai_avatar.brain.prompt_profile import (
    PromptProfile,
    goal_has_user_image,
    infer_prompt_profile,
    kinds_for_format_example,
    looks_like_fs_web_goal,
    profile_full,
)
from fawai_avatar.brain.reflect import Reflection, run_reflect, run_stagnation
from fawai_avatar.config import RuntimeConfig
from fawai_avatar.llm.client import LlmClient
from fawai_avatar.lore import lore_prompt_block
from fawai_avatar.skills.store import skills_prompt_block
from fawai_avatar.types import Action, ExecResult, Observation, Plan


def _system_prompt(
    *,
    coding: bool = False,
    goal: str = "",
    profile: PromptProfile | None = None,
) -> str:
    from fawai_avatar.platform_info import is_headless, platform_prompt_block

    prof = profile or profile_full(coding=coding)
    kind_set = kinds_for_format_example(prof)
    kinds = "|".join(sorted(kind_set))
    caps_block = capabilities_prompt_block(
        kinds=prof.kind_allow,
        compact=prof.compact_capabilities,
    )
    ctx = coding_context_prompt_block() if (coding and prof.include_coding_context) else ""
    ctx_block = f"\n{ctx}\n" if ctx else "\n"
    rules_block = rules_prompt_block(goal)
    rules_inject = f"\n{rules_block}\n" if rules_block else "\n"
    skills = "" if prof.short_rules else skills_prompt_block()
    skills_block = f"{skills}\n" if skills else ""
    lore = "" if prof.short_rules else lore_prompt_block(goal)
    lore_block = f"{lore}\n" if lore else ""
    coding_extra = ""
    if coding and not prof.short_rules:
        coding_extra = (
            "- 编码目标：优先 fs_patch；写后必须 code_check（或 shell_verify）；每步 1～2 个 actions。\n"
            "- 若启用原生 tool_calls：直接调用工具，不必再包一层 JSON plan；结束时调用 done。"
            "分层写路径由系统按 path 自动绑定默认 doc_refs；也可在写工具 args 里带 doc_refs"
            "或 architecture_skip_reason。\n"
            "- 未走原生、输出 JSON 时：写入 fawai_avatar 分层目录也须含 doc_refs 或 architecture_skip_reason"
            "（系统可按 path 自动补默认 doc_refs）。\n"
            "- **改写优先**：历史已给出明确 path + 渲染/修改点后，本步优先 fs_patch/fs_replace/fs_write；"
            "禁止无新问题继续盲读同一文件或整文件扫读；需要细节时按行号小窗 fs_read。\n"
            "- **高带宽定位**：开场 1～2 步优先 explore_search 或 fs_grep（带 context_before/after）；"
            "禁止无 pattern 的整文件盲 fs_read；已有 path:line 禁止同范围宽 grep。\n"
            "- **可见副作用**：若目标含页面/布局/附图，补丁须产生用户可感知变化；"
            "禁止只堆未引用 CSS 变量；success 的 thought 须写清可见变化，禁止 native tool_calls 占位。\n"
        )
    elif coding and prof.short_rules:
        coding_extra = (
            "- 编码：优先 fs_patch；写后 code_check；每步 1～2 actions；原生 tool_calls 时可直接调工具"
            "（分层写自动绑定 doc_refs）。已定位 path 后禁止无新问题盲读，改写优先。"
            "开场优先 explore_search/fs_grep+context，禁止整文件盲读。"
            "页面/布局类禁止未引用 CSS 变量假成功；收尾 thought 须写可见变化。\n"
        )
    elif not coding and prof.id == "fs":
        coding_extra = (
            "- 前端/文件改页：开场优先 explore_search 或 fs_grep+context 定位；"
            "已定位入口 path 与渲染点后优先 fs_patch/fs_replace；"
            "禁止反复整文件 fs_read / 同范围宽 grep；分层写自动绑定 doc_refs。\n"
            "- **可见副作用**：改布局/样式须改到实际生效的选择器或属性；"
            "禁止只新增未 var() 引用的 CSS 变量/注释后 success；"
            "write_set 非空≠验收通过。目标含用户附图时，须对照图中可见问题下刀。\n"
        )
    gui_rule = (
        "- 无界面：禁止规划 click/type/hotkey；observe_kinds 勿选 obs_screen。\n"
        if is_headless()
        else "- GUI 动作（click/type/hotkey）设 needs_foreground=true；纯 shell/后台设 false。\n"
    )
    if prof.id == "fs" or (
        prof.kind_allow is not None and prof.kind_allow.isdisjoint({"click", "type", "hotkey"})
    ):
        gui_rule = "- 本轮勿规划 click/type/hotkey；改文件走 fs_*。\n"
    image_extra = ""
    if goal_has_user_image(goal) and (
        looks_like_fs_web_goal(goal) or coding
    ):
        image_extra = (
            "- 目标含用户附图：thought/选型须点名图中至少一处可见布局或 UI 问题，"
            "再 fs_patch 改实际生效样式；禁止无视附图只堆未引用 CSS 变量。\n"
        )
    thought_rule = (
        "- **thought ≤40 字**（优先）；先保证 status/actions/observe_kinds 字段完整，禁止长篇推理占满输出。\n"
    )
    profile_line = f"本轮提示档：{prof.id}（按目的精简；硬校验仍用全量能力）。\n"
    if prof.short_rules:
        return f"""你是操作分身的执行大脑。据目标与观察规划**下一步**动作，并点选 observe_kinds。
{profile_line}平台：
{platform_prompt_block()}
{caps_block}
{rules_inject}{ctx_block}{thought_rule}规则：
- 只输出一个 JSON 对象，无 markdown 其它前后缀；禁止声称已执行物理操作。
- shell 用 args.cmd；fs_read 用 path + offset/limit；fs_grep 用 pattern；fs_patch/replace 的 old 须与原文一致。
- 定位优先 explore_search/fs_grep+context/code_symbols，再 fs_glob；禁止开场整文件盲读。
- 遵守【定位预算】/【宽检索降质】：已有 path:line 后改写优先，禁止同范围再宽搜。
- observe_kinds 必须从观察候选选；查代码/改产品优先 obs_fs 或 none。
- 遵守历史中的【执行反省】/【步骤重复】constraints。
- 最终给用户的话写在 success/failed 的 thought；勿贴原始 stdout。
- 默认尽最大努力；仅猜错代价高（不可逆写删/提交/外发/花钱/互斥路线）才 status=need_input；有选项须带 options≥2，勿改开放长答。
{coding_extra}{image_extra}{gui_rule}格式：
{{"thought":"≤40字","title":"≤16字","status":"continue|success|failed|need_input","done":false,"observe_kinds":["obs_fs"],"doc_refs":[],"architecture_skip_reason":"","options":[{{"id":"a","label":"选项A"}}],"actions":[{{"kind":"{kinds}","args":{{}},"needs_foreground":false,"write_resources":[]}}]}}
- continue 时 done=false；success/failed 通常 done=true；need_input 时 done=false、actions 空。尚未命名则必须给 title。"""

    return f"""你是操作分身的执行大脑。根据用户目标与当前观察，规划下一步控制动作，并点选**下一步**环境观察方式。
{profile_line}须根据下列运行平台事实选型（自动探测，勿臆造另一套 OS）：
{platform_prompt_block()}
{caps_block}
{skills_block}{lore_block}{rules_inject}{ctx_block}
规则：
- 你只输出计划，不执行操作；禁止声称已点击、已输入或已完成任何物理操作。
- 未走原生 tool_calls 时：仅输出一个 JSON 对象，不要 markdown 代码块或其它前后缀文字。
{thought_rule}- shell 动作的命令字段必须是 args.cmd（字符串）。也兼容 args.command，但请优先写 cmd。探测多 CLI：`command -v a b c || true`（which 缺一个即非零）。
- hotkey：args.keys 为字符串列表，如 ["cmd","tab"]；禁止只写 {{"key":"cmd+tab"}}（虽兼容，但请用 keys 列表）。
- fs_write/fs_replace/fs_patch：args.path + 相应字段；write_resources 必须列路径；replace/patch 的 old 必须与文件原文完全一致（先定向 fs_read）。
- **定位文件（高带宽）**：开场优先 `explore_search` 或 `fs_grep`（**必须**带有意义的 pattern，推荐 context_before/after），再 `fs_glob`/`code_symbols`。`fs_glob` 支持 `**` 与 `{{html,js}}`。禁止无 pattern/无 offset 的整文件盲 `fs_read` 当定位手段。若连续空结果或需要目录树概览，再用 shell（`ls`/`find`），并遵守上方平台命令口径（macOS 勿用 `find -printf`）。
- code_symbols / code_map / explore_search：改代码前先定位（多语言符号索引；explore 每条 query 自动 grep+symbols）；fs_grep 参数名是 **pattern**（不要写 query）；可带 context_before/after。索引可疑时用 code_index_refresh。
- fs_read：args.path 必填；按行定向读用 **offset**（1-based 起始行）+ **limit**；也兼容 start_line/end_line。已有 path:line 时只准小窗核对，禁止整段重读。
- fs_grep / explore_search 命中只在工具结果观察里；禁止臆造 `grep_result_*.txt` 再 fs_read_many。hits 已压缩或命中过宽时：收窄 pattern / 按行号读，禁止同范围再宽搜。
- 收到【定位预算】或【宽检索降质】时必须改写或收窄，不得继续同 path 空转确认。
- skill_write：args 含 id/title/when_to_use/steps（及可选 tags/source）；skill_search 用 args.query。
- 个人知识库：可先看上方【个人知识库】摘要；不足再用 lore_search / lore_get；显式写入用 lore_set / lore_note_upsert（旁路也会自动记 P0 路径）。
{coding_extra}{image_extra}{gui_rule}- **观察选取**：本步 actions 只能依据「当前观察」与历史里的「执行结果（工具结果观察）」；不要假装看到未观察的 GUI。
- 步首可能**无截屏图**，但观察摘要通常含「前台窗摘要」（当前前台 App/窗口标题）。目标依赖某 App 可见 UI 时，须先对照此前台态；需要画面证据再在 observe_kinds 点选 obs_screen，禁止无画面却断言屏幕上的具体内容。
- observe_kinds：为**下一步**环境观察点选方式，必须从当前观察 meta.observe_candidates（或提示中的候选）里选；可选 obs_fs / obs_screen / none（以候选为准）。查代码/改产品能力优先 obs_fs 或 none；GUI 目标也禁止无必要默选截屏——先看前台摘要，再决定是否截屏。
- 工具输出由闭环自动做工具结果观察（含 fs_grep 的 path:line hits）；你根据摘要继续规划。已有命中行时按行号附近定向 fs_read / fs_patch，禁止无视 hits 去盲扫整文件。
- 执行结果会在下一轮以「执行结果（工具结果观察）」返回；若上一轮失败，会先有【执行反省】裁决（retry/alternate/abort）。
- 收到【执行反省】/【步骤重复】时必须遵守其 constraints；禁止无视裁决原样重试同一指纹动作；alternate 须换工具或换命令结构；无法换道则 status=failed。不得把「按 grep 行号附近定向 fs_read」当成盲目无效。
- **面向用户的最终回答写在 success/failed 那一轮的 thought 里**（会话层会把该 thought 发给用户）；禁止把原始 stdout/top 全文当作结论；禁止在 thought 里写明文口令。
- **问询/状态/查找/配置盘点收尾**：thought 须直接写出可验收事实（状态、数字、是否完成、边界、来源路径）；禁止只写「已获取/已汇总/已找到并结束」等过程元叙述。查硬件/配置时用短结论 + 分项列表或表，覆盖证据里已有的 GPU/CPU/内存/磁盘/系统/路径等维度，禁止只报一两项交差；工具输出被压缩时须先 fs_read observe 落盘全文再总结。材料不足则 failed 或 need_input，说明缺什么。
- **执行中澄清**：默认尽最大努力完成；可查可推断的不要问。仅当继续猜会有明显坏结果（不可逆写/删、提交、外发、花钱、互斥执行路线必选一）时用 status=need_input；thought 为给用户的问句；done=false；actions 必须为空数组。若已有明确互斥选项，必须填 options（≥2 的 id+label），引导用户选一项，禁止改成开放式「请描述」。低风险缺省可合理假设并继续。
格式：
{{"thought":"简短推理或给用户的结论","title":"≤16字主题短名","status":"continue|success|failed|need_input","done":false,"observe_kinds":["obs_fs"],"doc_refs":["doc/功能/控制/说明.md"],"architecture_skip_reason":"","options":[{{"id":"a","label":"选项A"}}],"actions":[{{"kind":"{kinds}","args":{{}},"needs_foreground":false,"write_resources":[]}}]}}
- status 为 success 或 failed 时通常 done 为 true；continue / need_input 时 done 为 false。
- **title**：与目标主题强相关的短名（中文优先，≤16 字，不要任务 id、不要整句复述 goal）；若观察/历史提示「尚未命名」则本轮必须给出；已有正式标题时可省略。
- 若本步只跑查询、尚需根据结果回答用户：本步 status=continue、done=false；下一轮再 status=success、done=true、actions 为空，并在 thought 里用自然语言直接回答用户（例如「内存几乎用满，负载偏高」），不要粘贴命令原文，也不要只写「已查看并汇总」。"""

_VALID_STATUS = frozenset({"continue", "success", "failed", "need_input"})
_MUTATE_PREFER_REPLAN_HINT = (
    "【探索改写强制】上一步仍是只读探索。本步禁止 fs_read/fs_grep/fs_glob/explore_*；"
    "必须给出 fs_patch/fs_replace/fs_write（或 status=need_input/failed）。"
)
_VERIFY_READ_REPLAN_HINT = (
    "【反省开读】允许对本轮失败 path 定向 fs_read/fs_grep，核对 exact old 后立即 "
    "fs_patch/fs_replace；禁止无新问题盲读扩搜，也禁止空 actions 直接 status=failed。"
)
_REPLAN_HINT = (
    "上一次计划未通过能力/守规校验，请严格按能力清单与守规约束重规划。"
    "文件/工作区目标必须用 fs_* / version_*，禁止 click/type/hotkey。"
    "写入 fawai_avatar 分层目录须带 doc_refs 或 architecture_skip_reason。"
)
_ARCH_REPLAN_HINT = (
    "上一次计划未通过架构校验。本轮禁用原生 tool_calls，必须只输出一个 JSON 对象。"
    "在 JSON 顶层填写非空 doc_refs（指向对应 doc/功能/*/说明.md）"
    "或非空 architecture_skip_reason（≥4 字）；actions 可继续写 fs_patch/fs_replace。"
    "文件/工作区目标必须用 fs_* / version_*，禁止 click/type/hotkey。"
)
_FORMAT_FAIL_PREFIXES = ("无法解析 JSON：", "LLM 返回空内容")
_JSON_REPLAN_HINT = (
    "上一次输出无法解析为计划 JSON（也无可用的 tool_calls）。"
    "请严格只输出一个 JSON 对象，不要 markdown 或其它前后缀文字。"
    '字段含 thought/status/done/observe_kinds/actions；'
    "若需搜索或读代码，请在 actions 里写 fs_grep/fs_read，不要只用自然语言描述打算。"
)


def _mock_enabled() -> bool:
    return os.environ.get("FAWAI_BRAIN_MOCK", "").strip() in ("1", "true", "yes")


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = _strip_markdown_fences(text)
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _observation_user_text(goal: str, observation: Observation) -> str:
    parts = [f"目标：{goal}", "", "当前观察：", observation.summary]
    if observation.image_path:
        parts.append(f"截图路径：{observation.image_path}")
    if observation.width is not None and observation.height is not None:
        parts.append(f"画面尺寸：{observation.width}x{observation.height}")
    meta = observation.meta or {}
    if meta.get("need_task_title"):
        parts.append(
            "本任务尚未有正式短标题：请在本轮 JSON 中输出 title（≤16 字，与主题强相关；"
            "不要任务 id、不要整句复述 goal）。"
        )
    elif meta.get("task_title"):
        parts.append(f"任务短标题：{meta.get('task_title')}")
    cands = meta.get("observe_candidates")
    if isinstance(cands, list) and cands:
        parts.append(
            "下一步环境观察候选 observe_candidates（observe_kinds 必须从此选）："
            + ", ".join(str(x) for x in cands)
        )
    # 勿把大块内部 meta（含截图路径列表）整包塞进提示；只保留规划相关键
    slim = {
        k: meta[k]
        for k in (
            "observe_candidates",
            "observe_methods",
            "observe_method",
            "observe_label",
            "need_task_title",
            "task_title",
            "title_source",
        )
        if k in meta
    }
    if slim:
        parts.append(f"附加 meta：{json.dumps(slim, ensure_ascii=False)}")
    return "\n".join(parts)


def _image_data_url(path: str) -> str | None:
    import base64
    from pathlib import Path

    p = Path(path)
    if not p.is_file():
        return None
    raw = p.read_bytes()
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _resolve_goal_image_ref(ref: str) -> str | None:
    """把 goal 里的 `[image:…]` 引用解析为本地文件路径。"""
    from pathlib import Path

    from fawai_avatar.config import RuntimeConfig

    raw = (ref or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if p.is_file():
        return str(p.resolve())
    name = Path(raw).name
    if not name or name in {".", "/"}:
        return None
    data_dir = Path(RuntimeConfig.from_env().data_dir).expanduser()
    for cand in (
        data_dir / "session_media" / name,
        Path("data/session_media") / name,
    ):
        if cand.is_file():
            return str(cand.resolve())
    return None


def _observation_user_message(
    goal: str,
    observation: Observation,
    *,
    attach_images: bool = True,
) -> dict[str, Any]:
    """有用户附图或观察截图则走多模态 content，触发 VLM 路由；否则纯文本。

    attach_images=False（如 fs 档）时卸图，避免后续轮仍绑 VLM。
    """
    from fawai_avatar.channel.message import extract_image_marks

    clean_goal, goal_refs = extract_image_marks(goal)
    text = _observation_user_text(clean_goal, observation)
    if goal_refs and not attach_images:
        text += "\n（用户曾附图；本轮提示档已卸图，请依据目标文字与工具/观察历史继续。）"
    image_paths: list[str] = []
    if attach_images:
        for ref in goal_refs:
            resolved = _resolve_goal_image_ref(ref)
            if resolved and resolved not in image_paths:
                image_paths.append(resolved)
        if observation.image_path and observation.image_path not in image_paths:
            image_paths.append(observation.image_path)
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for path in image_paths:
        data_url = _image_data_url(path)
        if data_url:
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
    if len(parts) == 1:
        return {"role": "user", "content": text}
    return {"role": "user", "content": parts}


def _normalize_observe_kinds(raw: Any) -> list[str]:
    if raw is None:
        return []
    items: list[Any]
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[str] = []
    for item in items:
        m = str(item or "").strip().lower()
        if not m:
            continue
        if m in {"screen", "screenshot", "ui_observe"}:
            m = "obs_screen"
        elif m in {"fs", "workspace", "files"}:
            m = "obs_fs"
        elif m in {"skip", "no_observe"}:
            m = "none"
        if m not in out:
            out.append(m)
    return out


def _plan_from_payload(data: dict[str, Any]) -> Plan:
    from fawai_avatar.task.title import title_from_plan_payload

    thought = str(data.get("thought") or "")
    status = str(data.get("status") or "continue")
    if status not in _VALID_STATUS:
        status = "failed" if data.get("done") else "continue"
    done = bool(data.get("done", status in ("success", "failed")))
    if status == "need_input":
        done = False
    actions: list[Action] = []
    raw_actions = data.get("actions")
    if isinstance(raw_actions, list) and status != "need_input":
        for item in raw_actions:
            if isinstance(item, dict):
                actions.append(apply_capability_defaults(Action.from_dict(item)))
    observe_kinds = _normalize_observe_kinds(
        data.get("observe_kinds") or data.get("observe") or data.get("observe_methods")
    )
    return Plan(
        thought=thought,
        actions=actions,
        done=done,
        status=status,
        raw=data,
        observe_kinds=observe_kinds,
        title=title_from_plan_payload(data),
    )


def _failed_plan(thought: str) -> Plan:
    return Plan(thought=thought, actions=[], done=True, status="failed")


def _is_format_failure(plan: Plan) -> bool:
    """LLM 未产出可解析 JSON / 空内容时的失败（可重试一次）。"""
    if plan.status != "failed" or plan.actions:
        return False
    thought = (plan.thought or "").strip()
    return any(thought.startswith(p) for p in _FORMAT_FAIL_PREFIXES)


def _format_failure_prose(plan: Plan) -> str:
    """从格式失败 thought 里抽出模型原文，便于重规划对照。"""
    thought = (plan.thought or "").strip()
    for prefix in _FORMAT_FAIL_PREFIXES:
        if thought.startswith(prefix):
            rest = thought[len(prefix) :].strip()
            return rest or thought
    return thought


def _attach_llm_meta(
    plan: Plan,
    *,
    result: dict[str, Any],
    elapsed_ms: int,
    prompt_profile: str | None = None,
) -> Plan:
    """把本轮 LLM usage / 耗时 / 模型写入 plan.raw，供时间线展示。"""
    raw = dict(plan.raw or {})
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    if usage:
        raw["usage"] = usage
    raw["elapsed_ms"] = int(elapsed_ms)
    if result.get("model"):
        raw["model"] = result.get("model")
    if result.get("route"):
        raw["route"] = result.get("route")
    if prompt_profile:
        raw["prompt_profile"] = prompt_profile
    if plan.observe_kinds:
        raw["observe_kinds"] = list(plan.observe_kinds)
    return Plan(
        thought=plan.thought,
        actions=list(plan.actions),
        done=plan.done,
        status=plan.status,
        raw=raw,
        observe_kinds=list(plan.observe_kinds),
        title=plan.title or "",
    )


class Brain:
    def __init__(self, llm: LlmClient | None = None) -> None:
        self.llm = llm or LlmClient()

    def plan(
        self,
        goal: str,
        observation: Observation,
        *,
        history: list[dict[str, Any]] | None = None,
    ) -> Plan:
        if _mock_enabled():
            return mock_plan_for_goal(goal, history=history)

        # 规划用压缩窗；原 history 仍由闭环追加完整工具结果
        base_history = compress_history_for_plan(list(history or []))
        mutate_prefer = history_requests_mutate_prefer(base_history)
        verify_read = history_requests_verify_read(base_history)
        plan = self._plan_once(
            goal,
            observation,
            history=base_history,
            mutate_prefer=mutate_prefer,
            verify_read=verify_read,
        )

        # 闸门/强制改写后仍产出只读 → 再拒一次，收紧工具面
        # 反省开读轮：定向 fs_read/fs_grep 允许，不拒
        if (
            mutate_prefer
            and plan.status == "continue"
            and is_explore_only_actions(plan.actions)
            and not (verify_read and is_verify_read_only_actions(plan.actions))
        ):
            reject_history = base_history + [
                {"role": "assistant", "content": (plan.thought or "explore-only")[:800]},
                {
                    "role": "user",
                    "content": (
                        _VERIFY_READ_REPLAN_HINT
                        if verify_read
                        else _MUTATE_PREFER_REPLAN_HINT
                    ),
                },
            ]
            plan = self._plan_once(
                goal,
                observation,
                history=reject_history,
                mutate_prefer=True,
                verify_read=verify_read,
            )

        # 模型偶发输出自然语言而非 JSON → 附加格式约束重规划 1 次（与能力校验同构）
        if _is_format_failure(plan):
            prose = _format_failure_prose(plan)
            format_history = base_history + [
                {"role": "assistant", "content": prose[:1500]},
                {"role": "user", "content": _JSON_REPLAN_HINT},
            ]
            plan = self._plan_once(
                goal,
                observation,
                history=format_history,
                mutate_prefer=mutate_prefer,
                verify_read=verify_read,
            )
            if _is_format_failure(plan):
                return plan

        violation = validate_plan(goal, plan)
        if violation is None:
            return plan

        # 硬校验失败 → 附加约束重规划 1 次；架构门强制 JSON（禁原生）以便带 doc_refs
        arch_fail = "架构校验" in (violation or "")
        # 架构门：先对当前计划 auto-bind/skip，能救则不重规划、不整任务 failed
        if arch_fail:
            rescued0 = apply_architecture_skip_fallback(
                plan,
                reason="自动兜底：漏 doc_refs，按分层路径补齐后继续写入",
            )
            if validate_plan(goal, rescued0) is None:
                return rescued0
        hint = _ARCH_REPLAN_HINT if arch_fail else _REPLAN_HINT
        retry_history = base_history + [
            {"role": "assistant", "content": plan.thought or plan.status},
            {"role": "user", "content": f"{hint}\n原因：{violation}"},
        ]
        plan2 = self._plan_once(
            goal,
            observation,
            history=retry_history,
            force_json=arch_fail,
            mutate_prefer=False if arch_fail else mutate_prefer,
            verify_read=False if arch_fail else verify_read,
        )
        # JSON 重规划仍漏 doc_refs 时：按分层路径自动补默认声明（与原生通道同口径）
        if arch_fail:
            plan2 = ensure_architecture_decls(plan2, auto_default=True)
        violation2 = validate_plan(goal, plan2)
        if violation2 is None:
            return plan2
        # 架构二次仍失败：对原计划 / 重规划尝试兜底 skip，禁止整任务硬 failed 堵死改写相位
        still_arch = arch_fail or ("架构校验" in (violation2 or ""))
        if still_arch:
            for candidate in (plan2, plan):
                rescued = apply_architecture_skip_fallback(
                    candidate,
                    reason=(
                        "自动兜底：架构二次校验仍缺声明，允许写入后续补文档"
                    ),
                )
                if validate_plan(goal, rescued) is None:
                    return rescued
            # 最后兜底：绝不以「架构校验失败」文案硬 failed（其它能力违规仍可 failed）
            last = apply_architecture_skip_fallback(
                plan2,
                reason="自动兜底：架构声明无法自动对齐，允许本步写入",
            )
            last_err = validate_plan(goal, last)
            if last_err is None or "架构校验" in last_err:
                return last
            return _failed_plan(f"能力校验失败：{last_err}")
        return _failed_plan(f"能力校验失败：{violation2}")

    def reflect(
        self,
        goal: str,
        actions: list[Action],
        results: list[ExecResult],
        *,
        fingerprint: str,
        fail_count: int,
        history: list[dict[str, Any]] | None = None,
        prior_alternate_fps: set[str] | None = None,
    ) -> Reflection:
        """动作失败后的执行反省：retry / alternate / abort。"""
        return run_reflect(
            goal,
            actions,
            results,
            fingerprint=fingerprint,
            fail_count=fail_count,
            history=history,
            prior_alternate_fps=prior_alternate_fps,
            llm=None if _mock_enabled() else self.llm,
        )

    def stagnation(
        self,
        goal: str,
        actions: list[Action],
        results: list[ExecResult],
        *,
        fingerprint: str,
        hit_count: int,
        history: list[dict[str, Any]] | None = None,
    ) -> Reflection:
        """成功路径步骤重复：continue / alternate / abort。"""
        return run_stagnation(
            goal,
            actions,
            results,
            fingerprint=fingerprint,
            hit_count=hit_count,
            history=history,
            llm=None if _mock_enabled() else self.llm,
        )

    def _plan_once(
        self,
        goal: str,
        observation: Observation,
        *,
        history: list[dict[str, Any]],
        force_json: bool = False,
        mutate_prefer: bool = False,
        verify_read: bool = False,
    ) -> Plan:
        coding = looks_like_coding_goal(goal)
        profile = infer_prompt_profile(
            goal,
            history=history,
            observation_has_screen=bool(observation.image_path),
        )
        from fawai_avatar.brain.prompt_profile import looks_like_fs_web_goal

        # 前端改页可开原生工具；编码上下文仍只跟 profile.include_coding_context
        # 架构校验重规划时 force_json：禁用原生，便于顶层带 doc_refs
        native_eligible = coding or looks_like_fs_web_goal(goal)
        cfg = RuntimeConfig.from_env()
        use_native = bool(
            cfg.native_tools
            and native_eligible
            and profile.id != "vision"
            and not force_json
        )
        system = _system_prompt(
            coding=bool(profile.include_coding_context),
            goal=goal,
            profile=profile,
        )
        if mutate_prefer and verify_read:
            system = (
                system
                + "\n【本轮反省开读】允许对失败 path 定向 fs_read/fs_grep/fs_glob 核对原文，"
                "核对后必须立刻 fs_patch/fs_replace/fs_write；禁止无新问题盲读扩搜；"
                "禁止空 actions 直接 status=failed。\n"
            )
        elif mutate_prefer:
            system = (
                system
                + "\n【本轮改写优先】禁止只读探索工具；必须 fs_patch/fs_replace/fs_write"
                " 或 need_input/failed。已观察 path 直接改，勿再盲读。\n"
            )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system,
            }
        ]
        if history:
            messages.extend(history)
        messages.append(
            _observation_user_message(
                goal,
                observation,
                attach_images=profile.attach_user_images,
            )
        )

        tools = (
            openai_tools_for_coding(
                mutate_prefer=mutate_prefer, verify_read=verify_read
            )
            if use_native
            else None
        )
        t0 = time.perf_counter()
        result = self.llm.chat(messages, tools=tools)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        meta_kw = {"prompt_profile": profile.id}
        if not result.get("ok"):
            err = str(result.get("error") or "LLM 调用失败")
            return _attach_llm_meta(
                _failed_plan(err), result=result, elapsed_ms=elapsed_ms, **meta_kw
            )

        tool_calls = list(result.get("tool_calls") or [])
        if use_native and tool_calls:
            native = plan_from_tool_calls(
                tool_calls, content=str(result.get("content") or "")
            )
            if native is not None:
                return _attach_llm_meta(
                    native, result=result, elapsed_ms=elapsed_ms, **meta_kw
                )

        content = str(result.get("content") or "").strip()
        if not content:
            return _attach_llm_meta(
                _failed_plan("LLM 返回空内容"),
                result=result,
                elapsed_ms=elapsed_ms,
                **meta_kw,
            )

        payload = _parse_json_object(content)
        if payload is None:
            return _attach_llm_meta(
                _failed_plan(f"无法解析 JSON：{content[:200]}"),
                result=result,
                elapsed_ms=elapsed_ms,
                **meta_kw,
            )

        # JSON 写分层路径：与原生 tool_calls 一样自动补默认 doc_refs，避免空转后死在架构门
        parsed = ensure_architecture_decls(_plan_from_payload(payload), auto_default=True)
        return _attach_llm_meta(
            parsed,
            result=result,
            elapsed_ms=elapsed_ms,
            **meta_kw,
        )


def plan_next(
    goal: str,
    observation: Observation,
    *,
    history: list[dict[str, Any]] | None = None,
    llm: LlmClient | None = None,
) -> Plan:
    return Brain(llm=llm).plan(goal, observation, history=history)


if __name__ == "__main__":
    GOAL = "在工作区写入 notes/c2_hello.txt 内容 hello"
    MOCK = True  # True 时不调用 LLM（等同设置 FAWAI_BRAIN_MOCK=1）

    if MOCK:
        os.environ["FAWAI_BRAIN_MOCK"] = "1"

    demo_obs = Observation(summary="（本地试跑）尚无真实界面摘要。")
    out = plan_next(GOAL, demo_obs)
    print(json.dumps(out.to_dict(), ensure_ascii=False, indent=2))
