# GUI 操控：业界方案对照与差距

对照对象：fawai-avatar **当前**桌面/浏览器 GUI 能力（C3 窄场景 + 坐标兜底 + Playwright 浏览器），相对业界 Computer Use / GUI Agent 常见架构。  
目的：钉清「弱在哪一层」、领先方案在做什么、本仓架构上已有什么可复用；**不等于**功能放行。

关联真源：

| 类型 | 路径 |
|:---|:---|
| 自主操控 | [功能/自主操控](../功能/自主操控/说明.md)；[议题/自主操控C3](../议题/自主操控C3/结论.md) |
| 观察 / 控制 | [功能/观察](../功能/观察/说明.md)；[功能/控制](../功能/控制/说明.md) |
| 能力栈 | [议题/全能助手短板](../议题/全能助手短板/结论.md) L0～L3 |
| 评测证据 | [版本/v0.1.2](../版本/v0.1.2/评测结果.md) D4 |
| 租约 | [功能/任务与事件管理](../功能/任务与事件管理/说明.md) |

**已核：** 本仓 TextEdit+AX / `ui_*` / `verify_ui_ax` / `browser_*` / 前台 lease；业界 Pattern A/B/C 与 OmniParser、UI-TARS、Claude computer use、OSWorld 公开分数（见下表来源）。  
**未核：** Operator / Claude / UI-TARS 在本机同任务实测；各厂商最新 toolset 版本逐条参数（公开文档会变）。

---

## 1. 先定口径：业界「领先」领先在哪

公开基准上，**即便最强 Computer Use 也远未「任意 App 稳准」**：

| 基准（公开） | 大致水平 | 含义 |
|:---|:---|:---|
| OSWorld 类桌面长任务 | 约 20%～50% 量级（随模型/步数/harness 差很大） | 跨 App、长 horizon 仍脆 |
| ScreenSpot / ScreenSpot-Pro | 强模型/专用接地可到高几十；Pro 高分辨率小图标仍难 | **接地**（点对哪）是独立瓶颈 |
| WebArena 等网页 | 常高于纯桌面 | DOM/无障碍树比像素好用 |

因此对照不应写成「别人全能、我们一无是处」。更准确的分层是：

1. **感知与接地模型**（看图 → 坐标 / 控件 id）——业界砸数据与专用模型处。  
2. **动作与环境 harness**（截屏分辨率、zoom、批动作、沙箱桌面）——厂商 toolset 成熟处。  
3. **产品运行时**（选型路由、租约、回证、失败反省、浏览器 vs 桌面分流）——**fawai 相对有设计优势，业界许多 demo 反而薄**。

GUI「体感弱」主要落在 **1+2**；**3** 不必推倒重来。

---

## 2. fawai 现状（按 L0～L3）

对齐短板议题的栈：

| 层 | 业界要什么 | fawai 现状 | 强弱 |
|:---|:---|:---|:---|
| **L0 设备 I/O** | 键鼠、焦点、多屏、可选沙箱 VM | `click`/`type`/`hotkey`/`scroll`/…；`app_focus`；多屏 `displays`/`screen_target`；前台 lease | **中高**（本机驱动有；无标准沙箱桌面） |
| **L1 观察** | 截屏 + UI 树 + OCR/VLM 解析 | `obs_screen`、前台窗摘要、`obs_ax`（macOS）；浏览器 `browser_snapshot` | **中**：原生 AX 有；**无**通用屏解析/OCR 点准；非 Darwin AX 弱 |
| **L2 接地** | 意图→稳定定位→动作 | TextEdit：`ref`→`ui_click`/`ui_type`；否则像素坐标；浏览器编号 ref | **窄场景中、通用低**：Electron/任意 App 未承诺 |
| **L3 回证** | 动作后可机判成败 | `verify_ui_ax`；文件侧强；GUI 禁空树冒充 | **中**：AX 串断言有；**无**视觉/OCR 回证；真机 C3 live 未固定进评测流水 |

产品边界（已定）：文件主路径走 `fs_*`，禁止 GUI 代写；浏览器不占前台键鼠租约——这是正确分流，不是「GUI 弱」的借口，但会让「只会点鼠标」的用户体感更偏后台。

---

## 3. 业界三类架构（对照锚）

生产侧常见三分法（与 OmniParser / Computer-Use 工程文同族）：

| Pattern | 代表 | 状态输入 | 怎么点 | 代价 |
|:---|:---|:---|:---|
| **A 纯截屏+视觉环** | Anthropic computer use；OpenAI CUA/Operator 系；UI-TARS 端到端 | 每步截屏（可选 zoom） | 模型出像素坐标（或批动作） | token/延迟高；任意 GUI 可试；接地易漂 |
| **B 结构化抽取** | Playwright / browser-use / MCP 浏览器 | DOM / a11y 树，常不看图 | 元素 ref / 选择器 | 便宜准；**主要吃网页**；桌面 App 缺树则挂 |
| **C 混合屏解析** | OmniParser v2 + 任意 LLM；部分自建 harness | 截屏→检测框+图标说明→结构化列表 | 在列表上选再点 | 多一模型；跨桌面可泛化；依赖解析质量 |

**fawai 落点：**

- 桌面原生：**偏 B 的 AX 窄切片**（TextEdit）+ **弱 A 兜底**（坐标 `click`，无专用接地模型、无 zoom）。  
- 浏览器：**明确 Pattern B**（`browser_*`），与业界 browser-use 同族，是相对成熟的一条腿。  
- **缺 Pattern C**：没有 OmniParser / ScreenSpot 级「截屏→可点元素表」中间层，Electron/自定义绘制一遇 AX 稀薄就退回瞎点坐标。

---

## 4. 差距总表（相对业界领先方案）

「领先」取公开可指称的：Claude computer use、OpenAI CUA、UI-TARS、OmniParser+LLM、Jedi 等接地增强。

| 维度 | 业界领先常见能力 | fawai | 差距性质 |
|:---|:---|:---|:---|
| **通用桌面接地** | 截屏→坐标或屏解析框，跨 App 可试（成功率仍有限） | 仅 TextEdit 级 AX 闭环；其余靠估像素 | **核心差距（L2）** |
| **专用 GUI 模型** | UI-TARS / OmniParser / Jedi 等 grounding 数据与权重 | 通用多模态 API + 提示；无 GUI 微调环 | **模型资产差距** |
| **动作空间** | zoom、拖拽、批动作、归一化坐标、环境枚举 | 基础键鼠 + 少量原语；无 zoom；批动作弱 | **harness 差距** |
| **长 horizon / 基准** | OSWorld 等可报分；有重试与步数预算文化 | 无 OSWorld 对齐集；窄 demo | **评测与任务集差距** |
| **Electron / 自定义 UI** | 视觉接地为主路径之一 | 议题已标明后置；AX 弱 | **场景覆盖差距** |
| **回证** | 截屏再看 / 任务判成功脚本 / 部分结构化断言 | AX 含串 + 文件回证强 | **GUI L3 偏薄** |
| **浏览器** | Playwright/a11y 成熟 | `browser_*` 已有 | **接近同族，非主短板** |
| **多任务与安全** | 多在单会话单桌面沙箱；少谈本机多任务互斥 | 前台 lease + 写资源清单 + DRY_RUN/路径闸 | **fawai 超前（产品运行时）** |
| **选型路由（C2）** | 常默认「能点就点」 | 能力清单、`file_no_gui`、观察选取 | **fawai 超前（少乱点）** |
| **失败反省 / 复盘** | 部分有 reflection；产品级复盘改码少 | 执行反省 + 任务复盘进化链 | **fawai 相对完整** |
| **跨平台桌面** | Linux 沙箱桌面常见（厂商 demo） | macOS AX 主路径；Linux GUI 键鼠兜底、无 AT-SPI 同构 | **平台深度差距** |
| **手机** | AndroidWorld / adb 有公开线 | 明确后置 | **范围外，已知** |

一句话：**不是「没有键鼠」弱，是「看懂任意界面并稳定点中 + 回证」弱；运行时与分流反而比许多 Computer Use demo 完整。**

---

## 5. 架构视角：不必推倒的部分 vs 必须补的部分

### 5.1 应保留 / 加深的（已有领先或正确）

1. **三层解耦**：大脑不摸设备；控制硬闸门；观察可选——对齐业界「planner ≠ driver」。  
2. **双路径分流**：网页走 `browser_*`（Pattern B）；文件走 `fs_*`；GUI 只留给「必须见界面」的目标——避免用弱 GUI 硬扛强后台。  
3. **foreground_lease**：本机多任务真实互斥；厂商沙箱单 agent 往往不碰到。  
4. **窄场景 AX + `verify_ui_ax`**：方向正确（结构化优于纯像素）；缺的是**覆盖面**与**视觉回退**，不是换掉 ref 语义。  
5. **反省 / 步骤重复 / 能力清单**：降低「瞎点十轮」的产品事故。

### 5.2 必须补齐才能缩小「业界领先」体感

| 缺口 | 为何卡体感 |
|:---|:---|
| **无 Pattern C 屏解析** | Chrome/Cursor/游戏式 UI 一开，AX 空或脏 → 只能估坐标 → 失败像「GUI 不能用」 |
| **无专用 grounding 模型环** | 通用 VLM 未按 ScreenSpot 类目标训时，点小图标/高分屏不稳 |
| **观察–动作契约松** | 业界常「每步强制新截屏再动」；fawai 步 0 不默截屏合理省钱，但 GUI 任务若少采 `obs_screen`/`obs_ax` 易盲操作 |
| **L3 视觉回证缺失** | 只有 AX 串时，控件树不更新的 App 会假成功或假失败 |
| **无标准 GUI 评测集** | 无法与 OSWorld/自建窄集对齐迭代；D4 主要靠干跑与文件回证抬分 |

---

## 6. 改进方向（建议优先级；非放行）

原则：**先扩「可接地面」与「可回证」**，再谈任意 App；继续用 C2 把能走浏览器/文件的任务挡在弱 GUI 外。

| 优先级 | 方向 | 做法纲要 | 对标 |
|:---|:---|:---|:---|
| **P0** | **固定真机 C3 评测** | `FAWAI_C3_LIVE` 进发版流水；钉 TextEdit 成功率与失败类 | 自家基线，否则无法量差距 |
| **P0** | **GUI 任务强制观察契约** | `gui`/`full` 档：动作前须近期 `obs_ax` 或 `obs_screen`；缺观察则拒点 | 对齐 Pattern A「先看再点」 |
| **P1** | **Pattern C 中间层（屏→元素表）** | 接入 OmniParser 类或等价检测+caption；产出与 `obs_ax` 同形的 `ref`+bounds，供 `ui_click` | OmniParser v2；降低 Electron 依赖 AX |
| **P1** | **第二窄场景：Electron 或 Chrome 桌面壳** | 用屏解析或 DOM（若内嵌页）二选一验收；不承诺「任意」 | 议题开放的 Electron 线 |
| **P1** | **视觉 / 双通道回证** | `verify_ui_screen`（ROI 含文/图标）或「AX ∪ 截屏」；失败分类可机读 | 补 L3 |
| **P2** | **接地模型路由** | 可选：本地 UI-TARS/Qwen2.5-VL 细调只做「指哪」；规划仍用现有大脑 | UI-TARS / Jedi 分治：planner + grounder |
| **P2** | **动作空间补齐** | zoom/ROI 再截、drag、批键入；坐标归一化（防分辨率漂） | Claude zoom；Gemini 0–999 |
| **P2** | **Linux AT-SPI 或统一 `obs_a11y`** | 与 `obs_ax` 同 id 或并列；headless 仍禁 | 跨平台 |
| **P3** | **自建迷你 OSWorld** | 10～30 条可脚本验收的本机任务（设置、访达、浏览器、办公） | 替代「只 demo TextEdit」 |
| **P3** | **手机 / adb** | PC C3 过线后再开 | 短板议题既定 |

**刻意不做（近期）：**

- 用更大通用聊天模型替代接地层。  
- 取消 lease / 允许前台真并行。  
- 用 GUI 代替 `fs_*` 写工作区。  
- 一上来承诺「任意 App 点准」（业界基准也未到）。

---

## 7. 可选技术路线比较（选型备忘）

| 路线 | 优点 | 风险 | 与现架构契合 |
|:---|:---|:---|:---|
| **加深 AX + 多 App 白名单** | 准、便宜、易回证 | Electron/游戏墙；维护白名单 | 高：扩 `obs_ax` 目标 App |
| **OmniParser 式 Pattern C** | 跨 App；输出可接现有 `ref` | 本地 GPU/延迟；解析错 | **高**：观察插件化 |
| **整段换成 Claude/OpenAI computer tool** | 接地与动作空间开箱 | 绑厂商；难嵌租约/能力清单/复盘；贵 | 中：可作「grounder 外包」，不宜换掉总管 |
| **端到端 UI-TARS 替大脑** | 基准好看 | 失去 C2 分流与多工具；难与 fs/browser 统一 | 低作主脑；可作专项 grounder |
| **浏览器为主、桌面为辅** | 已有 `browser_*` | 非网页办公场景仍要桌面 | 产品策略可并行：「能网页则网页」 |

推荐默认组合：**保留现有大脑+租约+browser/fs；观察侧加 Pattern C；控制侧统一 `ref`；规划侧强制先观察；评测侧固定真机窄集再扩。**

---

## 7.1 专项：不用 VLM 估坐标，改 OCR + 文本模型？

用户侧常见体感：**通用 VLM 对界面点位不准**（小图标、Retina、多屏 origin、Electron 自定义绘）。问题不是「要不要看图」，而是**谁负责出坐标**。

### 判断（已形成）

| 方案 | 坐标从哪来 | 相对纯 VLM 估点 | 何时够用 |
|:---|:---|:---|:---|
| **A′ 纯 VLM 出 x/y**（现状兜底） | 模型在像素空间「猜」 | 基线；小目标与高分屏易漂 | 大块可点区域、容错高 |
| **OCR 文框 + 文本 LLM 选框** | OCR/检测器给 `text+bbox`；LLM 只选编号或中心 | **文字按钮/菜单/输入旁标签明显更好** | 界面以可读字为主 |
| **检测框+图标说明（OmniParser 族）+ 文本/轻 VLM 选 ref** | 检测器出框；LLM 选 `ref` | 无字图标、分割控件也覆盖；比纯 OCR 更接近 Pattern C | Electron / 工具栏图标 |
| **AX / DOM `ref`（现状主路径）** | 系统树 bounds | 仍最优；不替代 | 原生 App、浏览器 |

**结论：方向对，但不要停在「通用 OCR → 把全文丢给文本模型」。** 应做成与现有 `obs_ax` / `browser_snapshot` **同形的编号元素表**（`ref | 文案/角色 | bounds`），规划脑只输出 `ui_click(ref=…)`，由控制层取框心点击。坐标必须由**结构化感知**给出，禁止再让聊天/规划 VLM 自由报像素。

### 为何纯 OCR 不够、为何仍常比 VLM 估点好

1. **好在哪**：检测框中心是几何量，不是「看图估点」；文本模型做语义选择（点「发送」还是「取消」）比做空间回归稳。与浏览器 SoM、AX 表同一纪律。  
2. **OCR 单独的洞**：无字图标、纯色按钮、分割线、缩略图、关闭「×」、开关态——通用 OCR 常漏检。Electron/Cursor 类界面大量依赖图标。  
3. **噪声**：密集菜单、列表项重复文案、IME 候选、通知浮层会污染表；须裁窗（前台窗 ROI）+ 去重 + 上限节点，同 `obs_ax` 截断思路。  
4. **成本**：本地/云 OCR 可卸掉「每步大图进贵 VLM」；规划可用文本档。需要图标理解时再局部附图或走检测 caption，而不是全程绑 VLM 接地。

### 与现架构怎么接（非放行）

```text
obs_screen（已有）
    → 屏解析器（OCR 文框 和/或 图标检测）
    → 与 obs_ax 合并/并列的元素表（同一 ref 语义）
    → 规划：ui_click / ui_type(ref)   ← 禁止再猜图内 x/y
    → 控制：bounds 中心 + origin/Retina（已有）
```

- **macOS 有 AX 时**：仍优先 `obs_ax`；屏解析只补 AX 空/脏（Electron）或无障碍树缺失节点。  
- **无 AX / 树废时**：屏解析升为主观察，而不是退回 VLM `click(x,y)`。  
- **VLM 降级用途**：读图理解「现在在哪个对话框」、图标 caption、或专用 grounder；**不作默认坐标源**。  
- **回证**：OCR/检测表也可支撑「动作后 ROI 是否出现某文案」（补 L3），现网 `verify` 仍拒 `method=ocr/vlm`，属后续议题。

### 效果预期（诚实边界）

- 相对现状 **VLM 估坐标**：文字控件场景 **大概率明显更好**（已核：业界 Pattern C / SoM 共识；本仓未做同机 A/B，**未核数值**）。  
- 相对 **AX `ref`**：屏解析仍次之；有树就别换 OCR。  
- 相对 **OmniParser 级检测+caption**：纯 OCR 是 **C 的弱实现**；要扛 Cursor/Chrome 壳，迟早要图标检测，不能只押 OCR。

### 7.2 不训模型时的阿里云候选（Qwen-OCR API）

前提：用户无精力微调/自托管检测器；优先百炼现成 API。文档锚： [文字提取（Qwen-OCR）](https://help.aliyun.com/zh/model-studio/qwen-vl-ocr)、[API 参考](https://help.aliyun.com/zh/model-studio/qwen-vl-ocr-api-reference)（**已核文档**；价目与 Workspace URL 以控制台为准，**未核本账号实价**）。

#### 模型候选

| 优先级 | 模型名 | 为何选 / 不选 | GUI 接地要点 |
|:---|:---|:---|:---|
| **P0 默认** | `qwen3.5-ocr` | 官方主推新代；文档写明支持文字定位；`advanced_recognition` 示例即用此模型；输出长、多轮能力更强 | 必须开 `ocr_options.task=advanced_recognition`，吃 `ocr_result.words_info` |
| **P0 备胎** | `qwen-vl-ocr`（稳定）或 `qwen-vl-ocr-2025-11-20`（快照） | 同族 OCR；高精识别文档明确推荐 2025-11-20 及以后；稳定版易申请额度 | 同样走 `advanced_recognition`；`max_tokens` 默认偏低时桌面密文可能截断 |
| **不做主路径** | `qwen-vl-ocr-2024-10-28` | 官方标「不推荐 / 迁到 qwen3.5-ocr」 | — |
| **不做本任务** | 通用 `qwen3-vl-plus` 当 OCR | 已是 VLM 估点/自由描述路径；无内置 `words_info` 契约 | 继续只作理解/兜底，不作坐标源 |
| **可选旁路（非千问）** | 视觉智能开放平台通用 OCR / 表格 OCR | 老牌检测框 API；与百炼账单分离 | 仅当 Qwen-OCR 坐标不稳再对照；首期不必双接 |

**关键约束（接错任务等于白接）：**

1. **只要坐标**：`task=advanced_recognition`。`text_recognition` / 默认全文提取 **不保证**结构化框，不能当接地。  
2. **结构化字段优先**：DashScope SDK / 原生 multimodal HTTP 返回 `content[].ocr_result.words_info[]`，含 `text`、`rotate_rect=[cx,cy,w,h,angle]`、常有四角 `location`。点击用 **`rotate_rect` 的 cx/cy**（图内像素）。  
3. **OpenAI 兼容口**：文档写明须**手写任务 Prompt**，且不一定有稳定的 `ocr_result` 字段（常要从正文 JSON 解析）。fawai 已有 DashScope 路由，**OCR 角色优先走原生 multimodal + `ocr_options`**，不要硬套 chat.completions 猜 JSON。  
4. **图尺**：坐标相对**送入模型的那张图**；若 `min_pixels`/`max_pixels` 触发缩放，须在客户端记录缩放比，再映回截屏 PNG / Retina `coord_scale_*`（与现 `click` 同源）。桌面截屏建议尽量落在默认像素窗内少缩放。  
5. **额度**：单图体积上限（`qwen3.5-ocr` 文档约 20MB，旧版约 10MB）；GUI 只传前台窗 ROI，忌全桌面 5K 原图。

#### 接入后架构方案（非放行；对齐 ASR 角色）

与现有 `asr` 并列，加 **`ocr` 路由角色**（同百炼 Key / `dashscope` provider），默认模型 `qwen3.5-ocr`。

```text
obs_screen 已截 PNG + origin/scale
    → ocr_client.advanced_recognition(image)   # 新薄封装，仿 asr.py
    → 归一化为元素表：ref | text | cx,cy,w,h（图内）
    → 注入规划观察（文本表；可卸大图）
    → 规划：ui_click(ref) 或 click 由控制层用表内中心（禁止模型报 x/y）
    → 有 obs_ax 时：AX 优先；OCR 表补 AX 空/脏节点
```

| 层 | 建议落点 | 说明 |
|:---|:---|:---|
| **配置** | `FAWAI_LLM_OCR_MODEL` + settings 角色 `ocr` | 默认 `qwen3.5-ocr`；备胎可改 `qwen-vl-ocr-2025-11-20` |
| **客户端** | `fawai_avatar/llm/ocr.py`（拟） | Base64 Data URL；DashScope `MultiModalConversation` + `ocr_options`；解析 `words_info`；失败返回可机读 error |
| **观察** | `obs_screen` 可选后处理，或新 `obs_ocr` 与 screen 绑定 | 产出与 `obs_ax` 同形摘要；上限 N 条、去空串、可选合并近邻框 |
| **控制** | 复用 `resolve_click_global` / `ui_click` 框心 | OCR ref 进同一 registry（或 `ocr:` 前缀命名空间），避免与 AX ref 撞号 |
| **规划** | `gui`/`full` 档：有 OCR/AX 表时禁止裸像素 `click` | 与 §7.1「坐标职责在感知」一致 |
| **平台** | macOS/Linux 有桌面均可；headless 剔除 | 不依赖本机 GPU；只需 Key 与出网 |

**分阶段（均为必要项的实施先后，非默认可砍；砍掉须另标）：**

1. **P0**：OCR 客户端 + 单测/干跑（固定样图 → 表）；配置进百炼路由。  
2. **P0**：观察摘要接入闭环；规划提示改「选 ref」。  
3. **P1**：与 `obs_ax` 合并策略 + Electron 窄任务验收。  
4. **P1**：OCR 表支撑简易回证（动作后 ROI 是否出现某串）。  
5. **P2**：图标检测 / OmniParser 类补无字控件（仍可 API，不必自训）。

**刻意不做（首期）：** 用 `qwen3-vl-plus` 替代 OCR；OpenAI 兼容口硬解析当主路径；OCR 全文塞进 prompt 却不带 bounds；承诺无字 UI 全覆盖。

---

## 8. 结论（给后续开议题用）

1. **差距主因**：通用 **L2 接地**（尤其非 AX App）与 **L3 视觉回证**，不是缺键鼠或总管。  
2. **相对业界**：感知/专用模型/动作空间落后；**运行时（lease、C2 选型、回证纪律、浏览器结构化、反省复盘）不落后，部分更完整**。  
3. **业界上限也有限**：OSWorld 仍远低于人类；产品叙事应是「授权范围内可验收窄场景扩面」，而非对标营销话术里的「全能桌面」。  
4. **下一步最划算**：真机 C3 基线 → GUI 先观察契约 → Pattern C 接 `ref` → Electron/第二 App → 视觉回证；专用 grounder 与迷你基准随后。  
5. **对「OCR+文本模型」问法**：赞成把**坐标职责从 VLM 挪到结构化感知**；落地形态应是 **元素表 + `ref`**（OCR 可作第一版感知），不是「OCR 全文 + 模型再估点」。纯 OCR 盖不住无字 UI，完整 Pattern C 仍要检测/图标说明。  
6. **不训模型时的 API 默认**：百炼 **`qwen3.5-ocr` + `advanced_recognition`**（备胎 `qwen-vl-ocr` / `2025-11-20`）；DashScope 原生拿 `words_info`；产品侧仿 ASR 加 `ocr` 路由。细则见 §7.2。

要立项落地时：从本目录结论开/扩议题（建议挂在 [自主操控C3](../议题/自主操控C3/结论.md) 深化，或新开「屏解析接地」），再经用户明示写入 `doc/功能/`。

---

## 依据摘要

- 本仓能力以功能「自主操控 / 观察 / 控制」与 v0.1.2 D4 为准。  
- Pattern A/B/C 与 OmniParser、UI-TARS、Claude computer use、OSWorld 公开叙述为对照锚（**已核定位**；分数随版本变，本文用区间而非钉死单一排行）。  
- 未做与 Operator/Claude 的同机同任务对打（**未核**）。
