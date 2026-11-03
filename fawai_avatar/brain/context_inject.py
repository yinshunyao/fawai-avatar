# term:编码上下文  doc:doc/功能/编码闭环/说明.md
"""规划前注入 AGENTS.md / .cursor/rules 摘要。"""

from __future__ import annotations

from pathlib import Path

from fawai_avatar.config import RuntimeConfig


def _read_capped(path: Path, *, max_chars: int) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n…(truncated)"


def coding_context_prompt_block(*, max_total: int = 6000) -> str:
    cfg = RuntimeConfig.from_env()
    root = Path(cfg.work_root).expanduser().resolve()
    chunks: list[str] = []
    agents = root / "AGENTS.md"
    if not agents.is_file():
        agents = root / "doc" / "AGENTS.md"
    body = _read_capped(agents, max_chars=2500)
    if body:
        chunks.append(f"## 工作区 AGENTS.md\n{body}")

    rules_dir = root / ".cursor" / "rules"
    if rules_dir.is_dir():
        files = sorted(rules_dir.glob("*.mdc"))[:6]
        for f in files:
            part = _read_capped(f, max_chars=800)
            if part:
                chunks.append(f"## 规则 {f.name}\n{part}")

    if not chunks:
        return ""
    text = "编码上下文（只读注入，遵守其中约束）：\n" + "\n\n".join(chunks)
    if len(text) > max_total:
        text = text[:max_total] + "\n…(context truncated)"
    return text
