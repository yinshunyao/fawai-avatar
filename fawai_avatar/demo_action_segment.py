# term:动作段预期闸  doc:doc/功能/动作段预期闸/说明.md
"""动作段：expect 闸 + 机械成功跳过 LLM 观察反省。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fawai_avatar.brain.action_segment import (
    apply_expect_to_result,
    batch_is_mechanical_success,
    check_action_expect,
    format_mechanical_obs_summary,
    should_skip_obs_reflect_llm,
)
from fawai_avatar.brain.planner import Brain, _plan_from_payload
from fawai_avatar.brain.reflect import action_fingerprint
from fawai_avatar.types import Action, ExecResult


def _ok(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        raise AssertionError(f"{name} failed" + (f": {detail}" if detail else ""))
    print(f"ok: {name}")


def main() -> None:
    prev_mech = os.environ.get("FAWAI_OBS_REFLECT_MECHANICAL")
    try:
        # --- expect ---
        act = Action(
            kind="fs_read",
            args={"path": "a.md"},
            expect={"contains": "4090"},
        )
        ok_r = ExecResult(ok=True, message="host 4090 ssh ok", data={})
        bad_r = ExecResult(ok=True, message="no match here", data={})
        _ok("expect_pass", check_action_expect(act, ok_r)[0])
        _ok("expect_fail", not check_action_expect(act, bad_r)[0])
        wrapped = apply_expect_to_result(act, bad_r)
        _ok("expect_wrap_fail", not wrapped.ok and wrapped.message.startswith("expect failed:"))

        act_any = Action(
            kind="shell",
            args={"cmd": "ls"},
            expect={"contains_any": ["best.pt", "last.pt"]},
        )
        _ok(
            "contains_any_pass",
            check_action_expect(
                act_any, ExecResult(ok=True, message="files: last.pt\n")
            )[0],
        )
        _ok(
            "not_contains_fail",
            not check_action_expect(
                Action(kind="fs_read", args={}, expect={"not_contains": "ERROR"}),
                ExecResult(ok=True, message="ERROR: boom"),
            )[0],
        )

        # mutate 已落盘 + 臆造「patch applied」→ 保持 ok（避免假失败级联）
        patch_act = Action(
            kind="fs_patch",
            args={"path": "a.html", "old": "x", "new": "y"},
            expect={"contains": "patch applied"},
        )
        patch_landed = ExecResult(
            ok=True,
            message="fs_patch a.html · applied",
            data={
                "path": "a.html",
                "hunks_applied": 1,
                "written": True,
                "diff_mode": "auto",
                "diff": "--- a/a.html\n+++ b/a.html\n@@\n-x\n+y\n",
            },
        )
        patch_wrapped = apply_expect_to_result(patch_act, patch_landed)
        _ok(
            "expect_ignore_invented_after_write",
            patch_wrapped.ok
            and bool((patch_wrapped.data or {}).get("expect_ignored")),
            patch_wrapped.message,
        )

        # 真实内容 expect 失败但仍已写入 → ok=false 且文案标明已落盘
        content_act = Action(
            kind="fs_patch",
            args={"path": "a.html"},
            expect={"contains": "MUST_EXIST_IN_DIFF"},
        )
        content_wrapped = apply_expect_to_result(content_act, patch_landed)
        _ok(
            "expect_fail_but_disclose_write_landed",
            (not content_wrapped.ok)
            and "写入已落盘" in (content_wrapped.message or "")
            and bool((content_wrapped.data or {}).get("write_landed_despite_expect")),
            content_wrapped.message,
        )

        # 同 path 不同 hunk 指纹必须不同（步骤重复不得误伤换锚点）
        fp_a = action_fingerprint(
            Action(
                kind="fs_patch",
                args={"path": "a.html", "old": "aaa", "new": "bbb"},
            )
        )
        fp_b = action_fingerprint(
            Action(
                kind="fs_patch",
                args={
                    "path": "a.html",
                    "hunks": [{"old": "ccc", "new": "ddd"}],
                },
            )
        )
        _ok("mutate_fingerprint_differs_by_hunk", fp_a != fp_b, f"{fp_a} vs {fp_b}")
        _ok(
            "mutate_fingerprint_stable",
            fp_a
            == action_fingerprint(
                Action(
                    kind="fs_patch",
                    args={"path": "a.html", "old": "aaa", "new": "bbb"},
                )
            ),
        )

        # from_dict / args.expect
        parsed = Action.from_dict(
            {
                "kind": "fs_grep",
                "args": {"pattern": "x", "expect": {"contains": "hit"}},
            }
        )
        _ok("from_dict_expect", isinstance(parsed.expect, dict) and "contains" in parsed.expect)
        _ok("from_dict_args_clean", "expect" not in parsed.args)

        # --- mechanical skip ---
        reads = [
            Action(kind="fs_read", args={"path": "a"}),
            Action(kind="fs_glob", args={"glob": "*.md"}),
        ]
        results_ok = [
            ExecResult(ok=True, message="read a"),
            ExecResult(ok=True, message="glob 2"),
        ]
        _ok("batch_mechanical", batch_is_mechanical_success(reads, results_ok))
        if "FAWAI_OBS_REFLECT_MECHANICAL" in os.environ:
            del os.environ["FAWAI_OBS_REFLECT_MECHANICAL"]
        _ok("skip_default", should_skip_obs_reflect_llm(reads, results_ok))
        os.environ["FAWAI_OBS_REFLECT_MECHANICAL"] = "llm"
        _ok("skip_force_llm", not should_skip_obs_reflect_llm(reads, results_ok))
        del os.environ["FAWAI_OBS_REFLECT_MECHANICAL"]

        write_batch = [
            Action(kind="fs_patch", args={"path": "a"}, write_resources=["a"]),
        ]
        write_res = [ExecResult(ok=True, message="patched")]
        _ok(
            "write_not_mechanical",
            not should_skip_obs_reflect_llm(write_batch, write_res),
        )

        summary = format_mechanical_obs_summary(reads, results_ok)
        _ok("rule_summary_tag", "【观察反省·规则】" in summary)
        _ok("rule_summary_kinds", "fs_read" in summary and "fs_glob" in summary)

        # plan JSON 带 expect
        plan = _plan_from_payload(
            {
                "thought": "读两处",
                "status": "continue",
                "done": False,
                "observe_kinds": ["obs_fs"],
                "actions": [
                    {
                        "kind": "fs_read",
                        "args": {"path": "a.md", "offset": 1, "limit": 20},
                        "expect": {"contains": "host"},
                    },
                    {"kind": "fs_glob", "args": {"glob": "*.md"}},
                ],
            }
        )
        _ok("plan_actions_len", len(plan.actions) == 2)
        _ok("plan_expect0", bool(plan.actions[0].expect and plan.actions[0].expect.get("contains") == "host"))

        # 闭环：纯机械只读 → skipped_mechanical；写后仍有 LLM obs_reflect
        from fawai_avatar.control.executor import Controller
        from fawai_avatar.runtime.loop import WorkerLoop
        from fawai_avatar.task.supervisor import Supervisor

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            data = root / "data"
            work.mkdir()
            (work / "notes").mkdir()
            (work / "notes" / "a.md").write_text("hello host\n", encoding="utf-8")
            os.environ["FAWAI_WORK_ROOT"] = str(work)
            os.environ["FAWAI_BRAIN_MOCK"] = "1"
            os.environ.pop("FAWAI_OBS_REFLECT", None)
            os.environ.pop("FAWAI_OBS_REFLECT_MECHANICAL", None)

            class _ReadBrain(Brain):
                def plan(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                    # 首步只读一段；次步 success
                    hist = kwargs.get("history") or (args[3] if len(args) > 3 else [])
                    n_tool = sum(
                        1
                        for h in hist
                        if isinstance(h, dict)
                        and "执行结果" in str(h.get("content") or "")
                    )
                    if n_tool == 0:
                        return _plan_from_payload(
                            {
                                "thought": "读 notes",
                                "status": "continue",
                                "done": False,
                                "step_label": "读取笔记",
                                "observe_kinds": ["none"],
                                "actions": [
                                    {
                                        "kind": "fs_read",
                                        "args": {
                                            "path": "notes/a.md",
                                            "offset": 1,
                                            "limit": 10,
                                        },
                                    },
                                    {
                                        "kind": "fs_glob",
                                        "args": {"glob": "notes/*.md"},
                                    },
                                ],
                            }
                        )
                    return _plan_from_payload(
                        {
                            "thought": "已读完 notes/a.md，内容含 hello host。",
                            "status": "success",
                            "done": True,
                            "step_label": "答复",
                            "observe_kinds": ["none"],
                            "actions": [],
                        }
                    )

            supervisor = Supervisor(str(data))
            tid = supervisor.create_task("读 notes/a.md 并说明内容")
            loop = WorkerLoop(
                supervisor,
                brain=_ReadBrain(),
                controller=Controller(dry_run=False, supervisor=supervisor),
                max_steps=8,
                capture=False,
            )
            out = loop.run(tid)
            events = supervisor.recent_events(task_id=tid, limit=80)
            msgs = [str(getattr(e, "message", "") or "") for e in events]
            _ok(
                "loop_skipped_mechanical",
                any("skipped_mechanical" in m for m in msgs),
                json.dumps(msgs[-15:], ensure_ascii=False),
            )
            _ok(
                "loop_no_llm_obs_progress_line",
                not any(
                    m.startswith("obs_reflect:") and "skipped" not in m for m in msgs
                ),
                json.dumps(msgs[-15:], ensure_ascii=False),
            )
            _ok("loop_finished", bool(out.get("ok") or out.get("final_thought")))

            # expect 失败应进失败路径（不标 skipped_mechanical）
            class _ExpectBrain(Brain):
                def plan(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                    hist = kwargs.get("history") or []
                    if any(
                        "expect failed" in str(h.get("content") or "")
                        for h in hist
                        if isinstance(h, dict)
                    ):
                        return _plan_from_payload(
                            {
                                "thought": "expect 未满足，停止。",
                                "status": "failed",
                                "done": True,
                                "step_label": "止损",
                                "observe_kinds": ["none"],
                                "actions": [],
                            }
                        )
                    return _plan_from_payload(
                        {
                            "thought": "读并校验",
                            "status": "continue",
                            "done": False,
                            "step_label": "校验",
                            "observe_kinds": ["none"],
                            "actions": [
                                {
                                    "kind": "fs_read",
                                    "args": {
                                        "path": "notes/a.md",
                                        "offset": 1,
                                        "limit": 10,
                                    },
                                    "expect": {"contains": "MUST_NOT_EXIST_TOKEN"},
                                }
                            ],
                        }
                    )

            tid2 = supervisor.create_task("读 notes 并校验不存在的标记")
            loop2 = WorkerLoop(
                supervisor,
                brain=_ExpectBrain(),
                controller=Controller(dry_run=False, supervisor=supervisor),
                max_steps=8,
                capture=False,
            )
            out2 = loop2.run(tid2)
            events2 = supervisor.recent_events(task_id=tid2, limit=80)
            blob2 = json.dumps(
                [
                    {
                        "m": getattr(e, "message", ""),
                        "d": getattr(e, "data", None),
                    }
                    for e in events2
                ],
                ensure_ascii=False,
            )
            _ok(
                "expect_fail_seen",
                "expect failed" in blob2.lower()
                or "expect failed" in str(out2.get("final_thought") or "").lower()
                or out2.get("ok") is False,
                blob2[-800:],
            )

        print("demo_action_segment: all passed")
    finally:
        if prev_mech is None:
            os.environ.pop("FAWAI_OBS_REFLECT_MECHANICAL", None)
        else:
            os.environ["FAWAI_OBS_REFLECT_MECHANICAL"] = prev_mech
        os.environ.pop("FAWAI_WORK_ROOT", None)
        os.environ.pop("FAWAI_BRAIN_MOCK", None)
        os.environ.pop("FAWAI_OBS_REFLECT", None)


if __name__ == "__main__":
    main()
