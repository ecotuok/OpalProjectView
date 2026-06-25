"""Taskbar identity helpers for Opal's native window — ctypes/COM only (no PowerShell,
no pip deps, works under the bundled PyInstaller Python).

Stamping our own AppUserModelID onto the window (and a matching Start-Menu shortcut)
makes the taskbar button show the Opal logo and group as 'Opal', instead of grouping
under python.exe (or showing the Edge logo). All Opal windows share one AUMID, so they
group together. Everything is best-effort: on failure it returns None/False and the
launcher falls back to an Edge --app window.
"""

import ctypes as C
import os

AUMID = "Opal.T24.Reader"     # neutral — no org name (public repo)
APP_NAME = "Opal"

CLSCTX_INPROC_SERVER = 1
VT_LPWSTR = 31


class GUID(C.Structure):
    _fields_ = [("Data1", C.c_uint32), ("Data2", C.c_uint16),
                ("Data3", C.c_uint16), ("Data4", C.c_ubyte * 8)]


class PROPERTYKEY(C.Structure):
    _fields_ = [("fmtid", GUID), ("pid", C.c_uint32)]


class PROPVARIANT(C.Structure):
    _fields_ = [("vt", C.c_ushort), ("r1", C.c_ushort), ("r2", C.c_ushort),
                ("r3", C.c_ushort), ("p", C.c_void_p), ("p2", C.c_void_p)]


def _guid(s):
    g = GUID()
    C.oledll.ole32.CLSIDFromString(C.c_wchar_p(s), C.byref(g))
    return g


def _vmethod(ptr, index, *argtypes):
    """Bind a COM vtable method by index -> callable(ptr, *args)."""
    vtable = C.cast(ptr, C.POINTER(C.c_void_p))[0]
    fn = C.cast(vtable, C.POINTER(C.c_void_p))[index]
    return C.WINFUNCTYPE(C.c_long, C.c_void_p, *argtypes)(fn)


def set_process_aumid(aumid=AUMID):
    try:
        C.windll.shell32.SetCurrentProcessExplicitAppUserModelID(C.c_wchar_p(aumid))
    except Exception:
        pass


def _dests(appname):
    out = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", appname + ".lnk"))
    home = os.environ.get("USERPROFILE")
    if home:
        out.append(os.path.join(home, "Desktop", appname + ".lnk"))
    return out


def create(target, args, icon, aumid=AUMID, appname=APP_NAME):
    """Write Opal.lnk (Start Menu + Desktop) for `target` (+ args), with `icon` and our
    AppUserModelID. Returns the Start-Menu path or None."""
    ole32 = C.oledll.ole32
    ole32.CoInitialize(None)
    keepalive = []      # hold Python buffers alive across the COM calls
    try:
        psl = C.c_void_p()
        ole32.CoCreateInstance(C.byref(_guid("{00021401-0000-0000-C000-000000000046}")), None,
                               CLSCTX_INPROC_SERVER,
                               C.byref(_guid("{000214F9-0000-0000-C000-000000000046}")), C.byref(psl))

        _vmethod(psl, 20, C.c_wchar_p)(psl, target)                          # SetPath
        _vmethod(psl, 11, C.c_wchar_p)(psl, args or "")                      # SetArguments
        _vmethod(psl, 9, C.c_wchar_p)(psl, os.path.dirname(target) or os.getcwd())  # SetWorkingDirectory
        _vmethod(psl, 17, C.c_wchar_p, C.c_int)(psl, icon, 0)                # SetIconLocation
        _vmethod(psl, 7, C.c_wchar_p)(psl, "Opal - T24 project reader")      # SetDescription

        qi = _vmethod(psl, 0, C.c_void_p, C.c_void_p)                 # IUnknown::QueryInterface

        # IPropertyStore -> PKEY_AppUserModel_ID
        pps = C.c_void_p()
        qi(psl, C.byref(_guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")), C.byref(pps))
        pkey = PROPERTYKEY(_guid("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"), 5)
        buf = C.create_unicode_buffer(aumid)
        keepalive.append(buf)
        pv = PROPVARIANT()
        pv.vt = VT_LPWSTR
        pv.p = C.cast(buf, C.c_void_p)
        _vmethod(pps, 6, C.c_void_p, C.c_void_p)(pps, C.byref(pkey), C.byref(pv))  # SetValue
        _vmethod(pps, 7)(pps)                                         # Commit
        _vmethod(pps, 2)(pps)                                         # Release

        # IPersistFile -> Save to each destination
        ppf = C.c_void_p()
        qi(psl, C.byref(_guid("{0000010B-0000-0000-C000-000000000046}")), C.byref(ppf))
        save = _vmethod(ppf, 6, C.c_wchar_p, C.c_int)
        start_menu = None
        for d in _dests(appname):
            try:
                os.makedirs(os.path.dirname(d), exist_ok=True)
                save(ppf, d, 1)
                if "Start Menu" in d:
                    start_menu = d
            except Exception:
                pass
        _vmethod(ppf, 2)(ppf)
        _vmethod(psl, 2)(psl)
        return start_menu
    finally:
        ole32.CoUninitialize()


def ensure(target, args, icon, appname=APP_NAME):
    """Best-effort create; returns the Start-Menu .lnk path, or None."""
    try:
        return create(target, args, icon, AUMID, appname)
    except Exception:
        return None


def _set_hwnd_aumid(hwnd, aumid):
    ole, sh = C.oledll.ole32, C.oledll.shell32
    ole.CoInitialize(None)
    pps = C.c_void_p()
    sh.SHGetPropertyStoreForWindow(hwnd, C.byref(_guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")), C.byref(pps))
    pkey = PROPERTYKEY(_guid("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"), 5)
    buf = C.create_unicode_buffer(aumid)
    pv = PROPVARIANT()
    pv.vt = VT_LPWSTR
    pv.p = C.cast(buf, C.c_void_p)
    _vmethod(pps, 6, C.c_void_p, C.c_void_p)(pps, C.byref(pkey), C.byref(pv))   # SetValue
    _vmethod(pps, 7)(pps)                                                       # Commit
    _vmethod(pps, 2)(pps)                                                       # Release


def set_window_aumid(aumid=AUMID, title_contains="Opal"):
    """Stamp our AUMID onto THIS process's top-level window(s) so the taskbar groups
    them as Opal (own button -> the window's Opal icon shows) instead of under python.exe.
    Matches hidden windows too, so it can run BEFORE the window is shown."""
    try:
        u32, k32 = C.windll.user32, C.windll.kernel32
        u32.GetWindowTextLengthW.argtypes = [C.c_void_p]
        u32.GetWindowTextW.argtypes = [C.c_void_p, C.c_wchar_p, C.c_int]
        pid = k32.GetCurrentProcessId()
        found = []

        @C.WINFUNCTYPE(C.c_bool, C.c_void_p, C.c_void_p)
        def _cb(hwnd, _l):
            wpid = C.c_ulong()
            u32.GetWindowThreadProcessId(hwnd, C.byref(wpid))
            if wpid.value == pid:
                n = u32.GetWindowTextLengthW(hwnd)
                if n:
                    b = C.create_unicode_buffer(n + 1)
                    u32.GetWindowTextW(hwnd, b, n + 1)
                    if title_contains.lower() in b.value.lower():
                        found.append(hwnd)
            return True

        u32.EnumWindows(_cb, 0)
        if not found:
            return False
        for h in found:
            _set_hwnd_aumid(h, aumid)
        return True
    except Exception:
        return False
