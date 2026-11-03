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

    r = tools.execute(Action(kind="fs_glob", args={"pattern": "**/*.txt"}))
    print("glob", r.data)
    assert "notes/a.txt" in (r.data or {}).get("paths", [])

    # 前缀 + ** 与花括号：不得因 Path.match 坑返回空
    r = tools.execute(Action(kind="fs_glob", args={"pattern": "notes/**/*.txt"}))
    assert "notes/a.txt" in (r.data or {}).get("paths", []), r.data
    r = tools.execute(Action(kind="fs_glob", args={"pattern": "notes/**/*.{txt,md}"}))
    assert "notes/a.txt" in (r.data or {}).get("paths", []), r.data

    r = tools.execute(Action(kind="fs_grep", args={"pattern": "v2", "path": "notes"}))
    print("grep", r.data)
    # 兼容模型误写 query
    r = tools.execute(Action(kind="fs_grep", args={"query": "v2", "path": "notes"}))
    assert r.ok and (r.data or {}).get("hits"), r

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

    outside = Path(tempfile.mkdtemp(prefix="fawai-read-root-")) / "secret_cfg.md"
    outside.write_text("# cfg\nport=37443\n", encoding="utf-8")
    fs_ro = WorkspaceFs(work_root, read_roots=["/"])
    got = fs_ro.read(str(outside), offset=1, limit=10)
    assert any("37443" in row["text"] for row in got["lines"]), got
    try:
        fs_ro.write(str(outside), "nope")
        raise AssertionError("write outside work_root should fail")
    except WorkspacePathError as exc:
        assert "escapes work root" in str(exc), str(exc)
    print("read_roots ok", outside)

    print(json.dumps({"work_root": work_root, "status": "smoke ok"}, ensure_ascii=False))


if __name__ == "__main__":
    WORK = tempfile.mkdtemp(prefix="fawai_fs_")
    DATA = tempfile.mkdtemp(prefix="fawai_data_")
    print("WORK=", WORK)
    print("DATA=", DATA)
    main(WORK, DATA)
