# fawai-avatar

A PC / phone **operation avatar**: understand the goal → observe the environment → plan the next step → run tools / input → verify again. Readable docs live in `doc/`; implementation in `fawai_avatar/`; personal lore in `fawai_lore/`; reusable script shovels in `fawai_shovels/`.

中文说明见 [README.zh.md](README.zh.md).

## What it is

Not a chat-only bot, and not an IDE plugin that only edits code. It is a long-running assistant on your machine (or a headless server): you send tasks from a LAN PWA (or a public tunnel); it works **locally**—workspace files, shell checks, code search, skills and memory. With a desktop, it can also capture the screen and drive mouse/keyboard (GUI grounding is still evolving).

| Role | Job |
|:---|:---|
| **Brain** | Plan the next step from goal + observation; never touches devices directly |
| **Control** | Run tools / scripts / input; hard gates on leases and paths |
| **Observe** | Screen, filesystem state, tool-result digests → feed planning |
| **Task line** | One supervisor + many decoupled worker tasks; GUI serial, background parallel; complex goals can emit a plan graph |
| **Knowledge line** | Skill library, personal lore (KV/notes), script shovels, task review → code changes |

Single-task loop: `observe → brain → control → observe again`. The supervisor takes user requests and arbitrates resources; each worker keeps its own trace and checkpoint (resume after process restart, or from a chosen step in the PWA).

## Assistant capabilities (supported)

Source of truth: [`doc/功能/【总表】功能一览.md`](doc/功能/【总表】功能一览.md). Grouped by what you can ask it to do.

### Conversation & cross-device

- **Session gateway + PWA**: local HTTP/WS; open the passcode page on phone (same Wi‑Fi); optional frpc tunnel and cloud-relay skeleton
- **Task threads**: one conversation per task; new task / follow-up / soft append while running (pending inject; follow-ups stay out of the goal bubble); paste image as attachment then send; archive API still available (PWA entry temporarily hidden)
- **In-chat step stream**: progress folds into the thread (no separate timeline menu); same-step clusters merge/collapse; cluster headers keep step business intent `step_label`; facts show model · thinking on/off · tokens · elapsed; while waiting on the model, deep-thinking + normal output stream live into the step card (`meta.live`, not history); secret redaction temporarily off (planner can read credentials; security later)
- **Resume from step**: hover (desktop) or always-on (narrow) “resume from here” on a step card; checkpoint / restart resume for orphan tasks
- **Voice input**: mic → ASR into the compose box
- **UI zh/en**: switch anytime (widescreen top bar + System; narrow: System only); separate Model / System / Service panels

### Understanding & planning

- **LLM access**: OpenAI-compatible; text / multimodal routing; remote `/models` list; generation params per model profile; deep thinking by role + profile (main on / fast & obs_reflect off); Web model settings; after restart, routes follow disk `.env`
- **Brain → fast delegate**: main planner may hand a sub-goal to the fast-model route in Web model settings (empty = off); segment summary returns to the main brain
- **Complex-task plan mode**: orchestrator auto-judges complexity → emits a graph and schedules immediately (no human gate); three layers = orchestrator → supervisor → executor; mid-run append can truly `reorder` / `lease_adjust`; on failure default **retry failed nodes** (keep completed); full-graph replan only with explicit phrase / button; sidebar shows parent plan only, in-thread node cards + top-right digraph float; off with `FAWAI_PLAN_MODE=0`
- **Capability registry**: only registered tools; file goals must use `fs_*` (no GUI pretending to write files)
- **Prompt profiles**: trim system prompt / images by round purpose (`vision` / `fs` / `gui` / `full`)
- **Mid-run clarification**: try hard by default; `need_input` (optionally multiple-choice) only for high-cost ambiguity, then resume
- **Answer-coverage gate**: for Q&A / lookup / hardware inventory, bans “summarized and done” fake success
- **Necessary-path ledger**: recovery must not collapse to a single unfinished path; pairs with final failed delivery

### Mid-run correctives (same evolution chain as post-hoc review)

- **Action reflect** (on failure): `retry` / `alternate` only—**no abort**; stop-loss is planner `status=failed`; method-family recovery and remote-idle gates
- **Observe reflect** (on all-success tool digest): light thought / progress / notes / soft hints (`FAWAI_OBS_REFLECT=0` to disable); does not end the task
- **Step-repeat** (success isomorphism): fingerprint first (zero LLM), then LLM `continue` / `alternate`; optional `allow_repeat` skip

### Coding & files (background; not gated by `FAWAI_DRY_RUN`)

- **Workspace FS**: expandable read roots; writes limited to work_root; no shell bypass of fs
- **Precise patch / multi-file write set / session Diff**; ungrounded patch anchor → fail with original-text mini window; `code_check`; allowlisted `shell_verify`
- **Python session tools**: `py_write` / `py_pip` / `py_run` (session sandbox, runtime venv install, run + artifact dump)
- **Code index + search**: multi-language symbols / repo map; default path is `fs_grep` / `fs_glob` (explore temporarily deprioritized; symbol borrow needs `include_symbols`)
- **Script shovel lifecycle**: `shovel_search` before inventing one-off scripts (catalog `fawai_shovels/`); soft “shovel first” gate before write and soft “promote shovel” hint after; pick order: registered tools → existing shovel → temporary `py_*` / script
- **Git**: status / diff / commit (no auto push)
- **Gates**: weak-write block; UI goals must state visible effects; compliance (rule registry + layered-write doc refs). Explore / locate / wide-grep / tool-obs soft compress are **off by default** (opt-in via env)
- **Version snapshots**: optional auto-snapshot before write; query / rollback

### Desktop observe & control (bundled on macOS by default; Linux needs `.[gui]`; auto-off when headless)

- **Observe routing**: no default screenshot on step 0; LLM picks screen / **AX tree** / fs / tool digest; large logs compressed only when soft limits are set
- **Multi-display**: `displays` + `screen_target` (default: frontmost window’s display); image-local coords + `app_focus`; no auto sweep of all screens / Spaces
- **Control**: input + tools; global **foreground lease** (serial); `ui_click`/`ui_type` (by `ref`) + double-click / right-click / scroll / press
- **C3 narrow (macOS TextEdit)**: `obs_ax` → widget act → `verify_ui_ax`; not a promise for arbitrary Apps / Electron

### Browser tools (needs `.[browser]`; headless by default; independent of OS GUI)

- **Built-in Playwright page**: `browser_navigate` → `browser_snapshot` (numbered `ref`) → `browser_click` / `browser_type` / `browser_press`; `browser_close` to release
- Does **not** take the foreground lease; do not click system Chrome via desktop GUI instead
- Install: `pip install -e ".[browser]"` then `playwright install chromium`; set `FAWAI_BROWSER_HEADLESS=0` for headed

### Memory, evolution & ops

- **Personal lore** (`fawai_lore`): KV + notes + sidecar buffer; PWA memory page with three tabs (pending / stored / recent); stored entries enable / disable / delete
- **Skills**: searchable injection into planning; quality-gated writes; failures can land as `lesson`
- **Task review & failure analysis**: after approve, mechanism-first full analysis (plan / observe / act / reflect / outcome) → checklist (Skill / lesson / code / rule / prompt) → select, backup, apply; PWA single-task review (joint API retained); success and failure both reviewable
- **Self-evolution writes**: most of the tree writable; **blocked** minimum core (rollback / keeper / gates). Cursor / human disk edits unrestricted
- **Service keeper**: auto-restart on crash; child env overlays disk `.env` (model/system settings stay aligned after restart); code hot-restart switch (off by default; enable on Service & versions; deferred while tasks run); port reclaim for orphan gateway processes; console logs; one-click startup-repair task; runtime version archive / select-and-boot
- **System log**: subscribed disk log; skill/ops failures can one-click analyze → remediation task
- **Task stats**: tokens, wall time, rounds / tools; copy task id from sidebar; plan-mode parent usage = sum of node tasks

### Platforms

| Environment | What runs |
|:---|:---|
| **macOS + desktop** | Gateway + coding/files/shell + screen/input (default) + optional browser |
| **Linux headless** | Gateway + coding/files/shell + optional headless browser; no GUI/capture |
| **Linux with DISPLAY** | Optional `.[gui]` for desktop; browser still via `.[browser]` |

OS / headless are auto-detected (or force with `FAWAI_HEADLESS=1`).

## Architecture

A long-running local “observe → plan → act” avatar, with a cross-device session shell. Background coding / files are strong; desktop GUI grounding is still deepening. Complex goals can enter plan mode: orchestrator emits a graph, supervisor brains close each node, executor brains run step-level tools.

```text
Phone / browser PWA ──HTTP/WS──► session gateway ──► session hub
                                    │
                                    ▼
                             Supervisor
                    (create tasks / leases / arbitrate)
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
         worker A              worker B              … (decoupled)
              │                     │
              │              (plan mode) orchestrator graph
              │                     → supervisor children (hidden from sidebar)
              ▼                     ▼
     ┌─ observe ──► brain ──► control ─┐
     │  (screen / fs / tool digest)     │
     │  (route·delegate·plan-graph·reflect·gates)  (fs/shell/input) │
     └──────── observe again ◄──────────┘
              │
    knowledge: Skill / lore / shovels / task review & failure analysis → code
    ops: keeper / checkpoint / resume-from-step / runtime releases / syslog
```

| Layer | Does | Does not |
|:---|:---|:---|
| **Channel** | I/O, threads, attachments, voice, step stream, settings | Drive mouse/keyboard |
| **Task line** | Supervisor + workers; GUI lease serial; write-set arbitrate; checkpoints; complex-task plan graph (parent thread + node cards) | Cross-machine scheduling |
| **Brain** | Plan, capability pick, delegate, plan orchestrate/supervise, reflect / obs-reflect / step-repeat, compliance/answer gates | Touch devices |
| **Control** | Hard-run tools and input | Task orchestration |
| **Observe** | Environment → LLM-readable digest | Mutate system state |
| **Knowledge** | Skills, personal lore, script shovels, task review & failure analysis, on-demand patches | Auto-distill every task |

Learning loop (feeds self-evolution): mid-run **action reflect / observe reflect / step-repeat** corrects in place → optional **task review** after finish (mechanism-first full analysis → optimize checklist → backup then apply: Skill / lesson / code / rule / prompt) → **system log** / startup failures can one-click spawn a remediation task. See [`doc/功能/任务复盘/`](doc/功能/任务复盘/说明.md) and [分析规则](doc/功能/任务复盘/分析规则.md).

Capability suite: **C1 LLM / C2 complete-task / C4 self-evolution** are in place; **C3 autonomous GUI** narrow loop (TextEdit + AX) is supported; arbitrary Apps / Electron still later.

### Strengths

1. **Clean separation**: brain / control / observe + supervisor / workers; channel stays out of actuation; plan mode further splits orchestrator / supervisor / executor.
2. **Anti–fake-success**: capability registry, answer coverage, weak-write / visible-effect / compliance gates, necessary-path ledger, no shell bypass of fs.
3. **Background assistant works today**: coding loop, plan-mode graph scheduling with failed-node retry, checkpoint + resume-from-step, headless Linux for day-to-day repo work.
4. **Review & failure analysis drive self-learning**: task review finds mechanism roots (plan / observe / act / reflect / outcome), not just final-status text; fixes must generalize to similar goals and can land as Skill, lesson, code patch, rule, or prompt. Mid-run reflect / obs-reflect / step-repeat + syslog one-click analyze sit on the same evolution chain.
5. **Self-evolution with brakes**: blocked minimum core (rollback / keeper / gates); review requires “approve analysis → select apply” with backup first, no dirty auto-ratchet.
6. **Operable as a service**: keeper, `.env` overlay on relaunch, optional deferred hot-reload (off by default), port reclaim, runtime select-and-boot, one-click log / startup remediation, platform auto-detect.
7. **Docs match code**: issues → feature table → terminology; release discipline is explicit; mechanism contrasts live under `doc/分析/`.

### Next Todo

- [x] **C3 narrow GUI closed loop**: TextEdit + AX `obs_ax` / `ui_*` / UI verify (see feature「自主操控」)
- [x] **Screenshot grounding**: model settings choose VLM vs OCR; OCR defaults to `qwen3.5-ocr` → box table + `ui_click(ocrN)`
- [ ] **C3 deepen**: stronger Electron (Cursor/Chrome) grounding; arbitrary desktop; icon detectors
- [ ] **Align GUI with structured paths**: keep closing residual “coding strong / desktop weak” gaps
- [ ] **Gate observability**: unified gate ledger / hit reasons surfaced for cheaper debugging under stacked constraints
- [x] **Complex-task plan mode**: orchestrator graph auto-schedule, reorder/lease, failed-node retry, explicit full replan, in-chat digraph + node cards
- [x] **Script shovel lifecycle**: `shovel_search` + pre/post soft gates (catalog `fawai_shovels/`)
- [x] **Multi-display observe**: `displays` / `screen_target` / `app_focus` (real dual-monitor / Space still to verify)
- [ ] **Per-step cost & latency**: main→fast delegate landed (Web model route `fast`); keep trimming reflect / obs-reflect / observe LLM calls
- [ ] **Cancel & child-process lifecycle**: beyond cooperative stop, define hard-kill / reclaim boundaries for OS subprocesses
- [ ] **Memory & evolution quality**: human or auto quality bars for Skill/lore/shovel writes and review checklists; on-demand distill policy
- [ ] **Plan-mode drag-to-edit graph**: read-only digraph + machine revisions today; human drag of deps is optional later
- [ ] **On-device phone control**: after PC C3 bar, add adb / touch semantics (PWA is session-only today)
- [ ] **(Optional) multi-machine scheduling**: only if product needs throughput beyond single-host SQLite

## Doc map

| Path | Content |
|:---|:---|
| `doc/功能/` | Released / supported features and acceptance |
| `doc/议题/` | Discussion & principles (≠ auto-release) |
| `doc/分析/` | Mechanism contrasts & improvement notes (≠ release) |
| `doc/测试/` | Smoke / system / end-to-end test-task notes |
| `doc/版本/` | Weighted eval totals per release |
| `doc/术语与称呼索引.md` | Canonical names and tool ids |

## Run

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore          # personal lore KV + notes library
pip install -e "./fawai_lore[vector]"  # optional: sqlite-vec (then FAWAI_LORE_VECTOR defaults to auto/on)
pip install -e .                 # headless / coding·files·shell / gateway; macOS pulls GUI deps by platform marker
# pip install -e ".[gui]"        # Linux with DISPLAY: desktop capture & input
# pip install -e ".[browser]" && playwright install chromium  # optional: headless browser
cp .env.example .env   # system knobs; LLM credentials live in Web model settings DB; .env is gitignored
# default routes: text → qwen3.7-plus; multimodal → qwen3-vl-plus
# LLM / fast-model routes: configure in Web model settings (llm_settings.sqlite)
# optional: FAWAI_PLAN_MODE=0               # disable complex-task plan mode (default auto)
export FAWAI_DRY_RUN=1
.venv/bin/python -m fawai_avatar.llm.client        # smoke-test text route
.venv/bin/python -m fawai_avatar.demo_thinking     # deep thinking by role / profile
.venv/bin/python -m fawai_avatar.demo_llm_live     # live progress throttle (offline)
.venv/bin/python -m fawai_avatar.demo_smoke
.venv/bin/python -m fawai_avatar.demo_platform     # platform / headless gate
.venv/bin/python -m fawai_avatar.demo_browser      # browser tools (optional FAWAI_BROWSER_LIVE=1)
.venv/bin/python -m fawai_avatar.demo_fs_version   # background files + versions (no GUI)
.venv/bin/python -m fawai_avatar.demo_c2           # complete-task A/B/C/beyond
.venv/bin/python -m fawai_avatar.demo_clarification  # mid-run clarify need_input → resume
.venv/bin/python -m fawai_avatar.demo_obs_reflect  # observe-reflect after successful tools
.venv/bin/python -m fawai_avatar.demo_observe_select  # observe_kinds routing
.venv/bin/python -m fawai_avatar.demo_multiscreen  # multi-display displays / screen_target
.venv/bin/python -m fawai_avatar.demo_c3           # C3: gui profile / ui_* / obs_ax (optional FAWAI_C3_LIVE=1)
.venv/bin/python -m fawai_avatar.demo_c4           # self-evolution E1/E2
.venv/bin/python -m fawai_avatar.demo_lore         # fawai_lore KV+notes + lore sidecar P0
.venv/bin/python -m fawai_avatar.demo_code_index   # code index symbols/map
.venv/bin/python -m fawai_avatar.demo_coding_loop  # coding loop P0/P1
.venv/bin/python -m fawai_avatar.demo_py_session   # Python session: write/pip/run
.venv/bin/python -m fawai_avatar.demo_shovel_lifecycle  # script shovel: search + soft gates
FAWAI_BRAIN_MOCK=1 .venv/bin/python -m fawai_avatar.demo_plan_mode  # plan mode: enter / schedule / retry nodes / full replan
.venv/bin/python -m fawai_avatar.demo_resume_restart  # checkpoint resume after restart
.venv/bin/python -m fawai_avatar.demo_eval_priority  # version-eval priority improvement suite
# FAWAI_EVAL_CAPTURE=1 .venv/bin/python -m fawai_avatar.demo_eval_priority
```

Background actions (not gated by `FAWAI_DRY_RUN`): `fs_*` / `code_*` / `version_*` / `skill_*` / `lore_*` / `git_*` / `explore_*` / `shovel_search` / `shell_verify` / `py_*` / `diff_*`.  
Browser actions (gated by `FAWAI_DRY_RUN`, no foreground lease): `browser_*`.

## Linux headless

A desktop-less Linux server can run the gateway plus coding / files / shell; optional `.[browser]` for headless web. The process auto-detects OS and headless mode (or force with `FAWAI_HEADLESS=1`); the brain picks capabilities by platform—GUI / screen capture are unavailable.

```bash
pip install -e .
# optional tunnel: install a matching-arch frpc and set FAWAI_FRPC_BIN=/path/to/frpc
# repo bin/frpc is a macOS Mach-O and will be rejected on Linux
export FAWAI_DRY_RUN=0          # real file edits / shell
.venv/bin/python -m fawai_avatar.demo_platform
.venv/bin/python -m fawai_avatar.channel.lan_gateway
```

## Cross-device session (LAN PWA)

Start the session gateway on **macOS or Linux**; on the same Wi‑Fi, open the URL printed in the terminal and enter the passcode:

```bash
export FAWAI_DRY_RUN=1
.venv/bin/python -m fawai_avatar.channel.lan_gateway
# or: fawai-gateway / fawai-keeper
```

By default a **service keeper** wraps the process (auto-restart after crash or Web “Restart”; each relaunch overlays disk `.env` onto the child so model/system settings match the Web UI; **code hot restart is off by default**—enable on Web “Service & versions” or set `FAWAI_CODE_RELOAD=1`, deferred while a task is `running`/`blocked`; orphan port reclaim—disable with `FAWAI_PORT_RECLAIM=0`). Debug the gateway directly with `FAWAI_NO_KEEPER=1` (no hot reload / port reclaim).  
The Web **Service** panel can archive / manage **runtime versions**, view console logs, one-click repair startup errors, and start a chosen version.

Optional: `FAWAI_GATEWAY_PASSCODE` for a fixed passcode; `FAWAI_RELAY_URL` for the cloud relay skeleton.  
Public tunnel: set `FAWAI_FRP_SERVER_ADDR` / `FAWAI_FRP_REMOTE_PORT` (and optional `FAWAI_FRP_SERVER_PORT`) in `.env`; the gateway starts local `frpc` (`bin/frpc` or `FAWAI_FRPC_BIN`). Self-check: `.venv/bin/python -m fawai_avatar.channel.selfcheck`.
