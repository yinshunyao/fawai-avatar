# Install prefix build & GitHub Releases

Consumer packages embed a **private Python runtime**: extract → run Start → local browser opens `http://127.0.0.1:7980/`.

中文：[README.md](README.md)

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

1. Bump version, tag, push: `git tag v0.1.3 && git push origin v0.1.3`
2. Tag `v*` runs `.github/workflows/release-packages.yml` (matrix build + upload).
3. Or upload local archives:

```bash
bash packaging/publish_github_release.sh
```

Requires `gh auth login`.
