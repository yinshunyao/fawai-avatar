# fawai-avatar

PC/手机操作分身。可读文档在 `doc/`；实现在 `fawai_avatar/`。

English: [README.md](README.md).

## 运行

```bash
cd fawai-avatar
python3 -m venv .venv && source .venv/bin/activate
pip install -e ./fawai_lore          # 个人知识库 KV + 笔记库
pip install -e "./fawai_lore[vector]"  # 可选：sqlite-vec 笔记向量（装后 FAWAI_LORE_VECTOR 默认 auto 即开）
pip install -e .                 # 无界面 / 编码·文件·shell / 网关
# pip install -e ".[gui]"        # 可选：桌面截屏与键鼠（macOS 或有 DISPLAY 的 Linux）
cp .env.example .env   # 填写 FAWAI_LLM_API_KEY；.env 已 gitignore
# 默认路由：纯文本 qwen3.7-plus；多模态 qwen3-vl-plus
export FAWAI_DRY_RUN=1
.venv/bin/python -m fawai_avatar.llm.client        # 试跑文本路由
.venv/bin/python -m fawai_avatar.demo_smoke
.venv/bin/python -m fawai_avatar.demo_platform     # 平台/headless 闸门
.venv/bin/python -m fawai_avatar.demo_fs_version   # 后台文件+版本（无 GUI）
.venv/bin/python -m fawai_avatar.demo_c2           # 完成任务 A/B/C/beyond
.venv/bin/python -m fawai_avatar.demo_clarification  # 执行中澄清 need_input→续跑
.venv/bin/python -m fawai_avatar.demo_c4           # 自我进化 E1/E2
.venv/bin/python -m fawai_avatar.demo_lore         # fawai_lore KV+笔记与长记忆旁路 P0
.venv/bin/python -m fawai_avatar.demo_code_index   # 代码索引 symbols/map
.venv/bin/python -m fawai_avatar.demo_coding_loop  # 编码闭环 P0/P1
.venv/bin/python -m fawai_avatar.demo_eval_priority  # 版本评测优先改进套件
# FAWAI_EVAL_CAPTURE=1 .venv/bin/python -m fawai_avatar.demo_eval_priority
```

后台动作（不受 `FAWAI_DRY_RUN`）：`fs_*` / `code_*` / `version_*` / `skill_*` / `lore_*` / `git_*` / `explore_*` / `shell_verify` / `diff_*`。

## Linux 无界面

无桌面的 Linux 服务器可直接跑网关 + 编码/文件/shell。进程会自动探测 OS 与是否 headless（也可用 `FAWAI_HEADLESS=1` 强制）；大脑按平台选型，GUI/截屏不可用。

```bash
pip install -e .
# 可选穿透：安装本机架构 frpc，并设 FAWAI_FRPC_BIN=/path/to/frpc
# 仓库 bin/frpc 若为 macOS Mach-O，在 Linux 上会被拒绝
export FAWAI_DRY_RUN=0          # 真改文件 / 跑 shell
.venv/bin/python -m fawai_avatar.demo_platform
.venv/bin/python -m fawai_avatar.channel.lan_gateway
```

## 跨端会话（局域网 PWA）

在 **macOS 或 Linux** 本机启动会话网关，手机同一 Wi‑Fi 打开终端打印的 URL，输入口令：

```bash
export FAWAI_DRY_RUN=1
.venv/bin/python -m fawai_avatar.channel.lan_gateway
# 或：fawai-gateway / fawai-keeper
```

默认经**服务保活**外层拉起（崩溃与 Web「重启」后自动再起；**代码文件变更**也会自动重启网关，关：`FAWAI_CODE_RELOAD=0`）。调试直接跑网关：`FAWAI_NO_KEEPER=1`（无热重载）。  
Web「服务」面板可存档/管理**运行时版本**并选版启动。

可选：`FAWAI_GATEWAY_PASSCODE` 固定口令；`FAWAI_RELAY_URL` 挂云端中继骨架。  
公网穿透：`.env` 配 `FAWAI_FRP_SERVER_ADDR` / `FAWAI_FRP_REMOTE_PORT`（及可选 `FAWAI_FRP_SERVER_PORT`），网关会拉起本机 `frpc`（`bin/frpc` 或 `FAWAI_FRPC_BIN`）。自检：`.venv/bin/python -m fawai_avatar.channel.selfcheck`。

放行与验收见 `doc/功能/`。测试任务见 `doc/测试/`。各版加权评测总分见 `doc/版本/`。
