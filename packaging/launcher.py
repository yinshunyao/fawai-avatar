"""Release-prefix launcher: start keeper, wait for gateway, open http://127.0.0.1:<port>/.

Copied into each install prefix as ``launcher.py``. Messages: packaging/i18n (zh-CN / en).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
I18N_DIR = ROOT / "i18n"
DATA_DIR = ROOT / "data"
LANG_FILE = DATA_DIR / "install_lang.txt"
SUPPORTED = ("zh-CN", "en")


def _detect_lang() -> str:
    env = (os.environ.get("FAWAI_LANG") or os.environ.get("FAWAI_INSTALL_LANG") or "").strip()
    if env in SUPPORTED:
        return env
    if env.lower() in {"zh", "zh_cn", "zh-cn", "cn"}:
        return "zh-CN"
    if env.lower() in {"en", "en_us", "en-us"}:
        return "en"
    if LANG_FILE.is_file():
        saved = LANG_FILE.read_text(encoding="utf-8").strip()
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
    try:
        import locale

        loc2 = (locale.getdefaultlocale()[0] or "").lower()
        if loc2.startswith("zh"):
            return "zh-CN"
    except Exception:
        pass
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


def _bundled_python() -> Path:
    if os.name == "nt":
        candidates = [
            ROOT / "runtime" / "python.exe",
            ROOT / "runtime" / "python" / "python.exe",
        ]
    else:
        candidates = [
            ROOT / "runtime" / "bin" / "python3",
            ROOT / "runtime" / "bin" / "python",
            ROOT / "runtime" / "python" / "bin" / "python3",
        ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def _gateway_port() -> int:
    raw = (os.environ.get("FAWAI_GATEWAY_PORT") or "7980").strip()
    try:
        return int(raw)
    except ValueError:
        return 7980


def _local_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def _guess_lan_urls(port: int) -> list[str]:
    urls: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127."):
                continue
            urls.append(f"http://{ip}:{port}/")
    except Exception:
        pass
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:4]


def _port_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return _port_open(_gateway_port())


def _is_headless() -> bool:
    if (os.environ.get("FAWAI_HEADLESS") or "").strip() == "1":
        return True
    if (os.environ.get("FAWAI_HEADLESS") or "").strip() == "0":
        return False
    if os.name == "nt":
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _prepare_env(py: Path) -> dict[str, str]:
    env = os.environ.copy()
    app = ROOT / "app"
    deps = ROOT / "deps"
    parts = [str(app), str(deps)]
    old = env.get("PYTHONPATH", "")
    if old:
        parts.append(old)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["FAWAI_DATA_DIR"] = str(DATA_DIR)
    env.setdefault("FAWAI_NO_KEEPER", "0")
    # Prefer package-local .env if present
    dotenv = ROOT / ".env"
    if dotenv.is_file():
        env.setdefault("FAWAI_DOTENV", str(dotenv))
    # Ensure bundled python first on PATH for child tools
    bindir = str(py.parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    env["FAWAI_PREFIX_ROOT"] = str(ROOT)
    return env


def _save_lang(lang: str, messages: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LANG_FILE.write_text(lang + "\n", encoding="utf-8")
    print(_t(messages, "lang_saved", lang=lang), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fawai Avatar release launcher")
    parser.add_argument(
        "--lang",
        choices=SUPPORTED,
        default=None,
        help="Installer UI language (zh-CN|en)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the local browser",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=60.0,
        help="Seconds to wait for gateway before failing",
    )
    args = parser.parse_args(argv)

    lang = args.lang or _detect_lang()
    messages = _load_messages(lang)
    if args.lang:
        _save_lang(lang, messages)
    elif not LANG_FILE.is_file():
        _save_lang(lang, messages)

    os.environ["FAWAI_LANG"] = lang

    py = _bundled_python()
    if not py.is_file():
        print(_t(messages, "failed_python", path=str(py)), file=sys.stderr)
        return 2

    port = _gateway_port()
    url = _local_url(port)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(_t(messages, "starting"), flush=True)
    env = _prepare_env(py)
    # Foreground keeper (Ctrl+C stops)
    cmd = [str(py), "-m", "fawai_avatar.runtime.keeper"]
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)

    print(_t(messages, "waiting_port", url=url), flush=True)
    deadline = time.time() + max(5.0, float(args.wait_seconds))
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        if _http_ok(url) or _port_open(port):
            ready = True
            break
        time.sleep(0.4)

    if not ready:
        print(_t(messages, "failed_start", url=url), file=sys.stderr)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        return 1

    print(_t(messages, "phone_hint"), flush=True)
    lan = _guess_lan_urls(port)
    if lan:
        print(_t(messages, "lan_hint", lan_urls=", ".join(lan)), flush=True)

    open_browser = not args.no_browser and not _is_headless()
    if open_browser:
        print(_t(messages, "ready_open", url=url), flush=True)
        try:
            webbrowser.open(url)
        except Exception:
            print(_t(messages, "ready_no_browser", url=url), flush=True)
    else:
        if _is_headless():
            print(_t(messages, "headless_skip_browser"), flush=True)
        print(_t(messages, "ready_no_browser", url=url), flush=True)

    print(_t(messages, "stop_hint"), flush=True)
    return proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
