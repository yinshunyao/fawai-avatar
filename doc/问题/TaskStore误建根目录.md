# TaskStore 误建根目录

| # | 问题 | 状态 | 结论 | 改进方案 |
|:---|:---|:---|:---|:---|
| 1 | 项目根出现 `<fawai_avatar.task.store.TaskStore object at 0x…>` 目录（含 `tasks.sqlite` / `system_logs*`）。 | 已确认 | **表层**：临时脚本写了 `Supervisor(store)` / `SessionHub(sup)`，`str(store)` 被当成 `data_dir`，cwd 在仓根就建怪目录。正式入口（`lan_gateway` / `loop`）传路径字符串，无此调用。**机制**：`TaskStore` / `SystemLogStore` 等对任意对象 `str()` 后 `makedirs`，传错不报错。 | **已改**：`data_dir.py::resolve_data_dir` 只接受路径；对象与 `object at 0x` 字面量抛 `TypeError`。任务库/系统日志/记忆配置/LLM 设置库接入；`demo_smoke` 覆盖。垃圾目录已删。 |
