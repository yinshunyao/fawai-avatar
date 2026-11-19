# fawai-avatar v0.1.5 Release Notes

**Language:** English (default) · [中文](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.5/doc/%E7%89%88%E6%9C%AC/v0.1.5/RELEASE_NOTES.zh-CN.md)

| Item | Value |
|:---|:---|
| Version | `0.1.5` (`pyproject.toml`) |
| Date | 2026-11-19 |
| Previous | [v0.1.4 eval](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.5/doc/%E7%89%88%E6%9C%AC/v0.1.4/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) |
| Evaluation | [Evaluation report / 评测结果](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.5/doc/%E7%89%88%E6%9C%AC/v0.1.5/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) — **82.3 / A** (path: `doc/版本/v0.1.5/评测结果.md`) |

PC / phone operation avatar. On top of **0.1.4** (execution modes, Skill/MCP, ledgers, deliverables), this release adds **Cursor Agent** (local SDK, PWA page, compose + brain delegate), **diff / artifact browsing** in the PWA, and a **numbered `doc/` layout** (function / analysis / issues aligned by domain).

## Highlights

1. **Cursor Agent:** Side-nav page; browser login via `@cursor/sdk`; settings in `cursor_agent_settings.sqlite`; compose `cursor_agent` runtime with Cursor-style step cards; brain `tier=cursor` delegate; optional `pip install -e '.[cursor]'`.
2. **Diff & deliverables UX:** Pending diff store, hunk fold, full-text lazy load, change list, confirm actions; markdown render + office HTML/PDF **projection** for cross-device read-only preview.
3. **Channel polish:** Public media helpers; file projection APIs; task timeline replay for Cursor runs (`meta.steps` + `run_id`).
4. **Documentation:** Flat feature/analysis/issue trees reorganized into `NN-domain/` numbering; master tables updated; AgentBench public-benchmark notes under `doc/测试/公开基准/`.

## What's New

### Cursor Agent

- `fawai_avatar/cursor_agent/` — auth, settings, manager, runtime, session progress
- PWA `#cursorAgentPage`; `/api/cursor-agent/*` (login, models, sessions)
- `channel/hub.py` — `meta.runtime=cursor_agent`; resume vs new thread rules
- `brain/delegate.py` — `tier=cursor`; `native_tools` + `runtime/loop.py`
- Smoke: `python -m fawai_avatar.demo_cursor_agent`

### Workspace & delivery UI

- `workspace/{pending_diff,diff_store,dir_list}.py`
- `channel/{file_projection,media_public}.py`
- `demo_diff_browse` smoke

### Docs & rules

- `doc/功能|分析|议题|问题` → `00-横切` … `08-运行与分发`
- README / AGENTS cross-links refreshed

## Breaking / behavior changes

- Large doc path moves only — code imports unchanged; update bookmarks to numbered paths.
- Cursor Agent requires Node ≥22 (or env `CURSOR_API_KEY` / `FAWAI_CURSOR_API_KEY`) when enabled; without optional extra, feature reports `not_installed` and other paths stay up.

## Install (developer)

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore
pip install -e .
pip install -e '.[cursor]'   # optional Cursor Agent SDK
```

## Install (consumer package)

Download the matching archive from [GitHub Releases](https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.5), extract, run **Install** (smooth upgrade default) or **Start** (`INSTALL.en.txt` / `INSTALL.zh-CN.txt`).

Build locally:

```bash
python3 packaging/build_release.py --target auto
bash packaging/publish_github_release.sh   # needs gh auth
```

## Not in this release / known gaps

- Live Cursor API / same-LAN phone PWA with real Cursor login not forced in eval
- macOS notarization / code signing not automated (P1)
- Windows GUI/AX not at macOS parity
- Playwright Chromium not prebundled in the install package

## GitHub

- Tag / commit: `v0.1.5` (`main`)
- Release: https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.5
- Uploaded: platform archives under `dist/release/` (+ sha256); CI `release-packages` may add linux / windows matrix assets