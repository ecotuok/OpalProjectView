"""Open a borderless browser 'app' window (Edge/Chrome) at a URL.

Shared by run.py (first window) and /api/open (extra windows per project).
Stdlib only, so run.py can import it before any third-party deps exist.
"""

import os
import subprocess
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


def open_app_window(url: str) -> bool:
    """Open url in a dedicated --app window; remembers its own size/position.
    Falls back to the default browser if Edge/Chrome aren't found."""
    profile = _profile_dir()
    first_run = not os.path.isdir(profile)              # only size the very first launch
    extra = [f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check"]
    if first_run:
        extra.append("--window-size=1600,1000")         # afterwards: whatever you resized to
    for cand in BROWSERS:
        exe = os.path.expandvars(cand)
        if os.path.isfile(exe):
            subprocess.Popen([exe, f"--app={url}", *extra])
            return True
    webbrowser.open(url)
    return False
