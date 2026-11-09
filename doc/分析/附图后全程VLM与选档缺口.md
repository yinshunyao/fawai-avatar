# 附图后全程 VLM 与选档缺口

## 对照对象 / 目的

- **对照**：议题「规划提示精简」已定原则「有图 ≠ 全程 VLM」与功能「规划提示档」现网兑现度。
- **目的**：说明用户附图任务为何每轮仍走 `qwen*-vl-plus`、prompt  inflates；与自主规划闭环如何衔接；可复用优化方向（非单任务菜谱）。
- **证据任务**：`9cbc23ed528242b39567fe04810a93b6`（goal 附图优化手机端配置；轨迹上规划步持续 `qwen3-vl-plus`）。

| 关联 | 路径 |
|:---|:---|
| 功能 | [规划提示档](../功能/规划提示档/说明.md)、[大脑](../功能/大脑/说明.md)、[步骤分类与执行流](../功能/步骤分类与执行流/说明.md) |
| 议题 | [规划提示精简](../议题/规划提示精简/结论.md)（开放项 P2：vision 是否硬切一轮） |
| 代码 | `brain/prompt_profile.py`、`brain/planner.py::_observation_user_message`、`llm/router.py::messages_need_vision` |

## 现状

### 自主规划怎么转

闭环（简化）：

```text
观察(obs_*) → 大脑规划(_plan_once) → 执行 actions
  → 工具结果观察 →（失败则执行反省 → replan）→ 再规划 …
```

- 每轮由模型输出 `status` / `actions` / `observe_kinds` / `step_kind`（取证 / 串行 / 并行 / 分析 / 收尾）。
- `act_*` 段内可多工具，段末再观察；不是「一步一工具就结束任务」。
- 规划提示档按规则选 `vision|fs|gui|browser|full`，决定**是否附用户图**与提示子集；路由见消息里有无 `image_url` → VLM，否则文本模型。

### 附图如何进 VLM

1. 用户图写入 goal 的 `[image:…]` 标记。
2. `_observation_user_message(..., attach_images=profile.attach_user_images)` 每轮把图编成 data URL 塞进**本轮** user 消息。
3. 规划 history 窗本身是**纯文本**（不保留旧多模态 parts）；浪费来自**每轮从 goal 标记重新灌图**，不是 history 叠多张图。
4. `messages_need_vision` 见多模态 content → 路由 `vlm`（如 `qwen3-vl-plus`）。

### 选档缺口（机制）

设计期望：

| 阶段 | 期望档 | 附图 |
|:---|:---|:---|
| 有图且尚无工具回合 | `vision` | 是 |
| 已有 fs 工具历史且目标偏文件/前端 | `fs` | **否**（卸图走文本） |

现网 `infer_prompt_profile`：切到 `fs` 卸图的条件是 `tools_done and file_like`（file / coding / fs_web 启发）。  
**若 goal 有图、已跑 fs 工具，但文案未命中 file_like** → 越过 `vision` 后落入 **`full` 且 `attach_user_images=True`** → 每轮继续灌图 + 全程 VLM。

对照 demo：带「会话页面 / markdown」的附图目标，工具回合后能进 `fs`；本案类「手机端界面…配置需要优化」+ 图 → 工具后仍为 `full`+附图。议题开放项「vision 是否强制只跑一轮后硬切」**尚未落地**。

### 本案量级（证据）

- 各 `planned` 步 `model=qwen3-vl-plus`；单步 cum prompt 增量约 **17k～22k**（与 UI 上 ↑18k 量级一致）。
- 续跑累计 `prompt_tokens` 可到数十万级（同任务统计曾见约 72 万 prompt / 四十余轮量级），图贡献每轮重复计费。

## 落地（已实现）

下列为实施优先级（均已做）；编号仅表示当时方案先后。

1. **闭环运行时 / 选档**：`infer_prompt_profile` — `tools_done and (file_like or has_img)` 且非截屏观察 → `profile_fs` 卸图；GUI 目标仍优先 `gui` 不被误切。
2. **可观测**：`plan.raw.prompt_profile` / `attach_user_images`；`planned` progress 经 `_llm_progress_meta` 带出。
3. **自检**：`demo_prompt_profile` 覆盖「附图未命中 file hint + 工具历史 → fs 卸图」与 GUI 不被误伤。
4. **文档**：功能「规划提示档」验收 + 议题开放项闭合说明。

未做：未跑任何工具时的「vision 最多一轮」硬切（仍以工具历史为信号）。

## 续析：VL 过多是否导致「找不到代码」（任务 `9cbc23ed…`）

### 结论（先）

| 判断 | 口径 |
|:---|:---|
| VL 是否调用过多 | **是**（规划步持续 `qwen3-vl-plus`；单步 Δprompt 常约 17k～18k；任务累计 prompt 可到约 10^6 量级）→ 烧上下文与费用；**已用卸图硬切缓解** |
| 是否因此「能力下降到找不到代码」 | **不能当主因**。后期续跑在用户纠正后**已定位** `fawai-avatar/.../channel/static/index.html` 与 `rules-ui-mobile.mdc`；真源样式在同文件 `button.send` / `button.stop`（约 L449+、窄屏约 L5940+） |
| 「代码都没有找到」终稿 | **假归因**：DOM/`viewport`/`@media` 已有成功 `fs_read`/`fs_grep`；失败在**取证调用与证据吸收**，不是仓里没有前端 |

### 轨迹要点（机制）

1. **用户多轮**：追问「前端 UI 布局」+「代码在 fawai-avatar」——后者在续跑中**已兑现**（动作 path 几乎全在 `fawai-avatar/`）；早期曾漂到 `hipp-https/web` 属偏科，不是 VL 独有症状。
2. **工具本身**：`fs_read`/`fs_grep` 对正确 pattern（如 `send`、`@media`）能返回命中；`grep 0` 出现在把 `|` 写成字面 `\|` 时（如 `\\.send\\|\\.stop`、`send\\|stop`）——工具按正则诚实返回 0，易被模型读成「文件里没有」。属**契约可增强**（报错旁注），不是「读不到仓」。
3. **调用**：同构小窗反复读（多步同 offset）；用错 pattern 后不改成 `button.send` / 裸 `send`；已读到 send/stop **DOM**（约 L6320）却声称样式不存在；未读到 L449 基样式仍 `failed`；一度改 `shell grep` 相对路径 cwd 失败。符合：**探索→改写未切换** + **假归因终稿** + **同构只读空转**。
4. **VL 的真实伤害**：每轮重附图挤占 prompt，削弱近窗里工具正文的有效容量/注意力（加重「读了却当没读」），但**不是**「仓内无 index.html」的解释。

### 相对优化（通用）

1. **【工具代码】** `fs_grep` 在 pattern 含字面 `\|` 且 0 命中时旁注 — **已落地**（`grep_empty_escaped_pipe_hint`）。
2. **【提示词·工具使用 / 任务规划】** pattern `a|b`、CSS 优先 `button.send`；有证据禁空口「找不到样式」— **已落地**（capabilities + planner）。
3. **【闭环运行时】** 假归因「找不到源」且已有成功读/非空 grep → 回灌至多 1 次 — **已落地**（`false_missing_source` + `phase=false_missing`）。

## 明确不做

- 不为单张截图或单个 goal 文案加专用闸。
- 不把「换更大/更小 VLM」当唯一解；也不把「找不到代码」一概归咎于 VLM 变笨。
- 不建议关步骤重复/反省来省 token。
