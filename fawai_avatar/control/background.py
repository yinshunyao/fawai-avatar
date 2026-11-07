# term:工作区文件  tool:fs_*  doc:doc/功能/工作区文件/说明.md
# term:代码索引  tool:code_symbols/code_map/code_index_refresh  doc:doc/功能/代码索引/说明.md
# term:自我进化  tool:skill_*  doc:doc/功能/自我进化/说明.md
# term:编码闭环  tool:fs_patch/code_check/git_*/explore_*  doc:doc/功能/编码闭环/说明.md
# term:Python 扩展  tool:py_write/py_pip/py_run  doc:doc/功能/Python扩展/说明.md
# term:脚本铲子生命周期  tool:shovel_search  doc:doc/功能/脚本铲子生命周期/说明.md
"""后台动作：文件、代码索引、版本、Skill、编码闭环、Python 扩展、铲检索（不经 GUI / 不受 FAWAI_DRY_RUN 影响）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fawai_avatar.config import RuntimeConfig
from fawai_avatar.skills.store import SkillStore, SkillValidationError
from fawai_avatar.types import Action, ExecResult
from fawai_avatar.versioning.store import VersionStore
from fawai_avatar.workspace.check import coerce_code_check_args, run_code_check
from fawai_avatar.workspace.code_index import CodeIndex
from fawai_avatar.workspace.explore import coerce_explore_search_args, explore_search
from fawai_avatar.workspace.fs import (
    WorkspaceFs,
    patch_anchor_quality_error,
    resolve_patch_ops,
    resolve_read_range,
)
from fawai_avatar.workspace.git_ops import GitError, git_commit, git_diff, git_status
from fawai_avatar.workspace.paths import WorkspacePathError, format_file_missing_message
from fawai_avatar.workspace.pending_diff import PendingDiffStore, unified_diff
from fawai_avatar.workspace.py_session import (
    PySessionError,
    pip_install,
    run_script,
    session_root,
    write_file as py_write_file,
)
from fawai_avatar.workspace.shovel_ops import ShovelOpsError, search_shovels


_FS_KINDS = frozenset(
    {"fs_glob", "fs_grep", "fs_read", "fs_read_many", "fs_write", "fs_replace", "fs_patch"}
)
_CODE_KINDS = frozenset(
    {"code_symbols", "code_map", "code_index_refresh", "code_check", "explore_search"}
)
_GIT_KINDS = frozenset({"git_status", "git_diff", "git_commit"})
_VERSION_KINDS = frozenset(
    {
        "version_snapshot",
        "version_list",
        "version_get",
        "version_delete",
        "version_rollback",
    }
)
_SKILL_KINDS = frozenset({"skill_search", "skill_write", "skill_get"})
_LORE_KINDS = frozenset(
    {
        "lore_search",
        "lore_get",
        "lore_set",
        "lore_note_upsert",
        "lore_buffer_list",
        "lore_vector_reindex",
    }
)
_DIFF_KINDS = frozenset({"diff_confirm", "diff_reject", "diff_list"})
_VERIFY_KINDS = frozenset({"shell_verify"})
_PY_KINDS = frozenset({"py_write", "py_pip", "py_run"})
_SHOVEL_KINDS = frozenset({"shovel_search"})
BACKGROUND_KINDS = (
    _FS_KINDS
    | _CODE_KINDS
    | _GIT_KINDS
    | _VERSION_KINDS
    | _SKILL_KINDS
    | _LORE_KINDS
    | _DIFF_KINDS
    | _VERIFY_KINDS
    | _PY_KINDS
    | _SHOVEL_KINDS
)

_WRITE_KINDS = frozenset({"fs_write", "fs_replace", "fs_patch", "version_rollback", "git_commit"})


class BackgroundTools:
    def __init__(
        self,
        *,
        work_root: str | Path | None = None,
        data_dir: str | Path | None = None,
        auto_version: bool | None = None,
        diff_mode: str | None = None,
        task_id: str | None = None,
    ) -> None:
        cfg = RuntimeConfig.from_env()
        root = Path(work_root or cfg.work_root).expanduser().resolve()
        data = Path(data_dir or cfg.data_dir)
        self.auto_version = cfg.auto_version if auto_version is None else auto_version
        self.diff_mode = (diff_mode or cfg.diff_mode or "auto").strip().lower()
        self.task_id = task_id
        self.data_dir = data
        self.full_write_line_limit = cfg.full_write_line_limit
        self.fs = WorkspaceFs(root, read_roots=list(cfg.read_roots))
        self.code = CodeIndex(root, data_dir=data)
        self.versions = VersionStore(data / "versions", root)
        self.skills = SkillStore(data / "skills")
        self.pending = PendingDiffStore(data)

    def execute(self, action: Action) -> ExecResult:
        kind = (action.kind or "").strip().lower()
        try:
            if kind == "fs_glob":
                return self._fs_glob(action.args)
            if kind == "fs_grep":
                return self._fs_grep(action.args)
            if kind == "fs_read":
                return self._fs_read(action.args)
            if kind == "fs_read_many":
                return self._fs_read_many(action.args)
            if kind == "fs_write":
                return self._fs_write(action.args)
            if kind == "fs_replace":
                return self._fs_replace(action.args)
            if kind == "fs_patch":
                return self._fs_patch(action.args)
            if kind == "code_symbols":
                return self._code_symbols(action.args)
            if kind == "code_map":
                return self._code_map(action.args)
            if kind == "code_index_refresh":
                return self._code_index_refresh(action.args)
            if kind == "code_check":
                return self._code_check(action.args)
            if kind == "explore_search":
                return self._explore_search(action.args)
            if kind == "git_status":
                return self._git_status(action.args)
            if kind == "git_diff":
                return self._git_diff(action.args)
            if kind == "git_commit":
                return self._git_commit(action.args)
            if kind == "version_snapshot":
                return self._version_snapshot(action.args)
            if kind == "version_list":
                return self._version_list(action.args)
            if kind == "version_get":
                return self._version_get(action.args)
            if kind == "version_delete":
                return self._version_delete(action.args)
            if kind == "version_rollback":
                return self._version_rollback(action.args)
            if kind == "skill_search":
                return self._skill_search(action.args)
            if kind == "skill_write":
                return self._skill_write(action.args)
            if kind == "skill_get":
                return self._skill_get(action.args)
            if kind == "lore_search":
                return self._lore_search(action.args)
            if kind == "lore_get":
                return self._lore_get(action.args)
            if kind == "lore_set":
                return self._lore_set(action.args)
            if kind == "lore_note_upsert":
                return self._lore_note_upsert(action.args)
            if kind == "lore_buffer_list":
                return self._lore_buffer_list(action.args)
            if kind == "lore_vector_reindex":
                return self._lore_vector_reindex(action.args)
            if kind == "diff_confirm":
                return self._diff_confirm(action.args)
            if kind == "diff_reject":
                return self._diff_reject(action.args)
            if kind == "diff_list":
                return self._diff_list(action.args)
            if kind == "shell_verify":
                return self._shell_verify(action.args)
            if kind == "py_write":
                return self._py_write(action.args)
            if kind == "py_pip":
                return self._py_pip(action.args)
            if kind == "py_run":
                return self._py_run(action.args)
            if kind == "shovel_search":
                return self._shovel_search(action.args)
            return ExecResult(ok=False, message=f"unknown background kind: {kind!r}")
        except ShovelOpsError as exc:
            return ExecResult(ok=False, message=str(exc))
        except PySessionError as exc:
            return ExecResult(ok=False, message=str(exc))
        except FileNotFoundError as exc:
            return ExecResult(
                ok=False,
                message=format_file_missing_message(
                    exc, work_root=self.fs.work_root
                ),
            )
        except WorkspacePathError as exc:
            msg = str(exc)
            if "escapes" in msg.lower():
                return ExecResult(
                    ok=False,
                    message=(
                        f"{msg}；"
                        "此为路径闸拒绝（不在 work_root / FAWAI_READ_ROOTS）。"
                        "改用只读根内绝对路径 fs_read，或仅 work_root 相对路径；"
                        "禁止 shell cat/cp 绕过"
                    ),
                )
            return ExecResult(ok=False, message=msg)
        except (SkillValidationError, GitError, ValueError, OSError) as exc:
            return ExecResult(ok=False, message=str(exc))

    def _fs_glob(self, args: dict[str, Any]) -> ExecResult:
        pattern = str(args.get("pattern") or "*")
        limit = int(args.get("head_limit") or 100)
        # path 正式支持；兼容 root/dir；不得静默忽略后只扫 work_root
        search_path = str(
            args.get("path") or args.get("root") or args.get("dir") or "."
        ).strip() or "."
        paths = self.fs.glob(pattern, path=search_path, head_limit=limit)
        return ExecResult(
            ok=True,
            message=f"glob {len(paths)}",
            data={"paths": paths, "path": search_path, "pattern": pattern},
        )

    def _fs_grep(self, args: dict[str, Any]) -> ExecResult:
        # pattern 为主名；兼容模型误写的 query
        pattern = str(args.get("pattern") or args.get("query") or "")
        if not pattern:
            return ExecResult(ok=False, message="fs_grep requires pattern")
        # 体量靠工具参数限制（不做观察侧压缩/宽检索降质）；模型可加大 head_limit 翻页
        # 不限制 pattern 最短长度：短词（如「取消」「ok」）应可搜；噪声靠 head_limit/path/glob 收窄
        try:
            head_limit = int(args.get("head_limit") or 40)
        except (TypeError, ValueError):
            head_limit = 40
        head_limit = max(1, min(head_limit, 100))
        try:
            context_before = int(args.get("context_before") or args.get("B") or 0)
        except (TypeError, ValueError):
            context_before = 0
        try:
            context_after = int(args.get("context_after") or args.get("A") or 0)
        except (TypeError, ValueError):
            context_after = 0
        context_before = max(0, min(context_before, 5))
        context_after = max(0, min(context_after, 5))
        hits = self.fs.grep(
            pattern,
            path=str(args.get("path") or "."),
            glob=str(args.get("glob") or "*"),
            case_insensitive=bool(args.get("case_insensitive", False)),
            head_limit=head_limit,
            context_before=context_before,
            context_after=context_after,
        )
        return ExecResult(
            ok=True,
            message=f"grep {len(hits)}",
            data={"hits": hits, "count": len(hits)},
        )

    def _fs_read(self, args: dict[str, Any]) -> ExecResult:
        path = str(args.get("path") or "")
        if not path:
            return ExecResult(ok=False, message="fs_read requires path")
        # 正式名 offset/limit；兼容模型误写的 start_line/end_line/line
        offset, limit = resolve_read_range(args, default_limit=200)
        data = self.fs.read(path, offset=offset, limit=limit)
        return ExecResult(ok=True, message=f"read {path}", data=data)

    def _fs_read_many(self, args: dict[str, Any]) -> ExecResult:
        paths = args.get("paths")
        if not isinstance(paths, list) or not paths:
            return ExecResult(ok=False, message="fs_read_many requires paths: list[str]")
        invented = [
            str(p)
            for p in paths
            if Path(str(p)).name.startswith("grep_result_") and str(p).endswith(".txt")
        ]
        if invented:
            return ExecResult(
                ok=False,
                message=(
                    "fs_read_many 的 paths 须是工作区真实路径（来自 fs_glob/fs_grep hits）；"
                    f"禁止臆造 {invented[0]} 这类文件。请直接根据工具结果观察里的 path:line 定向 fs_read"
                ),
                data={"invented_paths": invented},
            )
        offset, limit = resolve_read_range(args, default_limit=120)
        data = self.fs.read_many(
            [str(p) for p in paths],
            offset=offset,
            limit=limit,
            max_files=int(args.get("max_files") or 8),
        )
        return ExecResult(ok=True, message=f"read_many {data.get('count', 0)}", data=data)

    def _fs_write(self, args: dict[str, Any]) -> ExecResult:
        path = str(args.get("path") or "")
        if not path:
            return ExecResult(ok=False, message="fs_write requires path")
        if "content" not in args:
            return ExecResult(ok=False, message="fs_write requires content")
        content = str(args.get("content"))
        force = bool(args.get("force_full_write", False))
        abs_path = self.fs.work_root / path
        if abs_path.is_file() and not force:
            lines = self.fs.line_count(path)
            if lines >= self.full_write_line_limit:
                return ExecResult(
                    ok=False,
                    message=(
                        f"refuse full fs_write on existing {path} ({lines} lines ≥ "
                        f"{self.full_write_line_limit}); use fs_patch or force_full_write=true"
                    ),
                )
        old_text = ""
        if abs_path.is_file():
            old_text = abs_path.read_text(encoding="utf-8", errors="replace")
        return self._apply_or_pend(
            kind="fs_write",
            path=path,
            old_text=old_text,
            new_text=content,
            apply_fn=lambda: self._do_write(path, content, args),
            args=args,
        )

    def _do_write(self, path: str, content: str, args: dict[str, Any]) -> dict[str, Any]:
        auto = self.auto_version if args.get("auto_version") is None else bool(args.get("auto_version"))
        version_id = None
        abs_path = self.fs.work_root / path
        if auto and abs_path.is_file():
            version_id = self.versions.snapshot_before_write([path], message=f"auto:before-write:{path}")
        result = self.fs.write(path, content, create_dirs=bool(args.get("create_dirs", True)))
        data = dict(result) if isinstance(result, dict) else {"path": path}
        if version_id:
            data["version_id"] = version_id
        return data

    def _fs_replace(self, args: dict[str, Any]) -> ExecResult:
        path = str(args.get("path") or "")
        old = args.get("old")
        new = args.get("new")
        if not path or old is None or new is None:
            return ExecResult(ok=False, message="fs_replace requires path, old, new")
        abs_path = self.fs.work_root / path
        if not abs_path.is_file():
            return ExecResult(
                ok=False,
                message=(
                    f"fs_replace target missing: {path}；"
                    "请先 fs_glob/fs_read 确认路径存在，禁止臆造文件名"
                ),
            )
        quality = patch_anchor_quality_error(str(old), str(new), label="fs_replace")
        if quality:
            return ExecResult(ok=False, message=quality)
        old_text = abs_path.read_text(encoding="utf-8", errors="replace")
        preview_new = old_text.replace(str(old), str(new), int(args.get("count") or 1))

        def apply() -> dict[str, Any]:
            auto = self.auto_version if args.get("auto_version") is None else bool(args.get("auto_version"))
            version_id = None
            if auto:
                version_id = self.versions.snapshot_before_write(
                    [path], message=f"auto:before-replace:{path}"
                )
            result = self.fs.replace(path, str(old), str(new), count=int(args.get("count") or 1))
            data = {k: v for k, v in dict(result).items() if k not in {"old_text", "new_text"}}
            if version_id:
                data["version_id"] = version_id
            data["diff"] = unified_diff(path, result.get("old_text", ""), result.get("new_text", ""))
            return data

        return self._apply_or_pend(
            kind="fs_replace",
            path=path,
            old_text=old_text,
            new_text=preview_new,
            apply_fn=apply,
            args=args,
        )

    def _fs_patch(self, args: dict[str, Any]) -> ExecResult:
        path = str(args.get("path") or "")
        if not path:
            return ExecResult(ok=False, message="fs_patch requires path")
        abs_path = self.fs.work_root / path
        if not abs_path.is_file():
            return ExecResult(
                ok=False,
                message=(
                    f"fs_patch target missing: {path}；"
                    "请先 fs_glob/fs_read 确认路径存在，禁止臆造文件名"
                ),
            )
        try:
            resolved = resolve_patch_ops(
                hunks=args.get("hunks"),
                old=args.get("old"),
                new=args.get("new"),
            )
        except ValueError as exc:
            return ExecResult(ok=False, message=str(exc))
        hunks_list = resolved.get("hunks")
        ops = resolved["ops"]
        # 统一走 hunks 列表，避免 string/旧分支分叉
        patch_hunks = hunks_list if isinstance(hunks_list, list) else ops
        preview = self.fs.patch(
            path,
            patch_hunks,
            require_unique=bool(args.get("require_unique", True)),
            write=False,
        )
        old_text = str(preview.get("old_text") or "")
        new_text = str(preview.get("new_text") or "")

        def apply() -> dict[str, Any]:
            auto = self.auto_version if args.get("auto_version") is None else bool(args.get("auto_version"))
            version_id = None
            if auto:
                version_id = self.versions.snapshot_before_write(
                    [path], message=f"auto:before-patch:{path}"
                )
            result = self.fs.patch(
                path,
                patch_hunks,
                require_unique=bool(args.get("require_unique", True)),
                write=True,
            )
            data = {k: v for k, v in dict(result).items() if k not in {"old_text", "new_text"}}
            if version_id:
                data["version_id"] = version_id
            return data

        return self._apply_or_pend(
            kind="fs_patch",
            path=path,
            old_text=old_text,
            new_text=new_text,
            apply_fn=apply,
            args=args,
        )

    def _apply_or_pend(
        self,
        *,
        kind: str,
        path: str,
        old_text: str,
        new_text: str,
        apply_fn: Any,
        args: dict[str, Any],
    ) -> ExecResult:
        mode = str(args.get("diff_mode") or self.diff_mode).lower()
        if mode == "confirm":
            return self._pend(kind=kind, path=path, old_text=old_text, new_text=new_text, args=args)
        data = apply_fn()
        diff = data.get("diff") or unified_diff(path, old_text, new_text)
        data["diff"] = diff
        data["diff_mode"] = "auto"
        data.setdefault("path", path)
        # 弱写入标注：死 CSS 变量等无目标副作用（闭环 success 闸会拦）
        try:
            from fawai_avatar.brain.write_effect import assess_patch_effect

            assessment = assess_patch_effect(
                path,
                old_text,
                new_text,
                goal=str(args.get("goal") or ""),
            )
            # html/css 等 path 无 goal 也会按后缀评估；闭环可再注入文案
            if assessment.weak:
                data["weak_write"] = True
                data["weak_write_reasons"] = list(assessment.reasons)
        except Exception:  # noqa: BLE001 — 标注失败不阻断写入
            pass
        # 稳定成功标记 · applied：供 expect/contains 与规划对齐；禁止臆造「patch applied」口令
        return ExecResult(ok=True, message=f"{kind} {path} · applied", data=data)

    def _pend(
        self,
        *,
        kind: str,
        path: str,
        old_text: str,
        new_text: str,
        args: dict[str, Any],
    ) -> ExecResult:
        task_id = str(args.get("task_id") or self.task_id or "") or None
        diff = unified_diff(path, old_text, new_text)
        rec = self.pending.put(
            task_id=task_id,
            path=path,
            old_text=old_text,
            new_text=new_text,
            kind=kind,
            diff=diff,
        )
        return ExecResult(
            ok=True,
            message=f"pending {kind} {path} (confirm required)",
            data={
                "pending": True,
                "pending_id": rec["id"],
                "path": path,
                "diff": diff,
                "diff_mode": "confirm",
                "kind": kind,
            },
        )

    def _diff_confirm(self, args: dict[str, Any]) -> ExecResult:
        pending_id = str(args.get("pending_id") or args.get("id") or "")
        task_id = str(args.get("task_id") or self.task_id or "")
        rec = None
        if pending_id:
            rec = self.pending.get(pending_id)
        elif task_id:
            items = self.pending.list_for_task(task_id)
            rec = items[0] if items else None
        if rec is None or rec.get("status") != "pending":
            return ExecResult(ok=False, message="no pending diff to confirm")
        path = str(rec["path"])
        auto = self.auto_version
        version_id = None
        if auto and (self.fs.work_root / path).is_file():
            version_id = self.versions.snapshot_before_write(
                [path], message=f"auto:before-confirm:{path}"
            )
        result = self.fs.write(path, str(rec["new_text"]), create_dirs=True)
        self.pending.mark(str(rec["id"]), "confirmed")
        data = dict(result)
        data["pending_id"] = rec["id"]
        data["diff"] = rec.get("diff")
        if version_id:
            data["version_id"] = version_id
        return ExecResult(ok=True, message=f"confirmed {path}", data=data)

    def _diff_reject(self, args: dict[str, Any]) -> ExecResult:
        pending_id = str(args.get("pending_id") or args.get("id") or "")
        task_id = str(args.get("task_id") or self.task_id or "")
        rec = None
        if pending_id:
            rec = self.pending.get(pending_id)
        elif task_id:
            items = self.pending.list_for_task(task_id)
            rec = items[0] if items else None
        if rec is None or rec.get("status") != "pending":
            return ExecResult(ok=False, message="no pending diff to reject")
        self.pending.mark(str(rec["id"]), "rejected")
        return ExecResult(
            ok=True,
            message=f"rejected {rec.get('path')}",
            data={"pending_id": rec["id"], "path": rec.get("path")},
        )

    def _diff_list(self, args: dict[str, Any]) -> ExecResult:
        task_id = str(args.get("task_id") or self.task_id or "")
        if not task_id:
            return ExecResult(ok=False, message="diff_list requires task_id")
        items = self.pending.list_for_task(task_id)
        slim = [
            {"id": i["id"], "path": i["path"], "kind": i["kind"], "diff": (i.get("diff") or "")[:2000]}
            for i in items
        ]
        return ExecResult(ok=True, message=f"pending {len(slim)}", data={"pending": slim})

    def _code_symbols(self, args: dict[str, Any]) -> ExecResult:
        hits = self.code.symbols(
            str(args.get("path") or "."),
            query=str(args.get("query") or args.get("q") or ""),
            kind=str(args.get("kind") or ""),
            head_limit=int(args.get("head_limit") or 100),
        )
        return ExecResult(ok=True, message=f"code_symbols {len(hits)}", data={"symbols": hits})

    def _code_map(self, args: dict[str, Any]) -> ExecResult:
        data = self.code.map(
            str(args.get("path") or "."),
            query=str(args.get("query") or args.get("q") or ""),
            kind=str(args.get("kind") or ""),
            head_limit=int(args.get("head_limit") or 80),
        )
        return ExecResult(ok=True, message=f"code_map {data.get('count', 0)}", data=data)

    def _code_index_refresh(self, args: dict[str, Any]) -> ExecResult:
        data = self.code.refresh(str(args.get("path") or "."))
        return ExecResult(
            ok=True,
            message=f"code_index_refresh updated={data.get('updated', 0)}",
            data=data,
        )

    def _code_check(self, args: dict[str, Any]) -> ExecResult:
        coerced = coerce_code_check_args(args)
        paths = coerced.get("paths")
        path_list = [str(p) for p in paths] if isinstance(paths, list) else None
        data = run_code_check(
            self.fs.work_root,
            path=str(coerced.get("path") or ""),
            paths=path_list,
            command=str(coerced["command"]) if coerced.get("command") else None,
            timeout_sec=float(coerced.get("timeout_sec") or args.get("timeout_sec") or 60),
        )
        ok = bool(data.get("ok"))
        return ExecResult(ok=ok, message="code_check ok" if ok else "code_check failed", data=data)

    def _explore_search(self, args: dict[str, Any]) -> ExecResult:
        coerced = coerce_explore_search_args(args)
        qlist = [str(q) for q in (coerced.get("queries") or []) if str(q).strip()]
        try:
            head_limit = int(coerced.get("head_limit") or 20)
        except (TypeError, ValueError):
            head_limit = 20
        head_limit = max(1, min(head_limit, 100))
        try:
            context_before = int(
                coerced.get("context_before") or coerced.get("B") or 0
            )
        except (TypeError, ValueError):
            context_before = 0
        try:
            context_after = int(coerced.get("context_after") or coerced.get("A") or 0)
        except (TypeError, ValueError):
            context_after = 0
        context_before = max(0, min(context_before, 5))
        context_after = max(0, min(context_after, 5))
        try:
            max_workers = int(coerced.get("max_workers") or 4)
        except (TypeError, ValueError):
            max_workers = 4
        data = explore_search(
            self.fs.work_root,
            queries=qlist or None,
            path=str(coerced.get("path") or "."),
            glob=str(coerced.get("glob") or "*"),
            symbols_query=str(coerced.get("symbols_query") or ""),
            include_symbols=bool(coerced.get("include_symbols")),
            max_workers=max_workers,
            head_limit=head_limit,
            context_before=context_before,
            context_after=context_after,
            code_index=self.code,
        )
        return ExecResult(ok=True, message="explore_search", data=data)

    def _git_status(self, args: dict[str, Any]) -> ExecResult:
        data = git_status(self.fs.work_root, porcelain=bool(args.get("porcelain", True)))
        return ExecResult(ok=True, message="git_status", data=data)

    def _git_diff(self, args: dict[str, Any]) -> ExecResult:
        data = git_diff(
            self.fs.work_root,
            path=str(args.get("path") or ""),
            staged=bool(args.get("staged", False)),
            head_limit=int(args.get("head_limit") or 8000),
        )
        return ExecResult(ok=True, message="git_diff", data=data)

    def _git_commit(self, args: dict[str, Any]) -> ExecResult:
        message = str(args.get("message") or "")
        paths = args.get("paths")
        path_list = [str(p) for p in paths] if isinstance(paths, list) else None
        data = git_commit(
            self.fs.work_root,
            message=message,
            paths=path_list,
            add_all=bool(args.get("add_all", False)),
        )
        return ExecResult(ok=True, message=f"git_commit {data.get('commit')}", data=data)

    def _shell_verify(self, args: dict[str, Any]) -> ExecResult:
        """白名单校验命令（编码跑测）；不受 FAWAI_DRY_RUN。"""
        cmd = str(args.get("cmd") or args.get("command") or "").strip()
        if not cmd:
            return ExecResult(ok=False, message="shell_verify requires cmd")
        allowed_prefixes = (
            "python",
            "python3",
            "py_compile",
            ".venv/bin/python",
            "pytest",
            "ruff",
            "mypy",
            "git status",
            "git diff",
            "git log",
        )
        low = cmd.lower()
        if not any(low.startswith(p) or f" {p}" in f" {low}" for p in allowed_prefixes):
            # also allow exact python -m py_compile / -m pytest
            if not any(token in low for token in ("py_compile", "pytest", "ruff", "mypy", "unittest")):
                return ExecResult(
                    ok=False,
                    message="shell_verify command not in whitelist (python/pytest/ruff/mypy/git read)",
                )
        data = run_code_check(
            self.fs.work_root,
            command=cmd,
            timeout_sec=float(args.get("timeout_sec") or 120),
        )
        ok = bool(data.get("ok"))
        return ExecResult(ok=ok, message="shell_verify ok" if ok else "shell_verify failed", data=data)

    def _resolve_task_id(self, args: dict[str, Any]) -> str:
        tid = str(args.get("task_id") or self.task_id or "").strip()
        if not tid:
            raise PySessionError("py_* requires task_id")
        return tid

    def _py_write(self, args: dict[str, Any]) -> ExecResult:
        path = str(args.get("path") or "").strip()
        if not path:
            return ExecResult(ok=False, message="py_write requires path")
        if "content" not in args:
            return ExecResult(ok=False, message="py_write requires content")
        root = session_root(self.data_dir, self._resolve_task_id(args))
        data = py_write_file(root, path=path, content=str(args.get("content")))
        return ExecResult(ok=True, message=f"py_write {data.get('path')}", data=data)

    def _py_pip(self, args: dict[str, Any]) -> ExecResult:
        packages = args.get("packages")
        if packages is None:
            packages = args.get("package") or args.get("pkgs")
        data = pip_install(
            packages=packages,
            work_root=self.fs.work_root,
            timeout_sec=float(args.get("timeout_sec") or 300),
        )
        ok = bool(data.get("ok"))
        pkgs = data.get("packages") or []
        label = " ".join(str(p) for p in pkgs[:4])
        if len(pkgs) > 4:
            label += "…"
        return ExecResult(
            ok=ok,
            message=f"py_pip ok ({label})" if ok else f"py_pip failed ({label})",
            data=data,
        )

    def _py_run(self, args: dict[str, Any]) -> ExecResult:
        root = session_root(self.data_dir, self._resolve_task_id(args))
        raw_args = args.get("args")
        argv: list[str] = []
        if isinstance(raw_args, list):
            argv = [str(x) for x in raw_args]
        elif isinstance(raw_args, str) and raw_args.strip():
            argv = raw_args.split()
        data = run_script(
            root,
            work_root=self.fs.work_root,
            script=str(args.get("script") or args.get("path") or "").strip() or None,
            code=args.get("code") if isinstance(args.get("code"), str) else None,
            module=str(args.get("module") or "").strip() or None,
            args=argv,
            timeout_sec=float(args.get("timeout_sec") or 60),
            result_path=str(args.get("result_path") or "").strip() or None,
        )
        ok = bool(data.get("ok"))
        entry = data.get("entry") or ""
        rc = data.get("returncode")
        return ExecResult(
            ok=ok,
            message=f"py_run ok {entry}" if ok else f"py_run failed {entry} exit={rc}",
            data=data,
        )

    def _shovel_search(self, args: dict[str, Any]) -> ExecResult:
        query = str(args.get("query") or args.get("q") or args.get("pattern") or "")
        limit = int(args.get("limit") or 20)
        data = search_shovels(query, work_root=self.fs.work_root, limit=limit)
        n = int(data.get("count") or 0)
        return ExecResult(
            ok=True,
            message=f"shovel_search {n} hits",
            data=data,
        )

    def _version_snapshot(self, args: dict[str, Any]) -> ExecResult:
        paths = args.get("paths")
        if not isinstance(paths, list) or not paths:
            return ExecResult(ok=False, message="version_snapshot requires paths: list[str]")
        vid = self.versions.snapshot(
            [str(p) for p in paths],
            message=str(args.get("message") or ""),
            recursive_dirs=bool(args.get("recursive_dirs", True)),
        )
        return ExecResult(ok=True, message=f"snapshot {vid}", data={"version_id": vid})

    def _version_list(self, args: dict[str, Any]) -> ExecResult:
        items = self.versions.list_versions(
            path_prefix=args.get("path_prefix"),
            limit=int(args.get("limit") or 50),
        )
        return ExecResult(ok=True, message=f"list {len(items)}", data={"versions": items})

    def _version_get(self, args: dict[str, Any]) -> ExecResult:
        vid = str(args.get("version_id") or "")
        if not vid:
            return ExecResult(ok=False, message="version_get requires version_id")
        info = self.versions.get(vid)
        return ExecResult(ok=bool(info.get("exists")), message=vid, data=info)

    def _version_delete(self, args: dict[str, Any]) -> ExecResult:
        vid = str(args.get("version_id") or "")
        if not vid:
            return ExecResult(ok=False, message="version_delete requires version_id")
        ok = self.versions.delete(vid)
        return ExecResult(ok=ok, message=f"delete {vid}", data={"version_id": vid, "deleted": ok})

    def _version_rollback(self, args: dict[str, Any]) -> ExecResult:
        vid = str(args.get("version_id") or "")
        if not vid:
            return ExecResult(ok=False, message="version_rollback requires version_id")
        paths = args.get("paths")
        path_list = [str(p) for p in paths] if isinstance(paths, list) else None
        result = self.versions.rollback(vid, paths=path_list)
        return ExecResult(
            ok=True,
            message=f"rollback {vid}",
            data=result if isinstance(result, dict) else {"result": result},
        )

    def _skill_search(self, args: dict[str, Any]) -> ExecResult:
        query = str(args.get("query") or args.get("q") or "")
        limit = int(args.get("limit") or 10)
        hits = self.skills.search(query, limit=limit)
        return ExecResult(
            ok=True,
            message=f"skill_search {len(hits)}",
            data={"skills": hits, "query": query},
        )

    def _skill_write(self, args: dict[str, Any]) -> ExecResult:
        payload = args.get("skill") if isinstance(args.get("skill"), dict) else args
        skill = self.skills.write(dict(payload))
        return ExecResult(
            ok=True,
            message=f"skill_write {skill['id']}",
            data={"skill": skill},
        )

    def _skill_get(self, args: dict[str, Any]) -> ExecResult:
        sid = str(args.get("id") or args.get("skill_id") or "")
        if not sid:
            return ExecResult(ok=False, message="skill_get requires id")
        skill = self.skills.get(sid)
        if skill is None:
            return ExecResult(ok=False, message=f"skill not found: {sid}")
        return ExecResult(ok=True, message=f"skill_get {sid}", data={"skill": skill})

    def _lore_store(self):
        from fawai_avatar.lore import open_lore_store

        return open_lore_store()

    def _lore_search(self, args: dict[str, Any]) -> ExecResult:
        from fawai_avatar.config import lore_vector_enabled
        from fawai_lore import retrieve

        query = str(args.get("query") or args.get("q") or "")
        limit = int(args.get("limit") or 10)
        use_vector = lore_vector_enabled()
        embed_fn = None
        if use_vector:
            try:
                from fawai_avatar.lore_embed import make_embed_fn

                embed_fn = make_embed_fn()
            except Exception:
                use_vector = False
        hits = [
            h.to_dict()
            for h in retrieve(
                self._lore_store(),
                query,
                limit=limit,
                embed_fn=embed_fn,
                use_vector=use_vector,
            )
        ]
        return ExecResult(
            ok=True,
            message=f"lore_search {len(hits)}",
            data={"hits": hits, "query": query, "vector": use_vector},
        )

    def _lore_get(self, args: dict[str, Any]) -> ExecResult:
        from fawai_lore import KvValidationError

        key = str(args.get("key") or args.get("id") or "").strip()
        if not key:
            return ExecResult(ok=False, message="lore_get requires key or id")
        lore = self._lore_store()
        entry = None
        try:
            entry = lore.kv.get(key)
        except KvValidationError:
            entry = None
        if entry is not None:
            return ExecResult(ok=True, message=f"lore_get kv {key}", data={"kv": entry.to_dict()})
        note = lore.notes.get(key)
        if note is None:
            return ExecResult(ok=False, message=f"lore not found: {key}")
        return ExecResult(ok=True, message=f"lore_get note {key}", data={"note": note.to_dict()})

    def _lore_set(self, args: dict[str, Any]) -> ExecResult:
        key = str(args.get("key") or "").strip()
        if not key:
            return ExecResult(ok=False, message="lore_set requires key")
        if "value" not in args:
            return ExecResult(ok=False, message="lore_set requires value")
        aliases = args.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = [str(aliases)]
        entry = self._lore_store().kv.set(
            key,
            args.get("value"),
            source=str(args.get("source") or "lore_set")[:200],
            aliases=[str(a) for a in aliases],
        )
        return ExecResult(ok=True, message=f"lore_set {entry.key}", data={"kv": entry.to_dict()})

    def _lore_note_upsert(self, args: dict[str, Any]) -> ExecResult:
        payload = args.get("note") if isinstance(args.get("note"), dict) else args
        note = self._lore_store().notes.upsert(dict(payload))
        try:
            from fawai_avatar.lore_embed import index_note_text

            index_note_text(note.id, f"{note.title}\n{note.body}")
        except Exception:
            pass
        return ExecResult(
            ok=True,
            message=f"lore_note_upsert {note.id}",
            data={"note": note.to_dict()},
        )

    def _lore_vector_reindex(self, args: dict[str, Any]) -> ExecResult:
        from fawai_avatar.lore_embed import reindex_all_notes

        result = reindex_all_notes()
        ok = bool(result.get("ok"))
        return ExecResult(
            ok=ok,
            message="lore_vector_reindex"
            + (f" {result.get('indexed')}" if ok else f" {result.get('error')}"),
            data=result,
        )

    def _lore_buffer_list(self, args: dict[str, Any]) -> ExecResult:
        from fawai_lore import list_buffer

        from fawai_avatar.config import resolve_lore_dir

        limit = int(args.get("limit") or 30)
        rows = list_buffer(resolve_lore_dir(), limit=limit)
        return ExecResult(
            ok=True,
            message=f"lore_buffer_list {len(rows)}",
            data={"rows": rows},
        )
