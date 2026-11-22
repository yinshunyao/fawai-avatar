# fawai-avatar v0.1.6 发布说明

**语言：** [English (default)](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.6/doc/%E7%89%88%E6%9C%AC/v0.1.6/RELEASE_NOTES.md) · 中文

| 项 | 内容 |
|:---|:---|
| 版本 | `0.1.6`（`pyproject.toml`） |
| 日期 | 2026-11-22 |
| 相对 | [v0.1.5 评测](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.6/doc/%E7%89%88%E6%9C%AC/v0.1.5/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) |
| 评测 | [评测结果](https://github.com/yinshunyao/fawai-avatar/blob/v0.1.6/doc/%E7%89%88%E6%9C%AC/v0.1.6/%E8%AF%84%E6%B5%8B%E7%BB%93%E6%9E%9C.md) — **84.0 / A**（仓内路径：`doc/版本/v0.1.6/评测结果.md`） |

在 **0.1.5**（Cursor Agent、Diff/产物、文档编号化）之上，本版落地 **任务级执行配置**、强制 **一对话一任务**，并修通 **Cursor 多轮同线程续跑**。

## 要点

1. **任务级执行配置**：打开任务时底栏跟该任务；进行中拨动立刻写入任务 meta（`POST /api/tasks/{id}/exec-config`）；已结束不回写历史。
2. **一对话一任务**：跟进 / 执行中追加 / 计划追加不再自动拆 sibling；同线程续跑或并入。
3. **Cursor 多轮**：列表/详情带 `exec_config`；前端与 hub 同源认 Cursor；同 `task_id` resume，避免刷新后新开卡片。
4. **运行稳健**：Cursor Node 探测（vendor bridge / nvm≥22）；可选跳过启动权限弹窗；同环 publish 防死锁；Cursor 回合失败可见。

## 行为变更

- 取消「跟进自动拆新任务」；要另开目标请从首页新开线程。
- `exec-config` **不**改 `runtime`（不能靠该接口把普通任务改成 Cursor）。
- 已结束任务改执行配置返回 409；界面只更新本机全局末次。

## 开发安装

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore
pip install -e .
pip install -e '.[cursor]'
```

## 消费安装

从 [GitHub Releases v0.1.6](https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.6) 下载对应平台压缩包，解压后运行 **Install**（默认平滑升级）或 **Start**（见包内 `INSTALL.zh-CN.txt` / `INSTALL.en.txt`）。

本地构建并上传：

```bash
python3 packaging/build_release.py --target auto
bash packaging/publish_github_release.sh
```

## 仍未覆盖

- 评测未强制真机 Cursor 长对话与同网手机联调
- macOS 公证、Windows GUI parity、安装包预置 Playwright 仍缺
- 启动权限探测 SIGSEGV 根因未除尽（可用跳过环境变量应急）

## GitHub

- 标签：`v0.1.6`（`main`）
- Release：https://github.com/yinshunyao/fawai-avatar/releases/tag/v0.1.6
