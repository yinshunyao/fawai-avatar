# fs_patch 失败率

证据任务：`437588780c74476986ec8bc5b6b297db`。机制全文：`doc/分析/fs_patch失败率与指纹expect.md`。

| # | 问题 | 状态 | 结论 | 改进方案 |
|:---|:---|:---|:---|:---|
| 1 | `fs_patch` 表面失败率极高（本案 5/5），是否工具坏了？ | 已确认 | **表层**：UI/stats 记 5 次失败。**机制（工具本身优先）**：① `_FINGERPRINT_KEYS` 无 `old`/`new`/`hunks`，同 path 不同补丁共指纹 `a810c…`，守卫把换锚点当同构重试；② 成功 message 是 `fs_patch {path}`，模型常 `expect.contains="patch applied"`，写入已成功仍被改成 `ok=false`（本案 2/5）；③ 假失败后文件已变，再 patch 易真 `old not found`。**调用侧**：编造 old、缩进错、字面 `\\n`（本案 3/5 真锚点失败）。 | **P0 已改**：① mutate 指纹哈希 `old`/`new`/`hunks`；② 成功回执 `· applied`；已写入时忽略臆造「patch applied」类 expect；真实 expect 失败标明【写入已落盘】+ diff 小窗。P1（规划默认可不带 expect）未改。 |
| 2 | 同指纹反复 alternate 是否掩盖了「其实已改文件」？ | 已确认 | 是。`write_set` 已有 path；两次 `hunks_applied=1` 仍进 reflect alternate；反省文案写「无法确认是否修改」。更深：假失败 + 粗指纹共同制造「同 fp 失败 N 次」叙事。 | **P0 已改**（同条 1）：换 hunk 指纹不同；臆造 expect 不再翻失败；披露已落盘。 |
