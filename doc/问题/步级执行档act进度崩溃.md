# 步级执行档 act 进度崩溃

`step_run=act` 且规则通过、准备推「观察反省：跳过」时，闭环崩在进度回调。逐条对齐记在这里。

| # | 问题 | 状态 | 结论 | 改进方案 |
|:---|:---|:---|:---|:---|
| 1 | 规划写出 `step_run=act`、工具全成功、规则通过后，时间线显示框架内部 `WorkerLoop._progress()` 报 `TypeError: got multiple values for keyword argument 'step_run'`，当前 run 直接崩溃，后续工具（含 SSH）无法再执行。 | 已确认 | **表层**：进度文案还没出站就异常退出。**机制**：`obs_reflect` 的 `skipped_act` 分支里对 `_progress` 同时写了显式关键字 `step_run="act"`，又 `**_step_label_extra(task_id, plan)`；后者在 `plan.step_run` 规范化为 `act` 时也会放入 `step_run` / `step_run_label`。Python 在调用处把同一关键字传了两次，抛确定性 `TypeError`。与工具参数、换道、SSH/训练目标无关；任意 `act`+规则通过的成功步都会撞。alternate 无法绕过，必须修框架。 | **已改**：去掉显式 `step_run=`；随后跳过路径改为**不推** `obs_reflect` 进度卡（与环境观察 `none` 真跳过同口径）；`demo_step_run` 仍保留「label_extra 与显式 step_run 撞关键字」回归。 |