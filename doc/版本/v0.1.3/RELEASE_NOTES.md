# fawai-avatar v0.1.3 Release Notes

| 项 | 内容 |
|:---|:---|
| 版本 | `0.1.3`（`pyproject.toml`） |
| 日期 | 2026-11-08 |
| 相对 | [v0.1.2](../v0.1.2/评测结果.md) |
| 评测 | [评测结果.md](./评测结果.md) — **79.5 / B** |

面向 PC / 手机的操作分身：本版在 **0.1.2 计划模式与 C3 窄场景** 之上，补齐**消费安装包**、**记忆/全文检索面**、**任务内上下文召回**，并加固 Web 出站脱敏与复盘进度体验。

## Highlights

1. **安装器（消费分发）**：私有 Python 前缀包（macOS / Linux / Windows）；Start 起网关后本机默认打开 `http://127.0.0.1:<port>/`；启动器与安装说明 **中/英**；GitHub Actions + Release 上传。
2. **个人知识库检索面**：`lore_search` 按 `scopes` / 索引配置汇合工作区正文 FTS、文件名索引、笔记 FTS 等；记忆配置页与索引重建进度。
3. **任务内上下文查询**：`task_context_search` / `task_context_get`，同任务 Recall；终态追问与检查点并集。
4. **Web 出站脱敏**：会话/中继出站经统一 redact；规划侧仍可读凭据（产品约定）。
5. **复盘与会话**：复盘进度与普通任务对齐改进；PWA 记忆/索引相关界面与 i18n 补强。

## What's New

### 分发与运维

- `packaging/build_release.py`：PBS 运行时 + `app/` + `deps/` + Start 包装
- `packaging/launcher.py` + `i18n/{zh-CN,en}.json`
- `.github/workflows/release-packages.yml`：`v*` 标签打三端包并上传 Release
- `packaging/publish_github_release.sh`：本机工件手动上传

### 记忆与检索

- `knowledge_search` / `memory_index`（正文 FTS、文件名、监视增量）
- 知识库笔记 FTS（`fawai_lore/fts.py`）
- 记忆配置：索引策略、工具测试、重建进度 API

### 规划与任务

- 任务内上下文工具与提示约束
- 检索命中观察渲染同时认 grep / summary / snippet

### 通道与安全

- `message_dict_for_client` / 出站 redact 规则扩展
- 会话网关与 lore API、系统配置联动记忆设置

## Breaking / 行为变化

- `lore_search` 主路径不再以旁路短 KV 为主搜；缺省 scopes 时按已开索引通道混合（见功能「个人知识库」）
- 旁路入库默认关；记忆页待确认入口 Web 侧收缩（后端能力保留）
- 消费用户可走 GitHub Release 安装包；开发态仍可用 README editable 安装

## Install（开发态）

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore
pip install -e .
```

## Install（消费包）

从 [GitHub Releases](https://github.com/yinshunyao/fawai-avatar/releases) 下载对应平台压缩包，解压后运行 Start（见包内 `INSTALL.zh-CN.txt` / `INSTALL.en.txt`）。

本机构建：

```bash
python3 packaging/build_release.py --target auto
bash packaging/publish_github_release.sh   # 需 gh auth
```

## Not in this release / Known gaps

- macOS 公证 / 代码签名未自动化（P1）
- Windows GUI/AX 未与 macOS 同等承诺
- 真机同网手机 PWA、`FAWAI_C3_LIVE` / `FAWAI_C2_LIVE` 真模型端到端未本轮强制
- Playwright Chromium 未预置进安装包

## GitHub

- 标签 / 提交：`v0.1.3`（`main`）
- Release：https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.3
- 已上传：`fawai-avatar-0.1.3-darwin-arm64.tar.gz`（+ sha256）
- Actions `release-packages` 将补齐 linux / windows / darwin-x64（跑完后自动上传）
