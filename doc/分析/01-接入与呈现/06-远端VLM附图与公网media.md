# 远端 VLM 附图与公网 media

对照对象：fawai-avatar 规划附图走 OpenAI `image_url`；本机/百炼多用 `data:`；远端自建口（如 4090 WeVisOne-VL）经公网反代。  
目的：厘清 **Error 400** 与「跨端可取图」两层问题，供接入与会话网关改进。  
关联：`doc/功能/01-接入与呈现/01-LLM接入/说明.md`、`03-会话网关/说明.md`；运维 `study/配置信息/测试-4090/WeVisOne-VL.md`。

## 现象

规划路由到 `WeVisOne-VL-4B` 时进度卡失败，文案多为 `Error code: 400`（约数百 ms，未等到长推理）。

## 机制（已核）

| 层 | 现状 | 与 400 / 跨端的关系 |
|:---|:---|:---|
| 规划附图 | `planner._observation_user_message` 把本地文件编成 `image_url` | 原先几乎总是 **`data:image/…;base64,…`** |
| 直播 | `LlmClient.chat(on_delta=…)` → **`stream=True`** | WeVisOne 首版 OpenAI 壳对 stream 直接 **HTTP 400**（与「有没有图」无关） |
| 会话预览 | 用户图 / 产物经 `/media/{name}`，**不验口令** | 他端浏览器同源相对路径已可预览；**远端 VLM 机器**看不到 `127.0.0.1` 或相对 `/media` |
| 穿透 | frp → `http://{FAWAI_FRP_SERVER_ADDR}:{REMOTE_PORT}/` | 可作为公网基址，拼绝对 media URL |

结论：

1. **本次 400 主因**：自建口不支持（或未实现）stream，而规划默认开流式直播。  
2. **跨端 / 远端取图**：相对 `/media` 只服务「打开了网关的浏览器」；远端 VLM 要从公网拉图，须 **绝对 `http(s)://…/media/…`**（或继续 `data:`，但大体量易被反代截断）。

## 改进方向

| 项 | 类型 | 说明 |
|:---|:---|:---|
| 自建口支持 SSE stream（或假流式） | 运维 / 壳 | WeVisOne `openai_server` 生成后按 chunk 推；忽略无害的 `stream_options` |
| 客户端 stream 失败回退非流式 | 产品代码 | `LlmClient` 遇 400/stream 类错误再打一枪非流式，直播只收终稿 |
| `FAWAI_PUBLIC_BASE_URL` + frp 回退 | 产品代码 | `media_public.llm_image_url`：有基址则绝对 media，否则 `data:` |
| 出站气泡仍用相对 `/media` | 保持 | 浏览器跟当前 origin，避免强绑公网 |

必要（实施先后）：stream 可通 → 公网 media 供远端 VLM。  
可选：按供应商策略强制「禁止 data:」、media 短期签名 URL。

## 与放行关系

不等于新议题放行；代码侧可先修壳与客户端回退、附图 URL 选择。功能说明须同步「公网基址」与 VLM 附图约定。
