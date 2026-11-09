# term:能力清单  doc:doc/功能/完成任务/说明.md
# term:代码索引  doc:doc/功能/代码索引/说明.md
# term:自我进化  doc:doc/功能/自我进化/说明.md
# term:编码闭环  doc:doc/功能/编码闭环/说明.md
# term:浏览器操作  doc:doc/功能/浏览器操作/说明.md
"""机读能力清单：供大脑选型与规划后硬校验。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from fawai_avatar.types import Action, Plan

_WRITE_RESOURCE_KINDS = frozenset(
    {"fs_write", "fs_replace", "fs_patch", "git_commit", "version_rollback"}
)

_GUI_KINDS = frozenset(
    {
        "click",
        "type",
        "hotkey",
        "app_focus",
        "ui_click",
        "ui_type",
        "ui_verify",
        "double_click",
        "right_click",
        "scroll",
        "press",
    }
)
_BROWSER_KINDS = frozenset(
    {
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_close",
    }
)
_FILE_GOAL_HINTS = (
    "写入",
    "写文件",
    "写到",
    "保存到",
    "读取",
    "读一下",
    "打开文件",
    "工作区",
    "fs_",
    "notes/",
    ".txt",
    ".md",
    ".json",
    ".py",
    "文件内容",
    "创建文件",
    "改文件",
    "替换文件",
    "skill_",
    "Skill",
    "沉淀",
    "教训",
    "评测结果",
    "versioning",
    "code_symbols",
    "code_map",
    "code_index_refresh",
    "符号",
    "仓地图",
    "代码索引",
    "fs_patch",
    "补丁",
    "改代码",
    "修改一下代码",
    "改一下代码",
    "修一下代码",
    "改下代码",
    "修改下代码",
    "code_check",
    "git_",
    "explore_search",
    "探仓",
)
_CODING_GOAL_HINTS = (
    "改代码",
    "修改代码",
    "修改一下代码",
    "改一下代码",
    "修一下代码",
    "改下代码",
    "修改下代码",
    "fs_patch",
    "补丁",
    "重构",
    "修 bug",
    "修bug",
    "函数",
    ".py",
    "code_check",
    "git commit",
    "git_status",
)
# 明显超出首期清单：应 failed，禁止 GUI 假成功
# 真硬闸提示词须够具体。禁止裸「破解」：儿童故事「破解魔法桥密码」等会误伤，
# 且会连带关掉假归因回灌（false_beyond 见 is_beyond 即放行真失败）。
_BEYOND_CAPABILITY_HINTS = (
    "实名认证",
    "银行转账",
    "刷脸支付",
    "安装未登记",
    "神秘 app",
    "神秘 App",
    "未登记软件",
    "绕过登录",
    "破解软件",
    "软件破解",
    "破解系统",
    "破解账号",
    "破解支付",
    "盗版破解",
)


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    modality: str  # background | foreground | meta
    when_to_use: str
    action_kind: str
    needs_foreground: bool = False
    write_resources: str = "none"  # none | path_arg
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 首期常量清单（与术语表工具 id / 控制 Action.kind 对齐）
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="fs_glob",
        name="工作区文件·查找",
        modality="background",
        when_to_use=(
            "按 glob 列路径（支持 ** 与 {a,b}）；须收窄 path=子目录；"
            "整 work_root 仅当 pattern 含明确文件名片段（≥3 字面）；禁止 **/*.{yaml,json} 扫整仓；"
            "仓外须显式子目录 path；优先 lore work_name；空结果再 shell ls/find"
        ),
        action_kind="fs_glob",
    ),
    Capability(
        id="fs_grep",
        name="工作区文件·搜索",
        modality="background",
        when_to_use=(
            "按内容搜文本；args.pattern 必填；path 须为子目录或单文件（禁止 . / 整 work_root）；"
            "先 lore_search(work_name) 定目录；整根会拒磁盘扫（可先走 FTS）；"
            "有线索后尽量用本工具收窄（少同文件盲读）；已确认相关处理后尽量精确 pattern；"
            "命中以 path:line 回灌，禁止臆造 grep_result_*.txt 再 fs_read；"
            "正则多选一用 a|b（勿写成 a\\|b，否则变字面竖线、易假 0 命中）；"
            "搜 CSS 类名优先 button.send / 类名正文，勿只搜转义后的 \\.send\\|\\.stop"
        ),
        action_kind="fs_grep",
    ),
    Capability(
        id="fs_read",
        name="工作区文件·读取",
        modality="background",
        when_to_use=(
            "读取工作区文件；args.path 必填；定向读用 offset+limit（1-based 起始行，"
            "文件头省略 offset 或写 1；0 会按 1 处理）；兼容 start_line/end_line"
        ),
        action_kind="fs_read",
    ),
    Capability(
        id="fs_write",
        name="工作区文件·写入",
        modality="background",
        when_to_use=(
            "创建或覆盖写入工作区文件；分层路径可带 args.doc_refs 或 architecture_skip_reason"
            "（原生模式下系统也会按 path 自动绑定默认说明）"
        ),
        action_kind="fs_write",
        write_resources="path_arg",
    ),
    Capability(
        id="fs_replace",
        name="工作区文件·替换",
        modality="background",
        when_to_use=(
            "替换工作区文件中的片段（args.path/old/new；old 须与文件原文完全一致；"
            "复杂改码优先 fs_patch；分层路径可带 doc_refs）"
        ),
        action_kind="fs_replace",
        write_resources="path_arg",
    ),
    Capability(
        id="fs_patch",
        name="精准补丁",
        modality="background",
        when_to_use=(
            "局部唯一匹配补丁；args.path + hunks[{old,new}]（或 old/new）；"
            "old 须为 fs_read/原文小窗逐字复制，禁止按意图编造；"
            "禁止无故整文件重写；分层路径可带 args.doc_refs 或 architecture_skip_reason"
        ),
        action_kind="fs_patch",
        write_resources="path_arg",
    ),
    Capability(
        id="fs_read_many",
        name="工作区文件·多读",
        modality="background",
        when_to_use=(
            "一次限额读取多个真实工作区路径（改前对照）；"
            "paths 来自 glob/grep hits，禁止填 grep_result_*.txt"
        ),
        action_kind="fs_read_many",
    ),
    Capability(
        id="code_symbols",
        name="代码索引·符号",
        modality="background",
        when_to_use=(
            "列出工作区符号（py/js/ts/… 类函数；html/css 的 id/class/selector/@media）；"
            "可按 path/query/kind；须记忆配置 FAWAI_CODE_INDEX 开（默认开）；"
            "关则改用 fs_grep；查询自动借用落盘索引"
        ),
        action_kind="code_symbols",
    ),
    Capability(
        id="code_map",
        name="代码索引·仓地图",
        modality="background",
        when_to_use=(
            "生成精简仓地图（含前端选择器摘要），改代码前定位入口；"
            "须 FAWAI_CODE_INDEX 开；自动借用落盘索引"
        ),
        action_kind="code_map",
    ),
    Capability(
        id="code_index_refresh",
        name="代码索引·刷新",
        modality="background",
        when_to_use=(
            "强制重建/增量刷新工作区符号索引（mtime 变更后或索引可疑时）；"
            "须 FAWAI_CODE_INDEX 开"
        ),
        action_kind="code_index_refresh",
    ),
    Capability(
        id="code_check",
        name="改码回证",
        modality="background",
        when_to_use=(
            "写后语法/约定检查（.py→py_compile）；args 须 path 或 paths 或 command"
            "（兼容 file/files/cmd）；缺参且本任务已有 write_set 时闭环回填；"
            "编码目标 success 前应通过"
        ),
        action_kind="code_check",
    ),
    Capability(
        id="shell_verify",
        name="编码跑测",
        modality="background",
        when_to_use="白名单校验命令（pytest/ruff/mypy/python -m）；不受 FAWAI_DRY_RUN",
        action_kind="shell_verify",
    ),
    Capability(
        id="py_write",
        name="Python扩展·写会话文件",
        modality="background",
        when_to_use=(
            "在 data/sessions/{task_id}/ 新建或覆盖临时脚本/结果文件；"
            "args.path 相对会话根 + content；临时算数/脚本优先于此，改产品源码仍用 fs_patch。"
            "新建脚本尽最大努力 stdout 打一行 OK 或 FAIL:原因，失败非零退出，便于调用侧 expect（精确或模糊）；非硬闸。"
            "交付物默认写会话根（相对路径/`FAWAI_PY_SESSION_ROOT`），勿默认 work_root/仓根；"
            "仅当上下文已感知具体项目目录时再写到该处"
        ),
        action_kind="py_write",
    ),
    Capability(
        id="py_pip",
        name="Python扩展·装依赖",
        modality="background",
        when_to_use=(
            "向主程序运行环境（sys.executable / .venv）pip install；"
            "args.packages 为包名列表或空格串；禁止 uninstall；装包影响共享主环境"
        ),
        action_kind="py_pip",
    ),
    Capability(
        id="py_run",
        name="Python扩展·执行",
        modality="background",
        when_to_use=(
            "用主程序解释器跑会话脚本/临时代码/模块；三选一 script|code|module；"
            "stdout/stderr/meta 落盘 runs/{ts}/；可选 result_path（相对会话根，交付物优先）；不做 pdb。"
            "生成文件：无项目证据时写出会话根；有感知目录则写该处；stdout `OK: 路径`。"
            "多步执行时倾向带 expect（contains 精确，或 contains_any 模糊）；非硬闸"
        ),
        action_kind="py_run",
    ),
    Capability(
        id="shovel_search",
        name="脚本铲子·检索",
        modality="background",
        when_to_use=(
            "造临时脚本/临时代码前先检索 fawai_shovels 台账；args.query 关键词；"
            "命中带 outcome.rate（运行成败维，≠设计三维分）与近失败一句；"
            "对照当前 goal：poor/近失败勿原样照抄；能调好铲则 shell 调 invoke；"
            "无命中再 py_* / 临时脚本；通用性≥3 才可升公共铲"
        ),
        action_kind="shovel_search",
    ),
    Capability(
        id="explore_search",
        name="探仓",
        modality="background",
        when_to_use=(
            "暂时降权：缺 path 时优先 fs_grep（单词）/ fs_glob（列文件）；"
            "仅多关键词且需一次并行才 explore_search（queries）；默认只 grep、不建符号索引；"
            "须收窄 path/glob；要符号加 include_symbols=true 或 symbols_query；"
            "已有 path 勿探仓"
        ),
        action_kind="explore_search",
    ),
    Capability(
        id="git_status",
        name="Git·status",
        modality="background",
        when_to_use="查看工作区 git 状态",
        action_kind="git_status",
    ),
    Capability(
        id="git_diff",
        name="Git·diff",
        modality="background",
        when_to_use="查看 git diff（可选 path/staged）",
        action_kind="git_diff",
    ),
    Capability(
        id="git_commit",
        name="Git·commit",
        modality="background",
        when_to_use="显式提交；须 message；不自动 push",
        action_kind="git_commit",
        write_resources="path_arg",
    ),
    Capability(
        id="diff_list",
        name="会话 Diff·列表",
        modality="background",
        when_to_use="列出 confirm 模式待确认补丁",
        action_kind="diff_list",
    ),
    Capability(
        id="diff_confirm",
        name="会话 Diff·确认",
        modality="background",
        when_to_use="确认并落盘待确认补丁",
        action_kind="diff_confirm",
        write_resources="path_arg",
    ),
    Capability(
        id="diff_reject",
        name="会话 Diff·拒绝",
        modality="background",
        when_to_use="拒绝待确认补丁",
        action_kind="diff_reject",
    ),
    Capability(
        id="version_snapshot",
        name="版本管理·快照",
        modality="background",
        when_to_use="对工作区路径打版本快照",
        action_kind="version_snapshot",
    ),
    Capability(
        id="version_list",
        name="版本管理·列表",
        modality="background",
        when_to_use="列出已有版本",
        action_kind="version_list",
    ),
    Capability(
        id="version_get",
        name="版本管理·查询",
        modality="background",
        when_to_use="查看某版本详情",
        action_kind="version_get",
    ),
    Capability(
        id="version_delete",
        name="版本管理·删除",
        modality="background",
        when_to_use="删除某版本快照",
        action_kind="version_delete",
    ),
    Capability(
        id="version_rollback",
        name="版本管理·回退",
        modality="background",
        when_to_use="回退到某版本",
        action_kind="version_rollback",
        write_resources="path_arg",
    ),
    Capability(
        id="skill_search",
        name="自我进化·检索 Skill",
        modality="background",
        when_to_use=(
            "按关键词检索已沉淀的 Skill/教训；结果分 imitate（可参考）与 avoid（勿照抄）；"
            "lesson/failure 在 avoid 栏"
        ),
        action_kind="skill_search",
    ),
    Capability(
        id="skill_write",
        name="自我进化·写入 Skill",
        modality="background",
        when_to_use="将可复用步骤写入 Skill 库（经质量门）",
        action_kind="skill_write",
    ),
    Capability(
        id="skill_get",
        name="自我进化·读取 Skill",
        modality="background",
        when_to_use="按 id 读取一条 Skill 全文",
        action_kind="skill_get",
    ),
    Capability(
        id="lore_search",
        name="个人知识库·检索",
        modality="background",
        when_to_use=(
            "按 scopes 检索（必读 search_menu：efficiency 5最快→1最慢）；"
            "例 scopes=[tasks]|[work_name]|[lore]|[work_content]|[logs]；"
            "先窄高效率，空再扩或并行多个 lore_search；缺省=已开混合（勿当首选）；"
            "回含 index_catalog/search_menu；不搜旁路短 KV"
        ),
        action_kind="lore_search",
    ),
    Capability(
        id="lore_get",
        name="个人知识库·读取",
        modality="background",
        when_to_use="按笔记 id、文件路径或索引命中读取一条（非 KV key 主路径）",
        action_kind="lore_get",
    ),
    Capability(
        id="lore_set",
        name="个人知识库·写入 KV",
        modality="background",
        when_to_use="显式写入/更新一条 KV（英文点分 key；值可中英）",
        action_kind="lore_set",
    ),
    Capability(
        id="lore_note_upsert",
        name="个人知识库·写入笔记",
        modality="background",
        when_to_use="写入/更新结构化笔记",
        action_kind="lore_note_upsert",
    ),
    Capability(
        id="lore_buffer_list",
        name="个人知识库·缓冲列表",
        modality="background",
        when_to_use="查看旁路未自动落盘的候选缓冲",
        action_kind="lore_buffer_list",
    ),
    Capability(
        id="lore_vector_reindex",
        name="个人知识库·向量重建",
        modality="background",
        when_to_use="重建笔记 sqlite-vec 索引（向量开关开且已装 sqlite-vec）",
        action_kind="lore_vector_reindex",
    ),
    Capability(
        id="lore_fts_reindex",
        name="个人知识库·FTS重建",
        modality="background",
        when_to_use="重建 KV/笔记 FTS5 倒排（记忆配置 FTS5 开）",
        action_kind="lore_fts_reindex",
    ),
    Capability(
        id="name_index_search",
        name="文件名索引·检索",
        modality="background",
        when_to_use="按文件名/路径片段查预建文件名索引（Everything 式；需先 memory_index_reindex）",
        action_kind="name_index_search",
    ),
    Capability(
        id="fts_search",
        name="正文FTS·检索",
        modality="background",
        when_to_use="按词法查可读区正文 FTS5（需配置 FTS 目录并重建；非正则 rg）",
        action_kind="fts_search",
    ),
    Capability(
        id="memory_index_reindex",
        name="记忆索引·重建",
        modality="background",
        when_to_use="重建文件名索引与/或正文 FTS（args.which=all|name|fts）",
        action_kind="memory_index_reindex",
    ),
    Capability(
        id="task_context_search",
        name="任务内上下文·检索",
        modality="background",
        when_to_use=(
            "搜**本任务**历史（用户追问/工具结果/compact 外的细节）；"
            "规划近窗没有的本任务事实先用本工具；勿用 lore_search/fts 冒充；"
            "默认仅当前 task_id"
        ),
        action_kind="task_context_search",
    ),
    Capability(
        id="task_context_get",
        name="任务内上下文·回灌",
        modality="background",
        when_to_use=(
            "按 search 命中的 msg_id/index 或 observe_path 回灌本任务原文片段；"
            "文件源码仍用 fs_read"
        ),
        action_kind="task_context_get",
    ),
    Capability(
        id="success_ledger_upsert",
        name="成功价值台账·写入",
        modality="background",
        when_to_use=(
            "本步业务操作已确认成功时写入价值卡（acceptance/commands/config_refs/"
            "code_refs/targets/evidence 等）；后台自评三维，过门则实写 lore；"
            "勿把失败试错整段灌进卡"
        ),
        action_kind="success_ledger_upsert",
    ),
    Capability(
        id="success_ledger_search",
        name="成功价值台账·检索",
        modality="background",
        when_to_use=(
            "搜**本任务**已落盘的成功价值台账；是否调用由你自决；"
            "跨任务可复用经验仍走 lore_search(scopes=[lore])"
        ),
        action_kind="success_ledger_search",
    ),
    Capability(
        id="success_ledger_get",
        name="成功价值台账·读取",
        modality="background",
        when_to_use="按 success_ledger_search 命中的 id 回灌整张成功卡",
        action_kind="success_ledger_get",
    ),
    Capability(
        id="failure_ledger_upsert",
        name="失败价值台账·写入",
        modality="background",
        when_to_use=(
            "已确认有价值的失败/换道错族时写入避坑卡（symptom/cause/do_not_retry/"
            "commands_tried 等）；勿把每次 ok=false 都灌进卡；过门后进 avoid lore"
        ),
        action_kind="failure_ledger_upsert",
    ),
    Capability(
        id="failure_ledger_search",
        name="失败价值台账·检索",
        modality="background",
        when_to_use=(
            "搜**本任务**失败价值台账（避坑）；是否调用由你自决；"
            "跨任务避坑用 lore_search，命中 avoid/lesson 勿当正例"
        ),
        action_kind="failure_ledger_search",
    ),
    Capability(
        id="failure_ledger_get",
        name="失败价值台账·读取",
        modality="background",
        when_to_use="按 failure_ledger_search 命中的 id 回灌整张失败卡",
        action_kind="failure_ledger_get",
    ),
    Capability(
        id="shell",
        name="控制·shell",
        modality="background",
        when_to_use=(
            "系统查询或非工作区命令（args.cmd）；写工作区文件优先用 fs_*；"
            "探测多 CLI 用 `command -v a b c || true`，勿用 which 多参后因缺一个失败。"
            "多步执行流倾向带 expect（精确 contains 或模糊 contains_any）；非硬闸"
        ),
        action_kind="shell",
    ),
    Capability(
        id="click",
        name="控制·点击",
        modality="foreground",
        when_to_use=(
            "坐标点击兜底；x/y 为**最近截图的图内坐标**（闭环加 origin/scale）；"
            "须落在画面尺寸内；越界/荒谬坐标会失败；"
            "有 obs_ax 时优先 ui_click(ref)；输入框已有文案待发送时优先 press/hotkey"
        ),
        action_kind="click",
        needs_foreground=True,
    ),
    Capability(
        id="double_click",
        name="控制·双击",
        modality="foreground",
        when_to_use="坐标双击；args.x/y（图内或 global）",
        action_kind="double_click",
        needs_foreground=True,
    ),
    Capability(
        id="right_click",
        name="控制·右键",
        modality="foreground",
        when_to_use="坐标右键；args.x/y",
        action_kind="right_click",
        needs_foreground=True,
    ),
    Capability(
        id="scroll",
        name="控制·滚动",
        modality="foreground",
        when_to_use="滚轮；args.clicks（>0 上/<0 下）；可选 x/y",
        action_kind="scroll",
        needs_foreground=True,
    ),
    Capability(
        id="press",
        name="控制·单键",
        modality="foreground",
        when_to_use=(
            "按下单键；args.key 如 enter/tab/escape；"
            "聊天/搜索框已有待发文案时优先于估像素 click 发送按钮"
        ),
        action_kind="press",
        needs_foreground=True,
    ),
    Capability(
        id="type",
        name="控制·键入",
        modality="foreground",
        when_to_use="向前台键入；有控件树时优先 ui_type(ref,text)",
        action_kind="type",
        needs_foreground=True,
    ),
    Capability(
        id="hotkey",
        name="控制·热键",
        modality="foreground",
        when_to_use=(
            "仅当目标必须向前台发送热键；args.keys 为字符串列表，"
            "例 [\"cmd\",\"tab\"]（兼容 key/combo=\"cmd+tab\"）；"
            "跨 Space 可试 Control+←/→，优先 app_focus"
        ),
        action_kind="hotkey",
        needs_foreground=True,
    ),
    Capability(
        id="app_focus",
        name="控制·应用前台",
        modality="foreground",
        when_to_use=(
            "激活指定 App（args.app 为应用名/bundle；兼容 app_name）；"
            "成功以 frontmost 回证为准，假成功会 ok=false 并写明前台仍是谁；"
            "找回外接屏/全屏 Space 上的窗时优先于盲目滑桌面；"
            "若仍非目标前台：换 hotkey 切 Space 后再 focus，或 status=failed 交付手动切出全屏；"
            "禁止只信 activated 文案、禁止 open -g（不抢前台）；"
            "macOS 主路径；Linux 有桌面尽力（wmctrl/xdotool）"
        ),
        action_kind="app_focus",
        needs_foreground=True,
    ),
    Capability(
        id="ui_click",
        name="控制·控件点击",
        modality="foreground",
        when_to_use=(
            "C3：args.ref 必须是最近 obs_ax 摘要表中的数字编号（如 \"3\"），"
            "禁止臆造 chat-input-field 等 DOM/语义 id；可选 button=left|right|double；"
            "表中无目标则改 obs_screen+click 坐标，勿硬编 ref"
        ),
        action_kind="ui_click",
        needs_foreground=True,
    ),
    Capability(
        id="ui_type",
        name="控制·控件键入",
        modality="foreground",
        when_to_use=(
            "C3：args.ref 须为 obs_ax 数字编号后键入 args.text；"
            "禁止臆造语义 id；无 ref 时等价前台 type；中文走剪贴板粘贴"
        ),
        action_kind="ui_type",
        needs_foreground=True,
    ),
    Capability(
        id="ui_verify",
        name="控制·界面回证",
        modality="foreground",
        when_to_use=(
            "C3 L3：再采 AX，断言含 args.contains（或 text/expect）；"
            "仅 method=ax（可省略）；禁止 method=ocr/vlm；"
            "GUI 窄场景 success 前须回证通过；AX 不可用时勿假装 OCR"
        ),
        action_kind="ui_verify",
        needs_foreground=True,
    ),
    Capability(
        id="browser_navigate",
        name="浏览器·打开",
        modality="background",
        when_to_use=(
            "打开/跳转网页；args.url；可选 viewport=mobile|iphone|{width,height}"
            "（窄屏/@media 验收）；Playwright 无头默认可；"
            "不占 foreground_lease；勿用桌面点 Chrome 代替"
        ),
        action_kind="browser_navigate",
    ),
    Capability(
        id="browser_snapshot",
        name="浏览器·快照",
        modality="background",
        when_to_use=(
            "采集页内可交互控件表（编号 ref）；"
            "随后 browser_click/browser_type 必须用表中编号"
        ),
        action_kind="browser_snapshot",
    ),
    Capability(
        id="browser_click",
        name="浏览器·点击",
        modality="background",
        when_to_use=(
            "点击页内控件；args.ref 为最近 browser_snapshot 编号；"
            "可选 button=left|right|double；或 args.selector"
        ),
        action_kind="browser_click",
    ),
    Capability(
        id="browser_type",
        name="浏览器·键入",
        modality="background",
        when_to_use=(
            "向页内输入框写入；args.ref + args.text；"
            "默认 clear 后 fill；args.clear=false 则追加"
        ),
        action_kind="browser_type",
    ),
    Capability(
        id="browser_press",
        name="浏览器·按键",
        modality="background",
        when_to_use="页内按键；args.key 如 Enter/Tab/Escape；可选先 ref 聚焦",
        action_kind="browser_press",
    ),
    Capability(
        id="browser_close",
        name="浏览器·关闭",
        modality="background",
        when_to_use="关闭内置浏览器会话；任务结束或切换站点前可调用",
        action_kind="browser_close",
    ),
    Capability(
        id="wait",
        name="控制·等待",
        modality="background",
        when_to_use="短暂等待后再观察或执行",
        action_kind="wait",
    ),
    Capability(
        id="done",
        name="控制·结束",
        modality="meta",
        when_to_use="本步无更多动作，标记结束",
        action_kind="done",
    ),
)


def _effective_capability(cap: Capability) -> Capability:
    """按运行平台 / 依赖动态下调可用性（如 headless 禁用 GUI）。"""
    if cap.action_kind in _BROWSER_KINDS:
        try:
            from fawai_avatar.control.browser import browser_package_available

            if browser_package_available():
                return cap
        except Exception:  # noqa: BLE001
            pass
        return Capability(
            id=cap.id,
            name=cap.name,
            modality=cap.modality,
            when_to_use=cap.when_to_use + "（未装 playwright：[browser] extras）",
            action_kind=cap.action_kind,
            needs_foreground=cap.needs_foreground,
            write_resources=cap.write_resources,
            available=False,
        )
    if cap.action_kind not in _GUI_KINDS:
        return cap
    try:
        from fawai_avatar.platform_info import gui_available

        if gui_available():
            return cap
    except Exception:  # noqa: BLE001
        return cap
    return Capability(
        id=cap.id,
        name=cap.name,
        modality=cap.modality,
        when_to_use=cap.when_to_use + "（当前运行平台无 GUI，不可用）",
        action_kind=cap.action_kind,
        needs_foreground=cap.needs_foreground,
        write_resources=cap.write_resources,
        available=False,
    )


def list_capabilities(*, available_only: bool = True) -> list[Capability]:
    items = [_effective_capability(c) for c in CAPABILITIES]
    try:
        from fawai_avatar.mcp.capabilities_dyn import list_mcp_capabilities

        items = list(items) + list(
            list_mcp_capabilities(available_only=available_only)
        )
    except Exception:  # noqa: BLE001
        pass
    if available_only:
        items = [c for c in items if c.available]
    return items


def known_action_kinds(*, available_only: bool = True) -> frozenset[str]:
    kinds = frozenset(c.action_kind for c in list_capabilities(available_only=available_only))
    # 允许规划发出任意已缓存 mcp__ 形态（refresh 后进清单；未 refresh 时仍拒）
    return kinds


def capabilities_prompt_block(
    *,
    kinds: frozenset[str] | None = None,
    compact: bool = False,
) -> str:
    """注入规划提示的能力清单。

    kinds：仅列出这些 action_kind（硬校验仍用全量）。
    compact：一行枚举 + 短硬规则，供 vision/fs/gui 档减体积。
    """
    from fawai_avatar.platform_info import is_headless

    caps = list_capabilities()
    if kinds is not None:
        caps = [c for c in caps if c.action_kind in kinds]

    if compact:
        kind_names = ", ".join(c.action_kind for c in caps) or "(无)"
        lines = [
            f"本轮可用 action_kind（只能从下列选型；observe_kinds 须落在候选内；工具结果由闭环观察）：{kind_names}",
        ]
        rules = (
            "硬规则：工作区读写/版本 → fs_* / version_*；改代码优先 fs_patch；"
            "缺 path：先 lore/work_name 定目录再窄 fs_grep；禁止整仓 path=.；多词才 explore_search；"
            "已有 path 定向读 fs_read offset+limit；"
            "写后 code_check（须 path/paths，或 shell_verify）；禁止用 GUI 代替写文件；"
            "清单约束的是 action_kind：缺无法等价完成的动作族、"
            "或真硬闸（实名/支付/软件破解等）、或无界面却规划 GUI → status=failed；"
            "大纲/脚本/故事=模型生成+fs_write，禁止因无创意 skill 而 failed；"
            "监管授权/租约/产物键由运行时兑现，勿当缺工具而 failed。"
        )
        if kinds is not None and not kinds.isdisjoint(_BROWSER_KINDS):
            rules += (
                "网页：browser_navigate→browser_snapshot→browser_click/type(ref)；"
                "窄屏验收加 viewport=mobile；勿用桌面点/截 Chrome 冒充手机；无头默认可。"
            )
        if kinds is not None and kinds.isdisjoint(_GUI_KINDS):
            rules += "本轮提示未列 GUI；勿规划 click/type/hotkey/ui_*。"
        elif is_headless():
            rules += "无界面：禁止 GUI/截屏/obs_ax；网页用 browser_*。"
        elif kinds is not None and not kinds.isdisjoint(_GUI_KINDS):
            rules += (
                "GUI：obs_screen/obs_ax 只写 observe_kinds 勿进 actions；"
                "优先 obs_ax+ui_click/ui_type(表内 ref)；"
                "键入后须界面回证；坐标 click 仅兜底；"
                "待发文案已在输入框时优先 press/hotkey 提交；"
                "勿用 expect 断言获得焦点。"
            )
        if any(c.action_kind.startswith("mcp__") for c in caps):
            rules += " MCP：仅调用清单中的 mcp__server__tool；args 按该工具 inputSchema。"
        lines.append(rules)
        return "\n".join(lines)

    lines = [
        "可用能力清单（只能从下列 action_kind 选型；环境观察由计划字段 observe_kinds 点选——须落在候选内；编码/查代码/改产品能力优先 obs_fs 或 none；GUI 亦勿无必要默选截屏，先看前台窗摘要；工具结果由闭环自动观察）："
    ]
    for cap in caps:
        fg = "foreground" if cap.needs_foreground else "no-fg"
        wr = cap.write_resources
        lines.append(
            f"- {cap.action_kind} [{cap.modality}/{fg}/write={wr}]：{cap.when_to_use}"
        )
    rules = (
        "选型硬规则：工作区文件的创建/读写/替换/版本 → 必须用 fs_* / version_*；"
        "改已有代码优先 fs_patch（唯一匹配）；大文件禁止无 force_full_write 的整文件 fs_write；"
        "缺明确 path：先 lore_search(work_name)/窄 fs_glob 定目录，再 fs_grep(path=子目录)；禁止整 work_root；"
        "已有 path 或 path:line 则定向读改，勿再宽探仓；"
        "连续空 glob 再用 shell ls/find（遵守平台口径）；"
        "定位代码结构 → code_symbols / code_map（含 html/css；须 FAWAI_CODE_INDEX 开；勿默认走 explore）；"
        "explore_search 暂时降权：仅多关键词并行时用；默认只 grep；"
        "要符号须 include_symbols=true 或 symbols_query；queries 兼容 query/pattern/q；"
            "单关键词文本搜索必须优先 fs_grep；pattern 多选一用 a|b 勿 a\\|b；"
            "CSS/类名优先 button.send 或 send 正文；索引过期可 code_index_refresh；"
            "检索软偏好（不定死）：有线索后尽量 fs_grep 收窄；已确认相关处理后尽量精确 pattern 再小窗读/patch；"
            "仍允许宽搜翻页、code_symbols、必要小窗读；禁止无新问题同文件盲读冒充进展；"
        "定向读文件用 fs_read 的 offset+limit（1-based，0 按文件头；也认 start_line/end_line）；"
        "写后用 code_check（或 shell_verify）回证，须带 path/paths；编码类目标未 check 不得直接 success；"
        "Git 用 git_status/git_diff/git_commit（不 push）；多文件改动须 write_resources 列全路径；"
        "不冲突的读和写可一段 4～8 actions 并 act_parallel；同一路径不要同批；前台和高风险 shell 不要与其它动作同批；"
        "多步倾向 expect（精确 contains 或模糊 contains_any，非硬闸）；"
        "新建脚本 stdout 打 OK/FAIL:原因；"
        "Skill/教训 → skill_write / skill_search / skill_get；"
        "造临时脚本前 → shovel_search（先查 fawai_shovels）；能调已有铲则 shell 调；"
        "临时算数 → py_write→py_run；需求参数化/env；通用性≥3 再升铲或 skill_write；"
        "检索先读提示里的【索引配置】：按区/业务已开 methods 自行选工具；"
        "本任务历史细节（近窗/摘要没有的追问或工具结果）→ task_context_search → task_context_get；"
        "勿用 lore_search/fts_search 冒充任务上下文；"
        "本步已确认成功 → success_ledger_upsert 写价值卡（可搜；是否 search 自决）；"
        "已确认错族/换道失败 → failure_ledger_upsert 写避坑卡（勿每步 fail 都写）；"
        "跨任务复用须台账三维过门（记忆配置阈值，默认 80%）后进 lore；"
        "成功卡用 lore_search(scopes=[lore])；失败卡过门为 avoid，勿当正例；勿只标 promoted；"
        "个人知识库 → lore_search（**传 scopes**，读 search_menu 效率档）/ lore_get；"
        "lore_set / lore_note_upsert / lore_buffer_list / lore_vector_reindex / lore_fts_reindex；"
        "专用 name_index_search / fts_search；现扫 fs_grep（效率1）；重建 → memory_index_reindex；"
        "勿把 data/memory_index 当文件再搜；"
        "fs_glob 无通配时可走文件名索引；fs_grep 可 prefer_fts=true 走正文 FTS；"
        "禁止用 click/type/hotkey 代替写文件或写 Skill；"
        "禁止改进化最少核（versioning/、runtime/releases|keeper|code_watch|reload_gate|port_guard.py、"
        "workspace/paths.py）与已存在的 doc/版本/v*/ 归档（仅本智能体写路径；人工/Cursor 不限）；"
        "MCP 外挂（若清单含 mcp__*）→ 仅调用列出的 mcp__server__tool，args 按 inputSchema；优先内置能力；"
    )
    rules += (
        "网页内操作 → browser_navigate / browser_snapshot / browser_click|type|press；"
        "snapshot 的 ref 编号才可点填；勿用 click/ui_click 点系统 Chrome 窗口代替；"
    )
    if is_headless():
        rules += (
            "当前为无界面运行：GUI（click/type/hotkey/app_focus）与截屏均不可用；"
            "网页仍可用 browser_*（无头）；"
        )
    else:
        rules += "GUI 仅在目标明确依赖屏幕 App UI 时使用；"
    rules += (
        "清单约束的是 action_kind 选型，不是禁止模型写正文，也不是能力上限："
        "仅当需要清单没有且无法用现有族等价完成的动作族、或命中真硬闸（实名/支付/软件破解等）、"
        "或无界面却规划 GUI 时 status=failed 并说明边界；禁止空动作或 GUI 假 success。"
        "大纲/脚本/故事/PPT 文案/互动设计=模型直接生成并用 fs_write（或会话产物路径）落盘，"
        "属清单内交付；禁止因「无创意专用 skill / 超出能力清单」而 failed。"
        "计划模式里的监管脑授权、节点租约、产物键绑定由运行时兑现，不是 action_kind；"
        "执行脑只需写出约定产物文件并 status=success，勿因清单无这些词而 failed。"
    )
    lines.append(rules)
    return "\n".join(lines)


def looks_like_coding_goal(goal: str) -> bool:
    g = goal or ""
    return any(h.lower() in g.lower() for h in _CODING_GOAL_HINTS)


def looks_like_file_goal(goal: str) -> bool:
    text = (goal or "").strip().lower()
    if not text:
        return False
    # 路径片段（保留大小写敏感的常见扩展已在 hints 里小写匹配）
    lowered = goal.lower()
    return any(hint.lower() in lowered for hint in _FILE_GOAL_HINTS)


def _primary_file_mutation(goal: str) -> bool:
    """主目标是写/改工作区文件（夹带「鼠标画圈」等误导仍算文件主目标）。"""
    g = goal or ""
    markers = (
        "写文件",
        "写入",
        "创建文件",
        "保存为",
        "保存到",
        "记到",
        "记下",
        "fs_write",
        "fs_replace",
        "fs_patch",
    )
    return any(m in g for m in markers)


def _gui_explicit_desktop(goal: str) -> bool:
    """目标明确要求操作可见桌面/App UI（允许 GUI，即使夹带文件词）。

    不含笼统「鼠标/桌面」：C2 误导句「用鼠标在桌面画个圈写文件」须仍禁 GUI。
    """
    g = goal or ""
    if _primary_file_mutation(g):
        return False
    tips = (
        "点击",
        "操作界面",
        "操控界面",
        "Cursor界面",
        "操作Cursor",
        "操作 cursor",
        "ui_click",
        "ui_type",
        "obs_ax",
        "TextEdit",
        "文本编辑",
        "屏幕上",
        "键入",
        "双击",
        "右键",
        "热键",
        "快捷键",
    )
    return any(t.lower() in g.lower() for t in tips)


def is_beyond_capability(goal: str) -> bool:
    """目标是否明显超出首期能力清单。"""
    g = goal or ""
    lowered = g.lower()
    return any(hint.lower() in lowered for hint in _BEYOND_CAPABILITY_HINTS)


def validate_plan(goal: str, plan: Plan) -> str | None:
    """返回违规原因；合法返回 None。"""
    kinds = known_action_kinds()
    if plan.status == "need_input":
        if plan.actions:
            return "need_input 时 actions 必须为空"
        from fawai_avatar.brain.clarification import options_from_plan

        opts = options_from_plan(plan)
        # 有 options 字段但不足 2：视为不完整选择题
        raw_opts = (plan.raw or {}).get("options") if isinstance(plan.raw, dict) else None
        if raw_opts is not None and len(opts) < 2:
            return "need_input 若带 options 须至少 2 项"
        return None
    if is_beyond_capability(goal):
        if plan.status == "success":
            return "目标超出能力清单，不得 status=success；请 failed 并说明边界"
        if any((a.kind or "").strip().lower() in _GUI_KINDS for a in plan.actions):
            return "超出能力的目标禁止用 GUI 假装完成"
    for action in plan.actions:
        kind = (action.kind or "").strip().lower()
        if kind not in kinds:
            return f"未知 action.kind={kind!r}，不在能力清单内"
        if kind in _GUI_KINDS and looks_like_file_goal(goal) and not _gui_explicit_desktop(goal):
            return (
                "文件/工作区类目标禁止用 GUI 动作代替；请改用 fs_write/fs_read/fs_replace 等"
            )
    if looks_like_coding_goal(goal) or looks_like_file_goal(goal):
        for action in plan.actions:
            kind = (action.kind or "").strip().lower()
            if kind in {"fs_write", "fs_replace", "fs_patch", "git_commit"}:
                path = str((action.args or {}).get("path") or "")
                paths = [str(p) for p in ((action.args or {}).get("paths") or [])]
                needed = [p for p in ([path] if path else []) + paths if p]
                if needed and not action.write_resources:
                    return f"{kind} 须填写 write_resources 路径清单"
                for p in needed:
                    if p not in action.write_resources:
                        return f"{kind} write_resources 缺少 {p}"
    if (
        plan.status == "success"
        and not plan.actions
        and looks_like_coding_goal(goal)
        and not _history_suggests_code_checked(plan, history=None)
    ):
        # 弱提示：真正强制在 WorkerLoop 用 history 判断
        pass
    # 守规约束：分层写路径须 doc_refs / architecture_skip_reason
    from fawai_avatar.brain.compliance import validate_compliance

    compliance_err = validate_compliance(goal, plan)
    if compliance_err:
        return compliance_err
    return None


def _history_suggests_code_checked(
    plan: Plan, *, history: list[dict[str, Any]] | None
) -> bool:
    thought = (plan.thought or "").lower()
    if any(t in thought for t in ("已通过 code_check", "check ok", "语法通过")):
        return True
    for msg in history or []:
        content = str(msg.get("content") or "")
        if "code_check" in content and "→ ok" in content:
            return True
        if "shell_verify" in content and "→ ok" in content:
            return True
    return False


def _history_suggests_file_done(plan: Plan) -> bool:
    thought = (plan.thought or "").lower()
    return any(token in thought for token in ("已写入", "已读取", "内容为", "hello", "成功"))


def normalize_tool_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """展平模型常见误包：`{"args": "{\"path\":...}"}` 或 `{"args": {...}}` → 顶层字段。

    原生 tool_calls 与 JSON actions 共用。顶层已有非空 path/old/new 等时优先顶层。
    """
    if not isinstance(args, dict):
        return {}
    out = dict(args)
    nested = out.get("args")
    if nested is None:
        return _coerce_hunks_in_args(out)
    if isinstance(nested, str):
        text = nested.strip()
        if not text:
            return _coerce_hunks_in_args(out)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return _coerce_hunks_in_args(out)
        nested = parsed
    if not isinstance(nested, dict):
        return _coerce_hunks_in_args(out)

    preferred = (
        "path",
        "paths",
        "old",
        "new",
        "content",
        "hunks",
        "pattern",
        "glob",
        "offset",
        "limit",
        "cmd",
        "command",
        "query",
        "queries",
        "symbols_query",
        "doc_refs",
        "architecture_skip_reason",
    )
    lifted = dict(nested)
    for key, value in out.items():
        if key == "args":
            continue
        empty = value in (None, "", [], {})
        if key in preferred:
            if not empty:
                lifted[key] = value
            elif key not in lifted:
                lifted[key] = value
        elif key not in lifted:
            lifted[key] = value
    return _coerce_hunks_in_args(lifted)


def _coerce_hunks_in_args(args: dict[str, Any]) -> dict[str, Any]:
    """规划侧尽早把 hunks JSON 字符串洗成 list（执行侧仍会再校验）。"""
    if "hunks" not in args:
        return args
    hunks = args.get("hunks")
    if hunks is None or isinstance(hunks, list):
        return args
    if not isinstance(hunks, str):
        return args
    text = hunks.strip()
    if not text:
        out = dict(args)
        out["hunks"] = None
        return out
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return args
    if not isinstance(parsed, list):
        return args
    out = dict(args)
    out["hunks"] = parsed
    return out


def apply_capability_defaults(action: Action) -> Action:
    """按清单补全 needs_foreground；展平嵌套 args；补 write_resources；fs_read/hotkey 别名归一。"""
    kind = (action.kind or "").strip().lower()
    raw_args = dict(action.args or {})
    args = normalize_tool_args(raw_args)
    needs_fg = bool(action.needs_foreground)
    write_resources = list(action.write_resources)
    changed = args != raw_args

    if kind in {"fs_read", "fs_read_many"}:
        from fawai_avatar.workspace.fs import resolve_read_range

        default_limit = 200 if kind == "fs_read" else 120
        try:
            offset, limit = resolve_read_range(args, default_limit=default_limit)
        except ValueError:
            offset, limit = None, None
        if offset is not None and limit is not None:
            if args.get("offset") != offset or args.get("limit") != limit:
                args["offset"] = offset
                args["limit"] = limit
                changed = True
            for alias in ("start_line", "end_line", "line"):
                if alias in args:
                    args.pop(alias, None)
                    changed = True

    if kind == "hotkey":
        from fawai_avatar.control.executor import normalize_hotkey_keys

        keys = normalize_hotkey_keys(args)
        if keys and args.get("keys") != keys:
            args["keys"] = keys
            for alias in ("key", "combo", "hotkey"):
                args.pop(alias, None)
            changed = True

    if kind == "explore_search":
        from fawai_avatar.workspace.explore import coerce_explore_search_args

        coerced = coerce_explore_search_args(args)
        if coerced != args:
            args = coerced
            changed = True

    if kind == "code_check":
        from fawai_avatar.workspace.check import coerce_code_check_args

        coerced = coerce_code_check_args(args)
        if coerced != args:
            args = coerced
            changed = True

    for cap in CAPABILITIES:
        if cap.action_kind == kind:
            if cap.needs_foreground and not needs_fg:
                needs_fg = True
                changed = True
            break

    if kind in _WRITE_RESOURCE_KINDS:
        path = str(args.get("path") or "").strip()
        extra_paths = [str(p).strip() for p in (args.get("paths") or []) if str(p).strip()]
        needed = ([path] if path else []) + extra_paths
        if needed:
            merged = list(write_resources)
            for item in needed:
                if item not in merged:
                    merged.append(item)
                    changed = True
            write_resources = merged

    if not changed:
        return action
    return Action(
        kind=action.kind,
        args=args,
        needs_foreground=needs_fg,
        write_resources=write_resources,
    )


def _ensure_arch_docs(plan: Plan) -> Plan:
    """mock：分层写路径自动补 doc_refs，避免离线冒烟被守规闸误伤。"""
    from fawai_avatar.brain.compliance import ensure_architecture_decls

    return ensure_architecture_decls(plan, auto_default=True)


def mock_plan_for_goal(
    goal: str,
    *,
    history: list[dict[str, Any]] | None = None,
) -> Plan:
    """离线 mock：文件写/读目标走 fs_*，便于 C2 冒烟。"""
    hist = history or []
    if _history_has_ok(hist, "code_check") or _history_has_ok(hist, "shell_verify"):
        return Plan(
            thought="mock: code_check ok；改码回证通过",
            actions=[],
            done=True,
            status="success",
        )
    if _history_has_ok(hist, "fs_write") or _history_has_ok(hist, "skill_write"):
        if looks_like_coding_goal(goal) and not _history_has_ok(hist, "code_check"):
            path = _guess_path(goal) or "notes/coding_sample.py"
            return Plan(
                thought="mock: need code_check after write",
                actions=[Action(kind="code_check", args={"path": path})],
                done=False,
                status="continue",
            )
        return Plan(
            thought="mock: write already ok",
            actions=[],
            done=True,
            status="success",
        )
    if (
        _history_has_ok(hist, "fs_read")
        or _history_has_ok(hist, "skill_search")
        or _history_has_ok(hist, "skill_get")
        or _history_has_ok(hist, "code_symbols")
        or _history_has_ok(hist, "code_map")
        or _history_has_ok(hist, "explore_search")
        or _history_has_ok(hist, "git_status")
        or _history_has_ok(hist, "git_diff")
    ):
        snippet = _history_content_snippet(hist) or "hello"
        return Plan(
            thought=f"mock: read/search ok；内容为 {snippet}",
            actions=[],
            done=True,
            status="success",
        )

    g = goal or ""
    gl = g.lower()
    if is_beyond_capability(g):
        return Plan(
            thought="mock: 目标超出能力清单（未提供该 App/实名/支付等能力），status=failed",
            actions=[],
            done=True,
            status="failed",
        )
    # 执行中澄清：演示目标需用户先选路线；答复注入后继续
    if any(
        k in g
        for k in (
            "澄清演示",
            "need_input",
            "【澄清】",
            "执行中澄清",
        )
    ) and "【用户澄清答复】" not in g and not any(
        "【用户澄清答复】" in str(h.get("content") or "") for h in hist if isinstance(h, dict)
    ):
        return Plan(
            thought="将覆盖 notes/demo.txt：选保留旧内容还是整文件重写？",
            actions=[],
            done=False,
            status="need_input",
            raw={
                "thought": "将覆盖 notes/demo.txt：选保留旧内容还是整文件重写？",
                "status": "need_input",
                "options": [
                    {"id": "keep", "label": "保留旧内容，追加一行"},
                    {"id": "rewrite", "label": "整文件重写为 hello"},
                ],
            },
        )
    if "【用户澄清答复】" in g and any(
        k in g for k in ("澄清演示", "need_input", "执行中澄清")
    ):
        path = _guess_path(g) or "notes/demo.txt"
        if "整文件重写" in g or "rewrite" in gl:
            return Plan(
                thought="mock: rewrite after clarification",
                actions=[
                    Action(
                        kind="fs_write",
                        args={"path": path, "content": "hello\n"},
                        write_resources=[path],
                    )
                ],
                done=False,
                status="continue",
            )
        return Plan(
            thought="mock: append after clarification",
            actions=[
                Action(
                    kind="fs_write",
                    args={"path": path, "content": "kept\nextra\n"},
                    write_resources=[path],
                )
            ],
            done=False,
            status="continue",
        )
    # E1：沉淀 Skill
    if any(k in g for k in ("沉淀 Skill", "写入 Skill", "skill_write", "沉淀技能", "写一条 Skill")):
        sid = _guess_skill_id(g) or "c4_hello"
        return Plan(
            thought="mock: skill_write",
            actions=[
                Action(
                    kind="skill_write",
                    args={
                        "id": sid,
                        "title": "C4 hello skill",
                        "when_to_use": "演示自我进化 Skill 写入",
                        "steps": "1. skill_write\n2. skill_search 验证",
                        "tags": ["c4", "demo"],
                        "source": "demo_c4",
                    },
                )
            ],
            done=False,
            status="continue",
        )
    if any(k in g for k in ("检索 Skill", "搜索 Skill", "skill_search", "查找 Skill")):
        q = _guess_skill_id(g) or "c4"
        return Plan(
            thought="mock: skill_search",
            actions=[Action(kind="skill_search", args={"query": q})],
            done=False,
            status="continue",
        )
    if _history_has_ok(hist, "fs_patch") and not _history_has_ok(hist, "code_check"):
        path = _guess_path(g) or _guess_code_path(g) or "sample.py"
        return Plan(
            thought="mock: code_check after patch",
            actions=[Action(kind="code_check", args={"path": path})],
            done=False,
            status="continue",
        )
    if any(k in g for k in ("探仓", "explore_search", "并行搜索")):
        return Plan(
            thought="mock: explore_search",
            actions=[
                Action(
                    kind="explore_search",
                    args={"queries": [_guess_code_query(g) or "def "], "path": "."},
                )
            ],
            done=False,
            status="continue",
        )
    if any(k in g for k in ("git status", "git_status", "仓库状态")):
        return Plan(
            thought="mock: git_status",
            actions=[Action(kind="git_status", args={})],
            done=False,
            status="continue",
        )
    if any(k in g for k in ("fs_patch", "精准补丁", "补丁改", "局部修改")) or (
        looks_like_coding_goal(g) and any(k in g for k in ("改", "修", "替换为", "改成"))
    ):
        path = _guess_path(g) or "notes/coding_sample.py"
        old = "return 1"
        new = "return 2"
        if "hello" in gl:
            old, new = 'return "hello"', 'return "hello-patched"'
        return _ensure_arch_docs(
            Plan(
                thought="mock: fs_patch",
                actions=[
                    Action(
                        kind="fs_patch",
                        args={"path": path, "old": old, "new": new},
                        write_resources=[path],
                    )
                ],
                done=False,
                status="continue",
            )
        )
    if any(k in g for k in ("仓地图", "code_map", "代码地图", "结构地图")):
        path = _guess_path(g) or _guess_code_path(g) or "."
        return Plan(
            thought="mock: code_map",
            actions=[Action(kind="code_map", args={"path": path, "query": _guess_code_query(g)})],
            done=False,
            status="continue",
        )
    if any(k in g for k in ("刷新索引", "code_index_refresh", "重建索引")):
        path = _guess_path(g) or _guess_code_path(g) or "."
        return Plan(
            thought="mock: code_index_refresh",
            actions=[Action(kind="code_index_refresh", args={"path": path})],
            done=False,
            status="continue",
        )
    if any(k in g for k in ("代码索引", "code_symbols", "列符号", "找函数", "找类", "符号表")):
        path = _guess_path(g) or _guess_code_path(g) or "."
        return Plan(
            thought="mock: code_symbols",
            actions=[
                Action(
                    kind="code_symbols",
                    args={"path": path, "query": _guess_code_query(g)},
                )
            ],
            done=False,
            status="continue",
        )
    if any(k in g for k in ("读取", "读一下", "读出", "打开并读")) or (
        "read" in gl and (".txt" in gl or "notes/" in gl)
    ):
        path = _guess_path(g) or "notes/c2_hello.txt"
        return Plan(
            thought="mock: file read via fs_read",
            actions=[Action(kind="fs_read", args={"path": path})],
            done=False,
            status="continue",
        )
    # 误导目标（鼠标/画圈）仍按写文件处理
    if looks_like_file_goal(g) and any(
        k in g for k in ("写入", "写到", "写文件", "创建", "保存", "write", "完成写")
    ):
        path = _guess_path(g) or "notes/c2_hello.txt"
        content = "hello" if "hello" in gl else "hello"
        return _ensure_arch_docs(
            Plan(
                thought="mock: file write via fs_write（忽略鼠标/画圈误导，走后台）",
                actions=[
                    Action(
                        kind="fs_write",
                        args={"path": path, "content": content},
                        write_resources=[path],
                    )
                ],
                done=False,
                status="continue",
            )
        )
    return Plan(
        thought="mock",
        actions=[Action(kind="done", args={})],
        done=True,
        status="success",
    )


def _history_has_ok(history: list[dict[str, Any]], kind: str) -> bool:
    for msg in history:
        if msg.get("role") != "user":
            continue
        content = str(msg.get("content") or "")
        if f"- {kind}" in content and "→ ok" in content:
            return True
    return False


def _history_content_snippet(history: list[dict[str, Any]]) -> str | None:
    for msg in reversed(history):
        if msg.get("role") != "user":
            continue
        content = str(msg.get("content") or "")
        if "content:" in content:
            part = content.split("content:", 1)[1].strip()
            return part.splitlines()[0].strip() if part else None
        # fs_read lines 回灌
        if "lines:" in content:
            part = content.split("lines:", 1)[1].strip()
            return part.splitlines()[0].strip() if part else None
    return None



def _guess_path(goal: str) -> str | None:
    import re

    match = re.search(r"([\w./-]+\.(?:txt|md|json|py|csv))", goal)
    if match:
        return match.group(1)
    match = re.search(r"(notes/[\w./-]+)", goal)
    if match:
        return match.group(1)
    match = re.search(r"(doc/版本/v[\w.-]+/[\w./-]+)", goal)
    if match:
        return match.group(1)
    match = re.search(r"(fawai_avatar/versioning/[\w./-]+)", goal)
    if match:
        return match.group(1)
    return None


def _guess_skill_id(goal: str) -> str | None:
    import re

    match = re.search(r"\bid[=: ]+([a-z0-9][a-z0-9_-]{1,63})", goal, re.I)
    if match:
        return match.group(1).lower()
    match = re.search(r"\b(c4_[a-z0-9_-]+)\b", goal, re.I)
    if match:
        return match.group(1).lower()
    return None


def _guess_code_path(goal: str) -> str | None:
    import re

    match = re.search(r"(fawai_avatar(?:/[\w.-]+)*)", goal)
    if match:
        return match.group(1)
    match = re.search(r"([\w./-]+/)", goal)
    if match and not match.group(1).startswith("http"):
        return match.group(1).rstrip("/")
    return None


def _guess_code_query(goal: str) -> str:
    import re

    match = re.search(r"(?:query|关键词|名叫|名为|符号)\s*[:=]?\s*([A-Za-z_][\w]*)", goal)
    if match:
        return match.group(1)
    match = re.search(r"\b([A-Z][A-Za-z0-9_]+)\b", goal)
    if match and match.group(1) not in ("Skill", "Python", "JSON"):
        return match.group(1)
    return ""
