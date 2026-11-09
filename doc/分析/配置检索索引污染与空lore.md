# 配置检索：空 lore + 正文索引污染 → 落进磁盘扫

对照对象：查账号/主机/SSH 落点类目标（证据任务 `3171117c0deb4fd1b3a1de9986099333`，goal「4090服务器现在的 AI训练任务状态」）。

关联：[个人知识库](../功能/个人知识库/说明.md)；[数据盘索引按业务划分](./数据盘索引按业务划分.md)；[检索索引先于磁盘](./检索索引先于磁盘.md)；[检索范围由LLM决策](./检索范围由LLM决策.md)。

## 现象

用户预期：缓存/索引应直接命中仓内配置（如 `study/配置信息/云服务重要信息.md`）。  
轨迹实际：`lore_search scopes=['lore']` → 0；反省写成「lore 无 4090 连接信息」；随后 `fs_grep path='.'`（被宽度闸拦）→ `fs_glob` 扫一堆 `.env` → 在 `fawai-avatar`/`hipp-or` 磁盘 grep。`task_context_search` 只回灌本任务先前噪声，不是配置落点。

## 复现核对（本机，已核）

| 检查 | 结果 |
|:---|:---|
| 目标文件在盘上且含 `4090` / AutoDL | 是 |
| 正文 FTS `docs` 含该 path | 是 |
| `lore` 笔记 FTS 条数 | **0**（`scopes=['lore']` 对任意配置查询恒空） |
| 正文 FTS 中仍残留 `FAWAI_DATA_DIR` 下文档 | **4548**（含大量 `observe/*`） |
| 查询 `4090` 全局 Top | 几乎全是 `data/observe` 反省/工具 dump，真配置排不上前窗 |
| 排除 `data_dir` 后 `4090` | 真配置约第 **21** 名；前排是 `测试-4090/README`、宣传文案、依赖说明等 |
| 查询 `云服务重要信息` / `seetacloud` | 真配置可进前 3 / 第 1 |

结论：**文件已进工作区正文索引**；「搜不到」主要是（1）首刀打进空笔记域；（2）查询面被数据盘二次入库污染；（3）单关键词 `4090` 在干净索引里也不够靠前；（4）模型未扩到 `work_content`/`work_name` 或更好 query，改走磁盘。

## 工具双栏

### 工具本身（P0）

1. **数据盘二次入库未清干净**：扫盘代码已 `_under_data_dir` 跳过，但现库仍有 4k+ 条 `fawai-avatar/data/**`；**查询路径不按 data_dir 过滤**。observe 里反复出现的 `4090` 把 bm25 顶满，配置正文被淹没。与「观察默认不进工作区 FTS」产品口径冲突。  
2. **`scopes=['lore']` 空库仍 `ok` + 0 hits**：规划/search_menu 把 lore 标成账号/长期笔记高效通道；库空时无「笔记库为空，请改 work_content/work_name」强提示，易诱发「索引没用 → 去磁盘」假归因。  
3. **查找落点提示与效率菜单错位**：提示写「先 lore 或 tasks」；效率上 `work_name`/`work_content` 才是仓内配置 md 的真通道。口令/SSH 多在工作区 md，不在空笔记库。

### 调用

- 首刀只打 `lore`，空结果后未并行/紧接 `work_content`（或 `work_name` +「配置/云服务」）。  
- 宽度闸挡住整根后，改 glob `.env` 与产品子树 grep——可选探索喧宾夺主。  
- `task_context_search` 误当「全局配置缓存」；该工具只回本任务 history。  
- 铲子 `remote_job_status` 已命中，但缺 host 时未回到「配置落点」必要路径的正确索引面。

## 机制根因（可复用名）

总述（用户口径可对齐）：**整体索引搜索在误导大模型**——不是「库里没文件 / 个数太少」，而是**检索产品面把规划带偏**：错通道仍像「搜过了」、有命中时排序把假相关顶到前窗、空结果缺少「该换哪条通道」的硬提示。

1. **索引通道空转 + 错域首刀**（工具提示把空 lore 当配置主通道；能力名「个人知识库·检索」却是全通道入口）。  
2. **空通道语义过软**（已核：`scopes=['lore']` 且 0 hits 时 **仍无 `hint`**；仅 `work_name` 空有换道提示——表内 P0「空 lore 强 hint」**尚未兑现**）。模型易读成「整库无配置」而非「笔记域空、该打 work_*」。  
3. **有命中也误导**：缺省/`work_*` 对「beyond 5080 ssh password」类 query，前窗常是论文文件名、`fawai.env`、甚至 `Password*.vue` 打包名，真口令文 `比昂农业.md` 不一定进窗——**排序/词面**把模型锁进错误落点。  
4. **正文 FTS 被运行态污染**（工具层：data_dir 残留；查询剔除已落地，purge 仍可选）。  
5. **索引 miss 叙事 → 磁盘/窄 glob 空转**（调用：未换 scope/query；宽度闸只挡住整根）。  
6. （次要）**单 token 配置检索排序弱**：即便清污染，仅 `4090`/`5080` 仍可能落在 head_limit 外——须文件名/目录词或更特指 token 补刀。

## 通用优化（禁止本案菜谱升闸）

| 优先级 | 层 | 项 | 验收 |
|:---|:---|:---|:---|
| **P0** | 工具 | 正文/文件名 **search 结果剔除 `FAWAI_DATA_DIR` 树**；提供一次 **purge 残留 data_dir 行**（重建或启动自检） | `lore_search(work_content, "4090")` Top 不再被 `data/observe` 占满 → **查询剔除已落地**；purge/重建仍可选 |
| **P0** | 工具 | 搜索与记忆配置开关取交：已关范围不查旧索引；缺省只跑已开通道 | 仅开 lore 时 hybrid/`scopes` 强制 work 均不回工作区命中 → **已落地** |
| **P0** | 工具 | `scopes=[lore]` 且笔记库空/0 hits：返回明确 `hint`（改 `work_content`/`work_name`，配置类勿死盯 lore） | 空 lore 不再被读成「整库无配置」 |
| **P1** | 提示/菜单 | 「查找账号/地址/配置落点」：**先 work_name / work_content**（高特指词），lore 仅当笔记业务已开且非空；tasks 只找旧任务 | 同类 goal 首刀不进空笔记域 |
| **P1** | 调用/闭环 | 索引空结果：强制换 scope 或换 query 族，**禁止**立刻整树/乱子树磁盘扫（对齐已有宽度闸 + 索引先于磁盘） | miss 后下一刀仍是索引面 |
| **P2** | 工具（可选） | 配置类 query 对 path 含 `配置`/`云服务`/`credentials` 等目录加权；或 search 回 `suggested_scopes` | 单关键词时真配置更易进前窗 |

## 界面开关（已与搜索配套）

| 操作 | 效果 |
|:---|:---|
| 取消数据区「观察落盘 / 其它 / 任务 / 日志」 | 对应通道不进 `lore_search`（观察本即 `engine=none`） |
| 仅保留「知识库存盘」 | 须同时关掉**工作区**（及可读区）FTS/文件名；保存后缺省混合只跑 lore |
| 不重建索引 | **可以**：已关范围不查；工作区命中仍会剔除 `data/` 路径残留 |
| 彻底清库 | 可选：重建工作区正文/文件名索引，从 sqlite 删掉 data_dir 行 |

查仓内配置 md 时再打开工作区 FTS（或定向 `work_content`），勿与「只要笔记库」混为一谈。

## 明确不做

- 不为「4090 / AutoDL / 某 md 文件名」开专用闸或专用提示词分支。  
- 不把 `task_context_*` 改成跨任务全局凭据库。  
- 不建议关掉宽度闸来「方便磁盘扫」。

## 证据任务短链

`3171117…`：`lore_search×8`（含多次 lore 空）、`fs_grep×20`、`fs_read×11`、`shovel_search` 已见 `remote_job_status`；反省 fingerprint `93bece79…` 把 lore 空写成缺凭证主因。

## 对照样本：Fast 开仍慢（2026-11-09）

任务 `9a6b48f9df2c4a2daf2f27083a603a75`（goal「beyond的5080服务器现在训练进展如何」）：`fast=true` / `effort=true` / `step_tier=true`，仍约 **368s**、21 轮。用户观感是「第一次没搜出正确配置、是不是索引磁盘文件太少」。

### 墙钟拆分（已核）

| 段 | 约耗时 | 内容 |
|:---|:---|:---|
| 配凭证（step 0～7） | ~106s（约 29%） | `lore` 空 → 错读 `fawai.env` → 窄 `fs_glob` → 反复读 README/部署说明 → 停滞闸才指向 `比昂农业.md` |
| 远端查进度（step 8～20） | ~262s（约 **71%**） | SSH 已通后仍 16 次 shell：错盯 `fawai-avatar.std` / `ai.std`、sudo 无 tty、晚才见 `pyfernet` |

Fast 已生效（obs 多为 `skipped_fast`，thinking 关）。慢主要不是深度思考，而是**错域检索 + 读到指针不跟 + 远端日志偏科**。

### 「索引文件个数太少？」——否

本机已核：`memory_index/names.sqlite` 约 **20 万** 文件名；`content.sqlite` 约 **9.2 万** 正文；`study/配置信息/比昂农业.md` **两边都有**。  
空结果来自 **调用锁死 `scopes=['lore']`**（笔记库对该 query 恒 0），不是索引盘点太少、也不是 head_limit 截掉了那一份文件。

同机复现：同一 query 若不锁 lore、走缺省/`work_content`/`work_name`，能打到 `beyond-5080/README.md`、`fawai.env`、`比昂农业.md`（query 换成「比昂农业 5080」时后者进前窗）。首刀锁 lore → 工具诚实返回 0 → 规划误当成「库里没有配置」。

### 本样本额外机制（叠在「错域首刀」上）

1. **窄 glob 当全库**：`fs_glob path=study/配置信息/beyond-5080 pattern=*` 只列该子树（约 5 条），不含父目录 `比昂农业.md`——个数少是**搜索根选窄**，不是索引上限。  
2. **已齐指针未推进**：step 3 已读到 README 文首「口令只在 `../比昂农业.md`」，且同页已有外网 SSH `8.137.33.38:37443`；step 4～5 仍同构重读头部，直到步骤重复/停滞闸才换文件。  
3. **远端必要路径偏科**：拿到口令后长期把 fawai 网关日志当训练进度；真训练是 `pyfernet` + `train_core_scls.log` 一类落点（对照同日快任务 `b1f494ec…` 更早切到该日志）。

### 对优化表的含义

- 仍优先表内 **P0「空 lore 强 hint」** 与 **P1「配置落点先 work_*」**——本样本再次命中，与索引体积无关。  
- 不新增「把 names 索引 head_limit 调大」项：本案不是截断问题。  
- 远端日志偏科属调用/方法族换道，沿用既有进度闸与方法族纪律，禁止升成「5080 专用读哪份 log」闸。

### 为何绕弯路没进知识库？（同日三任务已核）

`9a6b48…` / `8016592e…` / `b1f494ec…` 全程**零次** `success_ledger_upsert` / `lore_note_upsert` / `skill_write`；本机 `data/sessions/*/success_ledger.jsonl` 亦不存在；`lore` 笔记 FTS **0 条**。

产品口径（已更新）：跨任务知识仍可靠执行中 `success_ledger_upsert` → 过门升 lore；**E1 现默认终态条件短卡蒸馏**（多法成功 / 明确失败 → 台账 → lore；`FAWAI_TASK_DISTILL`；见 [自我进化](../功能/自我进化/说明.md)）。本案当时尚无该钩子，故弯路未入库。仍禁整段轨迹灌库与默认棘轮。

与「搜索误导」叠加：搜空 lore + 当时不写 lore → 闭环空转；条件蒸馏补的是写入侧。

## 对照样本：同题再跑仍弱感知配置/口令（2026-11-09）

任务 `df0e81197c2f44009748d9e0ca487e94`（goal「查看4090云服务器AI训练状态和进展」；追问含「按已有配置和 ssh 查询方式重查」）。工程根已迁到 workspace 顶层 `fawai-avatar/`（本文件随仓）。

### 现象（相对「已读过配置」的用户预期）

| 阶段 | 实际行为 | 观感 |
|:---|:---|:---|
| 开场 | `lore_search scopes=['lore']` + `task_context_search` + `shovel_search`；未首刀 `work_name`/`work_content` | 「知识库/缓存」不给 host/口令 |
| 中段 | 在 `fawai-avatar` 下 grep `*.env` 等 → 反省写「配置文件搜索无果，**排除静态配置查询路径**」 | 假归因：仓内明明有 `study/配置信息/云服务重要信息.md` |
| 晚到 | 约十余步后才 `fs_read` 该 md，SSH 才通 | 配置「感知慢」 |
| 追问/续跑 | 再读同一 md，却用 awk/grep/`HOST=` 进程替换去「抽密码」→ `Permission denied`；一度连错 host（`connect.yza1…:26592`） | 口令「记不住 / 用错」 |
| 蒸馏升 lore | 过门短卡 `success_sl_aeb881a6c3874182`：`config_refs.path=<credentials_config_path>`，命令里 `<user>@<host>` | 下次 `scopes=[lore]` 能命中笔记，**仍无真实落点与口令取法** |

### 工具双栏（本样本）

**工具本身**

1. 规划文案仍写「查找账号/配置落点：先 `scopes=[lore]`/`[tasks]`」（`brain/planner.py`）；仓内 SSH 口令真源在工作区 md → **提示与真通道错位**（同表 P1，未改）。  
2. `scopes=['lore']` 且 0 hits 时 **仍无 `hint`**（本机复核 query「绝对不存在的凭证…」：`ok=true count=0 hint=None`）——表内 P0 未兑现。  
3. 成功蒸馏为过门升 lore：**脱敏正确，但路径也占位化** → lore 命中无法充当「下次打开哪份配置」的指针；模型只能再猜/再扫。  
4. （调用叠加工具摩擦）默认 `/bin/sh` 不支持 `source <(…)`；口令在 Markdown 散文里，无结构化槽 → 模型爱写脆弱正则抽密，易抽错字段。

**调用**

- 首刀锁 lore / task_context；空/弱命中后改产品树磁盘扫，**未换 `work_name=云服务重要信息` 或 `work_content`**。  
- 已成功 SSH 的 host/port/口令取法未进入可复用结构化槽；追问仍「再解析 md」而非「沿用已验证 ssh 配方」。  
- 远端日志路径偏科（臆造 `…/qwe/`）与凭证问题叠加，放大「进度不对」的用户观感。

### 机制根因（可复用名；叠在上文总述上）

1. **错域首刀 + 空 lore 软失败**（同前）。  
2. **假排除静态配置**：窄磁盘 miss 被写成「没有配置文件路径」。  
3. **凭据无耐久指针**：脱敏升库后只剩占位符；任务内也无「已验证连接三元组」槽，续跑靠脆弱再抽取。  
4. **口令抽取当解析器**：把 Markdown 当 `KEY=VALUE` / awk 字段 → 认证失败，误判成「配置弱」实为**复用失败**。

### 对优化表的含义

- 仍置顶：**P0 空 lore 强 hint**、**P1 配置落点先 work_***。  
- **补强既有蒸馏/台账层（勿开 4090 专用闸）**：过门短卡的 `config_refs.path` 允许保留**非密**真实相对路径（如 `study/配置信息/…md`），口令仍禁止入库；或 `commands` 指向已有 `ssh-run-with-pass.sh` +「口令从授权 md 读入 env」的通用写法。验收：同类远端状态 goal 下次 lore/tasks 命中能直接打开正确 md，而不是再排除静态配置。  
- **明确不做**：不为单机写死 host/口令进提示词；不把 `task_context_*` 改成全局密码库；不建议明文口令进 lore 正文。
