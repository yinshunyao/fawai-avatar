# 安装包构建与 GitHub Release

给普通用户的**私有 Python 前缀**安装包：解压 → 运行 Start → 本机浏览器打开 `http://127.0.0.1:7980/`。

English: [README.en.md](README.en.md)

## 产物布局

```text
fawai-avatar-{version}-{target}/
  runtime/     # python-build-standalone
  app/         # fawai_avatar + fawai_lore（可写）
  deps/        # 第三方 wheel（pip --target）
  i18n/        # 启动器中/英
  launcher.py
  Start.sh / Start.command / Start.bat
  INSTALL.zh-CN.txt / INSTALL.en.txt
  data/
```

| target | 说明 |
|:---|:---|
| `darwin-arm64` | Apple Silicon |
| `darwin-x64` | Intel Mac |
| `linux-x64` | Linux x86_64 |
| `windows-x64` | Windows x86_64 |

## 本机构建

须在与目标**同类 OS** 上构建（否则原生 wheel 可能不对）。macOS 本机示例：

```bash
cd fawai-avatar
python3 packaging/build_release.py --target auto
# 产物：dist/release/fawai-avatar-*-*.tar.gz 与 .sha256
```

常用参数：

- `--target darwin-arm64|darwin-x64|linux-x64|windows-x64|auto`
- `--skip-archive` 只出目录、不打包
- `--cache-dir` PBS 下载缓存（默认 `dist/cache/python-build-standalone`）

## 启动器语言（中/英）

| 方式 | 示例 |
|:---|:---|
| 环境变量 | `FAWAI_LANG=en ./Start.sh` |
| 参数 | `./Start.sh --lang zh-CN` |
| 默认 | 读 `data/install_lang.txt`，否则按系统 `LANG` 猜 |

PWA 内界面语言仍用产品「界面多语言」，与启动器文案独立。

## 发布到 GitHub Release

1.  bump `pyproject.toml` version，打标签并推送：`git tag v0.1.3 && git push origin v0.1.3`
2. 推送 `v*` 标签会触发 [`.github/workflows/release-packages.yml`](../.github/workflows/release-packages.yml) 在三端 runner 构建并创建 Release、上传工件。
3. 或本机构建后手动上传：

```bash
bash packaging/publish_github_release.sh
# TAG=v0.1.3 bash packaging/publish_github_release.sh --draft
```

需要已 `gh auth login`，仓库 remote 为 `yinshunyao/fawai-avatar`。

## 用户怎么用

1. 从 GitHub Releases 下载对应系统的 `.tar.gz` / `.zip`
2. 解压后按 `START_HERE.txt` / `INSTALL.*.txt` 启动
3. 本机自动打开 127；手机自行打开局域网 IP:端口
