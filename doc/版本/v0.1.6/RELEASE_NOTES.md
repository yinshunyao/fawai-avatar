# fawai-avatar v0.1.6 Release Notes

**Language:** English (default) · [中文](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.6/doc/%E7%89%88%E6%9C%AC/v0.1.6/RELEASE_NOTES.zh-CN.md)

| Item | Value |
|:---|:---|
| Version | `0.1.6` (`pyproject.toml`) |
| Date | 2026-11-22 |
| Previous | [v0.1.5 eval](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.6/doc/%E7%89%88%E6%9C%AC/v0.1.5/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) |
| Evaluation | [Evaluation report / 评测结果](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.6/doc/%E7%89%88%E6%9C%AC/v0.1.6/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) — **84.0 / A** (path: `doc/版本/v0.1.6/评测结果.md`) |

PC / phone operation avatar. On top of **0.1.5** (Cursor Agent, diff/deliverables, numbered docs), this release makes **execution config task-scoped**, enforces **one conversation = one task**, and fixes **Cursor multi-turn resume** so follow-ups stay on the same thread.

## Highlights

1. **Task-level execution config:** Open a task and the compose panel mirrors that task’s prefs / Cursor flag; while running, toggles write `POST /api/tasks/{id}/exec-config` into task meta (ended tasks do not rewrite history).
2. **One conversation, one task:** Follow-up / running-append / plan-append no longer LLM-split into sibling tasks; same thread continues or merges.
3. **Cursor Agent multi-turn:** List/detail expose `exec_config`; PWA recognizes Cursor via `exec_config` + `meta`; resume same `task_id` / `agent_id` instead of spawning a new card after refresh.
4. **Runtime hardening:** Cursor Node discovery (vendor bridge / nvm ≥22 / `FAWAI_CURSOR_NODE`); optional `FAWAI_SKIP_STARTUP_PERMISSIONS` to avoid macOS permission-prompt SIGSEGV; hub same-loop publish without deadlock; Cursor turn failures surface as task fail + error bubble.

## What's New

### Execution config (task-scoped)

- `brain/mode_auto.py` — `public_exec_config`, `patch_exec_config_from_body`, `is_cursor_agent_meta`
- `channel/lan_gateway.py` — task list/detail `exec_config`; `POST /api/tasks/{id}/exec-config`
- PWA — open-task sync, in-flight persist, global last-used only when no focus

### Session routing

- `brain/task_route.py` — policy continue / apply only
- `channel/hub.py` — no sibling create on append; plan `new_task` coerced to soft merge

### Cursor Agent

- Identity from `exec_config.runtime` / `cursor_agent` as well as `meta.runtime`
- Opening Cursor from a fawai thread may set `related_task_id`
- Auth: prefer SDK vendor Node; pre-resolve Node into env at gateway start

### Platform

- `FAWAI_SKIP_STARTUP_PERMISSIONS=1` — status-only, no dialogs
- `permissions_mac.py` / `lan_gateway.py` startup path

## Breaking / behavior changes

- Auto “split to new task” on follow-up is **removed**; start a new thread from home if you need a separate goal.
- `exec-config` API does **not** rewrite `runtime` (cannot turn a normal task into Cursor Agent via that endpoint).
- Ended tasks reject exec-config writes (409); UI only updates global last-used prefs.

## Install (developer)

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore
pip install -e .
pip install -e '.[cursor]'   # optional Cursor Agent SDK
```

## Install (consumer package)

Download the matching archive from [GitHub Releases](https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.6), extract, run **Install** (smooth upgrade default) or **Start** (`INSTALL.en.txt` / `INSTALL.zh-CN.txt`).

Build locally:

```bash
python3 packaging/build_release.py --target auto
bash packaging/publish_github_release.sh   # needs gh auth
```

## Not in this release / known gaps

- Live Cursor login + long multi-turn not forced in eval
- Same-LAN phone PWA not retested this round
- macOS notarization / Windows GUI parity / prebundled Playwright still open
- Startup permission SIGSEGV root cause not fully eliminated (skip env is a workaround)

## GitHub

- Tag / commit: `v0.1.6` (`main`)
- Release: https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.6
- Uploaded: platform archives via CI `release-packages` (and/or `dist/release/` + sha256)
