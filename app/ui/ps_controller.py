"""PlayStation controllers (DualShock 4, DualSense / Edge) over USB or Bluetooth for TV mode.

They don't speak XInput, so they are read as plain HID devices with Windows' own hid.dll and
setupapi.dll (ctypes, no extra packages). A background thread waits for input reports; the
TV-mode poll timer reads the newest one. Only reading - nothing is sent to the controller."""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes

SONY = 0x054C
PRODUCTS = {0x05C4: "DualShock 4", 0x09CC: "DualShock 4", 0x0BA0: "DualShock 4", 0x0CE6: "DualSense",
            0x0DF2: "DualSense Edge"}
DUALSENSE = {0x0CE6, 0x0DF2}
HAT = {0: {"up"}, 1: {"up", "right"}, 2: {"right"}, 3: {"down", "right"}, 4: {"down"}, 5: {"down", "left"},
       6: {"left"}, 7: {"up", "left"}}
FACE = {0x10: "x", 0x20: "a", 0x40: "b", 0x80: "y"}          # square, cross, circle, triangle
SHOULDER = {0x01: "lb", 0x02: "rb", 0x10: "back", 0x20: "start"}   # L1, R1, Share/Create, Options
STICK_LO, STICK_HI, TRIGGER = 64, 192, 120


def parse_report(data: bytes, dualsense: bool, input_len: int) -> tuple[set[str], int] | None:
    """Button names (same as the Xbox ones: a = cross, b = circle...) and the right stick's
    volume direction (+1 up, -1 down, 0) from one input report, or None for other reports."""
    if not data:
        return None
    rid = data[0]
    if dualsense and rid == 0x01 and input_len == 64:      # DualSense over USB
        layout, off = "ds", 1
    elif dualsense and rid == 0x31:                         # DualSense over Bluetooth, full report
        layout, off = "ds", 2
    elif rid == 0x01:                                       # DualShock 4 (USB / Bluetooth basic), DualSense BT basic
        layout, off = "ds4", 1
    elif rid == 0x11:                                       # DualShock 4 over Bluetooth, full report
        layout, off = "ds4", 3
    else:
        return None
    need = off + (9 if layout == "ds" else 9)
    if len(data) < need:
        return None
    lx, ly, _rx, ry = data[off:off + 4]
    if layout == "ds4":
        b1, b2, l2, r2 = data[off + 4], data[off + 5], data[off + 7], data[off + 8]
    else:
        l2, r2, b1, b2 = data[off + 4], data[off + 5], data[off + 7], data[off + 8]
    names = set(HAT.get(b1 & 0x0F, ()))
    names |= {n for bit, n in FACE.items() if b1 & bit}
    names |= {n for bit, n in SHOULDER.items() if b2 & bit}
    if lx < STICK_LO:
        names.add("left")
    elif lx > STICK_HI:
        names.add("right")
    if ly < STICK_LO:
        names.add("up")
    elif ly > STICK_HI:
        names.add("down")
    if l2 > TRIGGER:
        names.add("lt")
    if r2 > TRIGGER:
        names.add("rt")
    vol = 1 if ry < STICK_LO else -1 if ry > STICK_HI else 0
    return names, vol


# ------------------------------------------------------------------------------------------ Windows HID
class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID), ("Flags", wintypes.DWORD),
                ("Reserved", ctypes.c_size_t)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT), ("ProductID", wintypes.USHORT),
                ("VersionNumber", wintypes.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
                ("InputReportByteLength", wintypes.USHORT), ("OutputReportByteLength", wintypes.USHORT),
                ("FeatureReportByteLength", wintypes.USHORT), ("Reserved", wintypes.USHORT * 17),
                ("NumberLinkCollectionNodes", wintypes.USHORT), ("NumberInputButtonCaps", wintypes.USHORT),
                ("NumberInputValueCaps", wintypes.USHORT), ("NumberInputDataIndices", wintypes.USHORT),
                ("NumberOutputButtonCaps", wintypes.USHORT), ("NumberOutputValueCaps", wintypes.USHORT),
                ("NumberOutputDataIndices", wintypes.USHORT), ("NumberFeatureButtonCaps", wintypes.USHORT),
                ("NumberFeatureValueCaps", wintypes.USHORT), ("NumberFeatureDataIndices", wintypes.USHORT)]


GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_RW, OPEN_EXISTING = 0x3, 3
INVALID = ctypes.c_void_p(-1).value


def _dlls():
    if sys.platform != "win32":
        return None
    try:
        hid, setup, k32 = ctypes.WinDLL("hid"), ctypes.WinDLL("setupapi"), ctypes.WinDLL("kernel32",
                                                                                          use_last_error=True)
    except OSError:
        return None
    setup.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setup.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setup.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID),
                                                  wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
    setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                       ctypes.c_void_p, wintypes.DWORD,
                                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    setup.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                wintypes.DWORD, ctypes.c_void_p]
    k32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                             ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    hid.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDD_ATTRIBUTES)]
    hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
    return hid, setup, k32


DLLS = None


def _get_dlls():
    global DLLS
    if DLLS is None:
        DLLS = _dlls() or False
    return DLLS or None


def find_controllers() -> list[tuple[str, int]]:
    """(device path, product id) of the connected Sony controllers."""
    d = _get_dlls()
    if not d:
        return []
    hid, setup, k32 = d
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    info = setup.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x12)   # PRESENT | DEVICEINTERFACE
    if not info or info == INVALID:
        return []
    found = []
    try:
        i = 0
        while True:
            ifd = SP_DEVICE_INTERFACE_DATA()
            ifd.cbSize = ctypes.sizeof(ifd)
            if not setup.SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(guid), i, ctypes.byref(ifd)):
                break
            i += 1
            need = wintypes.DWORD()
            setup.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(ifd), None, 0, ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if not setup.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(ifd), buf, need, None, None):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            if "054c" not in path.lower():
                continue
            h = k32.CreateFileW(path, 0, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
            if not h or h == INVALID:
                continue
            attrs = HIDD_ATTRIBUTES()
            attrs.Size = ctypes.sizeof(attrs)
            ok = hid.HidD_GetAttributes(h, ctypes.byref(attrs))
            k32.CloseHandle(h)
            if ok and attrs.VendorID == SONY and attrs.ProductID in PRODUCTS:
                found.append((path, attrs.ProductID))
    finally:
        setup.SetupDiDestroyDeviceInfoList(info)
    return found


class PsController:
    """One open controller: a thread keeps the newest input report in .latest."""

    def __init__(self, path: str, product: int):
        hid, _setup, k32 = _get_dlls()
        self.k32 = k32
        self.name = PRODUCTS.get(product, "PlayStation controller")
        self.dualsense = product in DUALSENSE
        self.h = k32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
        if not self.h or self.h == INVALID:
            self.h = k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
        if not self.h or self.h == INVALID:
            raise OSError("could not open the controller")
        caps = HIDP_CAPS()
        pre = ctypes.c_void_p()
        self.input_len = 64
        if hid.HidD_GetPreparsedData(self.h, ctypes.byref(pre)):
            if hid.HidP_GetCaps(pre, ctypes.byref(caps)) == 0x00110000:     # HIDP_STATUS_SUCCESS
                self.input_len = caps.InputReportByteLength or 64
            hid.HidD_FreePreparsedData(pre)
        self.latest: bytes = b""
        self.alive = True
        self.thread = threading.Thread(target=self._read, daemon=True, name="ps-controller")
        self.thread.start()

    def _read(self) -> None:
        buf = ctypes.create_string_buffer(self.input_len)
        n = wintypes.DWORD()
        while self.alive:
            if not self.k32.ReadFile(self.h, buf, self.input_len, ctypes.byref(n), None):
                self.alive = False                  # unplugged, switched off or closed
                break
            self.latest = buf.raw[:n.value]

    def state(self) -> tuple[set[str], int] | None:
        return parse_report(self.latest, self.dualsense, self.input_len)

    def close(self) -> None:
        self.alive = False
        try:
            self.k32.CancelIoEx(self.h, None)
            self.k32.CloseHandle(self.h)
        except OSError:
            pass


def open_first() -> PsController | None:
    for path, product in find_controllers():
        try:
            return PsController(path, product)
        except OSError:
            continue
    return None
