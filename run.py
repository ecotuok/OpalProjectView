"""Launch Opal — robust against stale instances and port conflicts.

Default (`py run.py` / Start.cmd):
    ensure deps -> retire ANY instance on our port (graceful, then force) ->
    if the port still won't free, fall back to the next free port (never 8765,
    Amethyst's) -> start uvicorn -> open one app window. This guarantees the
    window always talks to a FRESH backend (the classic stale-instance trap).

Open another window for a project (reuses the running server, no restart):
    py run.py --project gepg-integration
    py run.py --new                 (current default view, new window)

Force a clean restart even if one is healthy:
    py run.py --restart
"""

import argparse
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"
PREFERRED_PORT = 8766                       # Amethyst owns 8765 — we never touch it
PORT_RANGE = list(range(8766, 8780))        # fallback pool, excludes 8765
ROOT = Path(__file__).resolve().parent
REQUIRED = ["fastapi", "uvicorn", "yaml", "markdown", "pymdownx", "pygments", "webview"]
APPDIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Opal")

sys.path.insert(0, str(ROOT))               # so we can import app.win (stdlib-only)
from app.win import open_app_window          # noqa: E402


# ── native window (own taskbar icon) ────────────────────────────────────────
def _stable_icon():
    """Copy the Opal .ico to a stable path (a one-file build's bundled dir is a temp
    folder that vanishes on exit) so the taskbar icon survives the window closing."""
    src = ROOT / "static" / "favicon.ico"
    dst = os.path.join(APPDIR, "opal.ico")
    if src.is_file():
        os.makedirs(APPDIR, exist_ok=True)
        try:
            import shutil
            shutil.copyfile(src, dst)
        except Exception:
            pass
    return dst if os.path.isfile(dst) else None


def _ensure_shortcut(icon):
    """A gem… er, Opal-iconed launcher shortcut (Start Menu + Desktop) for pinning,
    carrying our AUMID so a pinned entry keeps the Opal icon."""
    try:
        from app import winshortcut
        if not icon:
            return
        if getattr(sys, "frozen", False):
            target, args = sys.executable, ""
        else:
            cmd = str(ROOT / "Start.cmd")
            target, args = (cmd, "") if os.path.isfile(cmd) else (sys.executable, f'"{ROOT / "run.py"}"')
        winshortcut.ensure(target, args, icon)
    except Exception:
        pass


def _run_window(url):
    """A single Opal window as its OWN process (so closing it never stops the server,
    and `--new`/`--project` still open extra windows). pywebview WebView2 window stamped
    with our AUMID + icon so the taskbar button shows the Opal logo, not Edge/python."""
    icon = _stable_icon()
    winshortcut = None
    try:
        from app import winshortcut as _ws
        winshortcut = _ws
        winshortcut.set_process_aumid()
    except Exception:
        pass
    try:
        import webview
    except Exception:
        from app.win import edge_app
        edge_app(url)                       # no WebView2 -> Edge --app window
        return

    win = webview.create_window("Opal", url, width=1600, height=1000, hidden=True)

    def identity_then_show():
        try:
            if winshortcut:
                for _ in range(60):         # stamp AUMID while hidden, then show
                    if winshortcut.set_window_aumid():
                        break
                    time.sleep(0.05)
        finally:
            try:
                win.show()
            except Exception:
                pass

    try:
        webview.start(identity_then_show, icon=icon)
    except TypeError:                       # older pywebview without start(icon=...)
        webview.start(identity_then_show)


# ── deps ──────────────────────────────────────────────────────────────────
def ensure_deps():
    missing = [m for m in REQUIRED if importlib.util.find_spec(m) is None]
    if not missing:
        return
    print(f"first run / new dependency — installing: {', '.join(missing)}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
    print("dependencies ready.")


# ── port / instance helpers ────────────────────────────────────────────────
def _busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((HOST, port)) == 0


def _is_opal(port):
    """True if a healthy Opal is answering on this port."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/version", timeout=1.5) as r:
            return json.loads(r.read()).get("app") == "Opal"
    except Exception:
        return False


def _pids_on(port):
    pids = set()
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return pids
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line:
            parts = line.split()
            if parts[1].endswith(f":{port}") and parts[-1].isdigit() and parts[-1] != "0":
                pids.add(parts[-1])
    return pids


def _force_kill(port):
    for pid in _pids_on(port):
        print(f"  force-killing PID {pid} on port {port}…")
        try:
            subprocess.run(["taskkill", "/PID", pid, "/F", "/T"], capture_output=True, timeout=5)
        except Exception:
            pass


def retire(port):
    """Free `port`: ask an Opal to exit gracefully, else force-kill. Returns True if freed."""
    if not _busy(port):
        return True
    print(f"port {port} in use — retiring the existing instance…")
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://{HOST}:{port}/api/shutdown",
                                                      method="POST"), timeout=3)
    except Exception:
        pass
    for _ in range(12):
        if not _busy(port):
            print("  …retired gracefully.")
            return True
        time.sleep(0.25)
    _force_kill(port)
    for _ in range(16):
        if not _busy(port):
            print("  …port freed.")
            return True
        time.sleep(0.25)
    return False


def choose_port():
    """Free the preferred port, or fall back to the next free one in our pool."""
    if retire(PREFERRED_PORT):
        return PREFERRED_PORT
    for p in PORT_RANGE:
        if not _busy(p):
            print(f"falling back to free port {p}.")
            return p
    print("WARNING: no free port in range; using preferred anyway.")
    return PREFERRED_PORT


def open_window(port, project="", doc=""):
    for _ in range(60):                      # wait for uvicorn to bind
        if _busy(port):
            break
        time.sleep(0.1)
    from urllib.parse import urlencode
    q = {k: v for k, v in (("project", project), ("doc", doc)) if v}
    url = f"http://{HOST}:{port}/" + (("?" + urlencode(q)) if q else "")
    open_app_window(url)


# ── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Launch Opal")
    ap.add_argument("--project", default="", help="open a window deep-linked to this project")
    ap.add_argument("--doc", default="", help="deep-link to a specific doc path")
    ap.add_argument("--new", action="store_true", help="just open another window on a running Opal")
    ap.add_argument("--restart", action="store_true", help="force a clean restart")
    ap.add_argument("--window-only", default="", help=argparse.SUPPRESS)   # internal: just a window at this URL
    args = ap.parse_args()

    # Internal: this process is JUST an Opal window (spawned by app.win.open_app_window).
    # The server lives in its own process, so closing a window never stops Opal.
    if args.window_only:
        _run_window(args.window_only)
        return

    ensure_deps()
    _ensure_shortcut(_stable_icon())          # Opal-iconed Start-Menu/Desktop shortcut for pinning

    # Reuse a healthy instance for extra windows (the multi-window path) —
    # no second server, no port conflict.
    if (args.new or args.project or args.doc) and not args.restart and _is_opal(PREFERRED_PORT):
        print("reusing the running Opal — opening a new window…")
        open_window(PREFERRED_PORT, args.project, args.doc)
        return

    port = choose_port()

    import threading
    import uvicorn

    threading.Thread(target=open_window, args=(port, args.project, args.doc), daemon=True).start()
    print(f"Opal on http://{HOST}:{port}")
    uvicorn.run("app.main:app", host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
