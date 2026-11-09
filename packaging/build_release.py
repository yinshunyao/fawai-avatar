"""Build a relocatable Fawai Avatar install prefix for one target platform.

Downloads python-build-standalone into ``runtime/``, copies app sources, pip-installs
dependencies into ``deps/``, writes Start / Install wrappers + bilingual notes, then
archives under ``dist/release/``. Install supports smooth upgrade (default, keeps data)
and overwrite.

CLI (release tooling — argparse OK)::

    python3 packaging/build_release.py --target darwin-arm64
    python3 packaging/build_release.py --target auto
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGING = Path(__file__).resolve().parent

# Pin: astral-sh/python-build-standalone (install_only). Bump intentionally.
PBS_TAG = "20250317"
PBS_VERSION = "3.12.9"

TARGETS: dict[str, dict[str, object]] = {
    "darwin-arm64": {
        "pbs": f"cpython-{PBS_VERSION}+{PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz",
        "gui": True,
        "archive": "tar.gz",
    },
    "darwin-x64": {
        "pbs": f"cpython-{PBS_VERSION}+{PBS_TAG}-x86_64-apple-darwin-install_only.tar.gz",
        "gui": True,
        "archive": "tar.gz",
    },
    "linux-x64": {
        "pbs": f"cpython-{PBS_VERSION}+{PBS_TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz",
        "gui": False,
        "archive": "tar.gz",
    },
    "windows-x64": {
        "pbs": f"cpython-{PBS_VERSION}+{PBS_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz",
        "gui": False,
        "archive": "zip",
    },
}


def _project_version() -> str:
    pyproject = REPO_ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("version"):
            # version = "0.1.2"
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


def _detect_target() -> str:
    sysname = platform.system().lower()
    machine = platform.machine().lower()
    if sysname == "darwin":
        return "darwin-arm64" if machine in {"arm64", "aarch64"} else "darwin-x64"
    if sysname == "linux":
        return "linux-x64"
    if sysname == "windows":
        return "windows-x64"
    raise SystemExit(f"unsupported host for --target auto: {sysname}/{machine}")


def _pbs_url(filename: str) -> str:
    return (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{PBS_TAG}/{filename}"
    )


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 1_000_000:
        print(f"  cache hit: {dest.name}")
        return
    print(f"  downloading {url}")
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _extract_pbs(archive: Path, runtime_dir: Path) -> None:
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    runtime_dir.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="fawai-pbs-") as tmp:
        tmp_path = Path(tmp)
        if archive.suffixes[-2:] == [".tar", ".gz"] or archive.name.endswith(".tar.gz"):
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(tmp_path)
        elif archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(tmp_path)
        else:
            raise SystemExit(f"unknown archive type: {archive}")
        # install_only → top folder usually named "python"
        children = [p for p in tmp_path.iterdir() if not p.name.startswith(".")]
        if len(children) == 1 and children[0].is_dir():
            src = children[0]
        else:
            src = tmp_path
        for item in src.iterdir():
            shutil.move(str(item), str(runtime_dir / item.name))


def _runtime_python(runtime_dir: Path, target: str) -> Path:
    if target.startswith("windows"):
        for p in (
            runtime_dir / "python.exe",
            runtime_dir / "python" / "python.exe",
        ):
            if p.is_file():
                return p
        return runtime_dir / "python.exe"
    for p in (
        runtime_dir / "bin" / "python3",
        runtime_dir / "bin" / "python",
    ):
        if p.is_file():
            return p
    raise SystemExit(f"python binary missing under {runtime_dir}")


def _copy_app_tree(dest_app: Path) -> None:
    if dest_app.exists():
        shutil.rmtree(dest_app)
    dest_app.mkdir(parents=True)

    def ignore_junk(dirpath: str, names: list[str]) -> list[str]:
        skip = {
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".venv",
            "node_modules",
            ".DS_Store",
            "fawai_avatar.egg-info",
            "fawai_lore.egg-info",
        }
        out = [n for n in names if n in skip or n.endswith(".pyc")]
        # skip tests noise under lore doc if huge — keep doc
        return out

    shutil.copytree(
        REPO_ROOT / "fawai_avatar",
        dest_app / "fawai_avatar",
        ignore=ignore_junk,
    )
    shutil.copytree(
        REPO_ROOT / "fawai_lore",
        dest_app / "fawai_lore",
        ignore=ignore_junk,
    )
    # Optional shovels catalog (tools metadata)
    shovels = REPO_ROOT / "fawai_shovels"
    if shovels.is_dir():
        shutil.copytree(shovels, dest_app / "fawai_shovels", ignore=ignore_junk)


def _pip_install_deps(py: Path, deps_dir: Path, *, with_gui: bool) -> None:
    if deps_dir.exists():
        shutil.rmtree(deps_dir)
    deps_dir.mkdir(parents=True)
    subprocess.check_call([str(py), "-m", "ensurepip", "--upgrade"])
    reqs = [
        "openai>=1.40.0",
        "Pillow>=10.0.0",
        "fastapi>=0.115.0",
        "uvicorn[standard]>=0.30.0",
        "python-multipart>=0.0.9",
    ]
    if with_gui:
        reqs.extend(
            [
                "mss>=9.0.0",
                "pyautogui>=0.9.54",
                "pyobjc-framework-Cocoa>=10.0",
                "pyobjc-framework-Quartz>=10.0",
                "pyobjc-framework-ApplicationServices>=10.0",
            ]
        )
    cmd = [
        str(py),
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pip",
        "--target",
        str(deps_dir),
        *reqs,
    ]
    print("  pip:", " ".join(cmd[-6:]), "…")
    subprocess.check_call(cmd)


def _write_install_notes(prefix: Path) -> None:
    for lang, name in (("zh-CN", "INSTALL.zh-CN.txt"), ("en", "INSTALL.en.txt")):
        messages = json.loads((PACKAGING / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        body = messages["install_title"] + "\n\n" + messages["install_body"] + "\n"
        (prefix / name).write_text(body, encoding="utf-8")
    # Default pointer
    (prefix / "INSTALL.txt").write_text(
        "Language / 语言\n"
        "  English → INSTALL.en.txt\n"
        "  中文 → INSTALL.zh-CN.txt\n"
        "Launcher: FAWAI_LANG=zh-CN|en  or  Start --lang zh-CN|en\n",
        encoding="utf-8",
    )


def _write_start_wrappers(prefix: Path, target: str) -> None:
    # Unix — Start (run gateway) + Install (fresh / upgrade)
    start_sh = """#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/runtime/bin/python3"
if [[ ! -x "$PY" ]]; then PY="$ROOT/runtime/bin/python"; fi
exec "$PY" "$ROOT/launcher.py" "$@"
"""
    (prefix / "Start.sh").write_text(start_sh, encoding="utf-8")
    (prefix / "Start.sh").chmod(0o755)

    start_command = """#!/bin/bash
cd "$(dirname "$0")"
./Start.sh "$@"
"""
    (prefix / "Start.command").write_text(start_command, encoding="utf-8")
    (prefix / "Start.command").chmod(0o755)

    install_sh = """#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/runtime/bin/python3"
if [[ ! -x "$PY" ]]; then PY="$ROOT/runtime/bin/python"; fi
exec "$PY" "$ROOT/install.py" "$@"
"""
    (prefix / "Install.sh").write_text(install_sh, encoding="utf-8")
    (prefix / "Install.sh").chmod(0o755)

    install_command = """#!/bin/bash
cd "$(dirname "$0")"
./Install.sh "$@"
"""
    (prefix / "Install.command").write_text(install_command, encoding="utf-8")
    (prefix / "Install.command").chmod(0o755)

    # Windows
    start_bat = """@echo off
setlocal
set ROOT=%~dp0
set PY=%ROOT%runtime\\python.exe
if not exist "%PY%" set PY=%ROOT%runtime\\python\\python.exe
"%PY%" "%ROOT%launcher.py" %*
exit /b %ERRORLEVEL%
"""
    (prefix / "Start.bat").write_text(start_bat, encoding="utf-8")

    install_bat = """@echo off
setlocal
set ROOT=%~dp0
set PY=%ROOT%runtime\\python.exe
if not exist "%PY%" set PY=%ROOT%runtime\\python\\python.exe
"%PY%" "%ROOT%install.py" %*
exit /b %ERRORLEVEL%
"""
    (prefix / "Install.bat").write_text(install_bat, encoding="utf-8")

    # Only include platform-primary hint file
    if target.startswith("darwin"):
        (prefix / "START_HERE.txt").write_text(
            "1) Double-click Install.command → browser install page (smooth upgrade default)\n"
            "2) Double-click Start.command in the install folder\n"
            "1) 双击 Install.command → 浏览器打开安装页（默认平滑升级，保留 data）\n"
            "2) 到安装目录双击 Start.command\n",
            encoding="utf-8",
        )
    elif target.startswith("windows"):
        (prefix / "START_HERE.txt").write_text(
            "1) Double-click Install.bat → browser install page (smooth upgrade default)\n"
            "2) Double-click Start.bat in the install folder\n"
            "1) 双击 Install.bat → 浏览器打开安装页（默认平滑升级，保留 data）\n"
            "2) 到安装目录双击 Start.bat\n",
            encoding="utf-8",
        )
    else:
        (prefix / "START_HERE.txt").write_text(
            "1) ./Install.sh → browser install page (smooth upgrade default)\n"
            "2) Run Start.sh in the install folder\n"
            "1) ./Install.sh → 浏览器打开安装页（默认平滑升级，保留 data）\n"
            "2) 到安装目录运行 ./Start.sh\n",
            encoding="utf-8",
        )


def _copy_packaging_runtime_files(prefix: Path) -> None:
    shutil.copy2(PACKAGING / "launcher.py", prefix / "launcher.py")
    shutil.copy2(PACKAGING / "install.py", prefix / "install.py")
    i18n_dest = prefix / "i18n"
    if i18n_dest.exists():
        shutil.rmtree(i18n_dest)
    shutil.copytree(PACKAGING / "i18n", i18n_dest)
    example = REPO_ROOT / ".env.example"
    if example.is_file():
        shutil.copy2(example, prefix / ".env.example")


def _maybe_copy_frpc(prefix: Path, target: str) -> None:
    src = REPO_ROOT / "bin" / "frpc"
    if not src.is_file():
        return
    # Only ship when host build matches; CI per-OS will have matching binary if placed.
    if target.startswith("darwin") and sys.platform == "darwin":
        dest = prefix / "bin"
        dest.mkdir(exist_ok=True)
        shutil.copy2(src, dest / "frpc")
        (dest / "frpc").chmod(0o755)


def _archive(prefix: Path, archive_kind: str, out_path: Path) -> Path:
    """Create zip or tar.gz next to ``prefix``. ``out_path`` is the final file path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    base = prefix.name
    parent = prefix.parent
    if archive_kind == "zip":
        # make_archive adds .zip to the base name argument
        stem = str(out_path.with_suffix(""))
        produced = Path(shutil.make_archive(stem, "zip", root_dir=parent, base_dir=base))
        if produced != out_path and produced.exists():
            produced.rename(out_path)
        return out_path
    stem = str(out_path)
    if stem.endswith(".tar.gz"):
        stem = stem[: -len(".tar.gz")]
    produced = Path(shutil.make_archive(stem, "gztar", root_dir=parent, base_dir=base))
    if produced != out_path and produced.exists():
        produced.rename(out_path)
    return out_path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build(target: str, *, out_dir: Path, cache_dir: Path, skip_archive: bool) -> Path:
    if target == "auto":
        target = _detect_target()
    if target not in TARGETS:
        raise SystemExit(f"unknown target {target}; choose from {', '.join(TARGETS)}")

    meta = TARGETS[target]
    version = _project_version()
    name = f"fawai-avatar-{version}-{target}"
    prefix = out_dir / name
    print(f"==> building {name}")

    if prefix.exists():
        shutil.rmtree(prefix)
    prefix.mkdir(parents=True)

    # 1) runtime
    pbs_name = str(meta["pbs"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive_path = cache_dir / pbs_name
    _download(_pbs_url(pbs_name), archive_path)
    runtime_dir = prefix / "runtime"
    print("  extracting python runtime…")
    _extract_pbs(archive_path, runtime_dir)
    py = _runtime_python(runtime_dir, target)
    print(f"  python: {py}")

    # Cross-OS note: pip --target wheels must match target; require host==target family
    host = _detect_target()
    if host.split("-")[0] != target.split("-")[0] and not (
        host.startswith("darwin") and target.startswith("darwin")
    ):
        # darwin-arm64 vs darwin-x64: allow if arch matches via arch command — else warn
        if host != target:
            print(
                f"WARNING: building target={target} on host={host}. "
                "Native wheels may be wrong; prefer CI matrix on matching runners.",
                file=sys.stderr,
            )

    # 2) app sources (writable)
    print("  copying app sources…")
    _copy_app_tree(prefix / "app")

    # 3) deps
    print("  installing dependencies into deps/…")
    with_gui = bool(meta["gui"]) and target.startswith("darwin") and sys.platform == "darwin"
    if bool(meta["gui"]) and not with_gui:
        print("  skip macOS GUI wheels on non-Darwin builder")
    _pip_install_deps(py, prefix / "deps", with_gui=with_gui)

    # 4) launcher + i18n + notes
    _copy_packaging_runtime_files(prefix)
    _write_start_wrappers(prefix, target)
    _write_install_notes(prefix)
    (prefix / "data").mkdir(exist_ok=True)
    (prefix / "VERSION").write_text(
        json.dumps(
            {
                "version": version,
                "target": target,
                "python": PBS_VERSION,
                "pbs_tag": PBS_TAG,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _maybe_copy_frpc(prefix, target)

    if skip_archive:
        print(f"==> prefix ready: {prefix}")
        return prefix

    arch_kind = str(meta["archive"])
    if arch_kind == "zip":
        out_archive = out_dir / f"{name}.zip"
    else:
        out_archive = out_dir / f"{name}.tar.gz"
    print(f"  archiving → {out_archive.name}")
    built = _archive(prefix, arch_kind, out_archive)
    # normalize path
    if not built.exists():
        # find newest matching
        matches = sorted(out_dir.glob(f"{name}.*"))
        if not matches:
            raise SystemExit("archive missing after make_archive")
        built = matches[-1]
    checksum = _sha256(built)
    (built.with_suffix(built.suffix + ".sha256")).write_text(
        f"{checksum}  {built.name}\n", encoding="utf-8"
    )
    print(f"==> done: {built} ({checksum[:12]}…)")
    return built


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Fawai Avatar release prefix")
    parser.add_argument(
        "--target",
        default="auto",
        help="darwin-arm64|darwin-x64|linux-x64|windows-x64|auto",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "dist" / "release",
        help="Output directory for prefix + archives",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=REPO_ROOT / "dist" / "cache" / "python-build-standalone",
        help="Download cache for PBS archives",
    )
    parser.add_argument(
        "--skip-archive",
        action="store_true",
        help="Only build the directory prefix",
    )
    args = parser.parse_args(argv)
    build(
        args.target,
        out_dir=args.out_dir.resolve(),
        cache_dir=args.cache_dir.resolve(),
        skip_archive=args.skip_archive,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
