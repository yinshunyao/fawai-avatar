# Install prefix build & GitHub Releases

Consumer packages embed a **private Python runtime**: extract → double-click **Install** (local browser install page; default smooth upgrade keeps `data/`) → run **Start** from the install dir → `http://127.0.0.1:7980/`.

中文：[README.md](README.md)

Upgrade (non-interactive):

```bash
./Install.sh --target ~/FawaiAvatar --mode smooth --yes
./Install.sh --target ~/FawaiAvatar --mode overwrite --yes   # wipes data
```


## Layout

See [README.md](README.md) for the directory tree and target table.

## Build on this machine

Build on an OS matching the target (native wheels). Example on macOS:

```bash
cd fawai-avatar
python3 packaging/build_release.py --target auto
```

## Launcher language (zh-CN / en)

| Method | Example |
|:---|:---|
| Env | `FAWAI_LANG=zh-CN ./Start.sh` |
| Flag | `./Start.sh --lang en` |
| Default | `data/install_lang.txt`, else guess from `LANG` |

PWA UI language is separate (in-product i18n).

## Publish to GitHub Releases

Release discipline: global Notes + About in ai-company `.cursor/rules/rules-github-release.mdc`; this repo’s paths in `.cursor/rules/rules-github-release.mdc` and `doc/版本/AGENTS.md`.

1. Bump `pyproject.toml` version; finish `doc/版本/vX.Y.Z/RELEASE_NOTES.md` (EN) + eval; check and update GitHub **About** if needed
2. Tag and push: `git tag v0.1.4 && git push origin v0.1.4`
3. Tag `v*` runs `.github/workflows/release-packages.yml` (matrix build + upload).
4. Or upload local archives:

```bash
bash packaging/publish_github_release.sh
# TAG=v0.1.4 bash packaging/publish_github_release.sh --draft
```

Requires `gh auth login`. Release body uses English Notes; eval links must be absolute blob URLs.
