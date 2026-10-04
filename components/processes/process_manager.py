"""极速进程管家：Windows 进程查看、结束及重启工具。"""
from __future__ import annotations

import ctypes
import json
from ctypes import wintypes
from collections import deque
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

try:
    import pythoncom
    import win32com.client
except ImportError:
    pythoncom = None
    win32com = None

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)

# 在创建 Tk 窗口前启用高 DPI 感知，避免 4K 屏由系统放大造成模糊。
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # Per-monitor DPI aware v2
except (AttributeError, OSError):
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except OSError:
        user32.SetProcessDPIAware()
gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
user32.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
user32.DestroyIcon.argtypes = [ctypes.c_void_p]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
DI_NORMAL = 3


class PMCX(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD), ("peak", ctypes.c_size_t),
                ("working", ctypes.c_size_t), ("qpeak", ctypes.c_size_t), ("q", ctypes.c_size_t),
                ("npeak", ctypes.c_size_t), ("n", ctypes.c_size_t), ("page", ctypes.c_size_t),
                ("pagepeak", ctypes.c_size_t), ("private", ctypes.c_size_t)]


class BMIH(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("width", ctypes.c_long), ("height", ctypes.c_long),
                ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                ("image", wintypes.DWORD), ("x", ctypes.c_long), ("y", ctypes.c_long),
                ("used", wintypes.DWORD), ("important", wintypes.DWORD)]


class BMI(ctypes.Structure):
    _fields_ = [("header", BMIH), ("colors", wintypes.DWORD * 3)]


class MEMSTAT(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD), ("total", ctypes.c_ulonglong),
                ("available", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong),
                ("virtual_total", ctypes.c_ulonglong), ("virtual_available", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]


class IFROW(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar * 256), ("index", wintypes.DWORD), ("type", wintypes.DWORD),
                ("mtu", wintypes.DWORD), ("speed", wintypes.DWORD), ("address_len", wintypes.DWORD),
                ("address", ctypes.c_ubyte * 8), ("admin", wintypes.DWORD), ("oper", wintypes.DWORD),
                ("last_change", wintypes.DWORD), ("in_octets", wintypes.DWORD), ("in_ucast", wintypes.DWORD),
                ("in_nucast", wintypes.DWORD), ("in_discards", wintypes.DWORD), ("in_errors", wintypes.DWORD),
                ("in_unknown", wintypes.DWORD), ("out_octets", wintypes.DWORD), ("out_ucast", wintypes.DWORD),
                ("out_nucast", wintypes.DWORD), ("out_discards", wintypes.DWORD), ("out_errors", wintypes.DWORD),
                ("out_queue", wintypes.DWORD), ("description_len", wintypes.DWORD), ("description", ctypes.c_ubyte * 256)]


def ft_int(value):
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


def cpu_time(handle):
    a = wintypes.FILETIME(); b = wintypes.FILETIME(); c = wintypes.FILETIME(); d = wintypes.FILETIME()
    return ft_int(c) + ft_int(d) if kernel32.GetProcessTimes(handle, ctypes.byref(a), ctypes.byref(b), ctypes.byref(c), ctypes.byref(d)) else None


def hung_pids():
    found = set()
    CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.IsHungAppWindow(hwnd):
            pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)); found.add(pid.value)
        return True
    user32.EnumWindows(CALLBACK(visit), 0)
    return found


def pretty_size(value):
    return f"{value / 1024**3:.2f} GB" if value >= 1024**3 else f"{value / 1024**2:.0f} MB"


def pretty_speed(value):
    if value >= 1024**3: return f"{value / 1024**3:.2f} GB/s"
    if value >= 1024**2: return f"{value / 1024**2:.1f} MB/s"
    return f"{max(0, value) / 1024:.0f} KB/s"


def norm_name(name):
    key = name.lower()
    return key[:-4] if key.endswith(".exe") else key


def finite_sensor_value(raw):
    """Actual sensor value, without clamping or replacing missing readings with zero."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def temp_celsius(raw):
    value = finite_sensor_value(raw)
    if value is None:
        return None
    return value / 10 - 273.15 if 1500 <= value <= 4500 else value - 273.15 if 250 <= value <= 500 else None


def classify_sensor(sensor, hardware=()):
    """Classify WMI records by hardware ancestry, never by global temperature maxima."""
    value = finite_sensor_value(sensor.get("Value", sensor.get("value")))
    sensor_type = str(sensor.get("SensorType", "")).lower()
    if value is None or sensor_type not in ("temperature", "power"):
        return None
    identifier = str(sensor.get("Identifier", ""))
    parent = str(sensor.get("Parent", ""))
    name = str(sensor.get("Name", ""))
    lookup = {str(h.get("Identifier", "")): h for h in hardware}
    ancestry, seen = [], set()
    current = parent
    while current and current not in seen:
        seen.add(current)
        h = lookup.get(current)
        if not h:
            break
        ancestry.append(h)
        current = str(h.get("Parent", ""))
    types = " ".join(str(h.get("HardwareType", "")) for h in ancestry)
    identity = (types + " " + parent + " " + identifier).lower()
    low = name.lower()
    if "acpi" in identity or "thermalzone" in identity or "thermal zone" in low:
        region = "system"
    elif re.search(r"gpu|nvidia|radeon|/ati/", identity):
        region = "gpu"
    elif re.search(r"cpu|intelcpu|amdcpu", identity):
        region = "cpu"
    elif re.search(r"storage|hdd|ssd|nvme", identity):
        region = "storage"
    elif re.search(r"motherboard|superio|superi/o|mainboard|lpc|embeddedcontroller|/ec/", identity):
        region = "system"
    else:
        region = "other"
    kind = "other"
    if region == "cpu":
        kind = "cpu_package" if re.search(r"package|\bpkg\b|tctl|tdie", low) else "cpu_core" if "core" in low else "cpu_other"
    # Explicit whole-system names only; component package/total names are not system input.
    if sensor_type == "power" and region not in ("cpu", "gpu", "storage") and re.fullmatch(r"(?:total system(?: power)?|system power|system total(?: power)?|whole system(?: power)?)", low.strip()):
        region, kind = "system", "system_total"
    if sensor.get("Kind") == "battery_discharge" and sensor_type == "power":
        region, kind = "system", "battery_discharge"
    return {"source": str(sensor.get("Source", "")), "name": name,
            "identifier": identifier, "parent": parent, "hardware_type": types,
            "region": region, "kind": kind, "sensor_type": sensor_type, "value": value}


def parse_sensor_payload(payload, hardware=(), source=""):
    records = []
    for sensor in payload:
        item = dict(sensor)
        item.setdefault("Source", source)
        record = classify_sensor(item, hardware)
        if record is not None:
            records.append(record)
    return records


def summarize_sensors(records):
    def values(region, sensor_type, kind=None):
        return [r["value"] for r in records if r["region"] == region and r["sensor_type"] == sensor_type and (kind is None or r["kind"] == kind)]
    packages = values("cpu", "temperature", "cpu_package")
    cores = values("cpu", "temperature", "cpu_core")
    gpu = values("gpu", "temperature")
    def first(region, kind):
        matches = values(region, "power", kind)
        return matches[0] if matches else None
    dram = [r["value"] for r in records if r["sensor_type"] == "power" and r["region"] == "cpu" and re.search(r"dram|memory", r["name"], re.I)]
    return {"cpu_temperature": max(packages) if packages else max(cores) if cores else None,
            "cpu_temperature_kind": "cpu_package" if packages else "cpu_core" if cores else None,
            "gpu_temperature": max(gpu) if gpu else None,
            "system_power": first("system", "system_total"),
            "cpu_package_power": first("cpu", "cpu_package"),
            "cpu_core_power": first("cpu", "cpu_core"),
            "dram_power": dram[0] if dram else None}


def read_bridge_config(script_dir):
    """Read an explicitly configured local PowerShell sensor bridge; no downloads."""
    try:
        with open(os.path.join(script_dir, "hardware_sensor_bridge.json"), encoding="utf-8-sig") as stream:
            config = json.load(stream)
        script = str(config["script_path"])
        snapshot = str(config["snapshot_path"])
        script = os.path.abspath(script if os.path.isabs(script) else os.path.join(script_dir, script))
        snapshot = os.path.abspath(snapshot if os.path.isabs(snapshot) else os.path.join(script_dir, snapshot))
        if not script.lower().endswith(".ps1") or not os.path.isfile(script):
            return None
        return {"script_path": script, "snapshot_path": snapshot}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def read_bridge_sensors(script_dir):
    """Read finite hardware-identified values only from a fresh (0..15s) snapshot."""
    config = read_bridge_config(script_dir)
    if config is None:
        return []
    try:
        with open(config["snapshot_path"], encoding="utf-8-sig") as stream:
            payload = json.load(stream)
        stamp = finite_sensor_value(payload.get("Timestamp"))
        if stamp is None or not 0 <= time.time() - stamp <= 15:
            return []
        return parse_sensor_payload(payload.get("Sensors", []), payload.get("Hardware", []), payload.get("Source", "LibreHardwareMonitor官方库"))
    except (OSError, ValueError, TypeError, AttributeError):
        # A partially written snapshot is unavailable, never an excuse to retain stale data.
        return []


def read_monitor_sensors(locator):
    """Read fresh LHM/OHM hardware identities and actual Temperature/Power values."""
    records = []
    for namespace in ("LibreHardwareMonitor", "OpenHardwareMonitor"):
        try:
            service = locator.ConnectServer(".", "root\\" + namespace)
            hardware = [{key: getattr(h, key, "") for key in ("Identifier", "Parent", "HardwareType", "Name")} for h in service.ExecQuery("SELECT * FROM Hardware")]
            sensors = [{key: getattr(item, key, None) for key in ("Name", "Identifier", "Parent", "SensorType", "Value")} for item in service.ExecQuery("SELECT * FROM Sensor WHERE SensorType='Temperature' OR SensorType='Power'")]
            records.extend(parse_sensor_payload(sensors, hardware, namespace))
        except Exception:
            continue
    return records


# ---------------------------------------------------------------------------
# CPU 型号识别与功耗上限（不再写死某一个型号）
# 「最大睿频功耗」= Intel 的 Maximum Turbo Power / PL2，AMD 侧取 PPT 上限。
# 下面只是一张兜底表：表里没有的型号会按后缀估算，而且曲线参考线始终取
# 「查表值」与「本机实测峰值」中的较大者，所以换任何一台电脑都不会报错。
# ---------------------------------------------------------------------------
APP_VERSION = "1.4.0"
CPU_POWER_TABLE = (
    # 正则（在清理后的 CPU 名称上做不区分大小写的搜索）      PL1   PL2   系列
    (r"ultra\s+x?[3579]\s*3\d{2}\s*hx",                    55,  160, "Panther Lake-HX"),
    (r"ultra\s+x?[3579]\s*3\d{2}\s*h",                     25,   80, "Panther Lake-H"),
    (r"ultra\s+x?[3579]\s*3\d{2}\s*v",                     17,   37, "Panther Lake-V"),
    (r"ultra\s+x?[3579]\s*3\d{2}",                         25,   80, "Panther Lake"),
    (r"ultra\s+[3579]\s*2\d{2}\s*hx",                      55,  160, "Arrow Lake-HX"),
    (r"ultra\s+9\s*2\d{2}\s*h",                            45,  115, "Arrow Lake-H"),
    (r"ultra\s+[357]\s*2\d{2}\s*h",                        28,  115, "Arrow Lake-H"),
    (r"ultra\s+[3579]\s*2\d{2}\s*v",                       17,   37, "Lunar Lake (200V)"),
    (r"ultra\s+[3579]\s*1\d{2}\s*hx",                      55,  115, "Meteor Lake-HX"),
    (r"ultra\s+[3579]\s*1\d{2}\s*h",                       28,  115, "Meteor Lake-H"),
    (r"ultra\s+[3579]\s*[123]\d{2}\s*[uv]",                15,   57, "Core Ultra-U"),
    (r"core\s+[3579]\s+2\d{2}\s*h",                        45,  115, "Core 200H (Raptor Lake 刷新)"),
    (r"core\s+[3579]\s+1\d{2}\s*u",                        15,   55, "Core 100U (Raptor Lake 刷新)"),
    (r"i[3579][- ]1[34]\d{3}\s*hx",                        55,  157, "Raptor Lake-HX"),
    (r"i[3579][- ]1[34]\d{3}\s*h",                         45,  115, "Raptor Lake-H"),
    (r"i[3579][- ]1[34]\d{3}\s*p",                         28,   64, "Raptor Lake-P"),
    (r"i[3579][- ]1[34]\d{3}\s*u",                         15,   55, "Raptor Lake-U"),
    (r"i[3579][- ]12\d{3}\s*hx",                           55,  157, "Alder Lake-HX"),
    (r"i[3579][- ]12\d{3}\s*h",                            45,  115, "Alder Lake-H"),
    (r"i[3579][- ]12\d{2}\s*p",                            28,   64, "Alder Lake-P"),
    (r"i[3579][- ]1[12]\d{2}\s*u",                         15,   55, "Alder Lake-U"),
    (r"ryzen\s+ai\s+max",                                  55,  120, "Ryzen AI Max (Strix Halo)"),
    (r"ryzen\s+ai\s+[3579]",                               28,   54, "Ryzen AI 300 (Strix Point)"),
    (r"ryzen\s+[3579]\s+\d{4}\s*hx",                       55,   90, "Ryzen HX"),
    (r"ryzen\s+[3579]\s+\d{4}\s*hs?",                      45,   65, "Ryzen HS/H"),
    (r"ryzen\s+[3579]\s+\d{4}\s*u",                        15,   28, "Ryzen U"),
    # 桌面平台（笔记本之外也常见）
    (r"i[3579][- ]1[34]\d{3}\s*k",                        125,  253, "Raptor Lake-K（桌面）"),
    (r"i[3579][- ]12\d{3}\s*k",                           125,  241, "Alder Lake-K（桌面）"),
    (r"ryzen\s+9\s+79\d{2}x",                             170,  230, "Ryzen 7000X（桌面）"),
    (r"ryzen\s+[3579]\s+5\d{3}x",                         105,  142, "Ryzen 5000X（桌面）"),
    (r"ryzen\s+[3579]\s+\d{4}x",                          105,  142, "Ryzen X（桌面）"),
    (r"ryzen\s+[3579]\s+\d{4}",                            65,   88, "Ryzen（桌面估算）"),
    (r"ryzen",                                             28,   54, "Ryzen 移动版（估算）"),
)


def cpu_brand_string():
    """读本机 CPU 型号：先查注册表（最快），失败再走 WMI 兜底。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
            if name:
                return name
    except Exception:
        pass
    if win32com is None:
        return ""
    try:
        pythoncom.CoInitialize()
        try:
            service = win32com.client.Dispatch("WbemScripting.SWbemLocator").ConnectServer(".", "root\\cimv2")
            service.Security_.ImpersonationLevel = 3
            for item in service.ExecQuery("SELECT Name FROM Win32_Processor"):
                name = str(item.Name).strip()
                if name:
                    return name
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        return ""
    return ""


def clean_cpu_name(raw):
    name = re.sub(r"\((?:R|TM|C)\)", " ", raw or "", flags=re.I)
    name = re.sub(r"\bCPU\b", " ", name, flags=re.I)
    name = re.sub(r"@\s*[\d.]+\s*ghz", " ", name, flags=re.I)  # 老型号尾巴上的 @ 1.60GHz
    return re.sub(r"\s+", " ", name).strip(" -") or "未知 CPU"


def estimate_power_fallback(low):
    """表里没有的型号：按核心后缀估算；估不出来就把参考线交给本机实测峰值。"""
    if "ryzen" in low or "amd" in low:
        return (45, 65, "AMD 后缀估算") if "hx" in low else (28, 54, "AMD 后缀估算")
    if re.search(r"hx(\s|$)", low): return 55, 157, "HX 后缀估算"
    if re.search(r"h(\s|$)", low): return 45, 115, "H 后缀估算"
    if re.search(r"p(\s|$)", low): return 28, 64, "P 后缀估算"
    if re.search(r"[uvy](\s|$)", low): return 15, 37, "U/V 后缀估算"
    return 0.0, 0.0, "未收录（参考线用本机实测峰值）"


def read_power_override(path):
    """可选覆盖：脚本目录放 power_limit.txt，内容 `25 80`（基础/最大）或只写 `80`。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
    except OSError:
        return None
    body = " ".join(line.split("#", 1)[0] for line in text.splitlines())  # 支持 # 注释
    numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", body)]
    if not numbers:
        return None
    if len(numbers) == 1:
        return 0.0, numbers[0], "自定义 power_limit.txt"
    return numbers[0], numbers[1], "自定义 power_limit.txt"


def detect_cpu_power(script_dir):
    """返回 (CPU 名称, 基础功耗, 最大睿频功耗, 依据说明)。"""
    name = clean_cpu_name(cpu_brand_string())
    override = read_power_override(os.path.join(script_dir, "power_limit.txt"))
    if override is not None:
        pl1, pl2, source = override
        return name, pl1, pl2, source
    low = name.lower()
    for pattern, pl1, pl2, family in CPU_POWER_TABLE:
        if re.search(pattern, low):
            return name, float(pl1), float(pl2), family
    pl1, pl2, source = estimate_power_fallback(low)
    return name, float(pl1), float(pl2), source


def find_sensor(readings, *wanted):
    """在 RAPL 读数里找传感器：先精确匹配，再按关键字模糊匹配。"""
    for name in wanted:
        for key, value in readings.items():
            if key.lower() == name.lower():
                return value
    for name in wanted:
        for key, value in readings.items():
            if name.lower() in key.lower():
                return value
    return None


def system_times():
    idle = wintypes.FILETIME(); kernel = wintypes.FILETIME(); user = wintypes.FILETIME()
    if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)): return None
    return ft_int(idle), ft_int(kernel), ft_int(user)


def network_bytes():
    size = wintypes.DWORD(0)
    if iphlpapi.GetIfTable(None, ctypes.byref(size), False) not in (0, 122) or not size.value: return 0, 0
    data = (ctypes.c_byte * size.value)()
    if iphlpapi.GetIfTable(ctypes.byref(data), ctypes.byref(size), False): return 0, 0
    count = ctypes.cast(ctypes.byref(data), ctypes.POINTER(wintypes.DWORD)).contents.value
    rows = ctypes.cast(ctypes.byref(data, ctypes.sizeof(wintypes.DWORD)), ctypes.POINTER(IFROW))
    # 6 = Ethernet, 71 = Wi-Fi. Legacy API uses 5 for an operational interface.
    # Windows may expose several Wi-Fi aliases carrying identical byte totals, so de-duplicate them.
    active = [rows[i] for i in range(count) if rows[i].oper in (4, 5) and rows[i].type in (6, 71)]
    unique = {(x.in_octets, x.out_octets) for x in active}
    return sum(x[0] for x in unique), sum(x[1] for x in unique)


def fallback_icon(name, size=24):
    colors = [(38, 108, 191), (0, 139, 123), (122, 78, 180), (211, 93, 28)]
    r, g, b = colors[sum(map(ord, name)) % len(colors)]
    data = bytearray(f"P6\n{size} {size}\n255\n".encode())
    for y in range(size):
        for x in range(size):
            data.extend((r, g, b) if (x - 12)**2 + (y - 12)**2 < 110 else (244, 246, 248))
    return tk.PhotoImage(data=bytes(data), format="PPM")


def program_icon(path, name):
    icon = wintypes.HICON()
    if not path or not shell32.ExtractIconExW(path, 0, None, ctypes.byref(icon), 1) or not icon.value:
        return fallback_icon(name)
    size = 24; bits = ctypes.c_void_p(); bmi = BMI()
    bmi.header.size = ctypes.sizeof(BMIH); bmi.header.width = size; bmi.header.height = -size; bmi.header.planes = 1; bmi.header.bits = 32
    bitmap = gdi32.CreateDIBSection(None, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    dc = gdi32.CreateCompatibleDC(None)
    if not bitmap or not dc:
        if bitmap: gdi32.DeleteObject(bitmap)
        if dc: gdi32.DeleteDC(dc)
        user32.DestroyIcon(icon)
        return fallback_icon(name)
    old = gdi32.SelectObject(dc, bitmap); user32.DrawIconEx(dc, 0, 0, icon, size, size, 0, None, DI_NORMAL)
    raw = (ctypes.c_ubyte * (size * size * 4)).from_address(bits.value)
    ppm = bytearray(f"P6\n{size} {size}\n255\n".encode())
    for i in range(0, len(raw), 4): ppm.extend((raw[i + 2], raw[i + 1], raw[i]))
    gdi32.SelectObject(dc, old); gdi32.DeleteObject(bitmap); gdi32.DeleteDC(dc); user32.DestroyIcon(icon)
    return tk.PhotoImage(data=bytes(ppm), format="PPM")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("极速进程管家")
        # 自绘图标：让窗口 / 任务栏显示本程序自己的图标（默认是 Tk 的羽毛图标）。
        try:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "极速进程管家.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass
        self.dpi_scale = max(1.0, self.winfo_fpixels("1i") / 96)
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(int(1280 * self.dpi_scale), screen_w - int(80 * self.dpi_scale))
        height = min(int(760 * self.dpi_scale), screen_h - int(100 * self.dpi_scale))
        self.geometry(f"{width}x{height}")
        self.minsize(int(900 * self.dpi_scale), int(560 * self.dpi_scale)); self.configure(bg="#f4f6f8")
        self.samples, self.icons, self.keys = {}, {}, {}
        # 默认：死机/未响应进程置顶，其余按 CPU 从高到低。
        self.auto, self.job, self.sort, self.desc = False, None, "priority", True
        self.sys_sample = None
        self.net_sample = None
        self.gpu, self.npu = None, None
        self.temp_cpu, self.temp_gpu = None, None
        self.sensor_records = []
        self.power_records = []
        self.cpu_temperature_kind = None
        self.last_sensor_at = 0.0
        self.last_power_at = 0.0
        # 温度 / 散热：多源读取 + 静态假传感器识别（见 build_thermal_tab）
        self.zone_temps, self.zone_precise = {}, {}
        self.thermal_third, self.thermal_hist = [], deque(maxlen=20)
        self.temp_trust, self.passive_limit, self.throttle_reasons = "unknown", None, None
        self.thermal_signature = None
        self.gpu_pids, self.disk_names, self.net_pids = {}, {}, {}
        self.queue = queue.Queue()
        self.collecting = False
        self.last_rows = []
        self.cpu_percent = 0.0
        self.power_sample = None
        self.power_history = deque(maxlen=180)
        self.power_stop = threading.Event()
        # 自动识别本机 CPU 型号与官方功耗上限（查表 + 后缀估算 + 实测峰值自适应）
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.cpu_name, self.power_base, self.power_limit, self.power_source = detect_cpu_power(self.script_dir)
        self.learned_peak = 0.0
        self.no_restart = {"svchost.exe", "sihost.exe", "explorer.exe", "shellhost.exe", "searchhost.exe", "startmenuexperiencehost.exe", "runtimebroker.exe", "textinputhost.exe", "lockapp.exe", "unsecapp.exe", "dllhost.exe", "conhost.exe", "splwow64.exe"}
        self.build()
        if "--power" in sys.argv:  # 命令行加 --power 就直接打开「CPU 实时功耗」标签页
            self.tabs.select(1)
        if "--temp" in sys.argv:   # 命令行加 --temp 就直接打开「温度 / 散热」标签页
            self.tabs.select(2)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(120, self.refresh)  # 启动时刷新一次
        self.after(120, self._drain)
        self.after(200, self.update_metrics)
        self.after(300, self.tree.focus_set)
        threading.Thread(target=self.poll_accelerators, daemon=True).start()
        threading.Thread(target=self.poll_power, daemon=True).start()
        threading.Thread(target=self.check_monitor_companion, daemon=True).start()

    def build(self):
        px = lambda value: int(value * self.dpi_scale)
        style = ttk.Style(self); style.theme_use("clam")
        style.configure("Treeview", rowheight=px(32), font=("Microsoft YaHei UI", 11), background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 11, "bold"), background="#e8edf3")
        style.map("Treeview", background=[("selected", "#d9eaff")])
        top = tk.Frame(self, bg="#f4f6f8", padx=16, pady=13); top.pack(fill="x")
        self.network = tk.Label(top, text="↑ --  ↓ --", font=("Microsoft YaHei UI", 10, "bold"), bg="#f4f6f8", fg="#0078d4")
        self.network.pack(side="left", padx=(0, 18))
        tk.Label(top, text="极速进程管家", font=("Microsoft YaHei UI", 17, "bold"), bg="#f4f6f8", fg="#172b4d").pack(side="left")
        tk.Label(top, text=f"v{APP_VERSION}", font=("Microsoft YaHei UI", 9), bg="#f4f6f8", fg="#8a97a8").pack(side="left", padx=(7, 0), pady=(7, 0))
        self.summary = tk.Label(top, text="正在读取…", font=("Microsoft YaHei UI", 10), bg="#f4f6f8", fg="#536171"); self.summary.pack(side="left", padx=18)
        self.metrics = tk.Label(top, text="CPU --  内存 --  GPU --  NPU --  磁盘 --", font=("Microsoft YaHei UI", 10, "bold"), bg="#f4f6f8", fg="#27364a")
        self.metrics.pack(side="right", padx=(0, 15))
        ttk.Button(top, text="↻ 立即刷新", command=lambda: self.refresh(manual=True)).pack(side="right")
        ttk.Button(top, text="结束选中进程", command=self.kill_selected).pack(side="right", padx=(0, 10))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        process_tab = tk.Frame(self.tabs, bg="#f4f6f8")
        power_tab = tk.Frame(self.tabs, bg="#f4f6f8")
        thermal_tab = tk.Frame(self.tabs, bg="#f4f6f8")
        self.tabs.add(process_tab, text=" 进程管理 ")
        self.tabs.add(power_tab, text=" CPU 实时功耗 ")
        self.tabs.add(thermal_tab, text=" 温度 / 散热 ")

        body = tk.Frame(process_tab, bg="#f4f6f8", padx=16); body.pack(fill="both", expand=True)
        cols = ("status", "name", "count", "cpu", "memory", "gpu", "disk", "net")
        self.tree = ttk.Treeview(body, columns=cols, show="tree headings", selectmode="extended")
        self.tree.heading("#0", text=""); self.tree.column("#0", width=px(42), minwidth=px(42), stretch=False)
        for key, label, width in [("status", "状态", 100), ("name", "软件", 420), ("count", "进程数", 88), ("cpu", "CPU", 115), ("memory", "内存", 125), ("gpu", "GPU", 85), ("disk", "磁盘", 110), ("net", "网络", 100)]:
            self.tree.heading(key, text=label, command=lambda c=key: self.change_sort(c)); self.tree.column(key, width=px(width), anchor="e" if key in ("count", "cpu", "memory", "gpu", "disk", "net") else "w", stretch=key == "name")
        self.tree.tag_configure("hung", foreground="#c62828"); self.tree.tag_configure("hot", foreground="#d35400")
        bar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview); self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side="left", fill="both", expand=True); bar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _: self.kill_selected()); self.tree.bind("<Button-3>", self.menu_open)
        self.tree.bind("<Up>", self.on_up); self.tree.bind("<Down>", self.on_down); self.tree.bind("<Home>", self.on_home); self.tree.bind("<End>", self.on_end)
        self.bind("<Up>", self.on_up); self.bind("<Down>", self.on_down); self.bind("<Home>", self.on_home); self.bind("<End>", self.on_end); self.bind("<Delete>", self.on_delete)
        self.menu = tk.Menu(self, tearoff=False, font=("Microsoft YaHei UI", 10)); self.menu.add_command(label="结束进程", command=self.kill_selected); self.menu.add_command(label="重启", command=self.restart_selected)
        bottom = tk.Frame(process_tab, bg="#f4f6f8", padx=16, pady=11); bottom.pack(fill="x")
        tk.Label(bottom, text="↑/↓ 切换选中，Delete 直接结束；右键可结束或重启；点表头可按 CPU/内存/GPU/磁盘/网络排序。", bg="#f4f6f8", fg="#687687", font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.auto_text = tk.StringVar(value="自动刷新：已关闭"); ttk.Button(bottom, textvariable=self.auto_text, command=self.toggle_auto).pack(side="right")
        self.build_power_tab(power_tab)
        self.build_thermal_tab(thermal_tab)

    # ------------------------------------------------------------------
    # 温度 / 散热（多源 + 假传感器识别）
    # ------------------------------------------------------------------
    def build_thermal_tab(self, parent):
        px = lambda value: int(value * self.dpi_scale)
        self.thermal_main_var = tk.StringVar(value="--")
        self.thermal_trust_var = tk.StringVar(value="判定中…")
        self.thermal_power_var = tk.StringVar(value="--.- W")
        self.thermal_throttle_var = tk.StringVar(value="--")

        header = tk.Frame(parent, bg="#f4f6f8", padx=18, pady=14); header.pack(fill="x")
        tk.Label(header, text="温度 / 散热", font=("Microsoft YaHei UI", 17, "bold"), bg="#f4f6f8", fg="#172b4d").pack(side="left")
        tk.Label(header, text="按硬件区域读取 · 温度与功耗明细 · ACPI 不代表 CPU", font=("Microsoft YaHei UI", 10), bg="#f4f6f8", fg="#687687").pack(side="left", padx=16)
        ttk.Button(header, text="重新判定", command=self.reset_thermal_stats).pack(side="right")

        cards = tk.Frame(parent, bg="#f4f6f8", padx=14); cards.pack(fill="x", pady=(0, 12))
        for column in range(4):
            cards.grid_columnconfigure(column, weight=1, uniform="thermal")
        for column, title, variable, color in (
            (0, "CPU 温度", self.thermal_main_var, "#c0392b"),
            (1, "CPU 温度类型", self.thermal_trust_var, "#0078d4"),
            (2, "CPU 封装功耗", self.thermal_power_var, "#008b72"),
            (3, "降频原因", self.thermal_throttle_var, "#7048a8"),
        ):
            card = tk.Frame(cards, bg="white", highlightbackground="#d7dee8", highlightthickness=1)
            card.grid(row=0, column=column, sticky="nsew", padx=6, pady=4)
            tk.Frame(card, bg=color, height=3).pack(fill="x")
            tk.Label(card, text=title, font=("Microsoft YaHei UI", 9), bg="white", fg="#687687").pack(anchor="w", padx=14, pady=(9, 0))
            tk.Label(card, textvariable=variable, font=("Microsoft YaHei UI", 18, "bold"), bg="white", fg=color).pack(anchor="w", padx=14, pady=(1, 11))

        panel = tk.Frame(parent, bg="white", highlightbackground="#d7dee8", highlightthickness=1)
        panel.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        cols = ("source", "instance", "value", "verdict")
        self.thermal_tree = ttk.Treeview(panel, columns=cols, show="headings", height=8)
        for key, label, width in (("source", "区域 / 来源", 260), ("instance", "实例 / 传感器", 300), ("value", "读数", 110), ("verdict", "硬件身份 / 类型", 240)):
            self.thermal_tree.heading(key, text=label)
            self.thermal_tree.column(key, width=px(width), anchor="w" if key != "value" else "e", stretch=key == "instance")
        self.thermal_tree.pack(fill="both", expand=True, padx=10, pady=10)

        foot = tk.Frame(parent, bg="#f4f6f8", padx=18, pady=6); foot.pack(fill="x")
        self.thermal_note = tk.Label(foot, text="", justify="left", wraplength=px(1160), bg="#f4f6f8", fg="#687687", font=("Microsoft YaHei UI", 9))
        self.thermal_note.pack(anchor="w")

    def reset_thermal_stats(self):
        self.thermal_hist.clear()
        self.temp_trust = "unknown"
        self.thermal_signature = None
        self.refresh_thermal_view()

    def _judge_thermal(self):
        # ACPI and load correlation never establish a CPU temperature identity.
        return "identified" if self.temp_cpu is not None else "absent"

    def decode_throttle(self, bits):
        try:
            value = int(float(bits))
        except (TypeError, ValueError):
            return "--"
        if value == 0:
            return "无降频"
        names = {0: "温度", 1: "功耗", 2: "电流", 3: "其他"}
        hit = [name for bit, name in sorted(names.items()) if value & (1 << bit)]
        text = " / ".join(hit) if hit else "未知位"
        return f"{text} (0x{value:X})"

    def refresh_thermal_view(self):
        if not hasattr(self, "thermal_tree"):
            return
        self.thermal_main_var.set("未提供" if self.temp_cpu is None else f"{self.temp_cpu:.1f} °C")
        self.thermal_trust_var.set({"cpu_package": "CPU 封装", "cpu_core": "CPU 核心最高值"}.get(self.cpu_temperature_kind, "未提供"))
        package = self.power_sample[1] if self.power_sample else None
        self.thermal_power_var.set("未提供" if package is None else f"{package:.1f} W")
        self.thermal_throttle_var.set("--" if self.throttle_reasons is None else self.decode_throttle(self.throttle_reasons))
        rows = []
        regions = {"cpu": "核心处理器 CPU", "gpu": "GPU", "system": "主板 / 机身 / 系统", "storage": "存储", "other": "其他"}
        records = [r for r in self.sensor_records if r["sensor_type"] == "temperature"] + self.power_records
        for region in regions:
            for r in records:
                if r["region"] != region:
                    continue
                unit = "°C" if r["sensor_type"] == "temperature" else "W"
                label = "电池放电功率（非插电整机输入）" if r["kind"] == "battery_discharge" else r["name"]
                rows.append((f"{regions[region]} · {r['source']}", label, f"{r['value']:.1f} {unit}", f"{r['kind']} · {r['hardware_type']} · {r['parent']} · {r['identifier']}"))
        for instance, value in sorted(self.zone_temps.items()):
            rows.append(("主板 / 系统 · ACPI 热区", instance, f"{value:.1f} °C", "固件热区；不是 CPU 温度"))
        for instance, value in sorted(self.zone_precise.items()):
            rows.append(("主板 / 系统 · ACPI 高精度热区", instance, f"{value:.1f} °C", "固件热区；不是 CPU 温度"))
        if self.passive_limit is not None:
            rows.append(("ACPI 被动散热限制", "% Passive Limit", f"{self.passive_limit:.0f} %", "固件指标"))
        if self.throttle_reasons is not None:
            rows.append(("ACPI 降频原因", "Throttle Reasons", f"0x{int(self.throttle_reasons):X}", self.decode_throttle(self.throttle_reasons)))
        signature = tuple(rows)
        if signature != self.thermal_signature:
            self.thermal_signature = signature
            self.thermal_tree.delete(*self.thermal_tree.get_children())
            for row in rows or [("—", "本机未提供可读传感器", "—", "—")]:
                self.thermal_tree.insert("", "end", values=row)
        self.thermal_note.configure(text="整机温度没有单一含义：主板 / 机身传感器分开显示。CPU 优先封装温度，否则显示核心最高值；GPU 独立。ACPI 热区永不当作 CPU 温度，也不按负载判可信。整机功率仅采用明确系统总功率传感器，不将 CPU + GPU 相加。支持 LibreHardwareMonitor 官方库 JSON 桥接 / LHM、OHM WMI。", fg="#687687")

    def build_power_tab(self, parent):
        px = lambda value: int(value * self.dpi_scale)
        self.power_platform_var = tk.StringVar(value="未提供")
        self.power_system_var = tk.StringVar(value="未提供")
        self.power_package_var = tk.StringVar(value="未提供")
        self.power_core_var = tk.StringVar(value="--.- W")
        self.power_dram_var = tk.StringVar(value="--.-- W")
        self.power_cpu_var = tk.StringVar(value="-- %")
        self.power_avg_var = tk.StringVar(value="平均 --.- W")
        self.power_peak_var = tk.StringVar(value="峰值 --.- W")
        self.power_status_var = tk.StringVar(value="正在连接 RAPL 功耗传感器…")

        header = tk.Frame(parent, bg="#f4f6f8", padx=18, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="CPU 实时功耗", font=("Microsoft YaHei UI", 17, "bold"), bg="#f4f6f8", fg="#172b4d").pack(side="left")
        tk.Label(header, text=f"{self.cpu_name} · 1 秒刷新 · 约 3 分钟历史", font=("Microsoft YaHei UI", 10), bg="#f4f6f8", fg="#687687").pack(side="left", padx=16)
        ttk.Button(header, text="重置统计", command=self.reset_power_stats).pack(side="right")

        cards = tk.Frame(parent, bg="#f4f6f8", padx=14)
        cards.pack(fill="x", pady=(0, 12))
        for column in range(6):
            cards.grid_columnconfigure(column, weight=1, uniform="power")
        self._power_card(cards, 0, "CPU PACKAGE", self.power_package_var, "#0078d4")
        self._power_card(cards, 1, "CPU 核心", self.power_core_var, "#008b72")
        self._power_card(cards, 2, "DRAM", self.power_dram_var, "#7048a8")
        self._power_card(cards, 3, "CPU 占用", self.power_cpu_var, "#d35400")
        self._power_card(cards, 4, "整机功率（系统总功率）", self.power_system_var, "#c0392b")
        self._power_card(cards, 5, "CPU 平台（非整机）", self.power_platform_var, "#7048a8")

        panel = tk.Frame(parent, bg="white", highlightbackground="#d7dee8", highlightthickness=1)
        panel.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        panel_head = tk.Frame(panel, bg="white")
        panel_head.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(panel_head, text="CPU Package 功耗曲线", font=("Microsoft YaHei UI", 11, "bold"), bg="white", fg="#27364a").pack(side="left")
        tk.Label(panel_head, textvariable=self.power_avg_var, font=("Microsoft YaHei UI", 10), bg="white", fg="#687687").pack(side="right", padx=(12, 0))
        tk.Label(panel_head, textvariable=self.power_peak_var, font=("Microsoft YaHei UI", 10, "bold"), bg="white", fg="#d35400").pack(side="right")
        self.power_canvas = tk.Canvas(panel, bg="white", highlightthickness=0, height=px(300))
        self.power_canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.power_canvas.bind("<Configure>", lambda _event: self.draw_power_graph())

        status = tk.Frame(parent, bg="#f4f6f8", padx=18)
        status.pack(fill="x", pady=(0, 12))
        self.power_status_label = tk.Label(status, textvariable=self.power_status_var, font=("Microsoft YaHei UI", 10, "bold"), bg="#f4f6f8", fg="#008b72")
        self.power_status_label.pack(side="left")
        tk.Label(status, text=f"数据源：RAPL / LHM / OHM　｜　{self.limit_caption()}", font=("Microsoft YaHei UI", 9), bg="#f4f6f8", fg="#687687").pack(side="right")

    def limit_caption(self):
        """底部右侧那句「最大睿频功耗」说明，随本机 CPU 自动变化。"""
        if self.power_limit <= 0:
            return "本机 CPU 未收录，参考线取实测峰值"
        base = f"（基础功耗 {self.power_base:.0f}W）" if self.power_base > 0 else ""
        return f"最大睿频功耗 {self.power_limit:.0f}W{base}　依据：{self.power_source}"

    def effective_limit(self):
        """参考线 = 查表值与本机实测峰值中的较大者：型号没收录也不会画错。"""
        return max(self.power_limit, self.learned_peak)

    def _power_card(self, parent, column, title, variable, color):
        frame = tk.Frame(parent, bg="white", highlightbackground="#d7dee8", highlightthickness=1)
        frame.grid(row=0, column=column, sticky="nsew", padx=(4, 4), pady=0)
        tk.Label(frame, text=title, font=("Microsoft YaHei UI", 9, "bold"), bg="white", fg="#687687").pack(anchor="w", padx=14, pady=(10, 2))
        tk.Label(frame, textvariable=variable, font=("Microsoft YaHei UI", 20, "bold"), bg="white", fg=color).pack(anchor="w", padx=14, pady=(0, 12))

    def check_monitor_companion(self):
        """Optional local sidecar; only the sensor helper is elevated, once per launch."""
        bridge = read_bridge_config(self.script_dir)
        if bridge is not None:
            fresh = False
            try:
                with open(bridge["snapshot_path"], encoding="utf-8-sig") as stream:
                    stamp = finite_sensor_value(json.load(stream).get("Timestamp"))
                fresh = stamp is not None and 0 <= time.time() - stamp <= 15
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            if not fresh:
                self.queue.put(("start_bridge", bridge["script_path"]))
            return
        config = os.path.join(self.script_dir, "hardware_monitor_path.txt")
        try:
            with open(config, encoding="utf-8-sig") as stream:
                executable = os.path.abspath(os.path.expandvars(stream.read().strip().strip('"')))
            if os.path.basename(executable).lower() not in ("librehardwaremonitor.exe", "librehardwaremonitor.windows.forms.exe", "openhardwaremonitor.exe") or not os.path.isfile(executable):
                return
        except (OSError, ValueError):
            return
        if pythoncom is None or win32com is None:
            return
        pythoncom.CoInitialize()
        try:
            locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
            for namespace in ("LibreHardwareMonitor", "OpenHardwareMonitor"):
                try:
                    service = locator.ConnectServer(".", "root\\" + namespace)
                    if any(True for _ in service.ExecQuery("SELECT Identifier FROM Hardware")):
                        return
                except Exception:
                    pass
            self.queue.put(("start_monitor", executable))
        except Exception:
            pass
        finally:
            pythoncom.CoUninitialize()

    def start_monitor_companion(self, executable, parameters=None, directory=None):
        # ShellExecuteW is invoked on the UI thread; UAC cancellation is not retried.
        execute = shell32.ShellExecuteW
        execute.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
        execute.restype = ctypes.c_void_p
        try:
            result = execute(None, "runas", executable, parameters, directory or os.path.dirname(executable), 1)
            if not result or result <= 32:
                self.power_status_var.set("硬件监控未启动 / 已取消；未提供的传感器不会估算")
        except (OSError, ValueError):
            self.power_status_var.set("硬件监控未启动；未提供的传感器不会估算")

    def poll_power(self):
        com_available = pythoncom is not None and win32com is not None
        if com_available:
            pythoncom.CoInitialize()
        try:
            while not self.power_stop.is_set():
                try:
                    try:
                        locator = win32com.client.Dispatch("WbemScripting.SWbemLocator") if com_available else None
                    except Exception:
                        locator = None
                    records = read_bridge_sensors(self.script_dir)
                    if locator is not None:
                        records.extend(read_monitor_sensors(locator))
                    summary = summarize_sensors(records)
                    readings = {}
                    try:
                        service = locator.ConnectServer(".", "root\\cimv2")
                        service.Security_.ImpersonationLevel = 3
                        for item in service.ExecQuery("SELECT Name, Power FROM Win32_PerfFormattedData_PowerMeterCounter_EnergyMeter"):
                            value = finite_sensor_value(item.Power)
                            if value is not None:
                                readings[str(item.Name)] = value / 1000.0
                    except Exception:
                        pass
                    package = find_sensor(readings, "RAPL_Package0_PKG", "PKG")
                    core = find_sensor(readings, "RAPL_Package0_PP0", "PP0")
                    dram = find_sensor(readings, "RAPL_Package0_DRAM", "DRAM")
                    if package is None:
                        package = summary["cpu_package_power"]
                        core, dram = summary["cpu_core_power"], summary["dram_power"]
                    else:
                        for name, value in readings.items():
                            records.append({"source": "Windows RAPL", "name": name, "identifier": name, "parent": "CPU", "hardware_type": "CPU", "region": "cpu", "kind": "cpu_package" if "PKG" in name.upper() else "cpu_core" if "PP0" in name.upper() else "other", "sensor_type": "power", "value": value})
                    # Battery output is not AC input and is never substituted for system total.
                    try:
                        battery_service = locator.ConnectServer(".", "root\\wmi")
                        for battery in battery_service.ExecQuery("SELECT InstanceName, Discharging, DischargeRate FROM BatteryStatus"):
                            rate = finite_sensor_value(battery.DischargeRate)
                            if battery.Discharging and rate is not None and 0 <= rate < 4294967295:
                                records.extend(parse_sensor_payload([{"Name": "电池放电功率（非插电整机输入）", "Identifier": str(battery.InstanceName), "Parent": "battery", "SensorType": "Power", "Value": rate / 1000.0, "Kind": "battery_discharge"}], source="Windows BatteryStatus"))
                    except Exception:
                        pass
                    self.queue.put(("power_snapshot", time.time(), records, (time.time(), package, core, dram) if package is not None else None))
                    self.power_stop.wait(1.0)
                except Exception as exc:
                    self.queue.put(("power_error", str(exc)))
                    self.power_stop.wait(3.0)
        finally:
            if com_available:
                pythoncom.CoUninitialize()

    def clear_power(self, message="未提供 CPU 封装功耗"):
        self.power_sample = None
        self.power_history.clear()
        self.learned_peak = 0.0
        self.power_package_var.set("未提供")
        self.power_platform_var.set("未提供")
        self.power_core_var.set("未提供")
        self.power_dram_var.set("未提供")
        self.power_avg_var.set("平均 未提供")
        self.power_peak_var.set("峰值 未提供")
        self.power_status_var.set(message)
        self.power_status_label.configure(fg="#687687")
        self.draw_power_graph()

    def apply_power_snapshot(self, stamp, records, sample):
        self.last_power_at = stamp
        self.last_sensor_at = stamp
        # Every one-second monitor/bridge snapshot replaces prior actual temperatures.
        counters = [r for r in self.sensor_records if r["source"] == "Windows GPU counter"]
        self.sensor_records = [r for r in records if r["sensor_type"] == "temperature"] + counters
        temperatures = summarize_sensors(self.sensor_records)
        self.temp_cpu = temperatures["cpu_temperature"]
        self.temp_gpu = temperatures["gpu_temperature"]
        self.cpu_temperature_kind = temperatures["cpu_temperature_kind"]
        self.temp_trust = self._judge_thermal()
        self.power_records = [r for r in records if r["sensor_type"] == "power"]
        total = summarize_sensors(self.power_records)["system_power"]
        self.power_system_var.set("未提供" if total is None else f"{total:.1f} W")
        if sample is None:
            self.clear_power()
        else:
            self.apply_power(sample)
        platforms = [r["value"] for r in self.power_records if r["region"] == "cpu" and r["name"].strip().lower() in ("cpu platform", "platform", "psys")]
        self.power_platform_var.set(f"{platforms[0]:.1f} W" if platforms else "未提供")
        self.refresh_thermal_view()

    def apply_power(self, sample):
        self.power_sample = sample
        self.power_history.append(sample)
        _, package, core, dram = sample
        self.power_package_var.set(f"{package:.1f} W")
        # 有些平台只暴露 Package，或者没有 DRAM 传感器 —— 显示「—」而不是误导性的 0.00 W
        self.power_core_var.set("未提供" if core is None else f"{core:.1f} W")
        self.power_dram_var.set("未提供" if dram is None else f"{dram:.2f} W")
        self.power_cpu_var.set(f"{self.cpu_percent:.0f} %")
        values = [item[1] for item in self.power_history]
        peak = max(values)
        if peak > self.power_limit * 1.05:
            self.learned_peak = peak  # 实测已超过查表值 → 参考线跟着实测走
        self.power_avg_var.set(f"平均 {sum(values) / len(values):.1f} W")
        self.power_peak_var.set(f"峰值 {peak:.1f} W")
        if self.power_limit > 0 and package >= self.power_limit * 0.95:
            text, color = f"接近 {self.power_limit:.0f}W 最大睿频功耗上限", "#c62828"
        elif self.cpu_percent >= 80:
            text, color = "高负载运行中", "#d35400"
        elif self.cpu_percent >= 30:
            text, color = "正常工作负载", "#008b72"
        else:
            text, color = "轻载运行", "#0078d4"
        self.power_status_var.set(text)
        self.power_status_label.configure(fg=color)
        self.draw_power_graph()

    def reset_power_stats(self):
        if self.power_sample:
            self.power_history.clear()
            self.apply_power(self.power_sample)

    def draw_power_graph(self):
        if not hasattr(self, "power_canvas"):
            return
        canvas = self.power_canvas
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 100), max(canvas.winfo_height(), 100)
        left, right, top, bottom = 48, 14, 12, 28
        plot_w, plot_h = max(width - left - right, 10), max(height - top - bottom, 10)
        values = [item[1] for item in self.power_history]
        observed = max(values, default=0.0)
        limit = self.effective_limit()
        y_max = max(40.0, math.ceil(max(observed, limit) * 1.1 / 10.0) * 10.0)

        for watts in range(0, int(y_max) + 1, 10):
            y = top + plot_h * (1.0 - watts / y_max)
            canvas.create_line(left, y, left + plot_w, y, fill="#e3e8ef")
            canvas.create_text(left - 8, y, text=str(watts), fill="#687687", anchor="e", font=("Microsoft YaHei UI", 8))

        if limit > 0:
            limit_y = top + plot_h * (1.0 - min(limit, y_max) / y_max)
            canvas.create_line(left, limit_y, left + plot_w, limit_y, fill="#d35400", dash=(5, 4))
            learned = self.power_limit <= 0 or self.learned_peak > self.power_limit * 1.05
            caption = f"{limit:.0f}W 实测峰值" if learned else f"{limit:.0f}W MTP"
            canvas.create_text(left + plot_w - 4, limit_y - 5, text=caption, fill="#d35400", anchor="se", font=("Microsoft YaHei UI", 8, "bold"))

        if not values:
            canvas.create_text(width / 2, height / 2, text="等待功耗数据…", fill="#687687", font=("Microsoft YaHei UI", 11))
            return

        points = []
        for index, value in enumerate(values):
            x = left + plot_w * index / max(len(values) - 1, 1)
            y = top + plot_h * (1.0 - min(value, y_max) / y_max)
            points.extend((x, y))
        if len(values) > 1:
            canvas.create_polygon([left, top + plot_h, *points, left + plot_w, top + plot_h], fill="#e2f2fb", outline="")
            canvas.create_line(points, fill="#0078d4", width=2, smooth=True)
        else:
            canvas.create_oval(points[0] - 2, points[1] - 2, points[0] + 2, points[1] + 2, fill="#0078d4", outline="")
        canvas.create_text(left, height - 6, text="约 3 分钟历史", fill="#687687", anchor="sw", font=("Microsoft YaHei UI", 8))
        canvas.create_text(left + plot_w, height - 6, text=time.strftime("%H:%M:%S", time.localtime(self.power_history[-1][0])), fill="#687687", anchor="se", font=("Microsoft YaHei UI", 8))

    def close(self):
        self.power_stop.set()
        self.destroy()

    def menu_open(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row); self.menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def select_relative(self, offset):
        items = self.tree.get_children()
        if not items: return "break"
        current = self.tree.selection()
        index = self.tree.index(current[0]) + offset if current else (0 if offset > 0 else len(items) - 1)
        index = max(0, min(len(items) - 1, index))
        self.tree.selection_set(items[index]); self.tree.focus(items[index]); self.tree.see(items[index])
        return "break"

    def on_up(self, event): return self.select_relative(-1)

    def on_down(self, event): return self.select_relative(1)

    def on_home(self, event):
        items = self.tree.get_children()
        if items: self.tree.selection_set(items[0]); self.tree.focus(items[0]); self.tree.see(items[0])
        return "break"

    def on_end(self, event):
        items = self.tree.get_children()
        if items: self.tree.selection_set(items[-1]); self.tree.focus(items[-1]); self.tree.see(items[-1])
        return "break"

    def on_delete(self, event):
        self.kill_selected(); return "break"

    def change_sort(self, key):
        self.desc = not self.desc if self.sort == key else key in ("cpu", "memory", "gpu", "disk", "net"); self.sort = key; self.refresh()

    def toggle_auto(self):
        self.auto = not self.auto; self.auto_text.set("自动刷新：开启" if self.auto else "自动刷新：已关闭")
        if self.auto: self.refresh()
        elif self.job: self.after_cancel(self.job); self.job = None

    def auto_refresh(self): self.job = None; self.refresh()

    def poll_accelerators(self):
        """GPU/NPU/温度/磁盘/网络等计数器在后台线程轮询，避免阻塞界面。"""
        command = """$o = New-Object System.Collections.Generic.List[string]
$paths = '\\GPU Engine(*)\\Utilization Percentage','\\NPU Engine(*)\\Utilization Percentage','\\GPU Adapter Temperature(*)','\\Process(*)\\IO Data Bytes/sec'
foreach ($x in $paths) {
  $rr = $null
  try { $rr = Get-Counter $x -ErrorAction Stop } catch { }
  if ($rr) {
    foreach ($s in $rr.CounterSamples) {
      if ($x -like '*GPU Engine*') {
        if ($s.InstanceName -match 'pid_(\\d+)') { $o.Add(('G|{0}|{1:F2}' -f $Matches[1], $s.CookedValue)) }
      } elseif ($x -like '*NPU Engine*') {
        $o.Add(('U|{0:F2}' -f $s.CookedValue))
      } elseif ($x -like '*Thermal Zone*') {
        $o.Add(('T|{0}' -f $s.CookedValue))
      } elseif ($x -like '*GPU Adapter*') {
        $o.Add(('V|{0}' -f $s.CookedValue))
      } elseif ($x -like '*IO Data*') {
        $n = ($s.InstanceName -split '#')[0]
        if ($n -ne '_Total' -and $n -ne 'idle' -and $n -ne 'Idle') { $o.Add(('D|{0}|{1:F0}' -f $n, $s.CookedValue)) }
      }
    }
  }
}
try {
  Get-CimInstance -ClassName Win32_PerfFormattedData_Counters_ThermalZoneInformation -ErrorAction Stop | ForEach-Object {
    $inst = if ($_.InstanceName) { $_.InstanceName } else { $_.Name }
    $o.Add(('TZ|{0}|{1}' -f $inst, $_.Temperature))
    if ($_.HighPrecisionTemperature -gt 0) { $o.Add(('HP|{0}|{1}' -f $inst, $_.HighPrecisionTemperature)) }
    $o.Add(('PL|{0}|{1}' -f $inst, $_.PercentPassiveLimit))
    $o.Add(('TR|{0}|{1}' -f $inst, $_.ThrottleReasons))
  }
} catch { }
foreach ($ns in 'root\\LibreHardwareMonitor','root\\OpenHardwareMonitor') {
  try {
    $hardware = @(Get-CimInstance -Namespace $ns -ClassName Hardware -ErrorAction Stop | Select-Object Identifier,Parent,HardwareType,Name)
    $sensors = @(Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction Stop | Where-Object { $_.SensorType -in 'Temperature','Power' } | Select-Object Name,Identifier,Parent,SensorType,Value)
    $payload = @{ Source=$ns.Substring(5); Hardware=$hardware; Sensors=$sensors } | ConvertTo-Json -Depth 6 -Compress
    $o.Add(('S|{0}' -f $payload))
  } catch { }
}
$c = @{}
Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | ForEach-Object { if ($_.OwningProcess) { $c[$_.OwningProcess] = 1 + $c[$_.OwningProcess] } }
Get-NetUDPEndpoint -ErrorAction SilentlyContinue | ForEach-Object { if ($_.OwningProcess) { $c[$_.OwningProcess] = 1 + $c[$_.OwningProcess] } }
foreach ($k in $c.Keys) { $o.Add(('N|{0}|{1}' -f $k, $c[$k])) }
$o"""
        while not self.power_stop.is_set():
            try:
                result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=20, creationflags=0x08000000)
                self.queue.put(("counters", result.stdout if result.returncode == 0 else "", time.time()))
            except (OSError, ValueError, subprocess.SubprocessError):
                self.queue.put(("counters", "", time.time()))
            self.power_stop.wait(3.0)

    def _parse_counters(self, stdout):
        # Called only by _drain on the Tk main thread; snapshots replace old values.
        gpu, npu, disk, net = {}, [], {}, {}
        gtemps, records = [], []
        zones, precise = {}, {}
        self.passive_limit = self.throttle_reasons = None
        for line in stdout.splitlines():
            parts = line.split("|")
            try:
                tag = parts[0]
                if tag == "S":
                    payload = json.loads(line.split("|", 1)[1])
                    records.extend(parse_sensor_payload(payload.get("Sensors", []), payload.get("Hardware", []), payload.get("Source", "")))
                elif tag == "G":
                    value = finite_sensor_value(parts[2])
                    if value is not None: gpu[int(parts[1])] = gpu.get(int(parts[1]), 0.0) + value
                elif tag == "U":
                    value = finite_sensor_value(parts[1])
                    if value is not None: npu.append(value)
                elif tag == "V":
                    value = finite_sensor_value(parts[1])
                    if value is not None: gtemps.append(value)
                elif tag == "D":
                    value = finite_sensor_value(parts[2])
                    if value is not None: disk[parts[1]] = disk.get(parts[1], 0.0) + value
                elif tag == "N": net[int(parts[1])] = net.get(int(parts[1]), 0) + int(parts[2])
                elif tag == "TZ": zones[parts[1]] = temp_celsius(parts[2])
                elif tag == "HP": precise[parts[1]] = temp_celsius(parts[2])
                elif tag == "T": zones["Legacy thermal zone"] = temp_celsius(parts[1])
                elif tag == "PL": self.passive_limit = finite_sensor_value(parts[2])
                elif tag == "TR": self.throttle_reasons = finite_sensor_value(parts[2])
                elif tag == "3": records.extend(parse_sensor_payload([{"Name": parts[2], "Value": parts[3], "SensorType": "Temperature"}], source=parts[1]))
            except (ValueError, TypeError, IndexError, AttributeError):
                continue
        for index, value in enumerate(gtemps):
            records.extend(parse_sensor_payload([{"Name": "GPU adapter temperature", "Parent": "/gpu/counter", "Identifier": f"/gpu/counter/{index}", "SensorType": "Temperature", "Value": value}], source="Windows GPU counter"))
        self.gpu_pids, self.disk_names, self.net_pids = gpu, disk, net
        self.gpu = min(100.0, sum(gpu.values())) if gpu else None
        self.npu = min(100.0, sum(npu)) if npu else None
        records = read_bridge_sensors(getattr(self, "script_dir", "")) + records
        self.sensor_records = records
        summary = summarize_sensors(records)
        self.temp_cpu, self.temp_gpu = summary["cpu_temperature"], summary["gpu_temperature"]
        self.cpu_temperature_kind = summary["cpu_temperature_kind"]
        self.zone_temps = {k: v for k, v in zones.items() if v is not None}
        self.zone_precise = {k: v for k, v in precise.items() if v is not None}
        self.thermal_third = records
        self.temp_trust = self._judge_thermal()
        self.refresh_thermal_view()

    def update_metrics(self):
        wall_now = time.time()
        if self.last_sensor_at and wall_now - self.last_sensor_at > 15:
            self._parse_counters("")
            self.last_sensor_at = 0.0
        if self.last_power_at and wall_now - self.last_power_at > 15:
            self.power_records = []
            self.power_system_var.set("未提供")
            self.clear_power("功耗传感器已过期 / 断连")
            self.last_power_at = 0.0
        now = time.perf_counter(); current = system_times(); cpu_text = "--"
        if current and self.sys_sample:
            idle = current[0] - self.sys_sample[0]; total = (current[1] + current[2]) - (self.sys_sample[1] + self.sys_sample[2])
            if total > 0:
                self.cpu_percent = max(0, min(100, (total - idle) / total * 100))
                cpu_text = f"{self.cpu_percent:.0f}%"
        if current: self.sys_sample = current
        self.power_cpu_var.set(f"{self.cpu_percent:.0f} %")
        memory = MEMSTAT(); memory.length = ctypes.sizeof(memory)
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            memory_text = f"{memory.load}%"
        else: memory_text = "--"
        incoming, outgoing = network_bytes()
        if self.net_sample:
            elapsed = now - self.net_sample[2]
            down = max(0, incoming - self.net_sample[0]) * 8 / max(elapsed, 0.001) / 1_000_000
            up = max(0, outgoing - self.net_sample[1]) * 8 / max(elapsed, 0.001) / 1_000_000
            self.network.config(text=f"↑ {up:.2f} Mbps  ↓ {down:.2f} Mbps")
        self.net_sample = (incoming, outgoing, now)
        try:
            drive = shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
            disk_text = f"{drive.used / drive.total * 100:.0f}%"
        except OSError: disk_text = "--"
        gpu_text = f"{self.gpu:.0f}%" if self.gpu is not None else "--"
        npu_text = f"{self.npu:.0f}%" if self.npu is not None else "--"
        cpu_temp = f"{self.temp_cpu:.0f}°C" if self.temp_cpu is not None else "--"
        if self.temp_cpu is not None:
            cpu_temp += "(封装)" if self.cpu_temperature_kind == "cpu_package" else "(核心最高)"
        gpu_temp = f"{self.temp_gpu:.0f}°C" if self.temp_gpu is not None else "--"
        power_text = f"{self.power_sample[1]:.1f}W" if self.power_sample else "--"
        self.metrics.config(text=f"CPU {cpu_text}  功耗 {power_text}  内存 {memory_text}  GPU {gpu_text}  NPU {npu_text}  磁盘 {disk_text}  CPU温 {cpu_temp}  GPU温 {gpu_temp}")
        self.refresh_thermal_view()
        self.after(1000, self.update_metrics)

    def collect(self):
        buf = (wintypes.DWORD * 4096)(); used = wintypes.DWORD()
        if not psapi.EnumProcesses(ctypes.byref(buf), ctypes.sizeof(buf), ctypes.byref(used)): return []
        now, hung, rows, alive = time.perf_counter(), hung_pids(), [], set()
        for value in buf[:used.value // 4]:
            pid = int(value)
            if not pid or pid in alive: continue
            alive.add(pid); handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
            if not handle: continue
            try:
                pathbuf = ctypes.create_unicode_buffer(1024); length = wintypes.DWORD(len(pathbuf))
                path = pathbuf.value if kernel32.QueryFullProcessImageNameW(handle, 0, pathbuf, ctypes.byref(length)) else ""
                name = os.path.basename(path) or f"PID {pid}"
                mem = PMCX(); mem.cb = ctypes.sizeof(mem); memory = int(mem.working) if psapi.GetProcessMemoryInfo(handle, ctypes.byref(mem), mem.cb) else 0
                total, cpu = cpu_time(handle), 0.0; old = self.samples.get(pid)
                if total is not None and old and now > old[1]: cpu = max(0.0, min(100.0, (total - old[0]) / 10_000_000 / (now - old[1]) / (os.cpu_count() or 1) * 100))
                if total is not None: self.samples[pid] = (total, now)
                rows.append({"pid": pid, "name": name, "path": path, "memory": memory, "cpu": cpu, "hung": pid in hung, "gpu": self.gpu_pids.get(pid, 0.0), "net": self.net_pids.get(pid, 0)})
            finally: kernel32.CloseHandle(handle)
        self.samples = {p: x for p, x in self.samples.items() if p in alive}
        return rows

    def refresh(self, manual=False):
        if self.job: self.after_cancel(self.job); self.job = None
        if self.auto: self.job = self.after(1200, self.auto_refresh)
        if self.collecting: return
        self.collecting = True
        threading.Thread(target=self._collect_worker, args=(manual, self.sort, self.desc), daemon=True).start()

    def _collect_worker(self, manual, sort, desc):
        try: rows = self.collect()
        except Exception: rows = None
        self.queue.put(("rows", rows, manual, sort, desc))

    def _drain(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg[0] == "rows":
                    self.collecting = False
                    if msg[1] is not None: self.apply_rows(msg[1], msg[2], msg[3], msg[4])
                elif msg[0] == "refresh":
                    self.collecting = False
                    self.refresh(manual=False)
                elif msg[0] == "start_bridge":
                    script = msg[1]
                    # Only configured existing .ps1 files; ordinary policy applies, no Bypass.
                    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
                    if os.path.isfile(script) and script.lower().endswith(".ps1"):
                        self.start_monitor_companion(powershell, subprocess.list2cmdline(["-NoProfile", "-File", script]), os.path.dirname(script))
                elif msg[0] == "start_monitor":
                    self.start_monitor_companion(msg[1])
                elif msg[0] == "counters":
                    self.last_sensor_at = msg[2]
                    self._parse_counters(msg[1] if time.time() - msg[2] <= 15 else "")
                elif msg[0] == "power_snapshot":
                    if time.time() - msg[1] <= 15:
                        self.apply_power_snapshot(msg[1], msg[2], msg[3])
                    else:
                        self.power_records = []
                        self.power_system_var.set("未提供")
                        self.clear_power("功耗传感器已过期")
                elif msg[0] == "power":
                    self.last_power_at = time.time()
                    self.apply_power(msg[1])
                elif msg[0] == "power_error":
                    self.power_records = []
                    self.power_system_var.set("未提供")
                    self.clear_power(f"功耗传感器读取失败：{msg[1]}")
                    self.power_status_label.configure(fg="#c62828")
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def apply_rows(self, rows, manual, sort, desc):
        self.last_rows = rows
        current_selection = self.tree.selection()
        selected = {self.keys.get(x) for x in current_selection}
        groups = {}
        fallback = self.tree.index(current_selection[0]) if current_selection else 0
        for row in rows:
            key = row["name"].lower()
            group = groups.setdefault(key, {"name": row["name"], "path": row["path"], "pids": [], "cpu": 0.0, "memory": 0, "gpu": 0.0, "disk": 0.0, "net": 0, "hung": False})
            group["pids"].append(row["pid"]); group["cpu"] += row["cpu"]; group["memory"] += row["memory"]
            group["gpu"] += row.get("gpu", 0.0); group["net"] += row.get("net", 0); group["hung"] |= row["hung"]
            group["disk"] = self.disk_names.get(norm_name(row["name"]), 0.0)
        rows = list(groups.values())
        if sort == "priority":
            sorter = lambda x: (int(x["hung"]), x["cpu"])
        elif sort == "status":
            sorter = lambda x: int(x["hung"])
        elif sort == "name":
            sorter = lambda x: x["name"].lower()
        else:
            sorter = lambda x: x[sort]
        rows.sort(key=sorter, reverse=desc)
        old = self.tree.get_children()
        if old: self.tree.delete(*old)
        self.keys = {}
        for row in rows:
            key = row["name"].lower()
            if key not in self.icons: self.icons[key] = program_icon(row["path"], row["name"])
            tag = "hung" if row["hung"] else "hot" if row["cpu"] >= 25 else ""
            values = ("未响应" if row["hung"] else "正常", row["name"], len(row["pids"]), f"{row['cpu']:.1f}%", pretty_size(row["memory"]),
                      f"{row['gpu']:.0f}%" if row["gpu"] else "--", pretty_speed(row["disk"]), f"{row['net']} 连接")
            iid = self.tree.insert("", "end", text="", image=self.icons[key], values=values, tags=(tag,) if tag else ())
            self.keys[iid] = key
            if key in selected: self.tree.selection_add(iid)
        children = self.tree.get_children()
        if children and not self.tree.selection():
            index = min(fallback, len(children) - 1)
            self.tree.selection_set(children[index]); self.tree.focus(children[index]); self.tree.see(children[index])
        if manual and children:
            self.tree.selection_set(children[0]); self.tree.focus(children[0]); self.tree.see(children[0])
        self.summary.config(text=f"{len(rows)} 个软件 · 未响应 {sum(x['hung'] for x in rows)} 个 · {'自动刷新中' if self.auto else '仅手动刷新'}")

    def targets(self):
        result = []
        for item in self.tree.selection():
            values = self.tree.item(item, "values")
            if values and values[1].lower() != os.path.basename(sys.executable).lower(): result.append((values[1], self.keys.get(item, values[1].lower())))
        return result

    def taskkill_all(self, targets):
        mapping = {}
        for row in self.last_rows: mapping.setdefault(row["name"].lower(), []).append(row["pid"])
        for _, key in targets:
            for pid in mapping.get(key, []):
                try: subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15, creationflags=0x08000000)
                except subprocess.SubprocessError: pass

    def kill_selected(self):
        targets = self.targets()
        if not targets: return
        threading.Thread(target=self._kill_worker, args=(targets,), daemon=True).start()

    def _kill_worker(self, targets):
        self.taskkill_all(targets)
        self.queue.put(("refresh",))

    def restart_selected(self):
        targets = self.targets()
        targets = [(name, key) for name, key in targets if name.lower() not in self.no_restart]
        if not targets: return
        paths = {x["name"].lower(): x["path"] for x in self.last_rows}
        threading.Thread(target=self._restart_worker, args=(targets, paths), daemon=True).start()

    def _restart_worker(self, targets, paths):
        self.taskkill_all(targets)
        for _, key in targets:
            path = paths.get(key)
            if path and os.path.isfile(path):
                try: subprocess.Popen([path], creationflags=0x08000000)
                except OSError: pass
        self.queue.put(("refresh",))


if __name__ == "__main__":
    if sys.platform != "win32": raise SystemExit("Windows only")
    shell32.SetCurrentProcessExplicitAppUserModelID("jg.FastProcessManager")
    App().mainloop()
