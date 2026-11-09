# fawai-avatar v0.1.4 发布说明

**语言：** [English (default)](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.4/doc/%E7%89%88%E6%9C%AC/v0.1.4/RELEASE_NOTES.md) · 中文

| 项 | 内容 |
|:---|:---|
| 版本 | `0.1.4`（`pyproject.toml`） |
| 日期 | 2026-11-09 |
| 相对 | [v0.1.3 评测](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.4/doc/%E7%89%88%E6%9C%AC/v0.1.3/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) |
| 评测 | [评测结果](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.4/doc/%E7%89%88%E6%9C%AC/v0.1.4/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) — **81.3 / A**（仓内路径：`doc/版本/v0.1.4/评测结果.md`） |

面向 PC / 手机的操作分身：本版在 **0.1.3 安装包与记忆检索** 之上，补齐**执行模式面**（极速 / 全力 / 分档 / 自动偏好）、**Skill·MCP 管理**、**成败价值台账**、**任务产物交付**，并改进安装平滑升级。

## Highlights

1. **模式面板**：极速（关深度思考、跳过观察反省、禁计划模式）、全力（必要路径与新鲜度优先、可修复失败禁止早退）、步级分档（`reflect` / `brief` / `act`）、四项自动/开/关与首轮分析回写。
2. **Skill / MCP 面**：PWA「技能工具」三表；stdio MCP 发现后以 `mcp__server__tool` 进能力清单并执行。
3. **价值台账与成败极性**：成功 / 失败价值卡过门写 lore；Skill 可参考 / 避坑分栏；铲子运行成败维进检索。
4. **任务产物交付**：未指定项目默认 `sessions/{task_id}`；附件预览 / 另存；`/media` Range。
5. **安装升级**：浏览器 Install 页；默认平滑升级保留 `data/` 与 `.env`；覆盖安装需确认。

## What's New

### 执行模式

- `brain/fast.py`、`brain/effort.py`、`brain/step_run.py`、`brain/mode_auto.py`
- compose `#modePop`：极速 / 全力 / 计划 / 分档（+ 自动偏好）
- 冒烟：`demo_fast`、`demo_effort`、`demo_step_run`、`demo_mode_auto`

### 扩展面

- `fawai_avatar/mcp/` + `/api/mcp/*`
- Skill 管理 API + PWA `#extPage`
- 冒烟：`demo_mcp`、`demo_skills_admin`

### 记忆与进化

- 成功 / 失败价值台账（`success_ledger`、`failure_ledger`、`ledger_gate`）
- Skill / 铲子检索成败极性
- 任务蒸馏路径保留并收紧
- 冒烟：`demo_success_ledger`、`demo_failure_ledger`、`demo_outcome_polarity`、`demo_task_distill`

### 通道与交付

- `channel/file_deliver.py` + media Range
- 计划会话时序 / 取消打断 / 进度体验修复
- Web 出站脱敏细化

### 打包

- `packaging/install.py`：本机 Install 页；平滑 vs 覆盖
- 打包 i18n / README 更新

## Breaking / 行为变化

- 开极速：强制关深度思考、跳过观察反省进度卡；期间禁止计划模式
- 模式偏好默认 **自动**（首轮分析可回写自动项）
- 安装默认 **平滑升级**（保留 data）；覆盖安装确认后清空 `data/` / `.env`

## Install（开发态）

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore
pip install -e .
```

## Install（消费包）

从 [GitHub Releases v0.1.4](https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.4) 下载对应平台压缩包，解压后运行 **Install**（默认平滑升级）或 **Start**（见包内 `INSTALL.zh-CN.txt` / `INSTALL.en.txt`）。

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
- 真 LLM「记一笔」类端到端未本轮强制重测

## GitHub

- 标签 / 提交：`v0.1.4`（`main`）
- Release：https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.4
- Actions `release-packages` 跑完后上传 linux / windows / darwin 矩阵包
