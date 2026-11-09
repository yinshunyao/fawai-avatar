# fawai-avatar v0.1.4 Release Notes

**Language:** English (default) · [中文](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.4/doc/%E7%89%88%E6%9C%AC/v0.1.4/RELEASE_NOTES.zh-CN.md)

| Item | Value |
|:---|:---|
| Version | `0.1.4` (`pyproject.toml`) |
| Date | 2026-11-09 |
| Previous | [v0.1.3 eval](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.4/doc/%E7%89%88%E6%9C%AC/v0.1.3/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) |
| Evaluation | [Evaluation report / 评测结果](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.4/doc/%E7%89%88%E6%9C%AC/v0.1.4/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) — **81.3 / A** (path: `doc/版本/v0.1.4/评测结果.md`) |

PC / phone operation avatar. On top of **0.1.3** (installer + memory search + in-task recall), this release adds **execution mode controls** (Fast / Effort / step tiers / auto prefs), **Skill·MCP admin**, **success/failure value ledgers**, **task file delivery**, plus smoother install upgrades.

## Highlights

1. **Mode panel:** Fast (skip deep think / obs-reflect; block plan mode), Effort (prefer necessary paths; no early exit on recoverable fails), step tiers (`reflect` / `brief` / `act`), and per-item auto/on/off with first-turn analysis write-back.
2. **Skill & MCP surface:** PWA「技能工具」— Skill / MCP / shovel tables; stdio MCP → `mcp__server__tool` in the capability list.
3. **Value ledgers & polarity:** success / failure cards gated into lore; Skill imitate/avoid columns; shovel outcome rates in search.
4. **Task deliverables:** default `sessions/{task_id}`; attachment preview / save-as; `/media` Range.
5. **Installer upgrade UX:** browser Install page; smooth upgrade keeps `data/` + `.env` by default; overwrite is explicit.

## What's New

### Execution modes

- `brain/fast.py`, `brain/effort.py`, `brain/step_run.py`, `brain/mode_auto.py`
- Compose `#modePop`: Fast / Effort / plan / step-tier (+ auto prefs)
- Demos: `demo_fast`, `demo_effort`, `demo_step_run`, `demo_mode_auto`

### Extensibility

- `fawai_avatar/mcp/` + `/api/mcp/*`
- Skill admin APIs + PWA `#extPage`
- Demos: `demo_mcp`, `demo_skills_admin`

### Memory & evolution

- Success / failure ledgers (`success_ledger`, `failure_ledger`, `ledger_gate`)
- Outcome polarity for Skill / shovel search
- Task distill path retained / tightened
- Demos: `demo_success_ledger`, `demo_failure_ledger`, `demo_outcome_polarity`, `demo_task_distill`

### Channel & delivery

- `channel/file_deliver.py` + media Range
- Plan-session ordering / cancel interrupt / progress UX fixes
- Web egress redact refinements

### Packaging

- `packaging/install.py` — local Install page; smooth vs overwrite
- Packaging i18n / README updates

## Breaking / behavior changes

- Fast on forces thinking off and skips obs-reflect progress cards; plan mode blocked while Fast is on
- Mode prefs default to **auto** (first-turn analysis may rewrite auto items)
- Install default is **smooth upgrade** (keeps data); overwrite wipes `data/` / `.env` after confirm

## Install (developer)

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore
pip install -e .
```

## Install (consumer package)

Download the matching archive from [GitHub Releases](https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.4), extract, run **Install** (smooth upgrade default) or **Start** (`INSTALL.en.txt` / `INSTALL.zh-CN.txt`).

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
- Live LLM “write a note” end-to-end not re-forced this round

## GitHub

- Tag / commit: `v0.1.4` (`main`)
- Release: https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.4
- Actions `release-packages` uploads linux / windows / darwin matrix when finished
