# fs_patch 失败率：指纹过粗 × expect 假失败

关联任务：`437588780c74476986ec8bc5b6b297db`（手机端步骤列表 UI）。  
口径：`doc/功能/任务复盘/分析规则.md`。

## 目标与终态

Goal：手机版步骤列表——名称可见、token 统计占位、操作按钮别太稀。  
任务当时仍在跑；`stats.tools.fs_patch=5`，表面上 **5/5 失败**。`write_set` 已含 `index.html`，说明有写入副作用落盘，与「全失败」文案矛盾。

## 工具双栏

### 工具本身（P0）

1. **动作指纹不含补丁正文**  
   `brain/reflect.py` 的 `_FINGERPRINT_KEYS` 有 `path`，**没有** `old` / `new` / `hunks`。  
   复现：任意改同一 `path` 的 `fs_patch`（正文完全不同）指纹恒为 `a810c12c11d54761`。  
   后果：步骤重复/反省把「换了锚点的新补丁」当成同指纹重试 → 连续 `alternate`、逼换方法族或 `failed`，**有效 mutate 被守卫误伤**。

2. **成功回执文案与模型 expect 脱节 → 假失败**  
   `_apply_or_pend` 成功 message 为 `` `{kind} {path}` ``（例：`fs_patch fawai-avatar/.../index.html`），**不含** `patch applied`。  
   规划提示「倾向带 expect contains」，模型常写 `expect.contains="patch applied"`。  
   `apply_expect_to_result` 在工具已 `ok` 且 **已 write** 后，因 haystack 缺该串把结果改成 `ok=false`（`expect_pre_ok=true`，`hunks_applied=1`）。  
   本案 5 次里 **2 次** 属此类：磁盘已改，闭环当失败。

3. **假失败后的级联**  
   文件已变，后续仍按「未改前」或脑补 `old` 再 patch → 真 `old not found`；同 path 又撞同一指纹 → 空转加重。

### 调用

1. **锚点未接地**：编造 `.step-item { color: #ffffff; }`；缩进/闭合括号与 `@media` 原文不一致。  
2. **字面量 `\\n`**：多行 `old` 写成反斜杠 n，文件里是真换行。  
3. **误 expect**：对 mutate 断言不存在的成功口令，而不是断言 diff 片段或省略 expect。  
4. **开场探仓偏科**：猜 `fawai-avatar/client`、臆造标识符；定位到 `channel/static/index.html` 后仍多次未逐字复制小窗。

## 必要 / 可选路径

| 槽位 | 类型 | 本案 |
|:---|:---|:---|
| 定位步骤列表 / 移动端样式 path | 必要 | 后半已齐（`index.html` @media） |
| 取证小窗原文作 old | 必要 | 多次未接地；假失败后原文过期未重读 |
| mutate（patch/replace）落地可见布局 | 必要 | 两次真写入被 expect 判败；后续 replace 又改一刀 |
| 宽搜仓 / VoiceFix 文档 | 可选 | 前段喧宾夺主 |

## 机制根因（可复用）

1. **mutate 指纹过粗** — 同 path 不同 hunk 同指纹。  
2. **expect 假失败掩盖已写入** — 成功文案与提示诱导的 needle 不一致；失败后不声明「已落盘」。  
3. **补丁锚点未接地 / 开读空转**（调用）— 编造 old、字面 `\\n`；被 1+2 放大。

## 证据链

| 步 | 现象 |
|:---|:---|
| step 19 | `old not found`，编造 `.step-item` |
| step 24 | `old not found`，缩进/闭合与 `@media` 不符；同批 git_* 未执行 |
| step 26 | **已 applied** + `expect failed: 'patch applied' missing`；`expect_pre_message=fs_patch …` |
| step 29 | `old` 含字面 `\\n` → `old not found`；指纹仍 `a810c…`（第 4 次） |
| step 30 | **再次已 applied** + 同一 expect 假失败 |
| fingerprint_fails | `a810c12c11d54761: 5`（五次正文不同的 patch 共指纹） |

桶：真锚点失败 3；expect 假失败 2；表面成功记 0。

## 通用优化清单

下列 P0 为**必要项**（已落地）；P1 仍待做。

| 优先级 | 层 | 项 | 状态 |
|:---|:---|:---|:---|
| P0 | 工具/闭环 | mutate 指纹纳入 `old`/`new`/`hunks`（全文哈希） | **已改** `brain/reflect.py` |
| P0 | 工具/闭环 | 成功 message `· applied`；已写入时忽略臆造「patch applied」expect | **已改** `control/background.py` + `brain/action_segment.py` |
| P0 | 工具/观察 | 真实 expect 失败且已写入 → `【写入已落盘】` + diff 小窗 | **已改** `action_segment` + `observe/tool_digest.py` |
| P1 | 提示词 | mutate 默认可不带 expect；若带则 needle 须来自 new/diff 片段 | 未改 |
| P1 | 调用/lesson | 补「字面 `\\n` ≠ 换行」 | 未改 |

## 明确不做

- 不为「手机步骤列表 / index.html」开专用闸。  
- 不靠关步骤重复绕过空转。  
- 不把本案改成「只怪模型不会用」而留下指纹与 expect 契约坑。
