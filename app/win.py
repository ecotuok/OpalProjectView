"""Open an Opal window at a URL.

Primary path: spawn a separate `run.py --window-only <url>` process — a native WebView2
window (pywebview) that stamps Opal's AppUserModelID + icon, so its taskbar button shows
the Opal logo (not Edge), and closing it never stops the server. Every window is its own
process, which preserves Opal's multi-window model (--new / --project / /api/open).

Fallback: an Edge/Chrome --app window (shows the Edge taskbar icon, but works everywhere).

Stdlib only, so run.py can import this before any third-party deps exist.
"""

import os
import subprocess
import sys
import webbrowser

BROWSERS = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
]


def _profile_dir() -> str:
    # dedicated browser profile (NOT in a cloud-synced folder) so Chromium remembers Opal's
    # window size/position across launches instead of reusing the default profile.
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Opal", "browser-profile")


def edge_app(url: str) -> bool:
    """Open url in a dedicated Edge/Chrome --app window (the fallback)."""
    profile = _profile_dir()
    first_run = not os.path.isdir(profile)              # only size the very first launch
    extra = [f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check"]
    if first_run:
        extra.append("--window-size=1600,1000")
    for cand in BROWSERS:
        exe = os.path.expandvars(cand)
        if os.path.isfile(exe):
            subprocess.Popen([exe, f"--app={url}", *extra])
            return True
    webbrowser.open(url)
    return False


def open_app_window(url: str) -> bool:
    """Open `url` in Opal's own native window (a separate --window-only process).
    Falls back to an Edge --app window if that can't be spawned."""
    run_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py")
    cmd = [sys.executable] + ([] if getattr(sys, "frozen", False) else [run_py]) + ["--window-only", url]
    flags = 0x08000000 if os.name == "nt" else 0        # CREATE_NO_WINDOW — no console flash
    try:
        subprocess.Popen(cmd, creationflags=flags)
        return True
    except Exception:
        return edge_app(url)
