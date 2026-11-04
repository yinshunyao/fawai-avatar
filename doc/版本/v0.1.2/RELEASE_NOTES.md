# fawai-avatar v0.1.2 Release Notes

| 项 | 内容 |
|:---|:---|
| 版本 | `0.1.2`（`pyproject.toml`） |
| 日期 | 2026-11-04 |
| 相对 | [v0.1.1](../v0.1.1/评测结果.md) |
| 评测 | [评测结果.md](./评测结果.md) — **78.5 / B** |

面向 PC / 手机的操作分身：本版在 **0.1.1 文件闭环与安全底座** 上，补齐计划模式、C3 窄场景 GUI、浏览器工具、Python 会话与脚本铲子，并加固执行中纠偏与会话步骤流。

## Highlights

1. **复杂任务计划模式**：编排脑出图 → 自动调度 → 监管/执行分层；支持 `reorder` / `lease_adjust`、失败节点重试、显式整图重规划；PWA 节点卡 + 有向图。
2. **C3 自主操控（窄场景）**：macOS TextEdit + AX（`obs_ax` / `ui_*` / `verify_ui_ax`）；多屏 `displays` / `screen_target` / `app_focus`。
3. **浏览器操作**：Playwright 内置页（`browser_*`），不占前台键鼠租约。
4. **Python 扩展 + 脚本铲子**：`py_write` / `py_pip` / `py_run`；造脚本前 `shovel_search`。
5. **执行中纠偏加深**：观察反省、步骤重复、主脑委派快模型；必要路径台账；补丁锚点未接地回执。
6. **会话 / 运维**：对话内步骤流与从步骤恢复；keeper 以磁盘 `.env` 覆盖子进程；热重启默认关。

## What's New

### 规划与任务

- 复杂任务计划模式（编排 / 监管 / 执行三层）
- 主脑委派快模型（`FAWAI_LLM_FAST_MODEL`）
- 模型档案生成参数 + 按角色深度思考
- 必要路径台账；答复覆盖闸延续

### 纠偏与闭环

- 观察反省（`FAWAI_OBS_REFLECT`）
- 步骤重复：指纹 → LLM 裁决 `continue` / `alternate`
- 执行反省仅 `retry` / `alternate`（止损归规划 `failed`）
- 编码：补丁锚点未接地 + 原文小窗；探仓暂时降权

### 观察与操控

- AX 控件树观察与 C3 TextEdit 窄场景
- 多屏观察与 `app_focus`
- Playwright `browser_*`（需 `.[browser]`）

### 后台能力

- Python 扩展会话目录
- 脚本铲子生命周期（`fawai_shovels/`）
- 自我进化写盘闸门保持（禁改回退 / 保活 / 路径最少核）

### 通道与运维

- PWA：步骤流、`step_label`、从步骤恢复、窄屏 i18n 入口调整
- 保活：`.env` 覆盖、端口回收、启动一键修复；代码热重启默认关

## Fixes（发版前评测闸）

- 文件主目标夹带「鼠标 / 桌面」误导时，硬校验仍禁止用 GUI 代替 `fs_*`（C2 回归）
- 优先改进套件 `D5d`：`BoomLoop._observe` 对齐 `observe_args` 签名，步骤崩溃可恢复重规划

## Breaking / 行为变化

- 无强制破坏性 API 变更；新增能力多为可选工具与环境开关
- 计划模式默认开（`FAWAI_PLAN_MODE=0` 可关）
- 探索 / 定位 / 宽检索降质 / 工具观察 soft 压缩：**默认关**（须显式环境变量开启）
- macOS 桌面 GUI 依赖随主包装齐；Linux GUI 仍需 `.[gui]`；浏览器需 `.[browser]` + Chromium

## Install

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore
pip install -e .
# 可选：pip install -e ".[gui]" / pip install -e ".[browser]" && playwright install chromium
```

冒烟与发版评测入口见 README；加权结论见 [评测结果.md](./评测结果.md)。

## Not in this release / Known gaps

- 真机同网手机 PWA 体感未本轮实测
- `FAWAI_C3_LIVE` TextEdit 真机闭环、Electron（Cursor/Chrome）控件级点准未承诺
- `FAWAI_C2_LIVE` 真模型误导/超清单未进本轮强制流水
- 任意桌面 App 自动点准、手机本机操控、计划图拖拽改依赖：后置

## GitHub

标签与推送：**待你指令后再执行**（本回合只升版本号、落 release notes 与评测，不 `git push` / 不打远程 tag）。
