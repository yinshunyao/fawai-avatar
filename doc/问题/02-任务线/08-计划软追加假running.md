# 计划软追加假 running

计划父任务图上节点已全部完成后，用户追问仍把父标成 `running`，却不拉起任何 Worker。逐条对齐；结论和改进改原条。

| # | 问题 | 状态 | 结论 | 改进方案 |
|:---|:---|:---|:---|:---|
| 1 | 全图 `completed` 后追问（如「直接输出 PPT」）为何侧栏仍「执行中」却无步骤？ | 已确认 | **表层**：进度「已下发追加约束 → running 0 · 新拉起 0」，父库 `running`。**机制**：`_continue_plan_soft_append` 对 `dispatch_note`/`no_graph_change` 只给 running 监管脑塞 `pending_appends` 或 `spawn_ready`；图已无活跃节点时两者皆空，却在此前 `requeue`+`set_running`。编排器父任务 `_maybe_start_worker` 本就不跑逐步 Worker，故空转。证据任务 `6917c303…`（n1–n5 已 completed）。 | **已改**：无 running 目标且无新拉起、图无活跃节点时，设 `plan_allow_session_worker` 并同线程拉起父任务 Worker 消费 followups；禁止无 Worker 的空 `running`。助手不得手改该任务库行救场（见 `rules-no-intervene-tasks`）。 |
