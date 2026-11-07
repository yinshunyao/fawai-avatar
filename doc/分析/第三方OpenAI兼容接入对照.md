# 第三方 OpenAI 兼容接入对照

对照材料：本地配置笔记里的「小黑」中转站（Codex / Cursor 风格 `config.toml` + `OPENAI_API_KEY`）。  
功能真源仍见 [LLM 接入](../功能/LLM接入/说明.md)。本文不等于放行。

## 那份配置是什么

不是 fawai-avatar 的配置，而是 **Codex CLI / Cursor Agent 的模型提供商 TOML**（常落在 `~/.codex/config.toml` 一类路径），再配环境变量 `OPENAI_API_KEY`。

| 字段 | 含义 |
|:---|:---|
| `model_provider = "OpenAI"` | 选用名为 OpenAI 的自定义提供商段 |
| `model` / `review_model` | 主对话 / 审阅用的模型 id（如 `gpt-5.5`） |
| `model_reasoning_effort` | Codex 侧推理强度（`xhigh` 等），**不是** fawai 的 `thinking_budget` |
| `base_url` | 中转站根地址（笔记里写 `https://443.hk`） |
| `wire_api = "responses"` | 走 OpenAI **Responses** 协议（含流式 / WS），不是 Chat Completions |
| `supports_websockets` / `responses_websockets_v2` | Codex 对 Responses 的 WebSocket 传输开关 |
| `requires_openai_auth` | 用 OpenAI 风格 Bearer Key |
| 账号页 URL（如 fuckme.443.hk） | 中转站注册 / 充值入口；与 `base_url` 可同族不同主机 |

本质：**把第三方中转站伪装成 OpenAI 官方端点**，给 Codex/Cursor 用。密钥与口令只留本地笔记 / `.env`，不进本目录正文。

## fawai 现状（已支持「换一家兼容口」）

当前产品是 **按角色选供应商 + OpenAI 兼容 Chat Completions**：

| 项 | 现状 |
|:---|:---|
| 凭证 / 路由 | Web「模型配置」→ `{FAWAI_DATA_DIR}/llm_settings.sqlite`（各供应商 Key；text/vlm/fast/asr/embed/force 各自「供应商→模型」） |
| 调用 | `openai.OpenAI` → `chat.completions.create`（见 `llm/client.py`；按角色解析端点） |
| 默认种子 | 百炼 + 小黄内置；首次可从旧 `.env` `FAWAI_LLM_*` 迁移 |
| 列表 | DashScope 走原生目录；其它兼容口走 `GET /models` + 启发式分类 |
| 思考 | 对 DashScope / 千问系与内置小黄（`kklt`）写 `extra_body.enable_thinking` |

因此：**若中转站提供 Chat Completions 兼容**（常见 `/v1/chat/completions`），在 Web 增加自定义供应商（Base URL + Key），再为各角色选该供应商与模型即可，不必先改协议栈。

与 Codex 那份 TOML 的对应关系：`base_url` / Key ↔ 该供应商凭证，`model` ↔ 各角色路由的 `model_id`。**不要**把 `wire_api` / `responses_websockets_v2` 原样搬进 fawai——产品当前不读这些键。

## 协议鸿沟（真正要扩展时才碰代码）

| Codex 配置 | fawai 现状 | 影响 |
|:---|:---|:---|
| `wire_api = responses` | 只用 `chat.completions` | 若中转站 **只** 开 Responses、不开 Chat Completions，配进 Web 会失败；需验证对方是否同时兼容 `/v1/chat/completions` |
| WebSocket Responses | HTTP SSE / 非流阻塞 | fawai 流式走 completion stream；无 Codex 那套 WS |
| `model_reasoning_effort` | 角色 + 模型档案 `enable_thinking` | 语义不同；小黄已写入 `enable_thinking`，但 Chat Completions 多数不回传可见推理正文；Codex 的 effort 仍属 Responses 路径 |
| 单模型名 | 文本 / VLM / ASR / 快模型 / embed 等角色 | 中转站往往只有 chat；ASR/embed 可另选百炼等第二家 |

结论：**「换第三方」多数情况是配置级；「跟 Codex 完全同协议」才是代码级（Responses 客户端）。按角色选不同供应商已支持。**

## 可选改进方向（未立项）

下列均为**可选项**（可砍；砍掉后仍可用 Web 配兼容口完成主验收）。若要落地须另开议题并经明示写入 `功能/`。

1. **接入前探测**：对给定 `base_url` 试探 `chat.completions` 与（可选）`responses`，在 Web 模型配置页提示「仅 Responses / 双协议 / 不可用」。
2. **Responses 适配层**（仅当目标站强制 Responses）：在 `LlmClient` 下增加 wire 分支，对外仍返回现有 `content` / `tool_calls` 形状；成本高，且 tool calling / 流式 / 思考字段要对齐现有 runtime。
3. **模型 id 别名表**：把口语名 / Codex 名映射到对方真实 id，避免 Web 下拉与路由各写一套。

## 实测：小黑 / 443AI（本机探测）

产品页标题为 **443AI - AI API Gateway**。门户与 API 主机要分开：

| 主机 | 角色 |
|:---|:---|
| `https://fuckme.443.hk` | 注册/充值前端（多数路径回 SPA HTML，**不要**当 API base） |
| `https://443.hk` | API 网关；兼容根用 **`https://443.hk/v1`** |

本机探测结果（同一 Key；**未**跑通生成，因账号返回余额不足）：

| 请求 | 结果 | 含义 |
|:---|:---|:---|
| 无 Key → `GET /v1/models` | `401 API_KEY_REQUIRED` | 鉴权存在；支持 Bearer / `x-api-key` / `x-goog-api-key` |
| 有 Key → `GET /v1/models` | `403 INSUFFICIENT_BALANCE` | 端点在；余额闸在鉴权之后 |
| 有 Key → `POST /v1/chat/completions` | `403 INSUFFICIENT_BALANCE` | **Chat Completions 兼容口存在**（fawai 可直接用） |
| 有 Key → `POST /v1/responses` | `403 INSUFFICIENT_BALANCE` | Responses 也在 |
| 有 Key → `POST /responses`（无 v1） | `403 INSUFFICIENT_BALANCE` | Codex `base_url=https://443.hk` + `wire_api=responses` 可对上 |
| 无 `/v1` 的 chat 路径 / fuckme 主机 | 多回 HTML 门户 | 配错 host 或少 `/v1` 会「假 200」 |

对 fawai 的直接含义：

1. **不必先上 Responses 客户端**——对方已挂 `/v1/chat/completions`；充值后在 Web「模型配置」加自定义供应商 `https://443.hk/v1` + Key，并为 text/vlm 等角色选该供应商即可。
2. 当前挡点是 **账号余额**，不是协议缺失；生成内容、tool calling、模型列表细节须充值后再验。
3. 门户文案写明对中国大陆等受限地区 IP 停服；本机打到 `443.hk` 的 API 已通到余额闸，换网络环境时仍可能被地区策略拦住。

## 实测：小黄 / kklt（本机探测）

栈为 **New API**（统一网关后台标题）。API 与门户分开：

| 主机 | 角色 |
|:---|:---|
| `https://fuckme.kklt.lol` / `https://pay.kklt.lol` | 门户 / 充值 |
| `https://api.kklt.lol/v1` | API 兼容根（笔记已写对） |

本机探测（Key 有效；用量 Bearer `GET /v1/usage` → `remaining: 100` USD，`is_active: true`）：

| 请求 | 结果 | 含义 |
|:---|:---|:---|
| `GET /v1/models` | 200，约 19 个 id | 列表可用；含 `gpt-6-sol`、`gpt-5.5`、Claude / Grok 等 |
| 无 `/v1` 的 `/models`、`/chat/completions` | HTML「New API」 | 少 `/v1` 会假 200 |
| `POST /v1/chat/completions` + `gpt-6-sol` | 200，回文 `ok` | **Chat Completions 可用** |
| 同上 + tools / `tool_choice=required` | 200，返回 `tool_calls` | **主脑所需 function calling 可用** |
| `POST /v1/responses` + `gpt-6-sol` | 200，`object=response` | **Responses 也可用**（Codex 可直连） |
| `gpt-5.5` 走 chat / responses | 404 `model_not_found` / 组内无账号 | **目录有名 ≠ 本组可调**；本单更像 GPT-6 通道 |
| `gpt-4o-mini` | 503 无可用渠道 | 未开通该模型 |

对 fawai：

1. Web「模型配置」已提供供应商下拉：**阿里云百炼 / 小黄 / 自定义**；选小黄会带 `https://api.kklt.lol/v1` 与默认 `gpt-6-sol`，并 `POST /api/llm/models` 同步列表。主填 API Key 即可。
2. 协议上 **Chat Completions 已够**；不必为接小黄先做 Responses 适配。
3. 上线前再扫一遍本组实际可调模型（列表 ∩ 本组渠道），避免点到目录里「看得见调不了」的 id。

## 建议验证顺序（配置级）

1. 门户充值后，用同一 Key：`GET https://443.hk/v1/models`（小黑）或 `GET https://api.kklt.lol/v1/models`（小黄）。
2. 再打对应站的 `POST …/v1/chat/completions`（带 tool 更好，因主脑依赖 tool calling）。
3. 成功后再改 fawai `.env` / Web「模型配置」，跑 `demo_llm_live`。
4. 小黑 / 小黄实测 **Chat Completions 与 Responses 端点都在**；fawai 优先走 Chat Completions。小黄已用 `gpt-6-sol` 验证回文 + tool calling。
