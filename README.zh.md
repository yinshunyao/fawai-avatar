# fawai-avatar

面向 PC / 手机的**操作分身**：理解目标 → 观察环境 → 规划下一步 → 执行工具/键鼠 → 再核对结果。可读文档在 `doc/`；实现在 `fawai_avatar/`；个人知识库在 `fawai_lore/`；可复用脚本铲在 `fawai_shovels/`。

English: [README.md](README.md).

## 它是什么

不是聊天机器人，也不是只改代码的 IDE 插件。它是一台本机（或无界面服务器）上常驻的智能助手：你从局域网 PWA / 公网穿透发任务，它在**本机**干活——读写工作区、跑 shell 校验、检索代码、沉淀技能与记忆；有桌面时还可截屏与键鼠操控（GUI 接地仍在演进）。

| 角色 | 职责 |
|:---|:---|
| **大脑** | 据目标与观察规划下一步；不直接摸设备 |
| **控制** | 执行工具 / 脚本 / 键鼠；硬闸门校验租约与路径 |
| **观察** | 截屏、文件态、工具结果摘要 → 回灌规划 |
| **任务线** | 一总管 + 多解耦工作任务；前台键鼠串行、后台可并行；复杂任务可出计划图 |
| **知识线** | Skill 库、个人知识库（KV/笔记）、脚本铲子、任务复盘改代码 |

单任务闭环：`观察 → 大脑 → 控制 → 再观察`。多任务由总管接需求、仲裁资源；各工作任务自有轨迹与检查点，进程重启可续跑，PWA 也可从指定步骤恢复。

## 智能助手能力（已支持）

功能真源：[`doc/功能/【总表】功能一览.md`](doc/功能/【总表】功能一览.md)。下面按「用户能让它做什么」归纳。

### 对话与跨端

- **会话网关 + PWA**：本机 HTTP/WS；手机同 Wi‑Fi 打开口令页即可聊；可选 frpc 公网穿透、云端中继骨架
- **任务线程**：一任务一条对话；新任务 / 追问 / 执行中软追加（pending 注入；追问不拼进 goal / 同泡）；粘贴图片挂附件再发；归档 API 仍可用（PWA 入口暂隐）
- **对话内步骤流**：progress 直接插进线程（无独立时间线菜单）；同步簇合并折叠；簇头粘住本步业务意图 `step_label`；facts 展示模型 · 思考开/关 · token · 耗时；等待模型时深度思考与普通输出直播进步骤卡（`meta.live`，不落历史）；规划/落盘可读凭据，**Web 出站脱敏**（`channel/redact.message_dict_for_client`）
- **从步骤恢复**：步骤卡悬停（桌面）或常显（窄屏）「从此恢复」；重启后孤儿任务按检查点续跑
- **语音输入**：麦克风 → ASR 填入输入框
- **界面中/英**：随时切换（宽屏顶栏 + 系统配置；窄屏仅系统配置）；模型配置、系统配置、服务运维分栏

### 理解与规划

- **LLM 接入**：OpenAI 兼容；纯文本 / 多模态智能路由；远端 `/models` 列表；生成参数按模型档案；深度思考按角色 + 档案（主脑开 / 快模型与观察反省关）；Web「模型配置」；重启后路由以磁盘 `.env` 为准
- **主脑委派快模型**：规划时可把子目标交给模型配置里的快模型路由（空则关闭）；段末摘要回灌主脑验收
- **复杂任务计划模式**：编排脑自动判复杂度 → 出任务图并立即调度（无人审）；三层 = 编排脑 → 监管脑 → 执行脑；追加可真改 `reorder` / `lease_adjust`；失败默认**重试失败节点**（保留已完成）；仅显式口令 / 按钮才整图重规划；侧栏只见父计划，线程内节点卡 + 右上角有向图浮层；关：`FAWAI_PLAN_MODE=0`
- **能力清单选型**：只从登记能力出招；文件类目标强制 `fs_*`，禁止用 GUI 假装写文件
- **规划提示档**：按本轮目的裁剪 system / 是否附图（vision / fs / gui / full）
- **执行中澄清**：尽最大努力默认；高代价歧义才 `need_input`（可出选择题）后续跑
- **答复覆盖闸**：问询 / 查找 / 硬件盘点类禁止「已汇总并结束」式假成功
- **必要路径台账**：恢复不得收成「只攻一条未决路径」；与终态 failed 交付配合

### 执行中纠偏（与事后复盘同一进化链）

- **执行反省**（动作失败）：仅 `retry` / `alternate`——**无 abort**；止损由规划 `status=failed`；含方法族换道与远端空转闸
- **观察反省**（本步工具全成功后）：轻量 thought / progress / notes / 软 hints（关：`FAWAI_OBS_REFLECT=0`）；不直接收束任务
- **步骤重复**（成功同构空转）：先指纹零 LLM，再 LLM 裁决 `continue` / `alternate`；可 `allow_repeat` 豁免本步

### 编码与文件（后台，不受 `FAWAI_DRY_RUN`）

- **工作区文件**：读可扩根；写限 work_root；禁 shell 绕过 fs
- **精准补丁 / 多文件清单 / 会话 Diff**；补丁锚点未接地 → 失败并附原文小窗；`code_check`、白名单 `shell_verify`
- **Python 扩展**：`py_write` / `py_pip` / `py_run`（会话目录写码、主程序 venv 装包、跑测落盘）
- **代码索引 + 检索**：多语言符号表 / 仓地图；默认主走 `fs_grep` / `fs_glob`（探仓暂时降权；符号借用须 `include_symbols`）
- **脚本铲子生命周期**：造临时脚本前 `shovel_search`（台账 `fawai_shovels/`）；写前【铲子优先】软闸、写后【铲子提炼】软提示；选型序：登记工具 → 已有铲 → 临时 `py_*` / 脚本
- **Git**：status / diff / commit（不自动 push）
- **闸门**：弱写入拦截、页面类须说清可见副作用、守规约束（规则注册表 + 分层写 doc 引用）。探索 / 定位 / 宽检索降质 / 工具观察 soft 压缩**默认关**（环境变量显式开启）
- **版本快照**：写入前可自动快照，可查询 / 回退

### 桌面观察与操控（macOS 默认随包装齐；Linux 需 `.[gui]`；headless 自动关掉）

- **观察选取**：步 0 不默截屏；LLM 点选截屏 / **AX 控件树** / 文件 / 工具结果等；仅配置 soft 上限时才压缩大日志
- **多屏**：`displays` + `screen_target`（默认前台窗所在屏）；图内坐标换算 + `app_focus`；禁自动扫全屏 / Space
- **控制**：键鼠与工具执行；前台 **lease** 全局串行；`ui_click`/`ui_type`（按 `ref`）+ 双击/右键/滚动/单键
- **C3 窄场景（macOS TextEdit）**：`obs_ax` → 控件接地 → `verify_ui_ax` 界面回证；不承诺任意 App / Electron 点准

### 浏览器操作（需 `.[browser]`；默认无头，与 OS headless 无关）

- **Playwright 内置页**：`browser_navigate` → `browser_snapshot`（编号 ref）→ `browser_click` / `browser_type` / `browser_press`；`browser_close` 释放
- **不占**前台键鼠租约；勿用桌面点系统 Chrome 代替
- 安装：`pip install -e ".[browser]"` 后 `playwright install chromium`；`FAWAI_BROWSER_HEADLESS=0` 可有头

### 记忆、进化与运维

- **个人知识库**（`fawai_lore`）：KV + 笔记 + 旁路缓冲；PWA 记忆页三 Tab（待确认 / 已入库 / 最近）；已入库可启用 / 停用 / 删除
- **Skill 库**：可检索注入规划；质量门入库；失败可沉淀为 `lesson`
- **任务复盘与失败分析**：放行后按机制视角全维深析（规划/观察/动作/反省/结果判定）→ 优化清单（Skill / lesson / 代码 / 规则 / 提示）→ 勾选后备份再改；PWA 单条复盘（联合 API 保留）；成功与失败均可复盘
- **自我进化写盘**：可改产品树大部分路径；**禁改**回退 / 保活 / 闸门最少核（Cursor / 人工改盘不受限）
- **服务保活**：崩溃自动拉起；子进程环境以磁盘 `.env` 覆盖（模型/系统配置重启后与 Web 一致）；代码热重启开关（默认关，服务与版本可开；执行中任务延后）；端口野进程回收；后台日志；启动错误一键修复任务；运行时版本存档 / 选版启动
- **系统日志**：订阅落盘；技能/操作失败可一键分析并建整改任务
- **任务统计**：token、墙钟、轮次 / 工具明细；侧栏复制 task id；计划模式父任务用量为各节点之和

### 平台

| 环境 | 能做什么 |
|:---|:---|
| **macOS + 桌面** | 网关 + 编码/文件/shell + 截屏键鼠（默认）+ 可选浏览器 |
| **Linux 无界面** | 网关 + 编码/文件/shell + 可选无头浏览器；无 GUI/截屏 |
| **有 DISPLAY 的 Linux** | 可装 `.[gui]` 开桌面能力；浏览器仍推荐 `.[browser]` |

进程自动探测 OS / headless（也可用 `FAWAI_HEADLESS=1` 强制）。

## 整体架构

本机常驻的「观察 → 规划 → 执行」操作分身，外挂跨端会话壳。后台改文件 / 编码已强；桌面 GUI 接地仍在加深。复杂目标可进计划模式：编排脑出图，监管脑按节点自闭环，执行脑跑步级工具。

```text
手机/浏览器 PWA ──HTTP/WS──► 会话网关 ──► 会话枢纽
                              │
                              ▼
                         总管 Supervisor
                    （建任务 / 租约 / 仲裁）
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         工作任务 A      工作任务 B      …（解耦并行）
              │               │
              │          （计划模式）编排脑出图
              │               → 监管脑子任务（不进侧栏）
              ▼               ▼
     ┌─ 观察 ──► 大脑 ──► 控制 ─┐
     │  (截屏/文件/工具摘要)      │
     │  (选型·委派·计划图·反省·闸门)  (fs/shell/键鼠) │
     └──────── 再观察 ◄──────────┘
              │
    知识线：Skill / lore / 铲子 / 任务复盘与失败分析 → 改码升版
    运维线：keeper / 检查点 / 从步骤恢复 / 运行时版本 / 系统日志
```

| 层 | 做什么 | 不做什么 |
|:---|:---|:---|
| **通道** | 收发、线程、附件、语音、步骤流、设置 | 不直接点键鼠 |
| **任务线** | 一总管 + 多 Worker；前台 lease 串行、后台写清单仲裁；检查点；复杂任务计划图（父会话 + 节点卡） | 不做跨机调度 |
| **大脑** | 规划、能力选型、委派、计划编排/监管、执行/观察反省、步骤重复、守规/答复闸 | 不摸设备 |
| **控制** | 工具与键鼠硬执行 | 不做任务编排 |
| **观察** | 环境态 → LLM 可读摘要 | 不擅自改系统 |
| **知识线** | Skill、个人知识库、脚本铲子、任务复盘与失败分析、按需改码 | 不做每任务自动蒸馏 |

学习闭环（促进自我进化）：执行中 **执行反省 / 观察反省 / 步骤重复** 当场纠偏 → 结束后可开 **任务复盘**（机制视角全维分析 → 优化清单 → 勾选后备份再改：Skill / lesson / 代码 / 规则 / 提示）→ **系统日志** / 启动失败可一键建整改任务。口径见 [`doc/功能/任务复盘/`](doc/功能/任务复盘/说明.md) 与 [分析规则](doc/功能/任务复盘/分析规则.md)。

能力四件套进度：**C1 LLM / C2 完成任务 / C4 自我进化** 已立住；**C3 自主操控** 窄场景（TextEdit + AX）已支持，任意 App / Electron 仍后置。

### 架构优点

1. **职责切得干净**：大脑 / 控制 / 观察 + 总管 / Worker 边界清楚；通道不掺执行；计划模式再拆编排 / 监管 / 执行三层。
2. **假成功防得认真**：能力清单、答复覆盖、弱写入、可见副作用、守规、必要路径台账、禁 shell 绕 fs——代码闸门压幻觉。
3. **后台助手已可用**：编码闭环、计划模式拆图调度与失败节点重试、检查点 + 从步骤恢复、headless Linux 可跑日常改仓。
4. **复盘与失败分析驱动自我学习**：任务复盘按智能体机制找根因（规划/观察/动作/反省/结果判定），不单复述终态文案；优化项须通用于同类任务，可落 Skill、lesson、代码补丁、规则与提示词。执行中反省 / 观察反省 / 步骤重复 + 日志一键分析与复盘同一进化链。
5. **自进化有刹车**：禁改回退 / 保活 / 闸门最少核；复盘须「放行分析 → 勾选执行」且先备份，避免脏写棘轮。
6. **工程可运维**：keeper、`.env` 覆盖拉起、可选热重启（默认关）、端口回收、运行时选版、日志/启动一键整改、平台自动探测。
7. **文档与实现对齐**：议题 → 功能总表 → 术语表，放行纪律清楚；机制对照见 `doc/分析/`。

### 下一步 Todo

- [x] **C3 窄场景 GUI 闭环**：TextEdit + AX `obs_ax` / `ui_*` / 界面回证（见功能「自主操控」）
- [x] **截屏识别接地**：模型配置可选 VLM / OCR；OCR 默认 `qwen3.5-ocr` 出框表 + `ui_click(ocrN)`
- [ ] **C3 深化**：Electron（Cursor/Chrome）控件级点准加强；任意桌面；图标检测
- [ ] **GUI 与结构化路径体感对齐**：继续压「后台强、桌面飘」的残余缺口
- [ ] **闸门可观测与可排障**：统一闸门台账 / 命中原因外显，降低多约束叠加时的调试成本
- [x] **复杂任务计划模式**：编排脑出图自动调度、reorder/lease、失败节点重试、显式整图重规划、会话有向图 + 节点卡
- [x] **脚本铲子生命周期**：`shovel_search` + 写前/写后软闸（台账 `fawai_shovels/`）
- [x] **多屏观察**：`displays` / `screen_target` / `app_focus`（真机双屏 / Space 仍待核）
- [ ] **步级成本与延迟**：主脑委派快模型已落地（模型配置 `fast` 路由）；继续压反省 / 观察反省 / 观察选型调用
- [ ] **取消与子进程生命周期**：协作停步之外，明确可强杀 / 可回收的 OS 子进程边界
- [ ] **记忆与进化质量门**：Skill / lore / 铲子写入与复盘清单的人审或自动质量门槛；按需蒸馏策略落地
- [ ] **计划模式会话拖拽改图**：当前只读有向图 + 机读修订；人机拖拽改依赖为可选后置
- [ ] **手机本机操控**：在 PC C3 达标后开 adb / 触控语义（PWA 目前是会话端，不是操控端）
- [ ] **（可选）跨机调度**：若产品需要多机吞吐，再评估跳出单机 SQLite 进程内模型

## 文档地图

| 路径 | 内容 |
|:---|:---|
| `doc/功能/` | 已放行 / 已支持功能与验收 |
| `doc/议题/` | 讨论与原则（≠ 自动放行） |
| `doc/分析/` | 机制对照与改进备忘（非放行） |
| `doc/测试/` | 冒烟 / 系统 / 端到端任务说明 |
| `doc/版本/` | 各版加权评测总分 |
| `doc/术语与称呼索引.md` | 规范名与工具 id 真源 |
| `study/介绍案例/小红书宣传/fawai-avatar/` | 对外宣传文案与 PWA 实拍配图（工作区 study，非本仓 `doc/`） |

## 运行

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore          # 个人知识库 KV + 笔记库
pip install -e "./fawai_lore[vector]"  # 可选：sqlite-vec 笔记向量（装后 FAWAI_LORE_VECTOR 默认 auto 即开）
pip install -e .                 # 无界面 / 编码·文件·shell / 网关；macOS 会按平台条件装 GUI
# pip install -e ".[gui]"        # Linux 有 DISPLAY 时补桌面截屏与键鼠
# pip install -e ".[browser]" && playwright install chromium  # 可选：无头浏览器
cp .env.example .env   # 系统项；LLM 在 Web「模型配置」写库；.env 已 gitignore
# 默认路由：纯文本 qwen3.7-plus；多模态 qwen3-vl-plus
# LLM / 快模型路由请在 Web「模型配置」设置（写入 llm_settings.sqlite）
# 可选：FAWAI_PLAN_MODE=0               # 关闭复杂任务计划模式（默认 auto）
export FAWAI_DRY_RUN=1
.venv/bin/python -m fawai_avatar.llm.client        # 试跑文本路由
.venv/bin/python -m fawai_avatar.demo_thinking     # 深度思考按角色/档案
.venv/bin/python -m fawai_avatar.demo_llm_live     # 直播节流自检（无网）
.venv/bin/python -m fawai_avatar.demo_smoke
.venv/bin/python -m fawai_avatar.demo_platform     # 平台/headless 闸门
.venv/bin/python -m fawai_avatar.demo_browser      # 浏览器工具（可选 FAWAI_BROWSER_LIVE=1）
.venv/bin/python -m fawai_avatar.demo_fs_version   # 后台文件+版本（无 GUI）
.venv/bin/python -m fawai_avatar.demo_c2           # 完成任务 A/B/C/beyond
.venv/bin/python -m fawai_avatar.demo_clarification  # 执行中澄清 need_input→续跑
.venv/bin/python -m fawai_avatar.demo_obs_reflect  # 工具成功后的观察反省
.venv/bin/python -m fawai_avatar.demo_observe_select  # observe_kinds 选取
.venv/bin/python -m fawai_avatar.demo_multiscreen  # 多屏 displays / screen_target
.venv/bin/python -m fawai_avatar.demo_c3           # C3：gui 档 / ui_* / obs_ax（可选 FAWAI_C3_LIVE=1）
.venv/bin/python -m fawai_avatar.demo_c4           # 自我进化 E1/E2
.venv/bin/python -m fawai_avatar.demo_lore         # fawai_lore KV+笔记与长记忆旁路 P0
.venv/bin/python -m fawai_avatar.demo_code_index   # 代码索引 symbols/map
.venv/bin/python -m fawai_avatar.demo_coding_loop  # 编码闭环 P0/P1
.venv/bin/python -m fawai_avatar.demo_py_session   # Python 扩展：会话写码/装包/跑测
.venv/bin/python -m fawai_avatar.demo_shovel_lifecycle  # 脚本铲子：search + 软闸
FAWAI_BRAIN_MOCK=1 .venv/bin/python -m fawai_avatar.demo_plan_mode  # 计划模式：进模/调度/重试节点/整图重规划
.venv/bin/python -m fawai_avatar.demo_resume_restart  # 重启后检查点续跑
.venv/bin/python -m fawai_avatar.demo_eval_priority  # 版本评测优先改进套件
# FAWAI_EVAL_CAPTURE=1 .venv/bin/python -m fawai_avatar.demo_eval_priority
```

后台动作（不受 `FAWAI_DRY_RUN`）：`fs_*` / `code_*` / `version_*` / `skill_*` / `lore_*` / `git_*` / `explore_*` / `shovel_search` / `shell_verify` / `py_*` / `diff_*`。  
浏览器动作（受 `FAWAI_DRY_RUN`，不占前台租约）：`browser_*`。

## Linux 无界面

无桌面的 Linux 服务器可直接跑网关 + 编码/文件/shell；可选装 `.[browser]` 做无头网页操作。进程会自动探测 OS 与是否 headless（也可用 `FAWAI_HEADLESS=1` 强制）；大脑按平台选型，GUI/截屏不可用。

```bash
pip install -e .
# 可选穿透：安装本机架构 frpc，并设 FAWAI_FRPC_BIN=/path/to/frpc
# 仓库 bin/frpc 若为 macOS Mach-O，在 Linux 上会被拒绝
export FAWAI_DRY_RUN=0          # 真改文件 / 跑 shell
.venv/bin/python -m fawai_avatar.demo_platform
.venv/bin/python -m fawai_avatar.channel.lan_gateway
```

## 消费安装包（GitHub Release）

给普通用户的前缀安装包（自带私有 Python，无需本机先装 Python / venv）：

```bash
python3 packaging/build_release.py --target auto   # 本机构建当前平台
# 推送 v* 标签 → GitHub Actions 打三端包并上传 Release
# 或：bash packaging/publish_github_release.sh
```

说明与中/英启动器文案见 [`packaging/README.md`](packaging/README.md)。用户解压后运行 Start；本机默认打开 `http://127.0.0.1:7980/`，手机自行打开局域网地址。

## 跨端会话（局域网 PWA）

在 **macOS 或 Linux** 本机启动会话网关，手机同一 Wi‑Fi 打开终端打印的 URL，输入口令：

```bash
export FAWAI_DRY_RUN=1
.venv/bin/python -m fawai_avatar.channel.lan_gateway
# 或：fawai-gateway / fawai-keeper
```

默认经**服务保活**外层拉起（崩溃与 Web「重启」后自动再起；拉起前用磁盘 `.env` 覆盖子进程环境，避免 Web 已改模型路由重启又跑回旧值；**代码热重启默认关**，可在 Web「服务与版本」打开或设 `FAWAI_CODE_RELOAD=1`，有 `running`/`blocked` 任务时延后到空闲；野进程端口回收，关：`FAWAI_PORT_RECLAIM=0`）。调试直接跑网关：`FAWAI_NO_KEEPER=1`（无热重载 / 端口回收）。  
Web「服务」面板可存档/管理**运行时版本**、查看后台日志、一键修复启动错误，并选版启动。

可选：`FAWAI_GATEWAY_PASSCODE` 固定口令；`FAWAI_RELAY_URL` 挂云端中继骨架。  
公网穿透：`.env` 配 `FAWAI_FRP_SERVER_ADDR` / `FAWAI_FRP_REMOTE_PORT`（及可选 `FAWAI_FRP_SERVER_PORT`），网关会拉起本机 `frpc`（`bin/frpc` 或 `FAWAI_FRPC_BIN`）。自检：`.venv/bin/python -m fawai_avatar.channel.selfcheck`。
