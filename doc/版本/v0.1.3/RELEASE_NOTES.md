# fawai-avatar v0.1.3 Release Notes

**Language:** English (default) · [中文](./RELEASE_NOTES.zh-CN.md)  
**On GitHub:** [中文说明](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.3/doc/%E7%89%88%E6%9C%AC/v0.1.3/RELEASE_NOTES.zh-CN.md) · [Evaluation report (zh)](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.3/doc/%E7%89%88%E6%9C%AC/v0.1.3/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md)

| Item | Value |
|:---|:---|
| Version | `0.1.3` (`pyproject.toml`) |
| Date | 2026-11-08 |
| Previous | [v0.1.2 eval](../v0.1.2/评测结果.md) |
| Evaluation | [评测结果.md](./评测结果.md) (Chinese report) — **79.5 / B** · [absolute link](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.3/doc/%E7%89%88%E6%9C%AC/v0.1.3/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) |

PC / phone operation avatar. On top of **0.1.2** (plan mode + narrow C3 GUI), this release adds **consumer install packages**, a **memory / full-text search surface**, **same-task context recall**, plus stronger Web egress redaction and review progress UX.

## Highlights

1. **Installer (consumer distribution):** private Python prefix for macOS / Linux / Windows; Start launches the gateway and opens `http://127.0.0.1:<port>/` locally; launcher + install notes in **zh-CN / en**; GitHub Actions + Release upload.
2. **Personal knowledge search:** `lore_search` merges workspace content FTS, filename index, notes FTS by `scopes` / index config; memory settings page + rebuild progress.
3. **In-task context tools:** `task_context_search` / `task_context_get` for same-task recall; final-state follow-up unions with checkpoints.
4. **Web egress redaction:** session / relay outbound goes through unified redact; planner side may still see credentials (product rule).
5. **Review & session:** review progress aligned with normal tasks; PWA memory/index UI + i18n.

## What's New

### Distribution & ops

- `packaging/build_release.py` — PBS runtime + `app/` + `deps/` + Start wrappers
- `packaging/launcher.py` + `i18n/{zh-CN,en}.json`
- `.github/workflows/release-packages.yml` — `v*` tag builds three platforms and uploads
- `packaging/publish_github_release.sh` — manual upload of local artifacts

### Memory & retrieval

- `knowledge_search` / `memory_index` (content FTS, filenames, watch increments)
- Lore notes FTS (`fawai_lore/fts.py`)
- Memory settings: index policy, tool tests, rebuild progress API

### Planning & tasks

- In-task context tools and prompt constraints
- Hit rendering accepts grep / summary / snippet shapes

### Channel & safety

- Extended `message_dict_for_client` / egress redact
- Gateway + lore API + system settings wired to memory settings

## Breaking / behavior changes

- `lore_search` no longer treats sidecar short KV as the main search; missing scopes mixes enabled index channels (see feature「个人知识库」)
- Sidecar ingest default off; Web pending-confirm entry shrunk (backend kept)
- Consumers can use GitHub Release packages; developers may still use README editable install

## Install (developer)

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore
pip install -e .
```

## Install (consumer package)

Download the matching archive from [GitHub Releases](https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.3), extract, run Start (`INSTALL.en.txt` / `INSTALL.zh-CN.txt`).

Build locally:

```bash
python3 packaging/build_release.py --target auto
bash packaging/publish_github_release.sh   # needs gh auth
```

## Not in this release / known gaps

- macOS notarization / code signing not automated (P1)
- Windows GUI/AX not promised at macOS parity
- Real same-LAN phone PWA / `FAWAI_C3_LIVE` / `FAWAI_C2_LIVE` not forced this round
- Playwright Chromium not prebundled in the install package

## GitHub

- Tag / commit: `v0.1.3` (`main`)
- Release: https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.3
- Uploaded: `fawai-avatar-0.1.3-darwin-arm64.tar.gz` (+ sha256)
- Actions `release-packages` fills linux / windows / darwin-x64 when the matrix finishes
