# fawai-avatar（beyond-5080）

会话网关部署在比昂 5080：本机听 `7980`，经网关内置 frpc 映到公网 **`48000`**。

| 在哪 | 准备 | 完成 | 保存 |
|:---|:---|:---|:---|
| SSH：见 [`../../比昂农业.md`](../../比昂农业.md)「5080」；代码仓 `fawai-avatar/` | 本机已能 `ssh -p 37443 beyond@8.137.33.38` | 下文步骤全部跑通 | 本目录 `fawai.env`、`Sfawai` |

## 现网标识

| 项 | 值 |
|:---|:---|
| 代码目录 | `/data/models/fawai-avatar` |
| 解释器 | `/data/models/fawai-avatar/.venv/bin/python`（由 yolo11 的 Python 3.11 建 venv） |
| HTTP | `http://127.0.0.1:7980/`（本机） |
| 公网 | `http://8.137.33.38:48000/` |
| 会话口令 | 见 `fawai.env` → `FAWAI_GATEWAY_PASSCODE` |
| supervisor | `[program:fawai-avatar]`，配置文件 `/data/models/supervisor/Sfawai` |
| 日志 | `/data/models/log/fawai-avatar.std` |
| frpc | 网关进程自拉；二进制 `FAWAI_FRPC_BIN=/usr/local/bin/frpc`；代理段名 `FAWAI_FRP_PROXY_NAME=fawai-avatar-gateway-5080`（勿与本机默认名撞车）；**不要**改本机开发 `.env` 的 `38443` |

> 机器上已有 supervisor `fprc`（`/home/beyond/frpc.ini`）只负责 SSH `37443`，与 fawai 的 `48000` 穿透无关。

## 步骤 1：同步代码

| 字段 | 内容 |
|:---|:---|
| **在哪** | 本机仓库根；工具 `fawai-avatar/hipp-tools/shell/rsync-ssh-deploy.sh` |
| **准备** | `DEPLOY_SSH_PASS` 已设（口令见比昂农业.md）；勿开 `DEPLOY_DELETE=1` 除非确认 |
| **完成** | 远端 `/data/models/fawai-avatar` 含 `fawai_avatar/`、`pyproject.toml` |
| **保存** | 无 |

```bash
cd <workspace>/fawai-avatar
DEPLOY_LOCAL="$PWD" \
DEPLOY_REMOTE="beyond@8.137.33.38:/data/models/fawai-avatar" \
DEPLOY_SSH_PORT=37443 \
DEPLOY_SSH_PASS='<口令>' \
DEPLOY_DELETE=0 \
DEPLOY_EXCLUDES='.git .venv __pycache__ .DS_Store *.log .pytest_cache data/runtime data/versions data/frpc.pid data/frpc.log' \
bash hipp-tools/shell/rsync-ssh-deploy.sh
```

## 步骤 2：环境与依赖

| 字段 | 内容 |
|:---|:---|
| **在哪** | 远端 `/data/models/fawai-avatar` |
| **准备** | 步骤 1 已完成；conda `yolo11` 可用 |
| **完成** | `.venv` 可 `import fawai_avatar`；`.env` 中 `FAWAI_FRP_REMOTE_PORT=48000` |
| **保存** | 底稿 `fawai.env` → 远端 `.env`（覆盖前先备份远端已有 `.env`） |

```bash
cd /data/models/fawai-avatar
/home/beyond/.conda/envs/yolo11/bin/python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
# 从本目录底稿拷贝（本机 scp 或粘贴），确保：
# FAWAI_FRP_REMOTE_PORT=48000
# FAWAI_FRPC_BIN=/usr/local/bin/frpc
# FAWAI_HEADLESS=1
# FAWAI_CODE_RELOAD=0
```

## 步骤 3：注册 supervisor

| 字段 | 内容 |
|:---|:---|
| **在哪** | `/data/models/supervisor/`（`supervisord.conf` 的 `[include] files = S*`） |
| **准备** | 步骤 2 完成；有 sudo |
| **完成** | `supervisorctl status fawai-avatar` 为 `RUNNING` |
| **保存** | 片段真源：本目录 `Sfawai` |

```bash
# 本机把 Sfawai 同步到远端后：
sudo cp /data/models/fawai-avatar/../supervisor/Sfawai /data/models/supervisor/Sfawai
# 若片段已放在部署目录外，直接：
# sudo cp <本目录Sfawai> /data/models/supervisor/Sfawai

sudo /home/beyond/.conda/envs/yolo11/bin/supervisorctl \
  -c /data/models/supervisor/supervisord.conf reread
sudo /home/beyond/.conda/envs/yolo11/bin/supervisorctl \
  -c /data/models/supervisor/supervisord.conf update
sudo /home/beyond/.conda/envs/yolo11/bin/supervisorctl \
  -c /data/models/supervisor/supervisord.conf start fawai-avatar
```

## 日常运维

```bash
CONF=/data/models/supervisor/supervisord.conf
CTL="sudo /home/beyond/.conda/envs/yolo11/bin/supervisorctl -c $CONF"

$CTL status fawai-avatar
$CTL restart fawai-avatar
$CTL stop fawai-avatar
tail -n 80 /data/models/log/fawai-avatar.std
```

改 `.env`（含换 `FAWAI_FRP_REMOTE_PORT`）后必须 `restart fawai-avatar`；frpc 会随网关重启重绑。

## 验收

1. 本机：`curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7980/` 非 000。
2. 公网：浏览器打开 `http://8.137.33.38:48000/`，口令同 `FAWAI_GATEWAY_PASSCODE`。
3. 日志含 frpc 公网 URL，且无 Mach-O / 二进制不匹配错误。

## 注意

- **本地开发 `.env` 保持 `FAWAI_FRP_REMOTE_PORT=38443`（或你本机原值），不要改成 48000。**
- 本机与 5080 若连同一 frps，代理段名必须不同；5080 用 `fawai-avatar-gateway-5080`。
- 仓库 `bin/frpc` 若是 macOS Mach-O，Linux 上会被拒绝；5080 必须用 `/usr/local/bin/frpc`。
- 无界面机不要依赖截屏；`FAWAI_HEADLESS=1`，`FAWAI_GATEWAY_CAPTURE=0`。
- 若公网打不开：查 `data/frpc.log` 是否有 `proxy name … already in use` / 端口占用；再看 `fawai-avatar.std`。
