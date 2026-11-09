# macOS 全屏 / Space 下 `app_focus` 假成功

对照对象：本机 GUI 控微信（及任意「须前台可见」的 App）；任务证据 `f458ebe90bb54ed49dbd4722c8737b38`。  
目的：钉死「osascript activate 已 ok ≠ 前台已切到目标」与续跑租约误报，避免把全屏 Space 问题只怪成「模型不会用」。  
关联：[功能/多屏与全屏观察](../功能/多屏与全屏观察/说明.md)；[议题/多屏与全屏观察](../议题/多屏与全屏观察/结论.md#已定结论)（真机全屏 Space 待核）；`control/app_focus.py`；`runtime/loop.py` 前台租约；`channel/hub.py` 追问续跑。

## 目标与终态（本案）

Goal：微信给「罗」发「你好」。终态 `cancelled`（用户停）。工具统计：`app_focus×5`、`shell×3`、`wait×2`。未完成发信。

## 用户多轮

有 followup「继续执行」。续跑后未再出现 `running` 状态事件；`app_focus` 连续 `cannot claim foreground_lease`，事件旁注 `claim rejected: task not running or waiting` 且 `status=pending`。纠偏「继续」未兑现为可领前台租约的 running 闭环。

## 工具双栏

| 栏 | 结论 |
|:---|:---|
| **工具本身** | ① **`app_focus` 假成功（P0）**：`tell application … to activate` / `open -a` 仅以进程退出码为 ok，**不回读 frontmost**；本案两次 `activated WeChat` 后观察仍 `frontmost_app=Google Chrome`，截屏亦为 fawai PWA（Chrome）。全屏 / 独立 Space下尤其易「脚本成功、画面未切」。② **参数别名缺口（P0）**：executor 认 `app`/`name`/`bundle`/`application`，**不认 `app_name`** → 首步硬失败文案正确但易烧掉一轮。③ **租约失败文案误导（P0）**：loop 统一 `cannot claim foreground_lease`；本案实为任务仍 `pending`（不在 `_CLAIMABLE`），反省却归因「微信不可聚焦 / 未初始化」。④ 能力说明写「找回全屏 Space 优先 app_focus」，但功能说明仍标真机全屏 Space **待核**——承诺与核验缺口并存。 |
| **调用** | 观察到 Chrome 仍前台后仍信 `activated` 再截 frontmost；追问后未换 Space/`hotkey` 族；误用 `open -g`（故意不抢前台）；`pgrep` 正则与 `expect contains ok`（osascript 成功常无 stdout）制造假失败链。 |

## 必要 / 可选路径

| 槽位 | 类型 | 本案 |
|:---|:---|:---|
| 微信 UI 真正前台（或 AX 落在微信） | 必要 | 阻塞：假成功 + 后续租约拒领 |
| 定位联系人「罗」 | 必要 | 未进入 |
| 输入并发送「你好」 | 必要 | 未进入 |
| 进程是否存活 / 再 launch | 可选旁证 | 被升成主线；`pgrep` 假阴性后又 `open -g` |
| 实机截屏验页 | 可选 | 已做，正确证明仍是 Chrome，但未据此否定 app_focus ok |

## 机制根因（可复用）

1. **前台动作以脚本退出码当验收** — 与「目标 App 须可见」脱节；全屏 Space / 他 App 霸屏时高发。  
2. **取消 → 追问续跑竞态** — 旧 Worker 可能仍活着（`pending` 在 `_task_control` 视为 ok），新 Worker 不双开；租约只认 `running|waiting` → GUI 动作必败且文案不像「任务未 running」。  
3. **假归因清洗未覆盖租约拒因** — 把产品闸门失败说成「微信没起来」。

## 证据链（短）

- step0：`args.app_name` → 需 `args.app`  
- step1/3：`ok: activated WeChat`；随后 observe `frontmost_app=Google Chrome`；截屏 `screen_20261109T075852Z_*.png` 为本地 fawai 页  
- 追问后：仅有 `reopened … pending`，无第二次 `running`；step5/8：`cannot claim foreground_lease` + `claim rejected … pending`  
- step7：`open -g /Applications/WeChat.app`；step9：osascript expect `ok` 假失败

## 通用优化清单

下列均为**必要项**的实施先后（编号=优先级，不是可砍）。  
每项按复盘规则标明 **优化方向**（`direction`）与落盘 `kind`；提示词另标 `prompt_scope`。

| 优先级 | 方向 | kind | 项 | 状态 |
|:---|:---|:---|:---|:---|
| **P0** | 【工具代码】`tool_code` | `code_patch` | `app_focus` frontmost 回证；假成功 → ok=false | **已落地** `control/app_focus.py` |
| **P0** | 【工具代码】`tool_code` | `code_patch` | 接受 `app_name` 别名 | **已落地** `executor._exec_app_focus` |
| **P0** | 【工具代码】`tool_code` | `code_patch` | 租约拒因区分 status / 他任务占用 | **已落地** `claim_foreground_result` + loop 文案 |
| **P1** | 【闭环运行时】`runtime` | `code_patch` | pending≠ok（requeued）；旧 Worker 让出后再拉新线程 | **已落地** `_task_control` + hub `_maybe_start_worker` |
| **P1** | 【提示词·工具使用】`prompt` / `tool_use` | `prompt_patch` | frontmost≠目标须换道；禁 open -g | **已落地** `capabilities` + `planner` GUI 规则 |

### 可选（可砍）

| 方向 | 项 | 砍掉后 |
|:---|:---|:---|
| 【工具代码】 | 粗窗口列表 / 是否全屏进观察 meta | 仍可靠 frontmost 回证 + 截屏 |
| 【提示词·工具使用】或热键能力说明 | 独立 `space_switch` | 继续用 `hotkey` + 文档提示 |

## 明确不做

- 不为「微信发消息」单开专用闸或写死 Mission Control 剧本。  
- 不把本案优化标题绑成「下次别 open -g 微信」（可作证据句，不作唯一条目）。  
- 不要求用户永远退出全屏才能用产品——应在工具层诚实失败或真正切到目标 Space。
