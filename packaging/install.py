"""Install / upgrade a Fawai Avatar prefix from this extracted package.

Default UX: open a simple local web page (path + smooth/overwrite + Install).
CLI retained for automation: ``--cli`` / ``--yes``.

Modes:
  - smooth (default): replace program files; keep ``data/`` and ``.env``
  - overwrite: replace the whole prefix (wipes ``data/`` and ``.env``)

Copied into each install prefix as ``install.py``. Messages: packaging/i18n.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
I18N_DIR = ROOT / "i18n"
SUPPORTED = ("zh-CN", "en")

PROGRAM_DIRS = ("app", "runtime", "deps", "i18n", "bin")
PROGRAM_FILES = (
    "launcher.py",
    "install.py",
    "Start.sh",
    "Start.command",
    "Start.bat",
    "Install.sh",
    "Install.command",
    "Install.bat",
    "VERSION",
    "INSTALL.txt",
    "INSTALL.zh-CN.txt",
    "INSTALL.en.txt",
    "START_HERE.txt",
    ".env.example",
)


def _detect_lang() -> str:
    env = (os.environ.get("FAWAI_LANG") or os.environ.get("FAWAI_INSTALL_LANG") or "").strip()
    if env in SUPPORTED:
        return env
    if env.lower() in {"zh", "zh_cn", "zh-cn", "cn"}:
        return "zh-CN"
    if env.lower() in {"en", "en_us", "en-us"}:
        return "en"
    for candidate in (ROOT / "data" / "install_lang.txt",):
        if candidate.is_file():
            saved = candidate.read_text(encoding="utf-8").strip()
            if saved in SUPPORTED:
                return saved
    loc = (
        os.environ.get("LC_ALL")
        or os.environ.get("LC_MESSAGES")
        or os.environ.get("LANG")
        or ""
    ).lower()
    if loc.startswith("zh"):
        return "zh-CN"
    return "en"


def _load_messages(lang: str) -> dict[str, str]:
    path = I18N_DIR / f"{lang}.json"
    if not path.is_file():
        path = I18N_DIR / "en.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _t(messages: dict[str, str], key: str, **kwargs: object) -> str:
    text = messages.get(key) or key
    try:
        return text.format(**kwargs)
    except Exception:
        return text


def _default_target() -> Path:
    env = (os.environ.get("FAWAI_INSTALL_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    home = Path.home()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
        return (base / "FawaiAvatar").resolve()
    if sys.platform == "darwin":
        apps = home / "Applications" / "FawaiAvatar"
        if apps.is_dir() or not (home / "FawaiAvatar").exists():
            return apps.resolve()
    return (home / "FawaiAvatar").resolve()


def _looks_like_prefix(path: Path) -> bool:
    if not path.is_dir():
        return False
    has_launcher = (path / "launcher.py").is_file()
    has_app = (path / "app" / "fawai_avatar").is_dir()
    has_runtime = (path / "runtime").is_dir()
    return has_launcher and (has_app or has_runtime)


def _is_source_package(path: Path) -> bool:
    return (path / "install.py").is_file() and (path / "app" / "fawai_avatar").is_dir()


def _rm_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)


def _copy_dir(src: Path, dst: Path) -> None:
    if dst.exists():
        _rm_tree(dst)
    shutil.copytree(src, dst)


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _ensure_empty_data(target: Path) -> None:
    (target / "data").mkdir(parents=True, exist_ok=True)


def install_fresh(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in PROGRAM_DIRS:
        src = source / name
        if src.is_dir():
            _copy_dir(src, target / name)
    for name in PROGRAM_FILES:
        src = source / name
        if src.is_file():
            _copy_file(src, target / name)
    _ensure_empty_data(target)
    for name in ("Start.sh", "Start.command", "Install.sh", "Install.command"):
        p = target / name
        if p.is_file() and os.name != "nt":
            p.chmod(0o755)


def install_smooth(source: Path, target: Path) -> None:
    if not _looks_like_prefix(target):
        install_fresh(source, target)
        return
    for name in PROGRAM_DIRS:
        src = source / name
        if not src.is_dir():
            continue
        _copy_dir(src, target / name)
    for name in PROGRAM_FILES:
        src = source / name
        if src.is_file():
            _copy_file(src, target / name)
    _ensure_empty_data(target)
    for name in ("Start.sh", "Start.command", "Install.sh", "Install.command"):
        p = target / name
        if p.is_file() and os.name != "nt":
            p.chmod(0o755)


def install_overwrite(source: Path, target: Path) -> None:
    if target.exists():
        for child in list(target.iterdir()):
            _rm_tree(child)
    install_fresh(source, target)


def perform_install(
    source: Path,
    target: Path,
    mode: str,
    *,
    messages: dict[str, str],
) -> tuple[bool, str]:
    """Run install. Returns (ok, user-facing message)."""
    if not _is_source_package(source):
        return False, _t(messages, "upgrade_bad_source", path=str(source))
    try:
        target = target.expanduser().resolve()
        source = source.expanduser().resolve()
    except OSError as exc:
        return False, _t(messages, "upgrade_failed", error=str(exc))
    if target == source:
        return False, _t(messages, "upgrade_same_dir")
    if mode not in {"smooth", "overwrite"}:
        mode = "smooth"
    exists = _looks_like_prefix(target)
    try:
        if not exists:
            install_fresh(source, target)
            return True, _t(messages, "upgrade_done_fresh", target=str(target))
        if mode == "overwrite":
            install_overwrite(source, target)
            return True, _t(messages, "upgrade_done_overwrite", target=str(target))
        install_smooth(source, target)
        return True, _t(messages, "upgrade_done_smooth", target=str(target))
    except OSError as exc:
        return False, _t(messages, "upgrade_failed", error=str(exc))


def _pick_folder(messages: dict[str, str], start: Path) -> str | None:
    """Native folder picker when available; None if cancelled / unsupported."""
    start_s = str(start if start.is_dir() else start.parent)
    try:
        if sys.platform == "darwin":
            script = (
                f'POSIX path of (choose folder with prompt "{_t(messages, "gui_browse_title")}" '
                f'default location POSIX file "{start_s}")'
            )
            out = subprocess.check_output(
                ["osascript", "-e", script],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            return out.rstrip("/") if out else None
        if os.name == "nt":
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                f"$d.Description = '{_t(messages, 'gui_browse_title')}'; "
                f"$d.SelectedPath = '{start_s.replace(chr(39), chr(39)+chr(39))}'; "
                "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"
            )
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            return out or None
        for cmd in (
            ["zenity", "--file-selection", "--directory", "--filename", start_s],
            ["kdialog", "--getexistingdirectory", start_s],
        ):
            try:
                out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
                if out:
                    return out
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return None


def _html_page(messages: dict[str, str], default_target: str) -> str:
    # Escape for HTML text / attributes
    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    title = esc(_t(messages, "gui_title"))
    hint = esc(_t(messages, "gui_hint"))
    label_dir = esc(_t(messages, "gui_label_dir"))
    browse = esc(_t(messages, "gui_browse"))
    label_mode = esc(_t(messages, "gui_label_mode"))
    smooth = esc(_t(messages, "gui_mode_smooth"))
    overwrite = esc(_t(messages, "gui_mode_overwrite"))
    btn = esc(_t(messages, "gui_install"))
    close_hint = esc(_t(messages, "gui_close_hint"))
    default_target_js = json.dumps(default_target)
    next_start_js = json.dumps(_t(messages, "upgrade_next_start", target="{target}"))
    running_js = json.dumps(_t(messages, "gui_running"))
    need_path_js = json.dumps(_t(messages, "gui_need_path"))

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
  :root {{
    --bg: #f6f4ef;
    --card: #fffdf8;
    --ink: #1c1917;
    --muted: #57534e;
    --line: #e7e5e4;
    --accent: #0f766e;
    --accent-ink: #ecfdf5;
    --warn: #b45309;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh;
    font-family: "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
    background: radial-gradient(1200px 600px at 10% -10%, #ccfbf1 0%, transparent 55%),
                radial-gradient(900px 500px at 100% 0%, #fde68a 0%, transparent 40%),
                var(--bg);
    color: var(--ink);
    display: flex; align-items: center; justify-content: center;
    padding: 24px;
  }}
  main {{
    width: min(520px, 100%);
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 28px 28px 24px;
    box-shadow: 0 12px 40px rgba(28, 25, 23, 0.08);
  }}
  h1 {{ margin: 0 0 8px; font-size: 1.35rem; font-weight: 650; letter-spacing: -0.02em; }}
  .hint {{ margin: 0 0 22px; color: var(--muted); font-size: 0.92rem; line-height: 1.45; }}
  label.block {{ display: block; font-size: 0.85rem; font-weight: 600; margin-bottom: 8px; }}
  .row {{ display: flex; gap: 8px; margin-bottom: 18px; }}
  input[type=text] {{
    flex: 1; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px;
    font-size: 0.95rem; background: #fff;
  }}
  button {{
    border: 0; border-radius: 10px; padding: 10px 14px; font-size: 0.92rem;
    cursor: pointer; font-weight: 600;
  }}
  button.secondary {{ background: #f5f5f4; color: var(--ink); }}
  button.primary {{
    width: 100%; margin-top: 8px; padding: 12px 16px;
    background: var(--accent); color: var(--accent-ink); font-size: 1rem;
  }}
  button:disabled {{ opacity: 0.55; cursor: not-allowed; }}
  .modes {{ display: grid; gap: 10px; margin-bottom: 18px; }}
  .mode {{
    display: flex; gap: 10px; align-items: flex-start;
    padding: 12px; border: 1px solid var(--line); border-radius: 12px;
    cursor: pointer; background: #fff;
  }}
  .mode:has(input:checked) {{ border-color: var(--accent); background: #f0fdfa; }}
  .mode input {{ margin-top: 3px; }}
  .mode span {{ font-size: 0.92rem; line-height: 1.4; }}
  #status {{
    margin-top: 16px; min-height: 1.4em; font-size: 0.9rem; color: var(--muted);
    white-space: pre-wrap;
  }}
  #status.ok {{ color: var(--accent); }}
  #status.err {{ color: #b91c1c; }}
  .foot {{ margin-top: 14px; font-size: 0.8rem; color: var(--muted); }}
</style>
</head>
<body>
<main>
  <h1>{title}</h1>
  <p class="hint">{hint}</p>
  <label class="block" for="target">{label_dir}</label>
  <div class="row">
    <input id="target" type="text" value="{esc(default_target)}" spellcheck="false"/>
    <button type="button" class="secondary" id="browse">{browse}</button>
  </div>
  <label class="block">{label_mode}</label>
  <div class="modes">
    <label class="mode">
      <input type="radio" name="mode" value="smooth" checked/>
      <span>{smooth}</span>
    </label>
    <label class="mode">
      <input type="radio" name="mode" value="overwrite"/>
      <span>{overwrite}</span>
    </label>
  </div>
  <button type="button" class="primary" id="go">{btn}</button>
  <div id="status"></div>
  <p class="foot">{close_hint}</p>
</main>
<script>
const DEFAULT_TARGET = {default_target_js};
const NEXT_START_TMPL = {next_start_js};
const CONFIRM_OW = {json.dumps(_t(messages, "gui_overwrite_confirm"))};
const RUNNING = {running_js};
const NEED_PATH = {need_path_js};
const statusEl = document.getElementById('status');
const goBtn = document.getElementById('go');

function setStatus(text, cls) {{
  statusEl.textContent = text || '';
  statusEl.className = cls || '';
}}

document.getElementById('browse').onclick = async () => {{
  try {{
    const r = await fetch('/api/browse?start=' + encodeURIComponent(document.getElementById('target').value || DEFAULT_TARGET));
    const j = await r.json();
    if (j.path) document.getElementById('target').value = j.path;
    else if (j.message) setStatus(j.message, '');
  }} catch (e) {{
    setStatus(String(e), 'err');
  }}
}};

goBtn.onclick = async () => {{
  const target = document.getElementById('target').value.trim();
  const mode = (document.querySelector('input[name=mode]:checked') || {{}}).value || 'smooth';
  if (!target) {{ setStatus(NEED_PATH, 'err'); return; }}
  if (mode === 'overwrite' && !window.confirm(CONFIRM_OW)) return;
  goBtn.disabled = true;
  setStatus(RUNNING, '');
  try {{
    const r = await fetch('/api/install', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ target, mode }})
    }});
    const j = await r.json();
    if (j.ok) {{
      const next = NEXT_START_TMPL.split('{{target}}').join(j.target || target);
      setStatus((j.message || '') + '\\n' + next, 'ok');
    }} else {{
      setStatus(j.message || 'error', 'err');
    }}
  }} catch (e) {{
    setStatus(String(e), 'err');
  }} finally {{
    goBtn.disabled = false;
  }}
}};
</script>
</body>
</html>
"""


def run_gui(*, source: Path, messages: dict[str, str]) -> int:
    default_target = str(_default_target())
    state = {"done": False, "code": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
            return

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                html = _html_page(messages, default_target)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/browse":
                qs = parse_qs(parsed.query)
                start_raw = (qs.get("start") or [default_target])[0]
                start = Path(start_raw).expanduser()
                picked = _pick_folder(messages, start)
                if picked:
                    payload = {"path": picked}
                else:
                    payload = {
                        "path": None,
                        "message": _t(messages, "gui_browse_fallback"),
                    }
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send(200, data, "application/json; charset=utf-8")
                return
            self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/api/install":
                self._send(404, b"not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                body = {}
            target = Path(str(body.get("target") or default_target))
            mode = str(body.get("mode") or "smooth")
            ok, message = perform_install(source, target, mode, messages=messages)
            payload = {
                "ok": ok,
                "message": message,
                "target": str(target.expanduser()),
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(200, data, "application/json; charset=utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(_t(messages, "gui_open", url=url), flush=True)

    def _serve() -> None:
        with server:
            server.serve_forever(poll_interval=0.3)

    thread = threading.Thread(target=_serve, name="fawai-install-gui", daemon=True)
    thread.start()
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print(_t(messages, "gui_waiting"), flush=True)
    try:
        while True:
            try:
                thread.join(timeout=0.5)
            except KeyboardInterrupt:
                print(_t(messages, "upgrade_cancelled"), flush=True)
                state["code"] = 1
                break
            # Keep process alive until Ctrl+C; user closes after install
            if not thread.is_alive():
                break
    finally:
        server.shutdown()
    return int(state["code"])


def _prompt(messages: dict[str, str], key: str, default: str) -> str:
    hint = _t(messages, key, default=default)
    try:
        raw = input(hint).strip()
    except EOFError:
        return default
    return raw or default


def _prompt_mode(messages: dict[str, str], default: str) -> str:
    print(_t(messages, "upgrade_mode_help"))
    raw = _prompt(messages, "upgrade_mode_prompt", default)
    normalized = raw.strip().lower()
    if normalized in {"1", "s", "smooth", "平滑", "平滑升级"}:
        return "smooth"
    if normalized in {"2", "o", "overwrite", "覆盖", "覆盖安装"}:
        return "overwrite"
    if normalized in {"smooth", "overwrite"}:
        return normalized
    return default if default in {"smooth", "overwrite"} else "smooth"


def run_cli(
    *,
    source: Path,
    messages: dict[str, str],
    target: Path | None,
    mode: str | None,
    yes: bool,
) -> int:
    default_target = _default_target()
    if target is not None:
        dest = target
    elif yes:
        dest = default_target
    else:
        print(_t(messages, "upgrade_intro"))
        raw = _prompt(messages, "upgrade_target_prompt", str(default_target))
        dest = Path(raw)

    exists = _looks_like_prefix(dest.expanduser())
    chosen = mode
    if chosen is None:
        if not exists or yes:
            chosen = "smooth"
        else:
            chosen = _prompt_mode(messages, "smooth")

    if not yes and exists and chosen == "overwrite":
        confirm = _prompt(messages, "upgrade_overwrite_confirm", "")
        if confirm.strip().upper() not in {"YES", "Y", "是"}:
            print(_t(messages, "upgrade_cancelled"))
            return 1

    if not yes and exists:
        print(
            _t(
                messages,
                "upgrade_confirm_summary",
                mode=_t(messages, f"upgrade_mode_{chosen}"),
                target=str(dest.expanduser()),
                source=str(source),
            )
        )
        ok = _prompt(messages, "upgrade_proceed_prompt", "Y")
        if ok.strip().lower() in {"n", "no", "否"}:
            print(_t(messages, "upgrade_cancelled"))
            return 1

    print(
        _t(
            messages,
            "upgrade_running",
            mode=_t(messages, f"upgrade_mode_{chosen}"),
            target=str(dest.expanduser()),
        ),
        flush=True,
    )
    ok, message = perform_install(source, dest, chosen or "smooth", messages=messages)
    print(message, file=sys.stdout if ok else sys.stderr)
    if ok:
        print(_t(messages, "upgrade_next_start", target=str(dest.expanduser())))
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install or upgrade Fawai Avatar from this package"
    )
    parser.add_argument("--lang", choices=SUPPORTED, default=None)
    parser.add_argument("--target", type=str, default=None)
    parser.add_argument("--mode", choices=("smooth", "overwrite"), default=None)
    parser.add_argument("--yes", action="store_true", help="Non-interactive CLI")
    parser.add_argument("--cli", action="store_true", help="Force terminal prompts")
    parser.add_argument("--gui", action="store_true", help="Force local web install UI")
    parser.add_argument("--source", type=str, default=None)
    args = parser.parse_args(argv)

    lang = args.lang or _detect_lang()
    messages = _load_messages(lang)
    source = Path(args.source).expanduser().resolve() if args.source else ROOT
    if not _is_source_package(source):
        print(_t(messages, "upgrade_bad_source", path=str(source)), file=sys.stderr)
        return 2

    use_gui = args.gui or (not args.cli and not args.yes and args.target is None)
    if use_gui:
        return run_gui(source=source, messages=messages)

    target = Path(args.target).expanduser() if args.target else None
    return run_cli(
        source=source,
        messages=messages,
        target=target,
        mode=args.mode,
        yes=args.yes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
