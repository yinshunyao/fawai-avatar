# fawai-avatar

A PC / phone **operation avatar**: understand the goal → observe the environment → plan the next step → run tools / input → verify again. Readable docs live in `doc/`; implementation in `fawai_avatar/`; personal lore in `fawai_lore/`.

中文说明见 [README.zh.md](README.zh.md).

## What it is

Not a chat-only bot, and not an IDE plugin that only edits code. It is a long-running assistant on your machine (or a headless server): you send tasks from a LAN PWA (or a public tunnel); it works **locally**—workspace files, shell checks, code search, skills and memory. With a desktop, it can also capture the screen and drive mouse/keyboard (GUI grounding is still evolving).

| Role | Job |
|:---|:---|
| **Brain** | Plan the next step from goal + observation; never touches devices directly |
| **Control** | Run tools / scripts / input; hard gates on leases and paths |
| **Observe** | Screen, filesystem state, tool-result digests → feed planning |
| **Task line** | One supervisor + many decoupled worker tasks; GUI serial, background parallel |
| **Knowledge line** | Skill library, personal lore (KV/notes), task review → code changes |

Single-task loop: `observe → brain → control → observe again`. The supervisor takes user requests and arbitrates resources; each worker keeps its own trace and checkpoint (resume after process restart).

## Assistant capabilities (supported)

Source of truth: [`doc/功能/【总表】功能一览.md`](doc/功能/【总表】功能一览.md). Grouped by what you can ask it to do.

### Conversation & cross-device

- **Session gateway + PWA**: local HTTP/WS; open the passcode page on phone (same Wi‑Fi); optional frpc tunnel and cloud-relay skeleton
- **Task threads**: one conversation per task; new task / follow-up / soft append while running; paste image as attachment then send
- **Voice input**: mic → ASR into the compose box
- **UI zh/en**: switch anytime; separate Model / System / Service panels

### Understanding & planning

- **LLM access**: OpenAI-compatible; text / multimodal routing; Web model settings
- **Capability registry**: only registered tools; file goals must use `fs_*` (no GUI pretending to write files)
- **Prompt profiles**: trim system prompt / images by round purpose (`vision` / `fs` / `gui` / `full`)
- **Mid-run clarification**: try hard by default; `need_input` (optionally multiple-choice) only for high-cost ambiguity, then resume
- **Answer-coverage gate**: for Q&A / lookup / hardware inventory, bans “summarized and done” fake success

### Coding & files (background; not gated by `FAWAI_DRY_RUN`)

- **Workspace FS**: expandable read roots; writes limited to work_root; no shell bypass of fs
- **Precise patch / multi-file write set / session Diff**; `code_check`; allowlisted `shell_verify`
- **Code index + explore**: multi-language symbols / repo map; parallel read-only search
- **Git**: status / diff / commit (no auto push)
- **Gates**: explore-too-long → force write; weak-write block; UI goals must state visible effects; compliance (rule registry + layered-write doc refs)
- **Version snapshots**: optional auto-snapshot before write; query / rollback

### Desktop observe & control (needs `.[gui]`; auto-off when headless)

- **Observe routing**: no default screenshot on step 0; LLM picks screen / fs / tool digest; large logs compressed
- **Control**: input + tools; global **foreground lease** (serial)
- Widget-level GUI grounding (C3) is still evolving—not a promise to click any App perfectly

### Memory, evolution & ops

- **Personal lore** (`fawai_lore`): KV + notes + sidecar buffer; PWA memory page enable / disable / delete
- **Skills**: searchable injection into planning; quality-gated writes
- **Task review**: instruction → full analysis → checklist → backup then apply; joint review supported
- **Self-evolution writes**: most of the tree writable; **blocked** minimum core (rollback / keeper / gates). Cursor / human disk edits unrestricted
- **Service keeper**: auto-restart on crash; code hot-reload (deferred while tasks run); runtime version archive / select-and-boot
- **System log**: subscribed disk log; one-click failure analysis → remediation task
- **Task stats**: tokens, wall time, rounds / tools; copy task id from sidebar

### Platforms

| Environment | What runs |
|:---|:---|
| **macOS + desktop** | Gateway + coding/files/shell + optional screen/input |
| **Linux headless** | Gateway + coding/files/shell; no GUI/capture |
| **Linux with DISPLAY** | Optional `.[gui]` for desktop abilities |

OS / headless are auto-detected (or force with `FAWAI_HEADLESS=1`).

## Doc map

| Path | Content |
|:---|:---|
| `doc/功能/` | Released / supported features and acceptance |
| `doc/议题/` | Discussion & principles (≠ auto-release) |
| `doc/测试/` | Reproducible test-task notes |
| `doc/版本/` | Weighted eval totals per release |
| `doc/术语与称呼索引.md` | Canonical names and tool ids |

## Run

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore          # personal lore KV + notes library
pip install -e "./fawai_lore[vector]"  # optional: sqlite-vec (then FAWAI_LORE_VECTOR defaults to auto/on)
pip install -e .                 # headless / coding·files·shell / gateway
# pip install -e ".[gui]"        # optional: desktop capture & input (macOS or Linux with DISPLAY)
cp .env.example .env   # set FAWAI_LLM_API_KEY; .env is gitignored
# default routes: text → qwen3.7-plus; multimodal → qwen3-vl-plus
export FAWAI_DRY_RUN=1
.venv/bin/python -m fawai_avatar.llm.client        # smoke-test text route
.venv/bin/python -m fawai_avatar.demo_smoke
.venv/bin/python -m fawai_avatar.demo_platform     # platform / headless gate
.venv/bin/python -m fawai_avatar.demo_fs_version   # background files + versions (no GUI)
.venv/bin/python -m fawai_avatar.demo_c2           # complete-task A/B/C/beyond
.venv/bin/python -m fawai_avatar.demo_clarification  # mid-run clarify need_input → resume
.venv/bin/python -m fawai_avatar.demo_c4           # self-evolution E1/E2
.venv/bin/python -m fawai_avatar.demo_lore         # fawai_lore KV+notes + lore sidecar P0
.venv/bin/python -m fawai_avatar.demo_code_index   # code index symbols/map
.venv/bin/python -m fawai_avatar.demo_coding_loop  # coding loop P0/P1
.venv/bin/python -m fawai_avatar.demo_eval_priority  # version-eval priority improvement suite
# FAWAI_EVAL_CAPTURE=1 .venv/bin/python -m fawai_avatar.demo_eval_priority
```

Background actions (not gated by `FAWAI_DRY_RUN`): `fs_*` / `code_*` / `version_*` / `skill_*` / `lore_*` / `git_*` / `explore_*` / `shell_verify` / `diff_*`.

## Linux headless

A desktop-less Linux server can run the gateway plus coding / files / shell. The process auto-detects OS and headless mode (or force with `FAWAI_HEADLESS=1`); the brain picks capabilities by platform—GUI / screen capture are unavailable.

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

By default a **service keeper** wraps the process (auto-restart after crash or Web “Restart”; **code file changes** also restart the gateway, deferred while a task is `running`/`blocked`—disable with `FAWAI_CODE_RELOAD=0`). Debug the gateway directly with `FAWAI_NO_KEEPER=1` (no hot reload).  
The Web **Service** panel can archive / manage **runtime versions** and start a chosen version.

Optional: `FAWAI_GATEWAY_PASSCODE` for a fixed passcode; `FAWAI_RELAY_URL` for the cloud relay skeleton.  
Public tunnel: set `FAWAI_FRP_SERVER_ADDR` / `FAWAI_FRP_REMOTE_PORT` (and optional `FAWAI_FRP_SERVER_PORT`) in `.env`; the gateway starts local `frpc` (`bin/frpc` or `FAWAI_FRPC_BIN`). Self-check: `.venv/bin/python -m fawai_avatar.channel.selfcheck`.
