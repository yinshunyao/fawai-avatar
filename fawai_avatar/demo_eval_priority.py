# term:版本评测  doc:doc/版本/评分量表.md
"""优先改进评测套件：D1–D6 可复跑证据（默认无真键鼠）。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fawai_avatar.brain.capabilities import (
    is_beyond_capability,
    looks_like_file_goal,
    mock_plan_for_goal,
    validate_plan,
)
from fawai_avatar.brain.planner import Brain
from fawai_avatar.control.background import BackgroundTools
from fawai_avatar.control.executor import Controller
from fawai_avatar.observe.verify import observe_act_verify_file, verify_observation_usable
from fawai_avatar.runtime.loop import WorkerLoop
from fawai_avatar.task.models import ResourceItem
from fawai_avatar.task.supervisor import Supervisor
from fawai_avatar.types import Action, Observation, Plan
from fawai_avatar.versioning.store import VersionStore
from fawai_avatar.workspace.fs import WorkspaceFs
from fawai_avatar.workspace.paths import WorkspacePathError, resolve_under_root


def _pass(name: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {"name": name, "ok": True}
    if detail:
        row["detail"] = detail
    print("PASS", name, json.dumps(detail or {}, ensure_ascii=False)[:200])
    return row


def _fail(name: str, error: str) -> dict[str, Any]:
    print("FAIL", name, error)
    return {"name": name, "ok": False, "error": error}


def case_d1_plan_exec_readback(work: Path, data: Path) -> dict[str, Any]:
    """D1：规划 → 执行 → 回读。"""
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    goal = "在工作区写入 notes/e2e_hello.txt 内容 hello"
    sup = Supervisor(str(data / "d1"))
    tid = sup.create_task(goal)
    brain = Brain()
    first = brain.plan(goal, Observation(summary="capture disabled"))
    if validate_plan(goal, first) or not any(a.kind == "fs_write" for a in first.actions):
        return _fail("D1-plan-exec-readback", f"bad first plan: {first.to_dict()}")
    loop = WorkerLoop(
        sup,
        brain=brain,
        controller=Controller(dry_run=False, supervisor=sup),
        max_steps=4,
        capture=False,
    )
    loop.controller._background = BackgroundTools(
        work_root=work, data_dir=data / "d1", auto_version=False
    )
    out = loop.run(tid)
    path = work / "notes" / "e2e_hello.txt"
    if not out.get("ok") or not path.is_file() or "hello" not in path.read_text(encoding="utf-8"):
        return _fail("D1-plan-exec-readback", f"out={out} path={path}")
    verifies = out.get("verifies") or []
    if not any(v.get("ok") for v in verifies):
        return _fail("D1-plan-exec-readback", f"missing file verify: {verifies}")
    return _pass(
        "D1-plan-exec-readback",
        {"task_id": out.get("task_id"), "verifies": verifies, "replan_count": out.get("replan_count")},
    )


def case_d1_gateway_http(data: Path) -> dict[str, Any]:
    """D1：会话网关 HTTP/口令/入站（本机自动化；真机同网仍属人机闸门加分项）。"""
    from fastapi.testclient import TestClient

    from fawai_avatar.channel.hub import SessionHub
    from fawai_avatar.channel.lan_gateway import create_app
    from fawai_avatar.channel.message import Message

    root = data / "gw"
    root.mkdir(parents=True, exist_ok=True)
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    sup = Supervisor(str(root))
    seen: list[Message] = []

    async def capture(msg: Message) -> None:
        seen.append(msg)

    hub_holder: dict = {"hub": None}

    def worker(task_id: str) -> dict:
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
        card = await hub.handle_inbound(
            Message.text_user("在工作区写入 notes/gw_hello.txt 内容 hello")
        )
        assert card is not None and card.kind == "card"
        for _ in range(80):
            if any(
                m.kind == "card" and m.meta.get("status") in {"completed", "failed"}
                for m in seen
            ):
                break
            await asyncio.sleep(0.05)

        app = create_app(hub, passcode="EVAL01", media_dir=root / "media")
        with TestClient(app) as client:
            assert client.get("/api/health").json()["ok"] is True
            assert client.get("/api/session", headers={"X-Fawai-Token": "WRONG"}).status_code == 401
            assert client.get("/api/session", headers={"X-Fawai-Token": "EVAL01"}).status_code == 200
            assert "fawai" in client.get("/").text.lower()

    try:
        asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        return _fail("D1-gateway-http", str(exc))
    return _pass("D1-gateway-http", {"outbound": len(seen)})


def case_d2_misleading_and_beyond() -> dict[str, Any]:
    """D2：误导写文件 + 超清单失败。"""
    goal_c = "用鼠标在桌面画个圈完成写文件 notes/c2_mislead.txt 内容 hello"
    assert looks_like_file_goal(goal_c)
    plan_c = mock_plan_for_goal(goal_c)
    err_c = validate_plan(goal_c, plan_c)
    if err_c or not any(a.kind == "fs_write" for a in plan_c.actions):
        return _fail("D2-misleading", f"{err_c} {plan_c.to_dict()}")
    # 硬校验：若强行 GUI 应违规
    bad = Plan(
        thought="click",
        actions=[Action(kind="click", args={"x": 1, "y": 2}, needs_foreground=True)],
        status="continue",
    )
    if not validate_plan(goal_c, bad):
        return _fail("D2-misleading", "GUI substitute not rejected")

    goal_x = "安装未登记的神秘 App 并完成银行实名认证登录"
    assert is_beyond_capability(goal_x)
    plan_x = mock_plan_for_goal(goal_x)
    if plan_x.status != "failed":
        return _fail("D2-beyond", f"expected failed, got {plan_x.to_dict()}")
    fake_ok = Plan(thought="假装点完了", actions=[], done=True, status="success")
    if not validate_plan(goal_x, fake_ok):
        return _fail("D2-beyond", "fake success not rejected")
    return _pass(
        "D2-misleading-beyond",
        {"mislead_kind": plan_c.actions[0].kind, "beyond_status": plan_x.status},
    )


def case_d4_observe_act_verify(work: Path, data: Path, *, capture: bool) -> dict[str, Any]:
    """D4：观察 → 写文件 → 文件/截图回证。"""
    out = observe_act_verify_file(
        work_root=work,
        observe_dir=data / "observe",
        path="notes/d4_verify.txt",
        content="verified-by-eval",
        capture=capture,
    )
    if not out.get("ok"):
        return _fail("D4-observe-act-verify", json.dumps(out, ensure_ascii=False)[:400])
    detail = {
        "file_verify": out.get("file_verify"),
        "capture_ok": out.get("capture_ok"),
        "capture_attempted": capture,
    }
    if capture and not out.get("capture_ok"):
        # 文件回证已过；截屏权限缺失记为部分通过细节，不整案失败
        detail["capture_note"] = "screenshot verify failed (permission?); file path ok"
    return _pass("D4-observe-act-verify", detail)


def case_d5_lease_reject_and_replan(work: Path, data: Path) -> dict[str, Any]:
    """D5：租约拒绝可观测；工具失败后重规划止损/改道。"""
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    sup = Supervisor(str(data / "d5"))
    t1 = sup.create_task("holder")
    t2 = sup.create_task("blocked-writer")
    assert sup.set_running(t1) and sup.set_running(t2)
    shared = str(work / "notes" / "lease.txt")
    work.joinpath("notes").mkdir(parents=True, exist_ok=True)
    Path(shared).write_text("x", encoding="utf-8")
    assert sup.claim_resources(t1, [ResourceItem("file", shared, "write")])
    assert not sup.claim_resources(t2, [ResourceItem("file", shared, "write")])

    ctrl = Controller(dry_run=True, supervisor=sup)
    ctrl._background = BackgroundTools(work_root=work, data_dir=data / "d5", auto_version=False)
    denied = ctrl.execute(
        Action(kind="fs_write", args={"path": "notes/lease.txt", "content": "y"}, write_resources=[shared]),
        task_id=t2,
    )
    if denied.ok or "not allowed" not in denied.message:
        return _fail("D5-lease-replan", f"expected write reject, got {denied.to_dict()}")

    class FailThenWriteBrain:
        def __init__(self) -> None:
            self.n = 0

        def plan(self, goal: str, observation: Observation, history=None) -> Plan:
            self.n += 1
            if self.n == 1:
                return Plan(
                    thought="first try missing path",
                    actions=[Action(kind="fs_read", args={"path": "notes/missing_replan.txt"})],
                    done=False,
                    status="continue",
                )
            return Plan(
                thought="replan: write then done",
                actions=[
                    Action(
                        kind="fs_write",
                        args={"path": "notes/replan_ok.txt", "content": "recovered"},
                        write_resources=["notes/replan_ok.txt"],
                    )
                ],
                done=False,
                status="continue",
            )

    # 第二次 plan 后还需要 success 收尾：用包装脑
    class FailWriteThenDoneBrain(FailThenWriteBrain):
        def plan(self, goal: str, observation: Observation, history=None) -> Plan:
            if self.n >= 2 and history and any(
                "fs_write" in str(m.get("content")) and "→ ok" in str(m.get("content"))
                for m in history
            ):
                return Plan(thought="done after recover", actions=[], done=True, status="success")
            return super().plan(goal, observation, history=history)

    brain = FailWriteThenDoneBrain()
    tid = sup.create_task("replan after tool error")
    loop = WorkerLoop(
        sup,
        brain=brain,  # type: ignore[arg-type]
        controller=Controller(dry_run=False, supervisor=sup),
        max_steps=5,
        capture=False,
    )
    loop.controller._background = BackgroundTools(
        work_root=work, data_dir=data / "d5b", auto_version=False
    )
    out = loop.run(tid)
    if not out.get("ok") or int(out.get("replan_count") or 0) < 1:
        return _fail("D5-lease-replan", f"replan not observed: {out}")
    if int(out.get("reflect_count") or 0) < 1:
        return _fail("D5-lease-replan", f"reflect not observed: {out}")
    decision = ((out.get("reflection") or {}).get("decision")) or ""
    if decision not in {"retry", "alternate", "abort"}:
        return _fail("D5-lease-replan", f"bad reflection decision: {out.get('reflection')}")
    if not (work / "notes" / "replan_ok.txt").is_file():
        return _fail("D5-lease-replan", "recovered file missing")
    return _pass(
        "D5-lease-replan",
        {
            "replan_count": out.get("replan_count"),
            "reflect_count": out.get("reflect_count"),
            "decision": decision,
            "lease_reject": denied.message,
        },
    )


def case_d5b_fingerprint_abort(work: Path, data: Path) -> dict[str, Any]:
    """D5b：同指纹空转 → 反省后 abort，不耗尽 max_steps。"""
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    from fawai_avatar.brain.reflect import action_fingerprint

    bad = Action(kind="fs_read", args={"path": "notes/never_exists_fp.txt"})
    fp = action_fingerprint(bad)

    class SameFailBrain:
        def plan(self, goal: str, observation: Observation, history=None) -> Plan:
            return Plan(
                thought="keep same failing read",
                actions=[bad],
                done=False,
                status="continue",
            )

    sup = Supervisor(str(data / "d5b_fp"))
    tid = sup.create_task("fingerprint abort")
    loop = WorkerLoop(
        sup,
        brain=SameFailBrain(),  # type: ignore[arg-type]
        controller=Controller(dry_run=False, supervisor=sup),
        max_steps=8,
        capture=False,
    )
    loop.controller._background = BackgroundTools(
        work_root=work, data_dir=data / "d5b_fp_bg", auto_version=False
    )
    out = loop.run(tid)
    if out.get("ok"):
        return _fail("D5b-fingerprint-abort", f"expected fail, got ok: {out}")
    reflect_count = int(out.get("reflect_count") or 0)
    if reflect_count < 2:
        return _fail("D5b-fingerprint-abort", f"expected >=2 reflects, got {out}")
    # 同指纹第二次应 abort（首次 alternate 后仍同指纹）
    decision = ((out.get("reflection") or {}).get("decision")) or ""
    if decision != "abort":
        return _fail("D5b-fingerprint-abort", f"expected abort, got {decision}: {out}")
    # 不应跑满 max_steps（8 步空转）
    steps = out.get("steps") or []
    if len(steps) >= 8:
        return _fail("D5b-fingerprint-abort", f"burned max_steps: {len(steps)}")
    return _pass(
        "D5b-fingerprint-abort",
        {
            "reflect_count": reflect_count,
            "steps": len(steps),
            "fingerprint": fp,
            "decision": decision,
        },
    )


def case_d5c_step_repeat_abort(work: Path, data: Path) -> dict[str, Any]:
    """D5c：成功同构步重复 → 步骤重复裁决后仍同指纹 → 硬 abort。"""
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    os.environ["FAWAI_STEP_REPEAT"] = "1"
    os.environ["FAWAI_STEP_REPEAT_N"] = "2"
    from fawai_avatar.brain.reflect import step_fingerprint

    notes = work / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "same.txt").write_text("hello-step-repeat\n", encoding="utf-8")
    same = Action(kind="fs_read", args={"path": "notes/same.txt"})
    fp = step_fingerprint([same], observe_methods=["none"])

    class SameOkBrain:
        def plan(self, goal: str, observation: Observation, history=None) -> Plan:
            return Plan(
                thought="keep reading same file",
                actions=[same],
                done=False,
                status="continue",
            )

    sup = Supervisor(str(data / "d5c_step"))
    tid = sup.create_task("step repeat abort")
    loop = WorkerLoop(
        sup,
        brain=SameOkBrain(),  # type: ignore[arg-type]
        controller=Controller(dry_run=False, supervisor=sup),
        max_steps=8,
        capture=False,
    )
    loop.controller._background = BackgroundTools(
        work_root=work, data_dir=data / "d5c_step_bg", auto_version=False
    )
    out = loop.run(tid)
    if out.get("ok"):
        return _fail("D5c-step-repeat-abort", f"expected fail, got ok: {out}")
    stag = int(out.get("stagnation_count") or 0)
    if stag < 2:
        return _fail(
            "D5c-step-repeat-abort",
            f"expected stagnation_count>=2, got {out}",
        )
    decision = ((out.get("reflection") or {}).get("decision")) or ""
    trigger = ((out.get("reflection") or {}).get("trigger")) or ""
    if decision != "abort":
        return _fail("D5c-step-repeat-abort", f"expected abort, got {decision}: {out}")
    if trigger != "step_repeat":
        return _fail(
            "D5c-step-repeat-abort", f"expected trigger=step_repeat, got {trigger}: {out}"
        )
    steps = out.get("steps") or []
    if len(steps) >= 8:
        return _fail("D5c-step-repeat-abort", f"burned max_steps: {len(steps)}")
    return _pass(
        "D5c-step-repeat-abort",
        {
            "stagnation_count": stag,
            "steps": len(steps),
            "step_fingerprint": fp,
            "decision": decision,
        },
    )


def case_d5d_step_crash_replan(work: Path, data: Path) -> dict[str, Any]:
    """D5d：步骤未捕获异常 → 反省后重规划并恢复成功。"""
    os.environ["FAWAI_BRAIN_MOCK"] = "1"
    notes = work / "notes"
    notes.mkdir(parents=True, exist_ok=True)

    class CrashThenWriteBrain:
        def __init__(self) -> None:
            self.n = 0

        def plan(self, goal: str, observation: Observation, history=None) -> Plan:
            self.n += 1
            # 首轮观察被打挂后，反省注入 history；下一轮正常写文件
            if history and any(
                "步骤异常" in str(m.get("content") or "")
                or "step_crash" in str(m.get("content") or "")
                or "步骤运行时异常" in str(m.get("content") or "")
                for m in history
            ):
                if any(
                    "fs_write" in str(m.get("content") or "") and "→ ok" in str(m.get("content") or "")
                    for m in history
                ):
                    return Plan(
                        thought="recovered after step crash",
                        actions=[],
                        done=True,
                        status="success",
                    )
                return Plan(
                    thought="after crash write file",
                    actions=[
                        Action(
                            kind="fs_write",
                            args={"path": "notes/crash_recover.txt", "content": "ok"},
                            write_resources=["notes/crash_recover.txt"],
                        )
                    ],
                    done=False,
                    status="continue",
                )
            return Plan(
                thought="before crash",
                actions=[Action(kind="fs_read", args={"path": "notes/crash_recover.txt"})],
                done=False,
                status="continue",
            )

    class BoomLoop(WorkerLoop):
        def _observe(self, goal: str, *, methods: list[str] | None = None) -> Observation:
            if not getattr(self, "_crashed_once", False):
                self._crashed_once = True
                raise RuntimeError("simulated observe boom")
            return Observation(summary="obs after recover", meta={"observe_methods": methods or []})

    sup = Supervisor(str(data / "d5d_crash"))
    tid = sup.create_task("recover after step crash")
    loop = BoomLoop(
        sup,
        brain=CrashThenWriteBrain(),  # type: ignore[arg-type]
        controller=Controller(dry_run=False, supervisor=sup),
        max_steps=6,
        capture=False,
    )
    loop.controller._background = BackgroundTools(
        work_root=work, data_dir=data / "d5d_crash_bg", auto_version=False
    )
    out = loop.run(tid)
    if not out.get("ok"):
        return _fail("D5d-step-crash-replan", f"expected ok after recover: {out}")
    if int(out.get("reflect_count") or 0) < 1:
        return _fail("D5d-step-crash-replan", f"reflect not observed: {out}")
    if int(out.get("replan_count") or 0) < 1:
        return _fail("D5d-step-crash-replan", f"replan not observed: {out}")
    trigger = ((out.get("reflection") or {}).get("trigger")) or ""
    if trigger != "step_crash":
        return _fail("D5d-step-crash-replan", f"expected trigger=step_crash, got {trigger}: {out}")
    if not (notes / "crash_recover.txt").is_file():
        return _fail("D5d-step-crash-replan", "recovered file missing")
    return _pass(
        "D5d-step-crash-replan",
        {
            "reflect_count": out.get("reflect_count"),
            "replan_count": out.get("replan_count"),
            "trigger": trigger,
            "decision": (out.get("reflection") or {}).get("decision"),
        },
    )


def case_d6_path_and_secret(work: Path, data: Path) -> dict[str, Any]:
    """D6：路径逃逸拒绝；.env 不入快照 / 默认不可写。"""
    fs = WorkspaceFs(work)
    try:
        resolve_under_root(fs.work_root, "../escape.txt")
        return _fail("D6-path-secret", "escape resolve should raise")
    except WorkspacePathError:
        pass
    esc = BackgroundTools(work_root=work, data_dir=data / "d6", auto_version=False).execute(
        Action(kind="fs_write", args={"path": "../escape.txt", "content": "nope"})
    )
    if esc.ok:
        return _fail("D6-path-secret", f"escape write ok unexpectedly: {esc.to_dict()}")

    env_path = work / ".env"
    env_path.write_text("FAWAI_SECRET=super-secret-eval\n", encoding="utf-8")
    write_env = BackgroundTools(work_root=work, data_dir=data / "d6", auto_version=False).execute(
        Action(kind="fs_write", args={"path": ".env", "content": "hacked\n"})
    )
    if write_env.ok:
        return _fail("D6-path-secret", "writing .env should be rejected")

    # 旁路：目录快照不得拷入 .env 内容
    (work / "notes").mkdir(parents=True, exist_ok=True)
    (work / "notes" / "ok.txt").write_text("public\n", encoding="utf-8")
    store = VersionStore(data / "d6" / "versions", work)
    vid = store.snapshot(["notes", ".env"], message="secret-check")
    info = store.get(vid)
    files = info.get("files") or []
    if any(str(p).endswith(".env") or p == ".env" for p in files):
        return _fail("D6-path-secret", f".env included in snapshot: {files}")
    # 扫快照目录无密钥明文
    hist = data / "d6" / "versions" / "history" / vid
    blob = ""
    if hist.is_dir():
        for p in hist.rglob("*"):
            if p.is_file():
                blob += p.read_text(encoding="utf-8", errors="ignore")
    if "super-secret-eval" in blob:
        return _fail("D6-path-secret", "secret leaked into version store")

    # 只读根：绝对路径可读；写仍拒逃逸
    outside = data / "d6" / "outside_cfg.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("port=9\n", encoding="utf-8")
    fs_ro = WorkspaceFs(work, read_roots=["/"])
    try:
        body = fs_ro.read(str(outside), offset=1, limit=5)
    except Exception as exc:  # noqa: BLE001
        return _fail("D6-path-secret", f"read_roots read failed: {exc}")
    if not any("port=9" in row["text"] for row in body.get("lines") or []):
        return _fail("D6-path-secret", f"read_roots content miss: {body}")
    try:
        fs_ro.write(str(outside), "hack\n")
        return _fail("D6-path-secret", "read_roots must not allow outside write")
    except WorkspacePathError:
        pass

    return _pass(
        "D6-path-secret",
        {"snapshot_files": files, "escape_msg": esc.message, "read_roots": True},
    )


def main(*, try_capture: bool) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="fawai_eval_"))
    work = root / "work"
    data = root / "data"
    work.mkdir()
    data.mkdir()
    os.environ["FAWAI_WORK_ROOT"] = str(work)
    os.environ["FAWAI_DATA_DIR"] = str(data)
    os.environ["FAWAI_DRY_RUN"] = "1"

    results = [
        case_d1_plan_exec_readback(work, data),
        case_d1_gateway_http(data),
        case_d2_misleading_and_beyond(),
        case_d4_observe_act_verify(work, data, capture=try_capture),
        case_d5_lease_reject_and_replan(work, data),
        case_d5b_fingerprint_abort(work, data),
        case_d5c_step_repeat_abort(work, data),
        case_d5d_step_crash_replan(work, data),
        case_d6_path_and_secret(work, data),
    ]
    # 可选：有截屏时再核一次 observation usable
    if try_capture:
        try:
            from fawai_avatar.observe.screen import observe

            obs = observe(data / "observe2")
            v = verify_observation_usable(obs)
            results.append(
                _pass("D4-observe-usable", v.to_dict())
                if v.ok
                else _fail("D4-observe-usable", v.message)
            )
        except Exception as exc:  # noqa: BLE001
            results.append(_fail("D4-observe-usable", str(exc)))

    ok_n = sum(1 for r in results if r.get("ok"))
    summary = {
        "ok": ok_n == len(results),
        "passed": ok_n,
        "total": len(results),
        "root": str(root),
        "results": results,
    }
    print(json.dumps({k: summary[k] for k in ("ok", "passed", "total", "root")}, ensure_ascii=False))
    if not summary["ok"]:
        raise SystemExit(1)
    return summary


if __name__ == "__main__":
    TRY_CAPTURE = os.environ.get("FAWAI_EVAL_CAPTURE", "").strip() in ("1", "true", "yes")
    KEEP = os.environ.get("FAWAI_EVAL_KEEP", "").strip() in ("1", "true", "yes")
    summary = main(try_capture=TRY_CAPTURE)
    if not KEEP:
        shutil.rmtree(summary["root"], ignore_errors=True)
