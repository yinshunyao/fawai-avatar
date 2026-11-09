# Fast（极速）模式

## 要解决什么

用户要「少想、快跑」：关掉供应商深度思考、少一次成功后的观察反省 LLM，并避免进计划图编排。与「全力」（Effort）正交——一个管收束谨慎度，一个管路径延迟。

## 与 Effort / 快模型角色

| 开关 | 管什么 | 默认 |
|:---|:---|:---|
| Fast（极速） | 关深度思考、跳过观察反省、禁止计划模式 | 关 |
| Effort（全力） | 收紧 enough_to_answer / 可修复失败不得早退 | 关 |
| `planner_role=fast` | 主脑委派快模型段（路由角色） | 按委派 |

二者解耦：可同时开（极速少想 + 全力收束），也可只开其一。

## 任务级覆盖

`meta.fast=true` → 闭环写入 `_plan_fast` / 传 `fast=` 给 reflect；`llm.chat(..., enable_thinking=False)` 盖过 `thinking_role=main` 与模型档案。

## 作用域与末次继承（产品口径）

与 Effort 共用同一套生命周期，见 [极速全力任务级开关](../问题/极速全力任务级开关.md)。

| 场景 | 行为 |
|:---|:---|
| 进行中任务 | 底栏 = 该任务 `meta.fast`；拨动立刻写回任务 meta，闭环下一拍生效；并刷新全局末次 |
| 已结束任务 | 打开时底栏展示该任务终态 `meta.fast`（不是全局偏好盖上去） |
| 新建任务 | 跟随全局末次设定（不区分任务类型）；仍可改 |
| 无当前任务 | 底栏显示全局末次 |

现状缺口：底栏只写本机 `localStorage`，入站才 merge；切任务不回填；只拨不发则 UI ≠ `task.meta`。

## 对照样本：同类查训练进度（2026-11-09）

同一 goal 族（Beyond 5080 训练进度），两任务都 `completed`、`effort=true`，轮次接近，墙钟差约 **2×**：

| | 慢 | 快 |
|:---|:---|:---|
| task_id | `8016592ea7e4410fad1c8af2965e1782` | `b1f494eca9384f1d9956d9b28edce344` |
| `fast` | **false** | **true** |
| `step_tier` | true | 无 |
| 墙钟 | 228s | 110s |
| completion_tokens | **8938**（约 1117/次） | **1605**（约 178/次） |
| prompt_tokens | ~109k | ~110k |
| rounds / llm_calls | 7 / 8 | 8 / 9 |
| obs_reflect | 首步 `brief` LLM；其后多为 `skipped_act` | 全程 `skipped_fast` |
| reflect 墙钟 | ~27s | ~6s |

可见 thought / actions 正文长度接近；completion 多出来的约 **76%** 无法用可见终稿解释，符合 **Fast 关 → 深度思考开** 的开销，而不是「多跑了很多工具轮」。

次要拖慢（同案）：慢任务首步读了 `beyond-5080/fawai-avatar` 部署材料、SSH 带 `nvidia-smi`/`sudo supervisor`、一步 shell `returncode=2` 触发较贵的执行反省；这些加重墙钟，但量级仍小于思考开关带来的每调用 completion 差。

可复用判断：同类「读配置 → SSH → 答进度」问询，**关极速**会把单次规划/反省拉到约 2× 墙钟与约 5～6× completion；要快先开 Fast（或确认模型档案未开 thinking），不要先怪工具链本身。
