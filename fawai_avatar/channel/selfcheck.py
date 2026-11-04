# term:会话通道  doc:doc/功能/会话通道/说明.md
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from fawai_avatar.channel.hub import (
    SessionHub,
    _HISTORY_KEEP,
    _HISTORY_PROGRESS_PER_TASK_MAX,
    _HISTORY_SOFT_MAX,
    _original_goal,
    _trim_history,
)
from fawai_avatar.channel.message import Message, extract_image_marks, strip_image_marks
from fawai_avatar.channel.relay import CloudRelayClient
from fawai_avatar.channel.task_timeline import (
    merge_live_and_rebuilt,
    rebuild_timeline_from_task,
    timeline_needs_rebuild,
)
from fawai_avatar.skills.review import _review_goal_title
from fawai_avatar.task.models import TaskRecord
from fawai_avatar.task.supervisor import Supervisor


def _check_image_marks() -> None:
    text, refs = extract_image_marks(
        "看这个截图\n[image:/tmp/a.jpg]\n[image:/media/in_x.png]"
    )
    assert text == "看这个截图", text
    assert refs == ["/tmp/a.jpg", "/media/in_x.png"], refs
    assert strip_image_marks("hi\n[image:/abs/p.jpg]") == "hi"
    fake = TaskRecord(
        task_id="62843e2559144321ac3d1816b7d50935",
        goal="看这个截图，现在还是有bug.\n[image:/Users/x/data/session_media/in_a.jpg]",
        status="failed",
    )
    title = _review_goal_title([fake])
    assert "[image:" not in title, title
    assert "附图" in title and "看这个截图" in title, title
    assert (
        _original_goal("首条目标\n\n【追问】再试一次\n\n【追问】再再试") == "首条目标"
    )


def _check_history_keeps_user_images() -> None:
    user = Message(
        kind="image",
        role="user",
        text="见图",
        media_url="/media/in_keep.png",
        media_path="/tmp/in_keep.png",
        message_id="user-img-keep",
        task_id="t1",
    )
    flood = [
        Message.progress(f"p{i}", task_id="t1", meta={"i": i, "run_id": "r1"})
        for i in range(_HISTORY_SOFT_MAX + 200)
    ]
    for i, m in enumerate(flood):
        m.message_id = f"flood-p-{i}"
    trimmed = _trim_history([user, *flood])
    assert any(m.message_id == "user-img-keep" for m in trimmed), len(trimmed)
    assert len(trimmed) <= _HISTORY_KEEP


def _check_history_keeps_pinned_task_timeline() -> None:
    """置顶用户所属任务的 progress 优先于无关任务的全局尾部。"""
    user = Message(
        kind="text",
        role="user",
        text="优化布局",
        task_id="task-old",
        message_id="user-old",
    )
    old_progress = []
    for i in range(40):
        m = Message.progress(
            f"old-{i}", task_id="task-old", meta={"phase": "act", "i": i, "run_id": "old"}
        )
        m.message_id = f"old-p-{i}"
        old_progress.append(m)
    flood = []
    for i in range(_HISTORY_SOFT_MAX + 100):
        m = Message.progress(
            f"new-{i}", task_id="task-new", meta={"phase": "act", "i": i, "run_id": "new"}
        )
        m.message_id = f"new-p-{i}"
        flood.append(m)
    trimmed = _trim_history([user, *old_progress, *flood])
    assert any(m.message_id == "user-old" for m in trimmed)
    kept_old = [m for m in trimmed if m.task_id == "task-old"]
    assert len(kept_old) >= 20, len(kept_old)
    assert len(trimmed) <= _HISTORY_KEEP


def _check_history_keeps_multi_run_segments() -> None:
    """同一任务多轮追问：裁剪后仍应留下不止最后一轮的 progress。"""
    user0 = Message(
        kind="text",
        role="user",
        text="首条",
        task_id="task-multi",
        message_id="u0",
    )
    user1 = Message(
        kind="text",
        role="user",
        text="追问1",
        task_id="task-multi",
        message_id="u1",
    )
    run_a = []
    for i in range(80):
        m = Message.progress(
            f"a-{i}",
            task_id="task-multi",
            meta={"phase": "act", "run_id": "run-a"},
        )
        m.message_id = f"a-{i}"
        run_a.append(m)
    run_b = []
    for i in range(80):
        m = Message.progress(
            f"b-{i}",
            task_id="task-multi",
            meta={"phase": "act", "run_id": "run-b"},
        )
        m.message_id = f"b-{i}"
        run_b.append(m)
    noise = []
    for i in range(_HISTORY_SOFT_MAX):
        m = Message.progress(
            f"n-{i}",
            task_id="other",
            meta={"phase": "act", "run_id": "noise"},
        )
        m.message_id = f"n-{i}"
        noise.append(m)
    # 顺序：首条 → runA → 追问 → runB → 大量无关
    trimmed = _trim_history([user0, *run_a, user1, *run_b, *noise])
    kept = [m for m in trimmed if m.task_id == "task-multi"]
    runs = {
        str((m.meta or {}).get("run_id") or "")
        for m in kept
        if m.kind == "progress"
    }
    assert "run-a" in runs and "run-b" in runs, runs
    assert any(m.message_id == "u0" for m in trimmed)
    assert any(m.message_id == "u1" for m in trimmed)


def _check_history_caps_fat_task_progress() -> None:
    """单个胖任务的 progress 不得超过硬上限，给其它置顶任务留名额。"""
    user_fat = Message(
        kind="text",
        role="user",
        text="胖任务",
        task_id="fat",
        message_id="u-fat",
    )
    user_thin = Message(
        kind="text",
        role="user",
        text="瘦任务",
        task_id="thin",
        message_id="u-thin",
    )
    fat = []
    for i in range(_HISTORY_PROGRESS_PER_TASK_MAX + 200):
        m = Message.progress(
            f"fat-{i}", task_id="fat", meta={"phase": "act", "run_id": "fat"}
        )
        m.message_id = f"fat-p-{i}"
        fat.append(m)
    thin = []
    for i in range(40):
        m = Message.progress(
            f"thin-{i}", task_id="thin", meta={"phase": "act", "run_id": "thin"}
        )
        m.message_id = f"thin-p-{i}"
        thin.append(m)
    flood = []
    for i in range(_HISTORY_SOFT_MAX):
        m = Message.progress(
            f"noise-{i}", task_id="noise", meta={"phase": "act", "run_id": "n"}
        )
        m.message_id = f"noise-p-{i}"
        flood.append(m)
    trimmed = _trim_history([user_fat, *fat, user_thin, *thin, *flood])
    fat_kept = [m for m in trimmed if m.task_id == "fat" and m.kind == "progress"]
    thin_kept = [m for m in trimmed if m.task_id == "thin" and m.kind == "progress"]
    assert len(fat_kept) <= _HISTORY_PROGRESS_PER_TASK_MAX, len(fat_kept)
    assert len(thin_kept) >= 20, len(thin_kept)


def _check_rebuild_timeline_from_steps() -> None:
    """会话只剩用户气泡时，从 meta.steps 回放处理时间线。"""
    task = TaskRecord(
        task_id="abcd1234efgh5678ijkl9012mnop3456",
        goal="查一下状态",
        status="completed",
        meta={
            "ok": True,
            "final_thought": "GPU 正常，有训练在跑。",
            "steps": [
                {
                    "plan": {
                        "thought": "先查 GPU",
                        "status": "continue",
                        "actions": [{"kind": "shell", "args": {"cmd": "nvidia-smi"}}],
                    },
                    "results": [{"ok": True, "message": "RTX 4090, 50%"}],
                },
                {
                    "plan": {
                        "thought": "GPU 正常，有训练在跑。",
                        "status": "success",
                        "actions": [],
                    },
                    "results": [],
                },
            ],
        },
        created_at=1000.0,
        updated_at=1100.0,
    )
    live = [
        Message.text_user("查一下状态", task_id=task.task_id),
    ]
    live[0].message_id = "u-live"
    live[0].ts = 1000.0
    assert timeline_needs_rebuild(live, task)
    rebuilt = rebuild_timeline_from_task(task)
    assert any(m.kind == "progress" and (m.meta or {}).get("phase") == "planned" for m in rebuilt)
    assert any(m.kind == "card" for m in rebuilt)
    assert any(m.kind == "text" and "GPU" in m.text for m in rebuilt)
    merged = merge_live_and_rebuilt(live, rebuilt)
    assert merged[0].role == "user"
    assert any(m.kind == "progress" for m in merged)
    assert any(m.kind == "text" and m.role == "assistant" for m in merged)
    # 已有 progress 时不再回放
    live2 = live + [
        Message.progress("已有", task_id=task.task_id, meta={"phase": "act", "run_id": "r"})
    ]
    assert not timeline_needs_rebuild(live2, task)


def _iter_phase_literals(py_root: Path) -> set[str]:
    """扫 Python 里 progress/meta 用到的 phase 字面量（不含测试噪声变量）。"""
    phases: set[str] = set()
    # phase="foo" / phase='foo' / "phase": "foo"
    pat = re.compile(
        r"""(?:phase\s*=\s*["']([a-z][a-z0-9_]*)["'])|(?:["']phase["']\s*:\s*["']([a-z][a-z0-9_]*)["'])"""
    )
    skip_dirs = {"__pycache__", ".venv", "runtime_releases", "data"}
    for path in py_root.rglob("*.py"):
        if any(part in skip_dirs for part in path.parts):
            continue
        # 自检/demo 里的假 phase 不强制进 i18n（仍会扫到 hub/loop 真源）
        if path.name in {"selfcheck.py"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in pat.finditer(text):
            phases.add(m.group(1) or m.group(2))
    return phases


def _check_progress_phase_i18n() -> None:
    """杜绝 progress.meta.phase 缺中英文标签：源码有 phase 则 zh-CN/en 必须有 phase.*。"""
    channel_dir = Path(__file__).resolve().parent
    pkg_root = channel_dir.parent  # fawai_avatar/
    static_i18n = channel_dir / "static" / "i18n"
    zh = json.loads((static_i18n / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((static_i18n / "en.json").read_text(encoding="utf-8"))
    used = _iter_phase_literals(pkg_root)
    # 仅要求会话可见 progress/card 阶段有标签；排除纯内部 crash 标记
    ignore = {"step"}  # crash_phase 变量值，非 progress.meta.phase
    missing_zh: list[str] = []
    missing_en: list[str] = []
    raw_looking: list[str] = []
    for phase in sorted(used - ignore):
        key = f"phase.{phase}"
        if key not in zh:
            missing_zh.append(key)
        if key not in en:
            missing_en.append(key)
        # 中文条目不得等于英文码本身（防「有键但没译」）
        if key in zh and str(zh[key]).strip() == phase:
            raw_looking.append(key)
    assert not missing_zh, f"zh-CN.json 缺 progress 阶段文案: {missing_zh}"
    assert not missing_en, f"en.json 缺 progress 阶段文案: {missing_en}"
    assert not raw_looking, f"zh-CN phase 标签仍是英文码: {raw_looking}"
    # 两边 phase.* 键集合一致
    zh_phases = {k for k in zh if k.startswith("phase.")}
    en_phases = {k for k in en if k.startswith("phase.")}
    only_zh = sorted(zh_phases - en_phases)
    only_en = sorted(en_phases - zh_phases)
    assert not only_zh and not only_en, f"phase.* 中英键不一致 only_zh={only_zh} only_en={only_en}"


def main() -> None:
    _check_image_marks()
    _check_progress_phase_i18n()
    _check_history_keeps_user_images()
    _check_history_keeps_pinned_task_timeline()
    _check_history_keeps_multi_run_segments()
    _check_history_caps_fat_task_progress()
    _check_rebuild_timeline_from_steps()
    root = Path(tempfile.mkdtemp(prefix="fawai_session_"))
    try:
        os.environ["FAWAI_BRAIN_MOCK"] = "1"
        sup = Supervisor(str(root))
        seen: list[Message] = []

        async def capture(msg: Message) -> None:
            seen.append(msg)

        hub_holder: dict = {"hub": None}

        def worker(task_id: str) -> dict:
            from fawai_avatar.control.executor import Controller
            from fawai_avatar.runtime.loop import WorkerLoop

            def on_progress(text: str, meta: dict) -> None:
                h = hub_holder["hub"]
                if h is not None:
                    h.emit_progress(text, task_id=task_id, meta=meta)

            return WorkerLoop(
                sup,
                controller=Controller(dry_run=True, supervisor=sup),
                max_steps=2,
                capture=False,
                on_progress=on_progress,
            ).run(task_id)

        hub = SessionHub(sup, worker=worker, auto_run_worker=True)
        hub_holder["hub"] = hub
        hub.add_broadcaster(capture)

        async def run() -> None:
            hub.bind_loop(asyncio.get_running_loop())
            card = await hub.handle_inbound(Message.text_user("打开备忘录写一句话"))
            assert card is not None and card.kind == "card"
            assert card.meta.get("mode") == "create"
            # 等后台 worker
            for _ in range(50):
                if any(m.kind == "card" and m.meta.get("status") in {"completed", "failed"} for m in seen):
                    break
                await asyncio.sleep(0.1)
            assert any(m.kind in {"progress", "card", "text"} for m in seen)
            paused = await hub.handle_inbound(Message.text_user(f"暂停 {card.task_id[:8]}"))
            assert paused is not None

            # 路由/追加自检不自动跑 worker
            quiet = SessionHub(sup, worker=None, auto_run_worker=False)

            # 终态同目标追问 → 续跑 reopen（mock 默认 continue）
            cont_done = await quiet.handle_inbound(
                Message.text_user("改成写两句话", task_id=card.task_id)
            )
            assert cont_done is not None and cont_done.kind == "card"
            assert cont_done.meta.get("mode") == "continue"
            assert cont_done.task_id == card.task_id
            assert cont_done.meta.get("reopen") is True

            # 线程内明示另开 → new_task
            branched = await quiet.handle_inbound(
                Message.text_user("另外一件事：查一下天气", task_id=card.task_id)
            )
            assert branched is not None and branched.kind == "card"
            assert branched.meta.get("mode") == "create"
            assert branched.task_id != card.task_id
            assert branched.meta.get("parent_task_id") == card.task_id

            # 无 task_id 仍新建
            fresh = await quiet.handle_inbound(Message.text_user("无关的新目标"))
            assert fresh is not None and fresh.meta.get("mode") == "create"

            # 非终态追问
            created = await quiet.handle_inbound(Message.text_user("起草邮件"))
            assert created.meta.get("mode") == "create"
            cont = await quiet.handle_inbound(
                Message.text_user("语气正式一点", task_id=created.task_id)
            )
            assert cont.meta.get("mode") == "continue"
            assert cont.task_id == created.task_id
            task = quiet.supervisor.get_task(created.task_id)
            assert task is not None
            # 追问不并入 goal；记在 meta.followups，会话气泡各自独立
            assert "【追问】" not in (task.goal or "")
            assert "起草邮件" in (task.goal or "")
            fus = list((task.meta or {}).get("followups") or [])
            assert any("语气正式" in str(x.get("text") or "") for x in fus)

            # 执行中追加：apply → pending_appends，不新开 Worker
            running = await quiet.handle_inbound(Message.text_user("整理桌面文件"))
            assert running.meta.get("mode") == "create"
            rid = running.task_id
            assert quiet.supervisor.set_running(rid)
            appended = await quiet.handle_inbound(
                Message.text_user("先只动下载文件夹", task_id=rid)
            )
            assert appended is not None and appended.kind == "card"
            assert appended.meta.get("mode") == "append"
            assert appended.task_id == rid
            run_task = quiet.supervisor.get_task(rid)
            assert run_task is not None and run_task.status == "running"
            assert "【追问】" not in (run_task.goal or "")
            pending = list((run_task.meta or {}).get("pending_appends") or [])
            assert len(pending) >= 1
            assert "下载" in str(pending[-1].get("text") or "")
            assert not quiet.has_live_worker(rid)

            # 执行中明示另开 → new_task
            branched_run = await quiet.handle_inbound(
                Message.text_user("另外一件事：查天气", task_id=rid)
            )
            assert branched_run.meta.get("mode") == "create"
            assert branched_run.task_id != rid
            assert branched_run.meta.get("parent_task_id") == rid

            # 入站幂等：同 message_id 不二次落盘/路由；短窗同文不落第二份
            idemp = SessionHub(sup, worker=None, auto_run_worker=False)
            idemp_seen: list[Message] = []

            async def idemp_capture(msg: Message) -> None:
                idemp_seen.append(msg)

            idemp.add_broadcaster(idemp_capture)
            first = Message.text_user("幂等探针甲", task_id=None)
            mid = first.message_id
            card1 = await idemp.handle_inbound(first)
            assert card1 is not None and card1.kind == "card"
            n_hist = sum(1 for m in idemp.history if m.message_id == mid)
            assert n_hist == 1
            assert any(
                m.role == "user" and m.message_id == mid and m.text == "幂等探针甲"
                for m in idemp_seen
            )
            again = Message.text_user("幂等探针甲")
            again.message_id = mid
            assert await idemp.handle_inbound(again) is None
            assert sum(1 for m in idemp.history if m.message_id == mid) == 1
            near = Message.text_user("幂等探针甲")
            assert near.message_id != mid
            assert await idemp.handle_inbound(near) is None
            assert sum(1 for m in idemp.history if (m.text or "") == "幂等探针甲") == 1

            # 历史落盘：新枢纽实例应能读回
            assert hub.history_path.is_file()
            reloaded = SessionHub(sup, worker=None, auto_run_worker=False)
            assert any(m.text == "打开备忘录写一句话" for m in reloaded.history)

            relay = CloudRelayClient(relay_url="wss://example.invalid/relay")
            assert relay.enabled
            await relay.connect()
            assert relay.connected
            await relay.send(Message.text_assistant("ping"))
            await relay.disconnect()

            # HTTP 网关冒烟
            from fastapi.testclient import TestClient

            from fawai_avatar.channel.lan_gateway import create_app

            media = root / "media"
            app = create_app(hub, passcode="TEST01", media_dir=media)
            with TestClient(app) as client:
                assert client.get("/api/health").json()["ok"] is True
                bad = client.get("/api/session", headers={"X-Fawai-Token": "WRONG"})
                assert bad.status_code == 401
                ok = client.get("/api/session", headers={"X-Fawai-Token": "TEST01"})
                assert ok.status_code == 200
                hist = ok.json().get("history") or []
                assert any(h.get("text") == "打开备忘录写一句话" for h in hist)
                tasks = client.get("/api/tasks", headers={"X-Fawai-Token": "TEST01"})
                assert tasks.status_code == 200
                assert isinstance(tasks.json().get("tasks"), list)
                task_rows = tasks.json().get("tasks") or []
                if task_rows:
                    tid = task_rows[0]["task_id"]
                    th = client.get(
                        f"/api/tasks/{tid}/history",
                        headers={"X-Fawai-Token": "TEST01"},
                    )
                    assert th.status_code == 200, th.text
                    body = th.json()
                    assert body.get("ok") is True
                    assert body.get("task_id") == tid
                    assert isinstance(body.get("history"), list)
                    assert all(
                        (m.get("task_id") == tid) for m in (body.get("history") or [])
                    )
                page = client.get("/")
                assert page.status_code == 200
                assert "fawai" in page.text.lower()
                assert 'id="tasksPage"' in page.text
                assert 'id="newChatBtn"' in page.text
                assert 'id="workspace"' in page.text
                assert 'id="home"' in page.text
                assert 'id="threadList"' in page.text
                assert 'id="chatEmpty"' in page.text
                assert 'id="backBtn"' in page.text
                assert 'id="attachBar"' in page.text
                # 上传图：回 inbound（含 media_url），且历史可展示用户图
                png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
                up = client.post(
                    "/api/upload",
                    headers={"X-Fawai-Token": "TEST01"},
                    data={"text": "见图说明", "task_id": ""},
                    files={"file": ("t.png", png, "image/png")},
                )
                assert up.status_code == 200
                up_body = up.json()
                assert up_body.get("ok") is True
                inbound = up_body.get("inbound") or {}
                assert inbound.get("kind") == "image"
                assert inbound.get("media_url", "").startswith("/media/")
                assert inbound.get("text") == "见图说明"
                # PWA 须能解析 goal 附图标记并映射 /media
                index_html = (Path(__file__).resolve().parent / "static" / "index.html").read_text(
                    encoding="utf-8"
                )
                assert "parseImageMarks" in index_html
                assert "mediaUrlFromRef" in index_html
                assert "newClientMessageId" in index_html
                assert "waitForInboundAck" in index_html
                assert "chat.sending" in index_html or "tr(\"chat.sending\")" in index_html

        asyncio.run(run())
        print("channel self-check ok", "outbound=", len(seen))
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
