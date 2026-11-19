# fawai-avatar v0.1.5 发布说明

**语言：** [English (default)](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.5/doc/%E7%89%88%E6%9C%AC/v0.1.5/RELEASE_NOTES.md) · 中文

| 项 | 内容 |
|:---|:---|
| 版本 | `0.1.5`（`pyproject.toml`） |
| 日期 | 2026-11-19 |
| 相对 | [v0.1.4 评测](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.5/doc/%E7%89%88%E6%9C%AC/v0.1.4/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) |
| 评测 | [评测结果](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.5/doc/%E7%89%88%E6%9C%AC/v0.1.5/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) — **82.3 / A**（仓内路径：`doc/版本/v0.1.5/评测结果.md`） |

在 **0.1.4**（执行模式、Skill/MCP、成败台账、产物交付）之上，本版落地 **Cursor Agent**（本机 SDK、独立页、直连对话 + 主脑委派）、PWA **Diff / 产物浏览**，并将 `doc/` **按域编号**（功能 / 分析 / 议题 / 问题）。

## 要点

1. **Cursor Agent**：侧栏独立页；`@cursor/sdk` 浏览器登录；`cursor_agent_settings.sqlite`；compose 选 Cursor 走步骤流卡；主脑 `tier=cursor` 委派；可选 `pip install -e '.[cursor]'`。
2. **修改与产物**：pending diff、hunk 折叠、懒加载全文、改动清单；办公类 HTML/PDF 投影跨端只读预览。
3. **文档**：扁平目录迁至 `00-横切`…`08-运行与分发`；总表与 AGENTS 互链更新。
4. **公开基准**：`doc/测试/公开基准/` 收录 AgentBench 样本与对照说明（非放行表）。

## 行为变更

- 仅文档路径大规模迁移；实现 import 不变，书签请改编号路径。
- 启用 Cursor Agent 需 Node ≥22 或环境变量 Key；未装可选依赖时功能自检为 `not_installed`，其它能力不受影响。

## 开发安装

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore
pip install -e .
pip install -e '.[cursor]'
```

## 消费安装

从 [GitHub Releases v0.1.5](https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.5) 下载对应平台压缩包，解压后运行 **Install**（默认平滑升级）或 **Start**（见包内 `INSTALL.zh-CN.txt` / `INSTALL.en.txt`）。

本地构建并上传：

```bash
python3 packaging/build_release.py --target auto
bash packaging/publish_github_release.sh
```

## 仍未覆盖

- 评测未强制真机 Cursor 登录 + 同网手机联调
- macOS 公证、Windows GUI  parity、安装包预置 Playwright 仍缺

## GitHub

- 标签：`v0.1.5`（`main`）
- Release：https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.5