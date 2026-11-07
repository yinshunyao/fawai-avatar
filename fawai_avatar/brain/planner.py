# term:大脑  tool:brain_plan  doc:doc/功能/大脑/说明.md
# term:规划提示档  tool:prompt_profile  doc:doc/功能/规划提示档/说明.md
# term:完成任务  doc:doc/功能/完成任务/说明.md
# term:执行反省  tool:brain_reflect  doc:doc/功能/执行反省/说明.md
# term:观察反省  tool:brain_obs_reflect  doc:doc/功能/观察反省/说明.md
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
    history_requests_verify_must_patch,
    history_requests_verify_read,
    is_blind_file_head_read,
    is_explore_only_actions,
    is_verify_read_only_actions,
)
from fawai_avatar.brain.durable_memory import durable_memory_text
from fawai_avatar.brain.history_window import plan_history
from fawai_avatar.brain.step_todos import format_todo_prompt
from fawai_avatar.brain.method_recovery import strip_disabled_remote_gate_history
from fawai_avatar.brain.native_tools import openai_tools_for_coding, plan_from_tool_calls
from fawai_avatar.brain.necessary_paths import path_ledger_prompt_lines
from fawai_avatar.brain.prompt_profile import (
    PromptProfile,
    goal_has_user_image,
    infer_prompt_profile,
    kinds_for_format_example,
    looks_like_fs_web_goal,
    profile_full,
    report_delivery_rule,
)
from fawai_avatar.brain.obs_reflect import ObsReflection, run_obs_reflect
from fawai_avatar.brain.reflect import Reflection, run_reflect, run_stagnation
from fawai_avatar.config import RuntimeConfig
from fawai_avatar.llm.client import LlmClient
from fawai_avatar.lore import lore_prompt_block
from fawai_avatar.skills.store import skills_prompt_block
from fawai_avatar.types import Action, ExecResult, Observation, Plan


def _delegate_prompt_lines(*, planner_role: str) -> str:
    """主脑可写 delegate；快模型禁止再委派。未配置快模型时不提示委派。"""
    from fawai_avatar.brain.delegate import fast_model_from_env

    if planner_role == "fast":
        return (
            "- **快模型段**：你只执行当前子目标；禁止输出 delegate；"
            "达成停条件或无法继续时 status=success/failed，thought 写清结果摘要给主脑。"
            "禁止 need_input（拿不准则 failed 并说明缺口）。\n"
        )
    if not fast_model_from_env():
        return ""
    return (
        "- **主脑委派（机械步优先）**：已配置快模型时，下列低风险子段**应优先**委派，勿逐步自跑烧主脑深度思考——"
        "只读定位/多文件检索、已知路径再读、远程 ls/进程/supervisor/端口探查、同构只读链、简单改写；"
        "可把多步探查收成一段 `goal`+`stop_when`。"
        '写法：顶层 `"delegate":{"tier":"fast","goal":"子目标","stop_when":"停条件","max_steps":5}`，'
        "此时 actions 必须为空、status=continue；段末摘要回灌后再由你验收、换道或终答。"
        "高风险终答/互斥路线/架构决断/需向用户澄清 → 勿委派，主脑自理。\n"
    )


def _system_prompt(
    *,
    coding: bool = False,
    goal: str = "",
    profile: PromptProfile | None = None,
    planner_role: str = "main",
) -> str:
    from fawai_avatar.platform_info import is_headless, platform_prompt_block
    from fawai_avatar.workspace.paths import workspace_paths_prompt_block

    paths_block = workspace_paths_prompt_block()

    prof = profile or profile_full(coding=coding)
    delegate_rule = _delegate_prompt_lines(planner_role=planner_role)
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
            "- 编码目标：优先 fs_patch；写后必须 code_check（或 shell_verify），args 须 path/paths"
            "（或 command；兼容 file/files/cmd）；"
            "**不冲突的读和写可一次排 4～8 个 actions 并 act_parallel**；"
            "同一路径的读与写、同一路径的多写、前台、shell 与其它动作不要同批。"
            "多步执行流倾向带 expect（精确 contains，或模糊 contains_any/not_contains），非硬闸。\n"
            "- **临时脚本/算数/装包**：先 `shovel_search`；无命中再 `py_write`→`py_pip`（需依赖时）→`py_run`（落盘"
            "data/sessions/{task_id}/）；参数/env；新建脚本尽最大努力 stdout 打 OK 或 FAIL:原因、失败非零退出；"
            "通用性≥3 可升铲；改产品源码仍用 fs_patch，勿用 py_* 改仓内业务文件。\n"
            "- 若启用原生 tool_calls：直接调用工具，不必再包一层 JSON plan；结束时调用 done。"
            "分层写路径由系统按 path 自动绑定默认 doc_refs；也可在写工具 args 里带 doc_refs"
            "或 architecture_skip_reason。\n"
            "- 未走原生、输出 JSON 时：写入 fawai_avatar 分层目录也须含 doc_refs 或 architecture_skip_reason"
            "（系统可按 path 自动补默认 doc_refs）。\n"
            "- **改写优先**：历史已给出明确 path + 渲染/修改点后，本步优先 fs_patch/fs_replace/fs_write；"
            "禁止无新问题继续盲读同一文件或整文件扫读；需要细节时按行号小窗 fs_read。\n"
            "- **定位（条件）**：目标已给明确 path → 定向 fs_read/fs_patch，不必探仓；"
            "仅缺 path 时优先 fs_grep（pattern）+ fs_glob（收窄 path/glob）；"
            "多词并行才 explore_search（默认只 grep）；禁止整文件盲读；已有 path:line 禁止同范围宽搜。\n"
            "- **可见副作用**：若目标含页面/布局/附图，补丁须产生用户可感知变化；"
            "禁止只堆未引用 CSS 变量；success 的 thought 须写清可见变化，禁止 native tool_calls 占位。"
            "实机截屏/浏览器验页为可选；做不到不得因此 failed。"
            "验手机窄屏用 browser_navigate(viewport=mobile)→snapshot，勿桌面截 Chrome 冒充。\n"
        )
    elif coding and prof.short_rules:
        coding_extra = (
            "- 编码：优先 fs_patch；写后 code_check（须 path/paths）；不冲突的读和写可一段多 actions（约 4～8）并 act_parallel；同一路径不要同批；前台和 shell 不要与其它动作同批；多步倾向带 expect（精确或模糊，非硬闸）；原生 tool_calls 时可直接调工具"
            "（分层写自动绑定 doc_refs）。已有 path 则定向读改，禁止无新问题盲读/为探仓而探仓；"
            "仅缺 path 时优先 fs_grep/fs_glob；多词才 explore_search。禁止整文件盲读。"
            "临时脚本先 shovel_search，再 py_write/py_pip/py_run；改产品源码勿用 py_*。"
            "页面/布局类禁止未引用 CSS 变量假成功；收尾 thought 须写可见变化；"
            "实机验页可选，验窄屏用 browser_*+viewport=mobile。\n"
        )
    elif not coding and prof.id == "fs":
        coding_extra = (
            "- 前端/文件改页：已有入口 path/渲染点 → 直接 fs_patch/fs_replace；"
            "仅不知文件时优先 fs_grep/fs_glob（收窄 path）；多词才 explore_search；"
            "禁止反复整文件 fs_read / 同范围宽 grep；分层写自动绑定 doc_refs。\n"
            "- **可见副作用**：改布局/样式须改到实际生效的选择器或属性；"
            "禁止只新增未 var() 引用的 CSS 变量/注释后 success；"
            "write_set 非空≠验收通过。目标含用户附图时，须对照图中可见问题下刀。"
            "实机验页可选；验窄屏用 browser_* + viewport=mobile，勿桌面截屏冒充手机；"
            "验不了仍可用 thought 对照 diff 收尾，不得因此 failed。\n"
        )
    gui_rule = (
        "- 无界面：禁止规划 click/type/hotkey/app_focus/ui_*；observe_kinds 勿选 obs_screen/obs_ax。\n"
        if is_headless()
        else (
            "- GUI：click/type/hotkey/app_focus/ui_click/ui_type/ui_verify 设 needs_foreground=true；"
            "纯 shell/后台设 false。\n"
            "- 控件级优先：observe_kinds 含 obs_ax → ui_click/ui_type；"
            "args.ref 抄 AX 编号（\"1\"/\"2\"…）或 OCR 表编号（\"ocr1\"…），禁止臆造 chat-input-field 等 id；"
            "观察已含 OCR 表时优先 ui_click(ocrN)，勿再估像素 click；"
            "表中无控件再 obs_screen+click 或 failed；键入后须界面回证，未回证不得 success。\n"
            "- 多屏：观察 meta.displays；选 obs_screen 时用 observe_args."
            '{"obs_screen":{"screen_target":"frontmost|main|index:N|all"}}（默认 frontmost）；'
            "未见目标则换 target 或 app_focus，禁止假定只在主屏。\n"
            "- click 的 x/y 为**图内坐标**（相对截图 origin）；闭环加 origin_x/y；"
            "勿与全局坐标混用（除非 args.coord_space=global）。\n"
            "- 截图已见输入框有待发文案时：优先 press/hotkey 提交（如 enter 或应用惯用快捷键），"
            "再用截屏确认是否发出；勿反复估发送按钮像素 click。\n"
            "- AX bounds 若明显荒谬或 ui_click 报 ax_bounds_invalid：换道 obs_screen+click 或键盘，禁止同 ref 空转。\n"
        )
    )
    if prof.id == "fs" or (
        prof.kind_allow is not None
        and prof.kind_allow.isdisjoint(
            {"click", "type", "hotkey", "app_focus", "ui_click", "ui_type"}
        )
    ):
        gui_rule = "- 本轮勿规划 click/type/hotkey/app_focus/ui_*；改文件走 fs_*。\n"
    image_extra = ""
    if goal_has_user_image(goal) and (
        looks_like_fs_web_goal(goal) or coding
    ):
        image_extra = (
            "- 目标含用户附图：thought/选型须点名图中至少一处可见布局或 UI 问题，"
            "再 fs_patch 改实际生效样式；禁止无视附图只堆未引用 CSS 变量。\n"
        )
    thought_rule = (
        "- **执行中 thought ≤40 字**（status=continue，或 need_input 的问句）；"
        "先保证 status/actions/observe_kinds 字段完整。"
        "success/failed 的终稿不受 40 字限制。\n"
        + report_delivery_rule(goal)
    )
    doc_screen_rule = (
        "- 目标是在读文档、图片或用户附图时：observe_kinds 不要选 obs_screen/obs_ax；"
        "内容已在文件或附图里，截本机屏幕与任务无关。"
        "只有目标明确要看或操作当前桌面/某 App 界面时才截屏。\n"
    )
    path_ledger = path_ledger_prompt_lines(short=prof.short_rules)
    profile_line = f"本轮提示档：{prof.id}（按目的精简；硬校验仍用全量能力）。\n"
    if prof.short_rules:
        return f"""你是操作分身的执行大脑。据目标与观察规划**下一步**动作，并点选 observe_kinds。
路径（本机配置，每轮以此为准，勿臆造另一套根）：
{paths_block}
{profile_line}平台：
{platform_prompt_block()}
{caps_block}
{rules_inject}{ctx_block}{thought_rule}{doc_screen_rule}规则：
- 只输出一个 JSON 对象，无 markdown 其它前后缀；禁止声称已执行物理操作。
- shell 用 args.cmd；fs_read 用 path + offset/limit；故意同窗再读加 allow_repeat=true；fs_grep 用 pattern；fs_patch/replace 的 old 须与原文一致。
- 缺 path 时定位：优先 fs_grep/fs_glob（收窄 path/glob，支持 `**` 与 `{{html,js}}`）；多词才 explore_search（默认只 grep）；结构用 code_symbols；已有 path 定向读改；禁止整文件盲读。
- **动作段**：不冲突的读和写可一次排 4～8 个 actions（减少反复进模），`step_kind` 用 `act_parallel`。同一路径的读与写、同一路径的多写不要同批。前台、`shell` 不要与其它动作同批。多步执行流倾向带 `"expect"`（精确 `contains`，模糊 `contains_any` / `not_contains`）；纯探路与 **fs_patch/replace 默认可不带**；勿断言不存在的 `patch applied`（成功回执是 `· applied`）。非硬闸。带 expect 的动作单独成串行段。新建脚本 stdout 打 `OK` 或 `FAIL:原因`，失败非零退出。
- **step_kind**（本步类型）：`sense` 取证；`act_serial` 串行执行段；`act_parallel` 不冲突的读和写并发；`analyze` 分析；`finalize` 收尾。纯执行/分析/收尾时 observe_kinds 优先 `none`（勿空刷工作区顶层列表）；需画面/AX 再点选。
- 工具结果默认全文回灌；检索体量靠 `head_limit`（默认宜小）与 `context_before/after`（≤5）控制，达上限可自行加大 limit 或收窄翻页，无宽检索降质闸。
- **过程相位（必要）**：定位 → 取证（小窗 fs_read / 已有 path:line）→ 改写或作答 → 验收。宽搜/整文件盲读属可选，不得替代取证。
- observe_kinds 必须从观察候选选；查代码/改产品在 act_* 后优先 **none**（已有工具结果够用）；首轮 sense 可用 obs_fs；GUI 可选 obs_ax / obs_screen。读文档或附图时候选里没有截屏，不要改去截本机屏幕。
- 遵守历史中的【执行反省】/【步骤重复】constraints；可参考【观察反省】hints（软建议），失败时仍以执行反省为准。
- 最终给用户的话写在 success/failed 的 thought；勿贴原始 stdout。
- 查找落点/账号优先 lore 与 fs_read（绝对路径须在上方只读根或工作区根内）；禁止开场 `grep -r ~`；勿用 shell cat/cp 绕过 fs。
- 升级/同步远端：优先 `fawai_shovels/shell/rsync-ssh-deploy.sh` 或 `ssh-run-with-pass.sh`（口令仅 env）；可在 work_root 临时写脚本再跑；禁止无口令 BatchMode ssh 空探针当主路径。
- 用户说不在电脑边/远程操作：禁止要求粘贴文件或拷进工作区；读不到则 failed 并在 thought 给出可复制命令。
- 问询收尾禁过程元叙述与过薄盘点；材料不足则 failed/need_input。
{path_ledger}- 默认尽最大努力；仅猜错代价高（不可逆写删/提交/外发/花钱/互斥路线）才 status=need_input；有选项须带 options≥2，勿改开放长答。
{delegate_rule}{coding_extra}{image_extra}{gui_rule}格式：
{{"thought":"执行中≤40字；终稿写报告","title":"≤16字","step_label":"≤16字本步意图","step_kind":"act_serial","status":"continue|success|failed|need_input","done":false,"observe_kinds":["none"],"observe_args":{{"obs_screen":{{"screen_target":"frontmost"}}}},"doc_refs":[],"architecture_skip_reason":"","options":[{{"id":"a","label":"选项A"}}],"delegate":null,"actions":[{{"kind":"{kinds}","args":{{}},"needs_foreground":false,"write_resources":[],"expect":null}}]}}
- continue 时 done=false；success/failed 通常 done=true；need_input 时 done=false、actions 空。尚未命名则必须给 title。每步必须给 step_label（本步在办什么，≠ title）。"""

    return f"""你是操作分身的执行大脑。根据用户目标与当前观察，规划下一步控制动作，并点选**下一步**环境观察方式。
路径（本机配置，每轮以此为准，勿臆造另一套根）：
{paths_block}
{profile_line}须根据下列运行平台事实选型（自动探测，勿臆造另一套 OS）：
{platform_prompt_block()}
{caps_block}
{skills_block}{lore_block}{rules_inject}{ctx_block}
规则：
- 你只输出计划，不执行操作；禁止声称已点击、已输入或已完成任何物理操作。
- 未走原生 tool_calls 时：仅输出一个 JSON 对象，不要 markdown 代码块或其它前后缀文字。
{thought_rule}- shell 动作的命令字段必须是 args.cmd（字符串）。也兼容 args.command，但请优先写 cmd。探测多 CLI：`command -v a b c || true`（which 缺一个即非零）。
- hotkey：args.keys 为字符串列表，如 ["cmd","tab"]；禁止只写 {{"key":"cmd+tab"}}（虽兼容，但请用 keys 列表）。
- fs_write/fs_replace/fs_patch：args.path + 相应字段；write_resources 必须列路径；replace/patch 的 old 必须与文件原文完全一致——只能从最近 fs_read/grep 命中行或失败回执「原文小窗」逐字复制，禁止按 goal 意图脑补闸门/文案（【补丁锚点未接地】）。
- **定位文件（条件）**：目标/历史已给明确 path → 定向 `fs_read`/`fs_patch`，**不必**先探仓。仅不知文件时：**优先** `fs_grep`（**pattern**）与 `fs_glob`（须收窄 `path`/`glob`）；`explore_search` 暂时降权，仅多关键词并行时用（默认只 grep，要符号加 `include_symbols=true`）。再 `code_symbols`。`fs_glob` 支持 `**` 与 `{{html,js}}`；仓外目录须传 `path=`。禁止整文件盲 `fs_read`。连续空结果再用 shell `ls`/`find`（macOS 勿用 `find -printf`）。
- code_symbols / code_map：改代码前看结构。explore_search：默认只并行 grep；`include_symbols`/`symbols_query` 才走索引。fs_grep 参数名是 **pattern**。索引可疑时用 code_index_refresh。
- **脚本铲子生命周期**：造临时脚本/临时代码前先 `shovel_search`（可兼 `skill_search`）；命中则 `shell` 调已有铲 `invoke`；无命中再临时脚本。临时脚本须参数/env，禁止明文口令与未公开绝对路径；成功后自评三维分，通用性≥3 升 `fawai_shovels`（文首 meta+台账）或 `skill_write`，≤2 仅本案。
- **Python 扩展**：临时脚本/算数/装包用 `py_write`（path+content，相对会话根）→ 可选 `py_pip`（packages）→ `py_run`（script|code|module 三选一）；stdout/stderr 落盘 `runs/`；改产品源码仍 `fs_patch`，禁止用 py_* 改仓内业务文件；不做 pdb。
- fs_read：args.path 必填；按行定向读用 **offset**（1-based 起始行）+ **limit**；也兼容 start_line/end_line；文件头省略 offset 或写 1（勿依赖 0）。已有 path:line 时只准小窗核对，禁止整段重读。**故意**再次同窗读（如刚 fs_patch 后验收）须在该动作 args 设 `allow_repeat=true`（或本步 JSON 顶层同名字段），否则步骤重复守卫会按同构步裁决；无新意图勿滥标。
- fs_grep / explore_search 命中只在工具结果观察里（默认全文回灌）；禁止臆造 `grep_result_*.txt` 再 fs_read_many。用适度 `head_limit`（默认宜 ≤40，硬顶 100）与 `context_before/after`（≤5）；达 head_limit 可自行加大或收窄后再搜翻页，无宽检索降质闸。
- 多轮分片 `fs_read` 取证允许，证据够再改写，读不到目标材料则 failed/need_input。
- **过程相位（必要）**：开场与重规划按相位推进——①定位（path 或 path:line）→ ②取证（按行号小窗 `fs_read` 或读清 hits）→ ③改写（`fs_patch`/`fs_replace`/`fs_write`）或面向用户作答 → ④验收对照 goal。宽搜、整文件盲读属**可选**，不得替代②；**不因只读步数强制 mutate**。
- **动作段（少进模）**：不冲突的读和写**优先一次排出 4～8 个 actions**（含 fs_grep/fs_glob/fs_read/shovel_search，以及路径互不相交的 fs_patch/fs_write），`step_kind` 用 `act_parallel`，减少「一步一规划」。同一路径的读与写、同一路径的多写不要同批。前台、`shell` 不要与其它动作同批。带 expect 的动作单独串行。多步执行流**倾向**声明机读预期（非硬闸）：精确 `"expect":{{"contains":"…"}}`，模糊 `contains_any`（多个可接受标记）或 `not_contains`；纯探路与 **fs_patch/replace 默认可不带**；勿断言 `patch applied`（成功回执是 `· applied`）。不满足才停段反省。
- **新建脚本的结果行**：`py_write` / 临时 shell / 新铲子尽最大努力在 stdout 打一行稳定标记（`OK` 或 `FAIL:原因`），业务失败用非零退出；随后 `py_run`/`shell` 用 expect 对齐该行。不要在脚本里再调模型。已有只读工具只靠 `ok` 即可，不必强行补 expect。
- **step_kind（步骤分类）**：本段类型必填语义——`sense`（环境取证）/ `act_serial`（有序执行，expect 自衔接）/ `act_parallel`（不冲突的读和写并发，段末统一观察）/ `analyze`（小结/反省向）/ `finalize`（面向用户交稿）。缺省可由系统回填。`act_*`/`analyze`/`finalize` 时 **observe_kinds 优先 `["none"]`**，禁止无必要再刷工作区顶层浅列表；需要画面/控件树再点 `obs_screen`/`obs_ax`。路径不冲突的多个读或写用 `act_parallel`。
- skill_write：args 含 id/title/when_to_use/steps（及可选 tags/source）；skill_search 用 args.query。
- 个人知识库：可先看上方【个人知识库】摘要；不足再用 lore_search / lore_get；显式写入用 lore_set / lore_note_upsert（旁路也会自动记 P0 路径）。
{delegate_rule}{coding_extra}{image_extra}{gui_rule}{doc_screen_rule}- **观察选取**：本步 actions 只能依据「当前观察」与历史里的「执行结果（工具结果观察）」；不要假装看到未观察的 GUI。
- 步首可能**无截屏图**，但观察摘要通常含「前台窗摘要」与「显示器清单」。目标依赖某 App 可见 UI 时，须先对照此前台态；需要画面证据再在 observe_kinds 点选 obs_screen，禁止无画面却断言屏幕上的具体内容。
- observe_kinds：为**下一步**环境观察点选方式，必须从当前观察 meta.observe_candidates（或提示中的候选）里选；可选 obs_fs / obs_ax / obs_screen / none（以候选为准）。**纯执行段之后优先 none**；首轮 sense / 缺工作区轮廓才 obs_fs；GUI 优先 obs_ax，需要画面再 obs_screen；禁止无必要默选截屏——先看前台摘要。
- **多屏 / 全屏 Space**：选 obs_screen 时填写 observe_args.obs_screen.screen_target（frontmost 默认 / main / index:N / all）；外接屏或未见目标时换屏或 app_focus（args.app），勿写死只截主屏；禁止一次扫完全部屏。
- click：x/y 相对本张截图左上角（meta.origin_x/y）；须落在画面尺寸内；不要直接填虚拟桌面负坐标除非 coord_space=global。
- 输入框已有待发文本：优先 press/hotkey 提交并截屏确认，避免反复点发送按钮坐标。
- 工具输出由闭环自动做工具结果观察（含 fs_grep 的 path:line hits）；你根据摘要继续规划。已有命中行时按行号附近定向 fs_read / fs_patch，禁止无视 hits 去盲扫整文件。
- 执行结果会在下一轮以「执行结果（工具结果观察）」返回；成功时可能附带【观察反省】（进度/hints 软建议）；若上一轮失败，会先有【执行反省】裁决（retry/alternate/abort）。
- 收到【执行反省】/【步骤重复】时必须遵守其 constraints；【观察反省】hints 仅供参考，与硬约束冲突时以执行反省/步骤重复为准。禁止无视裁决原样重试同一指纹动作；alternate 须换工具或换命令结构；无法换道则 status=failed。不得把「按 grep 行号附近定向 fs_read」当成盲目无效。
- **面向用户的最终回答写在 success/failed 那一轮的 thought 里**（会话层会把该 thought 发给用户）；禁止把原始 stdout/top 全文当作结论；禁止在 thought 里写明文口令。
- **问询/状态/查找/配置盘点收尾**：thought 须直接写出可验收事实（状态、数字、是否完成、边界、来源路径）；禁止只写「已获取/已汇总/已找到并结束」等过程元叙述。查硬件/配置时先一句结论，再用分项或表覆盖证据里已有的 GPU/CPU/内存/磁盘/系统/路径等维度，禁止只报一两项交差。材料不足则 failed 或 need_input，说明缺什么。
- **查找账号/地址/配置落点**：优先 lore_search / 规则已点名文档，再用 fs_read 绝对路径（须在上方只读根或工作区根内）；禁止开场对整个家目录 `grep -r ~`。禁止 shell cat/cp 绕过 fs。远端可用 shell ssh / rsync 部署 shovel（凭据从已授权可读文件取进 env，勿把明文口令写进 thought）。
- **升级/部署到远端**：优先在 work_root 调用已有 `rsync-ssh-deploy.sh` / `ssh-run-with-pass.sh`，或临时 `fs_write` 脚本再 shell 执行；禁止只做 BatchMode 公钥探针就 abort。
- **执行中澄清**：默认尽最大努力完成；可查可推断的不要问。用户表示远程/不在电脑边时，禁止 options 要求「粘贴配置/拷进工作区」。仅当继续猜会有明显坏结果（不可逆写/删、提交、外发、花钱、互斥执行路线必选一）时用 status=need_input；thought 为给用户的问句；done=false；actions 必须为空数组。若已有明确互斥选项，必须填 options（≥2 的 id+label），引导用户选一项，禁止改成开放式「请描述」。低风险缺省可合理假设并继续。
{path_ledger}格式：
{{"thought":"执行中短句；终稿按交稿体例写报告","title":"≤16字主题短名","step_label":"≤16字本步业务意图","step_kind":"act_serial","status":"continue|success|failed|need_input","done":false,"observe_kinds":["none"],"observe_args":{{"obs_screen":{{"screen_target":"frontmost"}}}},"doc_refs":["doc/功能/控制/说明.md"],"architecture_skip_reason":"","options":[{{"id":"a","label":"选项A"}}],"delegate":null,"actions":[{{"kind":"{kinds}","args":{{}},"needs_foreground":false,"write_resources":[],"expect":null}}]}}
- status 为 success 或 failed 时通常 done 为 true；continue / need_input 时 done 为 false。
- **title**：与目标主题强相关的短名（中文优先，≤16 字，不要任务 id、不要整句复述 goal）；若观察/历史提示「尚未命名」则本轮必须给出；已有正式标题时可省略。
- **step_label**：本步业务意图短名（中文优先，≤16 字），写给会话步骤簇头看，如「查找转换脚本」「转 VOC XML」；禁止写 phase 名（规划中/观察反省）或工具名堆砌；每轮 continue/success/failed/need_input 都应给出；与 title（整条任务名）分开。
- **step_kind**：本段类型（sense/act_serial/act_parallel/analyze/finalize）；路径不冲突的多个读或写用 act_parallel；交稿用 finalize。
- 若本步只跑查询、尚需根据结果回答用户：本步 status=continue、done=false；下一轮再 status=success、done=true、actions 为空，并在 thought 里用自然语言直接回答用户（例如「内存几乎用满，负载偏高」），不要粘贴命令原文，也不要只写「已查看并汇总」。"""

_VALID_STATUS = frozenset({"continue", "success", "failed", "need_input"})
_MUTATE_PREFER_REPLAN_HINT = (
    "【探索改写强制】上一步仍是只读探索。本步禁止 fs_read/fs_grep/fs_glob/explore_*；"
    "必须给出 fs_patch/fs_replace/fs_write（或 status=need_input/failed）。"
)
_VERIFY_READ_REPLAN_HINT = (
    "【反省开读】允许对本轮失败 path 定向小窗 fs_read（limit≤40，禁止文件头大窗），"
    "核对 exact old 后立即 fs_patch/fs_replace；若 history 已附原文小窗可直接 patch；"
    "禁止无新问题盲读扩搜，也禁止空 actions 直接 status=failed。"
)
_VERIFY_MUST_PATCH_REPLAN_HINT = (
    "【开读须改写】本步禁止再 fs_read/fs_grep/fs_glob；"
    "必须 fs_patch/fs_replace（old 逐字来自原文小窗）或 status=failed/need_input。"
)
_VERIFY_BLIND_HEAD_REPLAN_HINT = (
    "【反省开读】禁止文件头大窗盲读（offset≤1 且 limit≥80）。"
    "请用失败回执原文小窗直接 fs_patch，或只读命中行附近 limit≤40 后立即 patch。"
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


def _try_loads_dict(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _strip_dangling_json_tail(text: str) -> str:
    """去掉截断导致的半截 key / 无值字段，便于再闭合括号。"""
    s = text.rstrip()
    # ,"partial_key   或 ,"partial_key"
    s2 = re.sub(r",\s*\"[^\"]*\"?\s*$", "", s)
    if s2 != s:
        return s2.rstrip()
    # ,"key":
    s2 = re.sub(r",\s*\"[^\"]*\"\s*:\s*$", "", s)
    if s2 != s:
        return s2.rstrip()
    # ,"key": "partial
    s2 = re.sub(r",\s*\"[^\"]*\"\s*:\s*\"[^\"]*$", "", s)
    if s2 != s:
        return s2.rstrip()
    # ,"key": 123.  / true / null 半截数字较罕见，先不动
    # { "partial_key  （无前置逗号）
    s2 = re.sub(r"\{\s*\"[^\"]*\"?\s*$", "{", s)
    if s2 != s:
        return s2.rstrip()
    return s


def _scan_json_frame(text: str) -> tuple[bool, list[str]]:
    """返回 (是否仍在字符串内, 待闭合括号栈)。"""
    in_str = False
    escape = False
    stack: list[str] = []
    for ch in text:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
    return in_str, stack


def _close_truncated_json(text: str) -> str:
    """补全未闭合的引号与括号（应对模型输出半截 JSON）。"""
    out = text
    in_str, stack = _scan_json_frame(out)
    if in_str:
        out += '"'
    out = _strip_dangling_json_tail(out)
    in_str, stack = _scan_json_frame(out)
    if in_str:
        out += '"'
        out = _strip_dangling_json_tail(out)
        _, stack = _scan_json_frame(out)
    out += "".join(reversed(stack))
    return out


def _parse_repaired_json_object(text: str) -> dict[str, Any] | None:
    """从半截 / 尾部损坏的 JSON 抢救可解析对象。"""
    start = text.find("{")
    if start < 0:
        return None
    fragment = text[start:]
    # 逐步从尾部缩短，优先保留已写完的字段
    cuts = [fragment]
    for idx in range(len(fragment) - 1, max(len(fragment) // 3, 0), -1):
        ch = fragment[idx]
        if ch in ',}"\'':
            cuts.append(fragment[: idx + 1])
            if len(cuts) >= 24:
                break
    seen: set[str] = set()
    for cut in cuts:
        for candidate in (
            cut,
            _close_truncated_json(cut),
            _close_truncated_json(_strip_dangling_json_tail(cut)),
        ):
            if candidate in seen:
                continue
            seen.add(candidate)
            obj = _try_loads_dict(candidate)
            if obj is not None:
                return obj
    return None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = _strip_markdown_fences(text)
    obj = _try_loads_dict(cleaned)
    if obj is not None:
        return obj
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        obj = _try_loads_dict(match.group(0))
        if obj is not None:
            return obj
    # 半截 JSON（无闭合 } / 截在 key 中）→ 修补后再解析
    return _parse_repaired_json_object(cleaned)


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
    if meta.get("origin_x") is not None or meta.get("origin_y") is not None:
        parts.append(
            f"截图坐标原点（图内→全局）：origin=({meta.get('origin_x')},{meta.get('origin_y')})；"
            f"screen_target={meta.get('screen_target')}"
        )
    displays = meta.get("displays")
    if isinstance(displays, list) and displays:
        slim_disp = [
            {
                "index": d.get("index"),
                "is_main": d.get("is_main"),
                "bounds": d.get("bounds"),
            }
            for d in displays
            if isinstance(d, dict)
        ]
        parts.append(
            "显示器 displays（选 screen_target 用）："
            + json.dumps(slim_disp, ensure_ascii=False)
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
            "screen_target",
            "origin_x",
            "origin_y",
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
    # OCR 接地成功：默认卸掉截屏图，走文本模型选 ref（用户附图仍可附）
    detach_screen = bool((observation.meta or {}).get("detach_image_for_plan"))
    screen_path = (observation.image_path or "").strip()
    image_paths: list[str] = []
    if attach_images:
        for ref in goal_refs:
            resolved = _resolve_goal_image_ref(ref)
            if resolved and resolved not in image_paths:
                image_paths.append(resolved)
        if (
            screen_path
            and not detach_screen
            and screen_path not in image_paths
        ):
            image_paths.append(screen_path)
    if detach_screen and attach_images:
        text += (
            "\n（截屏已 OCR 出框表；本轮卸图，请用表中 ocrN ref 做 ui_click/ui_type，"
            "勿估像素坐标。）"
        )
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


def _normalize_observe_args(raw: Any) -> dict[str, Any]:
    """规范化 observe_args；兼容顶栏 screen_target。空输入返回 {}。"""
    from fawai_avatar.observe.displays import normalize_screen_target

    if not isinstance(raw, dict) or not raw:
        return {}
    out: dict[str, Any] = dict(raw)
    screen = out.get("obs_screen")
    target = None
    if isinstance(screen, dict):
        target = screen.get("screen_target")
        screen = dict(screen)
    elif isinstance(screen, str):
        target = screen
        screen = {}
    else:
        screen = {}
    if target is None and out.get("screen_target") is not None:
        target = out.get("screen_target")
    if target is not None or "obs_screen" in out or "screen_target" in out:
        screen["screen_target"] = normalize_screen_target(target)
        out["obs_screen"] = screen
    return out


def _plan_from_payload(data: dict[str, Any]) -> Plan:
    from fawai_avatar.brain.delegate import parse_delegate
    from fawai_avatar.brain.step_kind import (
        default_observe_kinds_for_step_kind,
        resolve_step_kind,
    )
    from fawai_avatar.task.title import (
        resolve_step_label,
        step_label_from_plan_payload,
        title_from_plan_payload,
    )

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
    observe_args = _normalize_observe_args(data.get("observe_args"))
    # 若点了截屏但未写 observe_args，仍带默认 frontmost，便于闭环传参
    if "obs_screen" in observe_kinds and "obs_screen" not in observe_args:
        observe_args = _normalize_observe_args({"obs_screen": {}})
    spec = parse_delegate(data.get("delegate"))
    # 委派段：本步由快模型执行，主脑 actions 应为空
    if spec is not None:
        actions = []
        if status == "need_input":
            status = "continue"
            done = False
    explicit_label = step_label_from_plan_payload(data)
    step_kind = resolve_step_kind(
        data.get("step_kind") or data.get("step_type") or data.get("kind"),
        status=status,
        actions=actions,
        observe_kinds=observe_kinds,
    )
    # act/analyze/finalize 未点选观察 → 下一步默认 none（治空壳 obs_fs）
    if not observe_kinds:
        default_obs = default_observe_kinds_for_step_kind(step_kind)
        if default_obs is not None:
            observe_kinds = list(default_obs)
    return Plan(
        thought=thought,
        actions=actions,
        done=done,
        status=status,
        raw=data,
        observe_kinds=observe_kinds,
        observe_args=observe_args,
        title=title_from_plan_payload(data),
        step_label=resolve_step_label(
            step_label=explicit_label,
            thought=thought,
            actions=actions,
        ),
        step_kind=step_kind,
        delegate=spec.to_dict() if spec else None,
    )


def _failed_plan(thought: str) -> Plan:
    return Plan(thought=thought, actions=[], done=True, status="failed")


def _is_format_failure(plan: Plan) -> bool:
    """LLM 未产出可解析 JSON / 空内容时的失败（可重试 / 软降级）。"""
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


def _soft_format_continue(plan: Plan) -> Plan:
    """格式重规划仍失败：降为 continue，禁止以 JSON 解析失败整任务 failed。"""
    raw = dict(plan.raw or {})
    raw["format_soft_fallback"] = True
    raw["format_fail_excerpt"] = (plan.thought or "")[:400]
    return Plan(
        thought="规划输出格式异常，本步跳过并重试（未中断任务）",
        actions=[],
        done=False,
        status="continue",
        raw=raw,
        observe_kinds=[],
        observe_args={},
        title=plan.title or "",
        step_label=plan.step_label or "规划格式重试",
        step_kind=plan.step_kind or "analyze",
        delegate=None,
    )


def format_soft_fallback_history_message(plan: Plan) -> str | None:
    """闭环空动作时：若为本轮格式软降级，注入格式重试提示。"""
    raw = plan.raw if isinstance(plan.raw, dict) else {}
    if not raw.get("format_soft_fallback"):
        return None
    excerpt = str(raw.get("format_fail_excerpt") or "").strip()
    if excerpt:
        return f"{_JSON_REPLAN_HINT}\n上一轮无法解析摘录：{excerpt[:300]}"
    return _JSON_REPLAN_HINT


def _attach_llm_meta(
    plan: Plan,
    *,
    result: dict[str, Any],
    elapsed_ms: int,
    prompt_profile: str | None = None,
) -> Plan:
    """把本轮 LLM usage / 耗时 / 模型 / 思考写入 plan.raw，供时间线展示。"""
    raw = dict(plan.raw or {})
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    if usage:
        raw["usage"] = usage
    raw["elapsed_ms"] = int(elapsed_ms)
    if result.get("model"):
        raw["model"] = result.get("model")
    if result.get("route"):
        raw["route"] = result.get("route")
    thinking = result.get("thinking")
    if isinstance(thinking, dict) and thinking:
        raw["thinking"] = thinking
    reasoning = result.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        raw["thinking_text"] = reasoning.strip()
    if prompt_profile:
        raw["prompt_profile"] = prompt_profile
    if plan.observe_kinds:
        raw["observe_kinds"] = list(plan.observe_kinds)
    if plan.observe_args:
        raw["observe_args"] = dict(plan.observe_args)
    if plan.step_kind:
        raw["step_kind"] = plan.step_kind
    if plan.delegate:
        raw["delegate"] = dict(plan.delegate)
    return Plan(
        thought=plan.thought,
        actions=list(plan.actions),
        done=plan.done,
        status=plan.status,
        raw=raw,
        observe_kinds=list(plan.observe_kinds),
        observe_args=dict(plan.observe_args or {}),
        title=plan.title or "",
        step_label=plan.step_label or "",
        step_kind=plan.step_kind or "",
        delegate=dict(plan.delegate) if plan.delegate else None,
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
        model: str | None = None,
        planner_role: str = "main",
        on_llm_delta: Any | None = None,
        history_compact: dict[str, Any] | None = None,
        step_todos: list[dict[str, str]] | None = None,
        allow_todos: bool = True,
    ) -> Plan:
        self._plan_allow_todos = bool(allow_todos) and planner_role != "fast"
        self._plan_step_todos = list(step_todos or [])
        self._history_compact_out = dict(history_compact or {})
        if _mock_enabled():
            return mock_plan_for_goal(goal, history=history)

        # 规划用压缩窗；原 history 仍由闭环追加完整工具结果
        # 远端进度闸默认关：已写入的闸门文案不再回灌
        base_history, self._history_compact_out = plan_history(
            strip_disabled_remote_gate_history(list(history or []), goal=goal),
            cache=history_compact,
            summarize=self._summarize_history,
        )
        mutate_prefer = history_requests_mutate_prefer(base_history)
        verify_must_patch = history_requests_verify_must_patch(base_history)
        # 开读须改写时不再放行只读；普通反省开读仍可定向小窗
        verify_read = (
            history_requests_verify_read(base_history) and not verify_must_patch
        )
        # 快模型段：禁止再委派；不走「改写优先」闸（由主脑验收）
        if planner_role == "fast":
            mutate_prefer = False
            verify_read = False
        plan = self._plan_once(
            goal,
            observation,
            history=base_history,
            mutate_prefer=mutate_prefer,
            verify_read=verify_read,
            model=model,
            planner_role=planner_role,
            on_llm_delta=on_llm_delta,
        )

        # 开读轮禁止文件头大窗盲读 → 拒一次
        if (
            verify_read
            and plan.status == "continue"
            and is_blind_file_head_read(plan.actions)
        ):
            reject_history = base_history + [
                {
                    "role": "assistant",
                    "content": (plan.thought or "blind-head-read")[:800],
                },
                {"role": "user", "content": _VERIFY_BLIND_HEAD_REPLAN_HINT},
            ]
            plan = self._plan_once(
                goal,
                observation,
                history=reject_history,
                mutate_prefer=True,
                verify_read=True,
                model=model,
                planner_role=planner_role,
                on_llm_delta=on_llm_delta,
            )

        # 闸门/强制改写后仍产出只读 → 再拒一次，收紧工具面
        # 反省开读轮：定向小窗 fs_read/fs_grep 允许，不拒；开读须改写则拒
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
                        _VERIFY_MUST_PATCH_REPLAN_HINT
                        if verify_must_patch
                        else (
                            _VERIFY_READ_REPLAN_HINT
                            if verify_read
                            else _MUTATE_PREFER_REPLAN_HINT
                        )
                    ),
                },
            ]
            plan = self._plan_once(
                goal,
                observation,
                history=reject_history,
                mutate_prefer=True,
                verify_read=verify_read and not verify_must_patch,
                model=model,
                planner_role=planner_role,
                on_llm_delta=on_llm_delta,
            )

        # 模型偶发输出自然语言/半截 JSON → 附加格式约束重规划 1 次；仍失败则软降级 continue
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
                model=model,
                planner_role=planner_role,
                on_llm_delta=on_llm_delta,
            )
            if _is_format_failure(plan):
                return _soft_format_continue(plan)

        # 快模型禁止再委派
        if planner_role == "fast" and plan.delegate:
            from fawai_avatar.brain.delegate import with_delegate

            plan = with_delegate(plan, None)

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
            model=model,
            planner_role=planner_role,
            on_llm_delta=on_llm_delta,
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

    def _summarize_history(self, excerpt: str) -> str | None:
        """窗口满时把旧步骤收成「做过什么、得到什么结论」。失败则由远窗机械摘要接手。"""
        result = self.llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "把下面已经做过的步骤收成短摘要，供下一轮规划使用。"
                        "必须留下做过什么、得到什么结论（主机、端口、路径、任务是否在跑等）。"
                        "不要写「禁止再读」或「勿重复盲读」。"
                        "摘要里没写到的事实，要明确说上下文里没有、需要定向再读。"
                        "口令、密码、token 不要原样抄出。"
                        "不超过 800 字。"
                    ),
                },
                {"role": "user", "content": excerpt[:12000]},
            ],
            tools=None,
            tool_choice=None,
            thinking_role="fast",
        )
        if not result.get("ok"):
            return None
        text = str(result.get("content") or "").strip()
        if not text:
            return None
        return durable_memory_text(text)[:1500]

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
        on_llm_delta: Any | None = None,
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
            on_llm_delta=on_llm_delta,
        )

    def obs_reflect(
        self,
        goal: str,
        actions: list[Action],
        results: list[ExecResult],
        *,
        tool_feedback: str = "",
        data_dir: str | None = None,
        on_llm_delta: Any | None = None,
    ) -> ObsReflection:
        """工具结果成功后的观察反省：进度小结 + 下一规划软建议。"""
        return run_obs_reflect(
            goal,
            actions,
            results,
            tool_feedback=tool_feedback,
            data_dir=data_dir,
            llm=None if _mock_enabled() else self.llm,
            on_llm_delta=on_llm_delta,
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
        prior_judged: bool = False,
        locate_anchors: list[str] | None = None,
    ) -> Reflection:
        """成功路径步骤重复：continue / alternate / abort（代码不硬止损）。"""
        return run_stagnation(
            goal,
            actions,
            results,
            fingerprint=fingerprint,
            hit_count=hit_count,
            history=history,
            prior_judged=prior_judged,
            locate_anchors=locate_anchors,
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
        model: str | None = None,
        planner_role: str = "main",
        on_llm_delta: Any | None = None,
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
        # 快模型段：强制 JSON（便于禁委派）；主脑原生仍可用
        native_eligible = coding or looks_like_fs_web_goal(goal)
        cfg = RuntimeConfig.from_env()
        use_native = bool(
            cfg.native_tools
            and native_eligible
            and profile.id != "vision"
            and not force_json
            and planner_role != "fast"
        )
        system = _system_prompt(
            coding=bool(profile.include_coding_context),
            goal=goal,
            profile=profile,
            planner_role=planner_role,
        )
        if mutate_prefer and verify_read:
            system = (
                system
                + "\n【本轮反省开读】允许对失败 path 至多一次定向小窗 fs_read（limit≤40），"
                "或直接用 history 里原文小窗作 old；随后必须立刻 fs_patch/fs_replace/fs_write；"
                "禁止文件头大窗盲读；禁止无新问题盲读扩搜；禁止空 actions 直接 status=failed。\n"
            )
        elif mutate_prefer:
            system = (
                system
                + "\n【本轮改写优先】禁止只读探索工具；必须 fs_patch/fs_replace/fs_write"
                " 或 need_input/failed。已观察 path / 原文小窗直接改，勿再盲读。\n"
            )
        if getattr(self, "_plan_allow_todos", False):
            system += "\n" + format_todo_prompt(getattr(self, "_plan_step_todos", None))
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
                mutate_prefer=mutate_prefer,
                verify_read=verify_read,
                allow_delegate=planner_role == "main",
            )
            if use_native
            else None
        )
        t0 = time.perf_counter()
        # 主脑开思考（可预算）；快模型段关思考——见 llm/thinking.py
        thinking_role = "fast" if planner_role == "fast" else "main"
        result = self.llm.chat(
            messages,
            tools=tools,
            model=model,
            route_role="fast" if planner_role == "fast" else None,
            thinking_role=thinking_role,
            on_delta=on_llm_delta,
        )
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
