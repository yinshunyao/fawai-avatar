# term:服务保活  doc:doc/功能/服务保活/说明.md
"""启动失败 → 一键创建整改任务（目标/要求/边界）。"""
from __future__ import annotations

from typing import Any


def build_startup_repair_goal(
    *,
    crash_text: str,
    console_tail: str = "",
    degraded: dict[str, Any] | None = None,
) -> str:
    """根据崩溃摘录 / 降级状态写清整改任务目标。"""
    crash = (crash_text or "").strip() or "（无 last_crash 摘录；请结合后台日志尾部）"
    if len(crash) > 10000:
        crash = crash[-10000:]
    tail = (console_tail or "").strip()
    if len(tail) > 6000:
        tail = tail[-6000:]
    deg = degraded or {}
    deg_lines: list[str] = []
    if deg.get("system_log_degraded") or deg.get("degraded"):
        deg_lines.append("- 系统日志库：降级（available=false）")
    if deg.get("system_log_error") or deg.get("init_error"):
        err = deg.get("system_log_error") or deg.get("init_error")
        deg_lines.append(f"- 系统日志 init_error：{err}")
    deg_block = "\n".join(deg_lines) if deg_lines else "- （未报告组件降级，以崩溃栈为准）"

    return f"""【启动错误一键修复】

## 目标
修复会话网关 / 保活子进程**启动失败或组件降级**，使服务可稳定拉起：Web 基本功能可用、系统日志库可恢复（若相关）、不再因同一根因崩溃重拉。

## 现象与证据
### 组件降级
{deg_block}

### 最近崩溃摘录（data/runtime/last_crash.log）
```
{crash}
```

### 后台日志尾部（节选）
```
{tail or "（空）"}
```

## 要求
1. 先读栈与相关源码定位根因；禁止凭印象大改。当前已知一类问题：旧 `system_logs.sqlite` 缺列时，`CREATE INDEX … analyze_task_id` 早于 `ALTER TABLE` 导致 `OperationalError`——若证据吻合，须保证**先迁移加列再建依赖索引**（或等价安全顺序），并自测旧库可起。
2. 改后本地验证：初始化 SystemLogStore / 起网关不再因该错误退出；降级路径仍保留作兜底。
3. 同步 `doc/功能/系统日志` 或 `服务保活`（若行为有变）与术语表实现映射。
4. 结束时在 success 的 thought 用中文说明：根因、改了哪些文件、如何防复发。

## 边界
- 只改与本次启动失败相关的初始化 / 迁移 / 降级兜底；禁止无关大重构。
- 遵守自我进化最少核：禁止改保活外层禁改路径的语义闸门；可改迁移与降级逻辑。
- 禁止动 `.env` 与密钥；禁止删除用户 `data/` 业务数据（可迁移 schema）。
- 复用编码闭环：`fs_grep` / `fs_read` / `fs_patch` / `code_check`；不要只口头建议。"""


def repair_title(degraded: dict[str, Any] | None = None) -> str:
    deg = degraded or {}
    if deg.get("system_log_error") or deg.get("init_error"):
        return "修复启动·系统日志库"
    return "修复启动错误"


def repair_intro(*, task_id: str) -> str:
    return (
        "已根据后台崩溃日志自动创建「启动错误修复」任务"
        f"（{task_id[:8]}…）。\n"
        "将分析启动栈并改初始化/迁移代码；请在本对话跟踪进度。"
    )
