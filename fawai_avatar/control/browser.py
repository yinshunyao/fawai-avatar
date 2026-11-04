# term:浏览器操作 tool:browser_* doc:doc/功能/浏览器操作/说明.md
"""Playwright 浏览器控制：无头可用，不占前台键鼠租约。

依赖可选 extras ``[browser]``；首次使用前需 ``playwright install chromium``。
默认无头：``FAWAI_BROWSER_HEADLESS=1``（设 ``0`` 可见窗口）。
"""
from __future__ import annotations

import atexit
import os
import sys
import threading
from typing import Any

from fawai_avatar.types import Action, ExecResult

BROWSER_KINDS = frozenset(
    {
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_close",
    }
)

_MAX_NODES = 80
_NAME_MAX = 80
_ATTR = "data-fawai-ref"

_lock = threading.RLock()
_playwright: Any | None = None
_browser: Any | None = None
_context: Any | None = None
_page: Any | None = None
# ref(str) -> meta
_REF_NODES: dict[str, dict[str, Any]] = {}


def browser_package_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _env_flag(name: str, *, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw == "":
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _timeout_ms() -> int:
    raw = (os.environ.get("FAWAI_BROWSER_TIMEOUT_MS") or "").strip()
    if not raw:
        return 30000
    try:
        return max(1000, int(raw))
    except ValueError:
        return 30000


def _install_hint() -> str:
    exe = sys.executable
    return (
        f"请用当前解释器安装：{exe} -m pip install -e '.[browser]' "
        f"&& {exe} -m playwright install chromium"
    )


def clear_browser_registry() -> None:
    with _lock:
        _REF_NODES.clear()


def get_browser_node(ref: str) -> dict[str, Any] | None:
    key = str(ref or "").strip()
    if not key:
        return None
    with _lock:
        node = _REF_NODES.get(key)
        return dict(node) if node else None


def close_browser_session() -> None:
    """关闭全局会话（幂等）。"""
    global _playwright, _browser, _context, _page
    with _lock:
        clear_browser_registry()
        for obj, method in (
            (_page, "close"),
            (_context, "close"),
            (_browser, "close"),
            (_playwright, "stop"),
        ):
            if obj is None:
                continue
            try:
                getattr(obj, method)()
            except Exception:  # noqa: BLE001 — 关闭尽力
                pass
        _page = None
        _context = None
        _browser = None
        _playwright = None


atexit.register(close_browser_session)


def _ensure_page() -> Any:
    global _playwright, _browser, _context, _page
    if not browser_package_available():
        raise RuntimeError(f"未安装 playwright。{_install_hint()}")
    if _page is not None:
        return _page
    from playwright.sync_api import sync_playwright

    headless = _env_flag("FAWAI_BROWSER_HEADLESS", default=True)
    channel = (os.environ.get("FAWAI_BROWSER_CHANNEL") or "").strip() or None
    pw = sync_playwright().start()
    launch_kwargs: dict[str, Any] = {"headless": headless}
    if channel:
        launch_kwargs["channel"] = channel
    try:
        browser = pw.chromium.launch(**launch_kwargs)
    except Exception as exc:  # noqa: BLE001
        try:
            pw.stop()
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(
            f"启动 Chromium 失败：{exc}。{_install_hint()}"
        ) from exc
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(_timeout_ms())
    _playwright = pw
    _browser = browser
    _context = context
    _page = page
    return page


_MARK_SCRIPT = f"""
() => {{
  const ATTR = {_ATTR!r};
  document.querySelectorAll('[' + ATTR + ']').forEach((el) => el.removeAttribute(ATTR));
  const sel = [
    'a[href]', 'button', 'input', 'textarea', 'select',
    '[role="button"]', '[role="link"]', '[role="textbox"]',
    '[role="checkbox"]', '[role="radio"]', '[role="tab"]',
    '[role="menuitem"]', '[contenteditable="true"]'
  ].join(',');
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(sel)) {{
    if (out.length >= {_MAX_NODES}) break;
    if (!(el instanceof HTMLElement)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) continue;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') continue;
    if (seen.has(el)) continue;
    seen.add(el);
    const ref = String(out.length + 1);
    el.setAttribute(ATTR, ref);
    const role = (el.getAttribute('role') || el.tagName || '').toLowerCase();
    let name = (
      el.getAttribute('aria-label')
      || el.getAttribute('placeholder')
      || el.getAttribute('title')
      || el.getAttribute('name')
      || (el.innerText || '')
      || (el.value || '')
      || ''
    ).replace(/\\s+/g, ' ').trim().slice(0, {_NAME_MAX});
    out.push({{
      ref,
      role,
      name,
      tag: (el.tagName || '').toLowerCase(),
      type: (el.getAttribute('type') || ''),
      href: (el.href || ''),
    }});
  }}
  return {{
    title: document.title || '',
    url: location.href || '',
    nodes: out,
  }};
}}
"""


def _snapshot_page(page: Any) -> ExecResult:
    data = page.evaluate(_MARK_SCRIPT)
    nodes = list(data.get("nodes") or [])
    with _lock:
        _REF_NODES.clear()
        for n in nodes:
            ref = str(n.get("ref") or "").strip()
            if not ref:
                continue
            _REF_NODES[ref] = dict(n)
    lines = [
        f"浏览器页：{data.get('title') or '(无标题)'}",
        f"URL：{data.get('url') or ''}",
        "可交互控件（用 browser_click/browser_type 的 args.ref=编号）：",
    ]
    if not nodes:
        lines.append("（无可交互控件）")
    else:
        for n in nodes:
            href = f" href={n.get('href')}" if n.get("href") else ""
            typ = f" type={n.get('type')}" if n.get("type") else ""
            lines.append(
                f"- [{n.get('ref')}] {n.get('role') or n.get('tag')} "
                f"{(n.get('name') or '')!r}{typ}{href}"
            )
    return ExecResult(
        ok=True,
        message="\n".join(lines),
        data={
            "title": data.get("title") or "",
            "url": data.get("url") or "",
            "nodes": nodes,
            "headless": _env_flag("FAWAI_BROWSER_HEADLESS", default=True),
        },
    )


def _resolve_locator(page: Any, args: dict[str, Any]) -> tuple[Any | None, str]:
    ref = str(args.get("ref") or "").strip()
    selector = str(args.get("selector") or args.get("css") or "").strip()
    if ref:
        node = get_browser_node(ref)
        if node is None:
            return None, f"未知 ref={ref!r}；请先 browser_snapshot"
        loc = page.locator(f"[{_ATTR}='{ref}']").first
        return loc, "ref"
    if selector:
        return page.locator(selector).first, "selector"
    return None, "missing"


class BrowserTools:
    """同步 Playwright 会话；进程内单例页。"""

    def execute(self, action: Action) -> ExecResult:
        kind = (action.kind or "").strip().lower()
        if kind not in BROWSER_KINDS:
            return ExecResult(ok=False, message=f"not a browser kind: {kind!r}")
        try:
            if kind == "browser_close":
                close_browser_session()
                return ExecResult(ok=True, message="browser closed", data={"closed": True})
            if kind == "browser_navigate":
                return self._navigate(action.args or {})
            if kind == "browser_snapshot":
                return self._snapshot(action.args or {})
            if kind == "browser_click":
                return self._click(action.args or {})
            if kind == "browser_type":
                return self._type(action.args or {})
            if kind == "browser_press":
                return self._press(action.args or {})
            return ExecResult(ok=False, message=f"unhandled browser kind: {kind!r}")
        except Exception as exc:  # noqa: BLE001
            return ExecResult(ok=False, message=str(exc))

    def _navigate(self, args: dict[str, Any]) -> ExecResult:
        url = str(args.get("url") or args.get("href") or "").strip()
        if not url:
            return ExecResult(ok=False, message="browser_navigate 需要 args.url")
        if "://" not in url:
            url = "https://" + url
        page = _ensure_page()
        wait_until = str(args.get("wait_until") or "domcontentloaded").strip()
        page.goto(url, wait_until=wait_until, timeout=_timeout_ms())
        clear_browser_registry()
        return ExecResult(
            ok=True,
            message=f"navigated {page.url}",
            data={"url": page.url, "title": page.title()},
        )

    def _snapshot(self, args: dict[str, Any]) -> ExecResult:
        page = _ensure_page()
        # 允许无导航先 snapshot（空白页）
        _ = args
        return _snapshot_page(page)

    def _click(self, args: dict[str, Any]) -> ExecResult:
        page = _ensure_page()
        loc, how = _resolve_locator(page, args)
        if loc is None:
            return ExecResult(
                ok=False,
                message=how if how != "missing" else "browser_click 需要 args.ref 或 args.selector",
            )
        button = str(args.get("button") or "left").strip().lower()
        click_kwargs: dict[str, Any] = {}
        if button == "right":
            click_kwargs["button"] = "right"
        elif button == "double":
            click_kwargs["click_count"] = 2
        loc.click(**click_kwargs)
        return ExecResult(
            ok=True,
            message=f"browser_click via {how}",
            data={"how": how, "ref": args.get("ref"), "selector": args.get("selector")},
        )

    def _type(self, args: dict[str, Any]) -> ExecResult:
        page = _ensure_page()
        text = args.get("text")
        if text is None:
            text = args.get("value")
        if text is None:
            return ExecResult(ok=False, message="browser_type 需要 args.text")
        text = str(text)
        loc, how = _resolve_locator(page, args)
        if loc is None:
            return ExecResult(
                ok=False,
                message=how if how != "missing" else "browser_type 需要 args.ref 或 args.selector",
            )
        clear = _env_flag("FAWAI_BROWSER_TYPE_CLEAR", default=True)
        if "clear" in args:
            clear = bool(args.get("clear"))
        if clear:
            loc.fill(text)
        else:
            loc.click()
            page.keyboard.type(text)
        return ExecResult(
            ok=True,
            message=f"browser_type via {how} ({len(text)} chars)",
            data={"how": how, "ref": args.get("ref"), "len": len(text)},
        )

    def _press(self, args: dict[str, Any]) -> ExecResult:
        page = _ensure_page()
        key = str(args.get("key") or args.get("keys") or "").strip()
        if not key:
            return ExecResult(ok=False, message="browser_press 需要 args.key（如 Enter/Tab/Escape）")
        # 若给了 ref，先聚焦
        loc, how = _resolve_locator(page, args)
        if loc is not None and how in {"ref", "selector"}:
            loc.focus()
        page.keyboard.press(key)
        return ExecResult(ok=True, message=f"browser_press {key}", data={"key": key})
