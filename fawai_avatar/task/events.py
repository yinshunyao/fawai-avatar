# term:任务与事件管理  doc:doc/功能/任务与事件管理/说明.md
"""工作任务事件类型常量。"""

from __future__ import annotations

# 生命周期
TASK_CREATED = "task.created"
TASK_PAUSED = "task.paused"
TASK_RESUMED = "task.resumed"
TASK_CANCELLED = "task.cancelled"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
TASK_ARCHIVED = "task.archived"
TASK_UNARCHIVED = "task.unarchived"
TASK_STATUS = "task.status"

# 租约
FOREGROUND_CLAIMED = "lease.foreground.claimed"
FOREGROUND_RELEASED = "lease.foreground.released"
FOREGROUND_BLOCKED = "lease.foreground.blocked"
RESOURCE_CLAIMED = "lease.resource.claimed"
RESOURCE_RELEASED = "lease.resource.released"
RESOURCE_BLOCKED = "lease.resource.blocked"

# 汇报
TASK_REPORT = "task.report"
TASK_FOLLOWUP = "task.followup"
