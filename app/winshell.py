"""Windows taskbar identity for the small portable app.

The app runs as python\\pythonw.exe, so without help Windows would pin "pythonw.exe" to the taskbar.
Two fixes, both through the shell's property stores (plain ctypes COM, no extra packages):
  * the main window carries the app ID plus a relaunch command (the launcher exe), name and icon,
    so pinning the running window pins Ai Book Reader;
  * shortcuts made by the app carry the same app ID, so a pinned shortcut and the running window
    share one taskbar button."""
from __future__ import annotations

import ctypes
import os
import sys
import uuid
from ctypes import POINTER, byref, c_void_p, wintypes
from pathlib import Path

APP_ID = "Farathim.AiBookReader"
_PKEY_FMTID = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"        # System.AppUserModel.*
PID_RELAUNCH_COMMAND, PID_RELAUNCH_ICON, PID_RELAUNCH_NAME, PID_ID = 2, 3, 4, 5
IID_IPropertyStore = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010B-0000-0000-C000-000000000046}"
VT_LPWSTR = 31


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        g = cls()
        ctypes.memmove(byref(g), uuid.UUID(text).bytes_le, 16)
        return g


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):           # 24 bytes on 64-bit: vt + 3 reserved + 16-byte union
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("pwszVal", ctypes.c_wchar_p), ("pad", c_void_p)]


def _method(obj: c_void_p, index: int, *argtypes):
    """Call slot `index` of a COM object's vtable."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)
    fn = proto(vtbl[index])
    return lambda *args: fn(obj, *args)


def _release(obj: c_void_p) -> None:
    if obj:
        vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtbl[2])(obj)


def _set_strings(store: c_void_p, values: dict[int, str]) -> None:
    set_value = _method(store, 6, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))     # IPropertyStore::SetValue
    for pid, text in values.items():
        key = PROPERTYKEY(GUID.of(_PKEY_FMTID), pid)
        var = PROPVARIANT(vt=VT_LPWSTR, pwszVal=text)
        set_value(byref(key), byref(var))
    _method(store, 7)()                                                          # Commit


def _get_string(store: c_void_p, pid: int) -> str:
    key = PROPERTYKEY(GUID.of(_PKEY_FMTID), pid)
    var = PROPVARIANT()
    _method(store, 5, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))(byref(key), byref(var))   # GetValue
    try:
        return (var.pwszVal or "") if var.vt == VT_LPWSTR else ""
    finally:
        ctypes.WinDLL("ole32").PropVariantClear(byref(var))


def read_app_id(path: Path) -> str:
    """The app ID stored in a shortcut ('' when it has none)."""
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHGetPropertyStoreFromParsingName.argtypes = [wintypes.LPCWSTR, c_void_p, ctypes.c_int, POINTER(GUID),
                                                          POINTER(c_void_p)]
    shell32.SHGetPropertyStoreFromParsingName.restype = ctypes.HRESULT
    store = c_void_p()
    try:
        shell32.SHGetPropertyStoreFromParsingName(str(path), None, 0, byref(GUID.of(IID_IPropertyStore)), byref(store))
        return _get_string(store, PID_ID)
    finally:
        _release(store)


def window_relaunch_command(hwnd: int) -> str:
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND, POINTER(GUID), POINTER(c_void_p)]
    shell32.SHGetPropertyStoreForWindow.restype = ctypes.HRESULT
    store = c_void_p()
    try:
        shell32.SHGetPropertyStoreForWindow(wintypes.HWND(hwnd), byref(GUID.of(IID_IPropertyStore)), byref(store))
        return _get_string(store, PID_RELAUNCH_COMMAND)
    finally:
        _release(store)


def launcher() -> Path | None:
    """The portable's 'Ai Book Reader.exe', when running from the small portable."""
    from .setup_runtime import is_lite
    from .paths import app_dir
    exe = app_dir() / "Ai Book Reader.exe"
    return exe if is_lite() and exe.exists() else None


def set_window_identity(hwnd: int, exe: Path | None = None, name: str = "Ai Book Reader") -> bool:
    """Give a top-level window the app ID and relaunch info (what a taskbar pin starts)."""
    exe = exe or launcher()
    if sys.platform != "win32" or not exe:
        return False
    store = c_void_p()
    try:
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND, POINTER(GUID), POINTER(c_void_p)]
        shell32.SHGetPropertyStoreForWindow.restype = ctypes.HRESULT
        shell32.SHGetPropertyStoreForWindow(wintypes.HWND(hwnd), byref(GUID.of(IID_IPropertyStore)), byref(store))
        # relaunch properties first; Windows uses them only together with the app ID
        _set_strings(store, {PID_RELAUNCH_COMMAND: f'"{exe}"', PID_RELAUNCH_NAME: name,
                             PID_RELAUNCH_ICON: f"{exe},0", PID_ID: APP_ID})
        return True
    except OSError:
        return False
    finally:
        _release(store)


def make_shortcut(path: Path, target: Path, description: str = "Ai Book Reader", args: str = "") -> None:
    """Write a .lnk that carries the app ID (so it shares the taskbar button with the window)."""
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitialize(None)
    link, store, persist = c_void_p(), c_void_p(), c_void_p()
    try:
        hr = ole32.CoCreateInstance(byref(GUID.of(CLSID_ShellLink)), None, 1,         # CLSCTX_INPROC_SERVER
                                    byref(GUID.of(IID_IShellLinkW)), byref(link))
        if hr != 0:
            raise OSError(f"CoCreateInstance failed: {hr:#x}")
        w = wintypes.LPCWSTR
        _method(link, 20, w)(str(target))                       # SetPath
        _method(link, 11, w)(args)                              # SetArguments
        _method(link, 9, w)(str(target.parent))                 # SetWorkingDirectory
        _method(link, 7, w)(description)                        # SetDescription
        _method(link, 17, w, ctypes.c_int)(str(target), 0)      # SetIconLocation
        qi = _method(link, 0, POINTER(GUID), POINTER(c_void_p))
        qi(byref(GUID.of(IID_IPropertyStore)), byref(store))
        _set_strings(store, {PID_ID: APP_ID})
        qi(byref(GUID.of(IID_IPersistFile)), byref(persist))
        path.parent.mkdir(parents=True, exist_ok=True)
        _method(persist, 6, w, wintypes.BOOL)(str(path), True)  # IPersistFile::Save
    finally:
        for obj in (persist, store, link):
            _release(obj)


def shortcut_paths(name: str = "Ai Book Reader") -> list[Path]:
    desktop = Path(os.environ.get("USERPROFILE", "~")).expanduser() / "Desktop"
    try:                                                       # the real Desktop (OneDrive can move it)
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:
            desktop = Path(buf.value)
    except (OSError, AttributeError):
        pass
    start = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return [desktop / f"{name}.lnk", start / f"{name}.lnk"]


def make_shortcuts() -> list[Path]:
    """Desktop + Start menu shortcuts to the portable's launcher. Returns what was written."""
    exe = launcher()
    if not exe:
        raise OSError("Shortcuts can only be made from the portable app.")
    done = []
    for p in shortcut_paths():
        make_shortcut(p, exe)
        done.append(p)
    return done
