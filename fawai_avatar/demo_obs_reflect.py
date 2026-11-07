# term:观察反省  doc:doc/功能/观察反省/说明.md
"""观察反省冒烟：mock 产出、history 格式、关闸、闭环成功路径注入。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fawai_avatar.brain.obs_reflect import (
    ObsReflection,
    _strip_digest_as_source,
    build_obs_reflect_preview,
    format_obs_reflect_user_message,
    format_obs_reflection_for_history,
    mock_obs_reflect,
    obs_reflect_enabled,
    run_obs_reflect,
)
from fawai_avatar.brain.planner import Brain
from fawai_avatar.types import Action, ExecResult


def _ok(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        raise AssertionError(f"{name} failed" + (f": {detail}" if detail else ""))
    print(f"ok: {name}")


def _has_obs_reflect_event(events: list) -> bool:
    for ev in events:
        msg = str(getattr(ev, "message", "") or "")
        if msg.startswith("obs_reflect:"):
            return True
        data = getattr(ev, "data", None) or {}
        if isinstance(data, dict) and data.get("obs_reflection"):
            return True
    return False


def main() -> None:
    prev_mock = os.environ.get("FAWAI_BRAIN_MOCK")
    prev_gate = os.environ.get("FAWAI_OBS_REFLECT")
    try:
        os.environ["FAWAI_BRAIN_MOCK"] = "1"
        if "FAWAI_OBS_REFLECT" in os.environ:
            del os.environ["FAWAI_OBS_REFLECT"]

        _ok("enabled_default", obs_reflect_enabled())

        actions = [
            Action(kind="fs_read", args={"path": "notes/a.py", "offset": 1, "limit": 20})
        ]
        results = [ExecResult(ok=True, message="ok", data={"text": "def hello():\n  pass\n"})]
        reflection = mock_obs_reflect(
            "读 notes/a.py 并说明内容",
            actions,
            results,
            tool_feedback="执行结果（工具结果观察）\nfs_read notes/a.py → ok",
        )
        _ok("mock_progress", reflection.progress in {"advanced", "stalled", "enough_to_answer"})
        _ok("mock_hints", bool(reflection.hints))
        blob = format_obs_reflection_for_history(reflection)
        _ok("history_tag", "【观察反省】" in blob, blob[:120])
        _ok("history_progress", "progress:" in blob)

        via_run = run_obs_reflect(
            "读 notes/a.py",
            actions,
            results,
            tool_feedback="preview",
            llm=None,
        )
        _ok("run_mock", "【观察反省】" in format_obs_reflection_for_history(via_run))

        brain = Brain()
        via_brain = brain.obs_reflect(
            "读 notes/a.py",
            actions,
            results,
            tool_feedback="preview",
        )
        _ok("brain_obs_reflect", bool(via_brain.thought))

        # 预览：短文不裁；长文走压缩摘录，并声明不是原文
        short_preview, short_meta = build_obs_reflect_preview("ok short")
        _ok("preview_short_no_clip", short_preview == "ok short" and not short_meta.get("preview_clipped"))

        with tempfile.TemporaryDirectory() as preview_tmp:
            long_body = (
                "HEAD_MARK\n"
                + ("x" * 4000)
                + "\nTAIL_LS_PS_EVIDENCE\n"
                + "nvidia-smi done\nls -lt /data/models\nps -ef train\n"
            )
            long_preview, long_meta = build_obs_reflect_preview(
                long_body, data_dir=preview_tmp, max_chars=2500
            )
            _ok("preview_long_clipped", bool(long_meta.get("preview_clipped")))
            dump = str(long_meta.get("dump_path") or "")
            _ok("preview_dump_exists", dump.endswith(".txt") and Path(dump).is_file())
            _ok("preview_has_head", "HEAD_MARK" in long_preview)
            _ok("preview_has_tail", "TAIL_LS_PS_EVIDENCE" in long_preview)
            _ok("preview_no_fake_trunc_tag", "…(截断)" not in long_preview)
            _ok(
                "preview_not_source",
                "非原文" in long_preview and "处置策略" in long_preview and "不能作为原文处理" in long_preview,
            )
            _ok("preview_no_reread_order", "请用 fs_read" not in long_preview)
            dumped = Path(dump).read_text(encoding="utf-8")
            _ok("preview_dump_full", "HEAD_MARK" in dumped and "TAIL_LS_PS_EVIDENCE" in dumped)

            user_msg, user_meta = format_obs_reflect_user_message(
                "查训练是否完成",
                [Action(kind="shell", args={"cmd": "ssh remote"})],
                [ExecResult(ok=True, message="exit 0")],
                tool_feedback=long_body,
                data_dir=preview_tmp,
            )
            _ok("user_msg_preview_meta", bool(user_meta.get("preview_clipped")))
            _ok("user_msg_has_tail", "TAIL_LS_PS_EVIDENCE" in user_msg)

            os.environ["FAWAI_OBS_REFLECT_PREVIEW_CHARS"] = "2500"
            via_clip = run_obs_reflect(
                "查训练是否完成",
                [Action(kind="shell", args={"cmd": "ssh remote"})],
                [ExecResult(ok=True, message="exit 0")],
                tool_feedback=long_body,
                data_dir=preview_tmp,
                llm=None,
            )
            hist = format_obs_reflection_for_history(via_clip)
            _ok(
                "history_not_source",
                "非原文" in hist
                and "不能作为原文处理" in hist
                and "处置策略" in hist
                and "fs_read" not in hist
                and dump not in hist,
                hist[-360:],
            )
            dirty = ObsReflection(
                thought=f"去读 {dump} 补全原文",
                hints=[
                    "fs_read obs_reflect_preview.txt 补全摘录",
                    "下一步看进程是否在训练",
                ],
                notes=["把摘录当作原文引用"],
            )
            cleaned = _strip_digest_as_source(dirty, dump)
            _ok(
                "strip_digest_reread",
                cleaned.hints == ["下一步看进程是否在训练"] and cleaned.notes == [],
            )
            _ok("strip_dump_from_thought", dump not in cleaned.thought)
            os.environ.pop("FAWAI_OBS_REFLECT_PREVIEW_CHARS", None)
            _ok(
                "raw_preview_meta",
                bool((via_clip.raw or {}).get("preview", {}).get("dump_path")),
            )

        os.environ["FAWAI_OBS_REFLECT"] = "0"
        _ok("gate_off", not obs_reflect_enabled())
        del os.environ["FAWAI_OBS_REFLECT"]
        _ok("gate_on_again", obs_reflect_enabled())

        # 闭环：成功工具步后应有 obs_reflect 事件；关闸则无
        from fawai_avatar.control.executor import Controller
        from fawai_avatar.runtime.loop import WorkerLoop
        from fawai_avatar.task.supervisor import Supervisor

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            data = root / "data"
            work.mkdir()
            notes = work / "notes"
            notes.mkdir()
            sample = notes / "coding_sample.py"
            sample.write_text(
                'def hello() -> str:\n    return "hello"\n', encoding="utf-8"
            )
            os.environ["FAWAI_WORK_ROOT"] = str(work)
            os.environ["FAWAI_BRAIN_MOCK"] = "1"
            if "FAWAI_OBS_REFLECT" in os.environ:
                del os.environ["FAWAI_OBS_REFLECT"]

            supervisor = Supervisor(str(data))
            task_id = supervisor.create_task(
                '用 fs_patch 把 notes/coding_sample.py 里 return "hello" '
                '改成 return "hello-patched"'
            )
            loop = WorkerLoop(
                supervisor,
                brain=Brain(),
                controller=Controller(dry_run=True, supervisor=supervisor),
                max_steps=6,
                capture=False,
            )
            out = loop.run(task_id)
            events = supervisor.recent_events(task_id=task_id, limit=80)
            _ok(
                "loop_has_obs_reflect",
                _has_obs_reflect_event(events),
                json.dumps(
                    [getattr(e, "message", "") for e in events[-12:]],
                    ensure_ascii=False,
                ),
            )
            _ok("loop_ok_or_progress", bool(out.get("ok") or out.get("steps")))

            # 关闸重跑：不应再有 obs_reflect
            os.environ["FAWAI_OBS_REFLECT"] = "0"
            sample.write_text(
                'def hello() -> str:\n    return "hello"\n', encoding="utf-8"
            )
            task_id2 = supervisor.create_task(
                '用 fs_patch 把 notes/coding_sample.py 里 return "hello" '
                '改成 return "hello-gated"'
            )
            out2 = loop.run(task_id2)
            events2 = supervisor.recent_events(task_id=task_id2, limit=80)
            _ok(
                "loop_gate_off_no_obs_reflect",
                not _has_obs_reflect_event(events2),
                json.dumps(
                    [getattr(e, "message", "") for e in events2[-12:]],
                    ensure_ascii=False,
                ),
            )
            _ok("loop_gate_off_ran", bool(out2.get("ok") or out2.get("steps")))

        print("demo_obs_reflect: all passed")
    finally:
        if prev_mock is None:
            os.environ.pop("FAWAI_BRAIN_MOCK", None)
        else:
            os.environ["FAWAI_BRAIN_MOCK"] = prev_mock
        if prev_gate is None:
            os.environ.pop("FAWAI_OBS_REFLECT", None)
        else:
            os.environ["FAWAI_OBS_REFLECT"] = prev_gate
        os.environ.pop("FAWAI_WORK_ROOT", None)


if __name__ == "__main__":
    main()
