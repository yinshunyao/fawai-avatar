# Cursor 多轮拆成新任务

| # | 问题 | 状态 | 结论 | 改进方案 |
|:---|:---|:---|:---|:---|
| 1 | 同一 Cursor 对话多轮发送会新开任务/agent，对不上原会话？ | 已确认 | **表层**：第二句起侧栏多出一条 Cursor 卡。**机制**：① 发送前 `outboundTaskIdForSend` 在「直连开 + 当前焦点不像 Cursor」时剥掉 `task_id`，服务端无焦点则 `_create_cursor_agent_task`；② `isCursorAgentTaskId` 只看 `meta.runtime`，而 `/api/tasks` 列表摘要常只有 `exec_config.runtime`/`cursor_agent`、无完整 `meta`——刷新或从侧栏点开后误判为 fawai，下一句被剥 id。 | **P0 已改**：① `isCursorAgentTaskId` 与底栏同源吃 `exec_config`+`meta`；② `refreshHome` 把列表 `exec_config.runtime` 提进 `meta`；③ hub `_is_cursor_agent_task` 改用 `is_cursor_agent_meta`；从 fawai 新开 Cursor 时带上 `related_task_id`。 |
