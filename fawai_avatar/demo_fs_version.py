# term:版本管理  doc:doc/功能/版本管理/说明.md
"""后台文件 + 版本管理冒烟（无 GUI）。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.types import Action


def main(work_root: str, data_dir: str) -> None:
    tools = BackgroundTools(work_root=work_root, data_dir=data_dir, auto_version=True)

    r = tools.execute(Action(kind="fs_write", args={"path": "notes/a.txt", "content": "v1\n"}))
    assert r.ok, r.message
    print("write1", r.data)

    r = tools.execute(
        Action(kind="fs_write", args={"path": "notes/a.txt", "content": "v2-changed\n"})
    )
    assert r.ok and r.data.get("version_id"), r
    vid = r.data["version_id"]
    print("write2 auto snapshot", vid)

    r = tools.execute(Action(kind="fs_read", args={"path": "notes/a.txt"}))
    assert "v2-changed" in r.data["lines"][0]["text"]

    # 多行样例：正式 offset/limit 与 start_line/end_line 别名须指向同一区间
    Path(work_root, "notes/lines.txt").write_text(
        "\n".join(f"L{i}" for i in range(1, 21)) + "\n",
        encoding="utf-8",
    )
    r = tools.execute(
        Action(kind="fs_read", args={"path": "notes/lines.txt", "offset": 10, "limit": 3})
    )
    assert r.ok and [row["text"] for row in r.data["lines"]] == ["L10", "L11", "L12"], r.data
    r = tools.execute(
        Action(
            kind="fs_read",
            args={"path": "notes/lines.txt", "start_line": 10, "end_line": 12},
        )
    )
    assert r.ok and [row["n"] for row in r.data["lines"]] == [10, 11, 12], r.data
    assert [row["text"] for row in r.data["lines"]] == ["L10", "L11", "L12"], r.data
    # 0-based 习惯：offset=0 须收成文件头，不得 FAIL 空转
    r = tools.execute(
        Action(kind="fs_read", args={"path": "notes/lines.txt", "offset": 0, "limit": 2})
    )
    assert r.ok and [row["text"] for row in r.data["lines"]] == ["L1", "L2"], r.message
    r = tools.execute(
        Action(kind="fs_read", args={"path": "notes/lines.txt", "start_line": 0, "limit": 1})
    )
    assert r.ok and [row["n"] for row in r.data["lines"]] == [1], r.message

    r = tools.execute(Action(kind="fs_glob", args={"pattern": "**/*.txt", "path": "notes"}))
    print("glob", r.data)
    assert "notes/a.txt" in (r.data or {}).get("paths", [])

    # 前缀 + ** 与花括号：不得因 Path.match 坑返回空
    r = tools.execute(Action(kind="fs_glob", args={"pattern": "notes/**/*.txt"}))
    assert "notes/a.txt" in (r.data or {}).get("paths", []), r.data
    r = tools.execute(Action(kind="fs_glob", args={"pattern": "notes/**/*.{txt,md}"}))
    assert "notes/a.txt" in (r.data or {}).get("paths", []), r.data

    # 整 work_root + 仅扩展名 glob → 拒
    r = tools.execute(
        Action(kind="fs_glob", args={"pattern": "**/*.{yaml,yml,json}"})
    )
    assert not r.ok and "检索根过宽" in (r.message or ""), r.message

    # 整 work_root + 无法定目录 → 拒（禁止回退整仓扫）
    r = tools.execute(
        Action(
            kind="fs_grep",
            args={"pattern": "zzz_no_such_token_xyz", "use_index": False},
        )
    )
    assert not r.ok and "检索根过宽" in (r.message or ""), r.message

    # 索引/宽根路径：按顶层相关目录分批 grep，不扫整仓
    insect = Path(work_root, "insect")
    insect.mkdir(exist_ok=True)
    (insect / "train.yaml").write_text("max_epochs: 12\n", encoding="utf-8")
    r = tools.execute(
        Action(
            kind="fs_grep",
            args={"pattern": "insect|max_epochs", "use_index": False},
        )
    )
    assert r.ok and (r.data or {}).get("via") == "batched_dirs", r
    assert (r.data or {}).get("hits"), r.data
    assert "insect" in ((r.data or {}).get("batched_dirs") or [])

    # 短 pattern（2 字）须可搜；勿再加最小长度闸
    r = tools.execute(Action(kind="fs_grep", args={"pattern": "v2", "path": "notes"}))
    print("grep", r.data)
    assert r.ok and (r.data or {}).get("hits"), r
    # 兼容模型误写 query
    r = tools.execute(Action(kind="fs_grep", args={"query": "v2", "path": "notes"}))
    assert r.ok and (r.data or {}).get("hits"), r

    # 字面 \| 且 0 命中 → 旁注（勿当成「文件无匹配」）
    r = tools.execute(
        Action(
            kind="fs_grep",
            args={
                "pattern": r"v2\|no_such_token_xyz",
                "path": "notes",
                "use_index": False,
            },
        )
    )
    assert r.ok and (r.data or {}).get("count", 1) == 0, r
    assert "pattern_hint" in (r.data or {}), r.data
    assert "\\|" in (r.message or "") or "字面" in (r.message or ""), r.message
    # 正确或运算仍可命中，且无旁注
    r = tools.execute(
        Action(
            kind="fs_grep",
            args={"pattern": "v2|no_such_token_xyz", "path": "notes", "use_index": False},
        )
    )
    assert r.ok and (r.data or {}).get("hits"), r
    assert "pattern_hint" not in (r.data or {}), r.data

    from fawai_avatar.brain.false_missing_source import (
        failed_needs_false_missing_retry,
        thought_claims_missing_source,
    )

    assert thought_claims_missing_source(
        "已穷尽：.send/.stop 样式未找到；fs_glob 未命中任何 .css"
    )
    assert failed_needs_false_missing_retry(
        thought="找不到样式定义来源，可能位于 dist",
        read_ok_paths=["fawai-avatar/fawai_avatar/channel/static/index.html"],
    )
    assert not failed_needs_false_missing_retry(
        thought="找不到样式定义来源",
        read_ok_paths=["notes/a.txt"],
    )

    r = tools.execute(Action(kind="version_list", args={"path_prefix": "notes"}))
    assert any(v["id"] == vid for v in r.data["versions"]), r.data
    print("list", len(r.data["versions"]))

    r = tools.execute(Action(kind="version_get", args={"version_id": vid}))
    assert r.ok and r.data["exists"]

    r = tools.execute(Action(kind="version_rollback", args={"version_id": vid}))
    assert r.ok, r.message
    text = Path(work_root, "notes/a.txt").read_text(encoding="utf-8")
    assert text == "v1\n", text
    print("rollback ok", text.strip())

    r = tools.execute(
        Action(kind="fs_replace", args={"path": "notes/a.txt", "old": "v1", "new": "v3"})
    )
    assert r.ok and r.data.get("version_id")

    r = tools.execute(Action(kind="version_delete", args={"version_id": vid}))
    assert r.ok and r.data.get("deleted")
    r = tools.execute(Action(kind="version_get", args={"version_id": vid}))
    assert not r.data.get("exists")
    print("delete ok")

    # 只读根：仓外绝对路径可读；写仍不可逃逸
    from fawai_avatar.workspace.fs import WorkspaceFs
    from fawai_avatar.workspace.paths import WorkspacePathError

    outside_dir = Path(tempfile.mkdtemp(prefix="fawai-read-root-"))
    outside = outside_dir / "secret_cfg.md"
    outside.write_text("# cfg\nport=37443\n", encoding="utf-8")
    (outside_dir / "云服务重要信息.md").write_text("# 4090\nssh demo\n", encoding="utf-8")
    fs_ro = WorkspaceFs(work_root, read_roots=["/"])
    got = fs_ro.read(str(outside), offset=1, limit=10)
    assert any("37443" in row["text"] for row in got["lines"]), got
    try:
        fs_ro.write(str(outside), "nope")
        raise AssertionError("write outside work_root should fail")
    except WorkspacePathError as exc:
        assert "escapes work root" in str(exc), str(exc)
    print("read_roots ok", outside)

    # fs_glob path=仓外目录：须真正从该目录扫，不得静默只扫 work_root
    found = fs_ro.glob("**/*云*.md", path=str(outside_dir), head_limit=20)
    assert any(str(outside_dir) in p and "云服务重要信息.md" in p for p in found), found
    try:
        fs_ro.glob("**/*", path="/", head_limit=5)
        raise AssertionError("path=/ broad pattern should fail")
    except ValueError as exc:
        assert "过宽" in str(exc), str(exc)
    # 经 BackgroundTools：错路径须 file missing 文案（临时挂上只读根）
    tools.fs = fs_ro
    r = tools.execute(
        Action(
            kind="fs_glob",
            args={"path": str(outside_dir), "pattern": "**/*云*.md", "head_limit": 20},
        )
    )
    assert r.ok and (r.data or {}).get("paths"), r
    r = tools.execute(Action(kind="fs_glob", args={"path": "/", "pattern": "**/*"}))
    assert not r.ok and "过宽" in (r.message or ""), r.message
    r = tools.execute(
        Action(kind="fs_read", args={"path": str(outside_dir / "no-such.md")})
    )
    assert not r.ok and "file missing" in (r.message or ""), r.message
    assert "≠ 沙箱越界" in (r.message or ""), r.message
    # 父目录在、文件不在：须明示「仅文件名缺失」，禁止一律催拼接纠偏
    assert "仅该文件名缺失" in (r.message or ""), r.message
    assert "勿当作路径拼接错误" in (r.message or ""), r.message
    assert "请核对路径拼接" not in (r.message or ""), r.message

    print(json.dumps({"work_root": work_root, "status": "smoke ok"}, ensure_ascii=False))


if __name__ == "__main__":
    WORK = tempfile.mkdtemp(prefix="fawai_fs_")
    DATA = tempfile.mkdtemp(prefix="fawai_data_")
    print("WORK=", WORK)
    print("DATA=", DATA)
    main(WORK, DATA)
