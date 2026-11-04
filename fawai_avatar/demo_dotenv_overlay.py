# term:服务保活  doc:doc/功能/服务保活/说明.md
"""冒烟：磁盘 .env 覆盖进程旧环境（重启不回退 Web 模型配置）。"""
from __future__ import annotations

from pathlib import Path


def _write_env(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_overlay_disk_wins(tmp_path: Path | None = None) -> None:
    from fawai_avatar.config import overlay_dotenv, parse_dotenv_pairs

    root = tmp_path or Path(__file__).resolve().parents[1] / "data" / "_demo_dotenv"
    root.mkdir(parents=True, exist_ok=True)
    env_path = root / ".env"
    _write_env(
        env_path,
        "FAWAI_LLM_TEXT_MODEL=qwen3.7-plus\n"
        "FAWAI_LLM_FAST_MODEL=qwen3.8-27b\n"
        "# comment\n"
        "FAWAI_DRY_RUN=0\n",
    )
    pairs = dict(parse_dotenv_pairs(env_path))
    assert pairs["FAWAI_LLM_TEXT_MODEL"] == "qwen3.7-plus"
    assert "comment" not in pairs

    # 模拟保活父进程仍持有 Web 改之前的旧值
    child = {
        "FAWAI_LLM_TEXT_MODEL": "qwen3.8-max",
        "FAWAI_LLM_FAST_MODEL": "qwen3.7-flash",
        "PATH": "/usr/bin",
    }
    overlay_dotenv(child, env_path)
    assert child["FAWAI_LLM_TEXT_MODEL"] == "qwen3.7-plus"
    assert child["FAWAI_LLM_FAST_MODEL"] == "qwen3.8-27b"
    assert child["FAWAI_DRY_RUN"] == "0"
    assert child["PATH"] == "/usr/bin"


def test_load_dotenv_override(tmp_path: Path | None = None) -> None:
    import os

    from fawai_avatar.config import load_dotenv

    root = tmp_path or Path(__file__).resolve().parents[1] / "data" / "_demo_dotenv"
    root.mkdir(parents=True, exist_ok=True)
    env_path = root / ".env.override"
    _write_env(env_path, "FAWAI_DEMO_DOTENV_KEY=from-disk\n")
    os.environ["FAWAI_DEMO_DOTENV_KEY"] = "from-process"
    try:
        load_dotenv(env_path, override=False)
        assert os.environ["FAWAI_DEMO_DOTENV_KEY"] == "from-process"
        load_dotenv(env_path, override=True)
        assert os.environ["FAWAI_DEMO_DOTENV_KEY"] == "from-disk"
    finally:
        os.environ.pop("FAWAI_DEMO_DOTENV_KEY", None)
        env_path.unlink(missing_ok=True)


def main() -> None:
    test_overlay_disk_wins()
    test_load_dotenv_override()
    print("demo_dotenv_overlay: all passed")


if __name__ == "__main__":
    main()
