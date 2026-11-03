# term:任务与事件管理  doc:doc/功能/任务与事件管理/说明.md
"""工作任务与总管（Supervisor）。"""

from fawai_avatar.task.models import Event, ResourceItem, TaskRecord
from fawai_avatar.task.supervisor import Supervisor

__all__ = [
    "Supervisor",
    "TaskRecord",
    "ResourceItem",
    "Event",
]
