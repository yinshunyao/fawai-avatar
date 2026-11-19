# Cursor Agent 接入对照

## 对照对象

Paseo 一类工具的 **Agent Provider**（连接开关、状态点、模型目录弹窗）与 fawai-avatar 现有 **LLM 聊天供应商**（OpenAI 兼容 `llm_settings`）。

## 现状判断

| 面 | Paseo / Cursor SDK | 本仓 LLM 接入 | 结论 |
|:---|:---|:---|:---|
| 鉴权 | `CURSOR_API_KEY` / Dashboard Key | Base URL + API Key | 不同协议，不可共用一张供应商表 |
| 模型目录 | `Cursor.models.list()` | `/models` 或百炼原生目录 | 目录 API 不同；Cursor 模型 id 如 `composer-2.5` |
| 运行时 | `Agent.create` / `send` / `resume`（local/cloud） | `LlmClient.chat` tool call | Cursor 是整段编码 Agent，不是单轮 chat |
| 配置 UI | Provider 行：开关 + 可用/错误 + N 个 Model | 凭证卡 + 角色两级下拉 | **独立侧栏页** `#cursorAgentPage`（登录/开关/模型）；不进模型配置 |

## 本仓切法

1. **分库**：`cursor_agent_settings.sqlite`，禁止写入 `llm_settings.providers`。
2. **两入口**：Web 直连对话（`meta.runtime=cursor_agent`）；主脑 `tier=cursor` 委派回灌。
3. **本轮**：仅 local；`cwd` = 任务工作区 / `work_root`。Cloud 留作可选。

## 不等于放行

落地以 [功能/CursorAgent](../../功能/06-工作区与编码/07-CursorAgent/说明.md) 为准。
