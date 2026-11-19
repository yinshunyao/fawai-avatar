# Cursor Agent 步骤流与展示

## 对照对象

- Paseo 对接 Cursor Agent 的行式过程（Thinking / Shell / Search / Read / Other）
- fawai 原生任务步骤簇（`#N` + 取证 / 查询 / 串行执行等）
- fawai 直连 Cursor Agent 当前弱展示（多仅为状态条 + 终稿气泡）

关联功能真源：[功能/CursorAgent](../../功能/06-工作区与编码/07-CursorAgent/说明.md)。鉴权与分库见同层 [05-CursorAgent接入对照](./05-CursorAgent接入对照.md)。

## 目的

让 Cursor 直连路径的**消息拆解、工具类型与可回放时间线**达到与原生任务同级，而不是另起一套 Paseo 行列表 UI。

## 现状

| 面 | 判断 |
|:---|:---|
| SDK 事件 | `runtime.stream_agent_run` 已解析 thinking / tool-call；`session_progress` 可映射为 `plan`/`planned`/`act`/`acted` |
| 落盘 | Cursor 回合**不写** `task.meta.steps`；裁剪 progress 后无法走 `task_timeline` 回放 |
| `run_id` | 多只打在 `run_start` / settle，步骤卡难按轮归组 |
| 中途正文 | `assistant_segment` 未进进度直播；界面只见终稿 `text_assistant` |
| 展示 | 无步骤簇时只剩 `cursor_status` + 终稿，观感远弱于原生 `#N` 卡 |

## 切法

1. **复用原生步骤管道**：progress `phase` + PWA `stepFamily`；工具名用已有 `meta.tool`（Shell / Search / Read…）。
2. **回合累积 `meta.steps`**：结构对齐 Worker 落盘（`plan.thought` / `actions` / `results`），结束 `merge_task_meta`，支持历史回放。
3. **每条 progress 带 `run_id`/`agent_id`**；settle meta 带 `cursor_tools` / `cursor_events` 计数，避免「只有终稿」时无从排查。
4. **`assistant_segment` 仅直播** `content_text`（`live=True`）；终稿仍以助手气泡为准。
5. **思考合并**：SDK 流式 thinking（含 ConversationStep token）只进 `thinking_delta`；`thinking_done` 仅在 `thinking-completed`、工具前或流结束时 flush 一次。禁止「I / need / to」各成一步 `规划完成（continue）`。

## 不做

- 不仿 Paseo 单独做 Thinking/Shell 纯行列表 UI。
- 不新建「工具操作」独立表；仍用 `Message` + `meta.steps`。
- 不改主脑 Worker 步骤语义。

## 不等于放行

落地以功能说明验收为准；本文只作机制对照与整改依据。
