# fawai-avatar

PC / phone operation avatar. Readable docs live in `doc/`; implementation in `fawai_avatar/`.

中文说明见 [README.zh.md](README.zh.md).

## Run

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore          # personal lore KV + notes library
pip install -e "./fawai_lore[vector]"  # optional: sqlite-vec note search (or pip install sqlite-vec -i https://pypi.org/simple/)
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

By default a **service keeper** wraps the process (auto-restart after crash or Web “Restart”; **code file changes** also restart the gateway—disable with `FAWAI_CODE_RELOAD=0`). Debug the gateway directly with `FAWAI_NO_KEEPER=1` (no hot reload).  
The Web **Service** panel can archive / manage **runtime versions** and start a chosen version.

Optional: `FAWAI_GATEWAY_PASSCODE` for a fixed passcode; `FAWAI_RELAY_URL` for the cloud relay skeleton.  
Public tunnel: set `FAWAI_FRP_SERVER_ADDR` / `FAWAI_FRP_REMOTE_PORT` (and optional `FAWAI_FRP_SERVER_PORT`) in `.env`; the gateway starts local `frpc` (`bin/frpc` or `FAWAI_FRPC_BIN`). Self-check: `.venv/bin/python -m fawai_avatar.channel.selfcheck`.

Release gates and acceptance live under `doc/功能/`. Test tasks under `doc/测试/`. Weighted eval totals per version under `doc/版本/`.
