# term:观察  doc:doc/功能/观察/说明.md
"""窄场景回证：文件态 / 观察截图是否可用（不做 OCR/VLM 定位）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fawai_avatar.types import Action, ExecResult, Observation
from fawai_avatar.workspace.fs import WorkspaceFs
from fawai_avatar.workspace.paths import WorkspacePathError


def verify_observation_usable(obs: Observation) -> ExecResult:
    """截图回证：有路径、文件存在、宽高为正。"""
    if not obs.image_path:
        return ExecResult(ok=False, message="observation missing image_path")
    path = Path(obs.image_path)
    if not path.is_file():
        return ExecResult(ok=False, message=f"screenshot missing: {path}")
    size = path.stat().st_size
    if size < 32:
        return ExecResult(ok=False, message=f"screenshot too small: {size} bytes")
    if obs.width is not None and obs.width <= 0:
        return ExecResult(ok=False, message=f"invalid width: {obs.width}")
    if obs.height is not None and obs.height <= 0:
        return ExecResult(ok=False, message=f"invalid height: {obs.height}")
    return ExecResult(
        ok=True,
        message="observation usable",
        data={
            "image_path": str(path),
            "bytes": size,
            "width": obs.width,
            "height": obs.height,
        },
    )


def verify_fs_write(
    work_root: str | Path,
    action: Action,
    *,
    expect_substr: str | None = None,
) -> ExecResult:
    """文件态回证：fs_write 后路径存在且内容匹配。"""
    path = str((action.args or {}).get("path") or "")
    if not path:
        return ExecResult(ok=False, message="verify_fs_write: missing path")
    content = (action.args or {}).get("content")
    needle = expect_substr if expect_substr is not None else (
        str(content) if content is not None else None
    )
    try:
        fs = WorkspaceFs(work_root)
        raw = (fs.work_root / path).read_text(encoding="utf-8", errors="replace")
    except (WorkspacePathError, FileNotFoundError, ValueError, OSError) as exc:
        return ExecResult(ok=False, message=f"verify read failed: {exc}")
    if needle is not None and needle not in raw:
        return ExecResult(
            ok=False,
            message=f"content mismatch: expect {needle!r}",
            data={"path": path, "got": raw[:200]},
        )
    return ExecResult(ok=True, message=f"verified {path}", data={"path": path})


def verify_actions_file_state(
    work_root: str | Path,
    actions: list[Action],
    results: list[ExecResult],
) -> list[ExecResult]:
    """对已成功的写类动作做文件态回证。"""
    out: list[ExecResult] = []
    for i, result in enumerate(results):
        if not result.ok or i >= len(actions):
            continue
        action = actions[i]
        kind = (action.kind or "").strip().lower()
        if kind == "fs_write":
            if isinstance(result.data, dict) and result.data.get("pending"):
                continue
            out.append(verify_fs_write(work_root, action))
        elif kind in {"fs_replace", "fs_patch"}:
            if isinstance(result.data, dict) and result.data.get("pending"):
                continue
            path = str((action.args or {}).get("path") or "")
            new = (action.args or {}).get("new")
            if kind == "fs_patch" and isinstance((action.args or {}).get("hunks"), list):
                hunks = action.args.get("hunks") or []
                if hunks and isinstance(hunks[-1], dict) and hunks[-1].get("new") is not None:
                    new = hunks[-1].get("new")
            if path and new is not None:
                out.append(
                    verify_fs_write(
                        work_root,
                        Action(kind="fs_write", args={"path": path, "content": str(new)}),
                        expect_substr=str(new),
                    )
                )
            elif path and isinstance(result.data, dict) and result.data.get("path"):
                # 仅确认文件仍存在
                out.append(
                    verify_fs_write(
                        work_root,
                        Action(kind="fs_write", args={"path": path, "content": ""}),
                        expect_substr="",
                    )
                )
    return out


def observe_act_verify_file(
    *,
    work_root: str | Path,
    observe_dir: str | Path,
    path: str,
    content: str,
    capture: bool = True,
) -> dict[str, Any]:
    """窄场景：观察 → fs_write → 文件回证（可选截屏）。"""
    from fawai_avatar.control.background import BackgroundTools
    from fawai_avatar.observe.screen import observe
    from fawai_avatar.types import Action

    pre: Observation | None = None
    pre_v: ExecResult | None = None
    if capture:
        try:
            pre = observe(observe_dir)
            pre_v = verify_observation_usable(pre)
        except Exception as exc:  # noqa: BLE001
            pre_v = ExecResult(ok=False, message=str(exc))

    tools = BackgroundTools(work_root=work_root, data_dir=Path(observe_dir).parent, auto_version=False)
    write = tools.execute(Action(kind="fs_write", args={"path": path, "content": content}))
    file_v = (
        verify_fs_write(work_root, Action(kind="fs_write", args={"path": path, "content": content}))
        if write.ok
        else ExecResult(ok=False, message="skip verify: write failed")
    )

    post: Observation | None = None
    post_v: ExecResult | None = None
    if capture and pre_v is not None and pre_v.ok:
        try:
            post = observe(observe_dir)
            post_v = verify_observation_usable(post)
        except Exception as exc:  # noqa: BLE001
            post_v = ExecResult(ok=False, message=str(exc))

    ok = write.ok and file_v.ok and (pre_v is None or pre_v.ok or not capture)
    # 截屏失败不阻断文件回证主链，但标记 capture_ok
    capture_ok = (pre_v.ok if pre_v else False) and (post_v.ok if post_v else True)
    return {
        "ok": ok and file_v.ok,
        "capture_ok": capture_ok,
        "write": write.to_dict(),
        "file_verify": file_v.to_dict(),
        "pre_observe": pre.to_dict() if pre else None,
        "pre_verify": pre_v.to_dict() if pre_v else None,
        "post_observe": post.to_dict() if post else None,
        "post_verify": post_v.to_dict() if post_v else None,
    }
