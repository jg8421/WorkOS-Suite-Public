"""Local, read-only file workbench for quickly previewing common office files."""

from __future__ import annotations

import json
import io
from html import escape
import hashlib
import ctypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
import struct
from ctypes import wintypes
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, VERTICAL, X, Y, filedialog, messagebox, simpledialog, ttk
import tkinter as tk
from tkinter import font as tkfont
from xml.etree import ElementTree as ET

import fitz
from PIL import Image, ImageDraw, ImageTk
from tksheet import Sheet
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
import markdown
from tkinterweb import HtmlFrame
from msg_reader import MsgReader


OFFICE_EXTENSIONS = {".doc", ".docx", ".xlsx", ".xlsm", ".ppt", ".pptx"}
MSG_EXTENSIONS = {".msg"}
ZIP_EXTENSIONS = {".zip"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tif", ".tiff"}
PREVIEWABLE_TEXT = {".txt", ".md", ".csv", ".py", ".ps1", ".json", ".html", ".xml"}
SEARCHABLE_CONTENT_EXTENSIONS = PREVIEWABLE_TEXT | {".pdf", ".docx", ".msg"}
MAX_SEARCH_FILE_BYTES = 25 * 1024 * 1024
MAX_SEARCH_TEXT_CHARS = 60_000
MAX_SEARCH_RESULTS = 200
MAX_SEARCH_CACHE_ENTRIES = 400
MAX_SEARCH_FILES = 20_000
MAX_TEXT = 250_000
if getattr(sys, "frozen", False):
    # User data (settings/cache) lives in %APPDATA%\FileWorkbench so the release
    # folder stays clean and portable; bundled helpers live in the _internal
    # bundle directory that PyInstaller exposes via sys._MEIPASS.
    BASE_DIR = Path(os.environ.get("APPDATA") or str(Path.home())) / "FileWorkbench"
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)).resolve()
else:
    BASE_DIR = Path(__file__).resolve().parent
    BUNDLE_DIR = BASE_DIR
BASE_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = BASE_DIR / "settings.json"
AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE = "FileWorkbench"

PREVIEW_CACHE = BASE_DIR / "preview_cache"
OFFICE_RENDER_SCRIPT = BUNDLE_DIR / "render_office_preview.ps1"
EXCEL_EDIT_SCRIPT = BUNDLE_DIR / "edit_excel_cell.ps1"
APP_ICON_PATH = BUNDLE_DIR / "file_workbench.ico"
NATIVE_PDF_PREVIEW = BUNDLE_DIR / "native_pdf_preview.exe"
# Show a useful continuous reading window by default.  The cache is deliberately
# large, so reopening a long document stays fast after its first render.
MAX_PREVIEW_PAGES = 16
PREVIEW_RASTER_SCALE = 2.0
OFFICE_QUICK_SCALE = 1.2
OFFICE_QUICK_PAGES = 2
PREVIEW_CACHE_LIMIT = 5 * 1024 * 1024 * 1024
PREVIEW_CACHE_TARGET = 4 * 1024 * 1024 * 1024
PV_WORKING_ROOT = Path.home() / "Documents"


def enable_high_dpi() -> None:
    """Ask Windows for sharp per-monitor rendering before Tk creates a window."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


enable_high_dpi()
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return "-"


def make_file_icon(color: str, folder: bool = False) -> ImageTk.PhotoImage:
    image = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if folder:
        draw.rounded_rectangle((2, 6, 18, 17), radius=2, fill=color)
        draw.rounded_rectangle((3, 3, 10, 9), radius=2, fill="#f6c343")
    else:
        draw.rounded_rectangle((4, 2, 16, 18), radius=2, fill="#ffffff", outline=color, width=2)
        draw.rectangle((6, 9, 14, 11), fill=color)
        draw.rectangle((6, 13, 12, 14), fill=color)
    return ImageTk.PhotoImage(image)


def ensure_app_icon() -> Path:
    if APP_ICON_PATH.exists():
        return APP_ICON_PATH
    image = Image.new("RGBA", (256, 256), "#2563eb")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((28, 58, 228, 206), radius=22, fill="#f8fafc")
    draw.rounded_rectangle((42, 38, 126, 88), radius=16, fill="#f4b942")
    draw.rectangle((58, 104, 198, 119), fill="#2563eb")
    draw.rectangle((58, 137, 176, 152), fill="#60a5fa")
    draw.rectangle((58, 170, 150, 185), fill="#93c5fd")
    image.save(APP_ICON_PATH, format="ICO", sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])
    return APP_ICON_PATH


def load_settings() -> dict[str, object]:
    default_workspace = PV_WORKING_ROOT if PV_WORKING_ROOT.is_dir() else Path.home()
    defaults: dict[str, object] = {
        "last_folder": str(default_workspace),
        "favorites": [str(Path.home() / "Documents"), str(Path.home() / "Downloads")],
        "tabs": [str(default_workspace)],
        "active_tab": 0,
    }
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            defaults.update(saved)
    except (OSError, json.JSONDecodeError):
        pass
    return defaults


def office_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            if path.suffix.lower() == ".docx":
                root = ET.fromstring(archive.read("word/document.xml"))
                paragraphs = []
                for p in root.findall(".//w:p", NS):
                    value = "".join(node.text or "" for node in p.findall(".//w:t", NS)).strip()
                    if value:
                        paragraphs.append(value)
                return "\n\n".join(paragraphs[:3000]) or "(此 Word 文件没有可读取正文)"

            if path.suffix.lower() in {".xlsx", ".xlsm"}:
                shared = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                    for si in shared_root.findall("s:si", NS):
                        shared.append("".join(t.text or "" for t in si.findall(".//s:t", NS)))
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                sheets = workbook.findall(".//s:sheet", NS)
                output = [f"Workbook preview: {len(sheets)} sheet(s)"]
                for index, sheet in enumerate(sheets[:6], 1):
                    sheet_name = sheet.attrib.get("name", f"Sheet {index}")
                    target = f"xl/worksheets/sheet{index}.xml"
                    output.extend(["", f"[{sheet_name}]"])
                    if target not in archive.namelist():
                        output.append("(preview unavailable)")
                        continue
                    root = ET.fromstring(archive.read(target))
                    rows = []
                    for row in root.findall(".//s:sheetData/s:row", NS):
                        values = []
                        for cell in row.findall("s:c", NS):
                            value = cell.findtext("s:v", default="", namespaces=NS)
                            if cell.attrib.get("t") == "s" and value.isdigit() and int(value) < len(shared):
                                value = shared[int(value)]
                            if not value:
                                value = cell.findtext("s:f", default="", namespaces=NS)
                                if value:
                                    value = "=" + value
                            values.append(value)
                        if any(values):
                            rows.append(" | ".join(values[:18]))
                        if len(rows) >= 45:
                            break
                    output.extend(rows or ["(empty sheet)"])
                return "\n".join(output)

            if path.suffix.lower() == ".pptx":
                slides = sorted(
                    name for name in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                )
                output = [f"Presentation preview: {len(slides)} slide(s)"]
                for number, name in enumerate(slides[:80], 1):
                    root = ET.fromstring(archive.read(name))
                    text = " ".join(node.text or "" for node in root.findall(".//a:t", NS)).strip()
                    output.extend(["", f"Slide {number}", text or "(no text)"])
                return "\n".join(output)
    except (KeyError, zipfile.BadZipFile, ET.ParseError, PermissionError) as exc:
        return f"Preview unavailable: {exc}"
    return "Preview unavailable for this file type."


def text_preview(path: Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in MSG_EXTENSIONS:
        return MsgReader(path).preview_text()
    if suffix in OFFICE_EXTENSIONS:
        return office_text(path)
    if suffix in PREVIEWABLE_TEXT:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:MAX_TEXT]
        except OSError as exc:
            return f"Preview unavailable: {exc}"
    if suffix == ".pdf":
        return "PDF quick preview is not rendered locally in this first version. Select Open to view it in your preferred PDF reader."
    return "No inline preview for this format. Select Open to launch the default application."


def markdown_html(source: str) -> str:
    body = markdown.markdown(source, extensions=["extra", "tables", "fenced_code", "sane_lists", "nl2br"])
    return """<!doctype html><html><head><meta charset='utf-8'><style>
    body { font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI', sans-serif; font-weight: 400; letter-spacing: 0.2px; color: #171717; background: #ffffff; max-width: 100%; margin: 20px 8px 60px; padding: 0 22px; font-size: 20px; line-height: 1.9; }
    h1,h2,h3 { color: #0f172a; line-height: 1.3; margin-top: 1.6em; } h1 { font-size: 2em; border-bottom: 1px solid #e2e8f0; padding-bottom: .35em; } h2 { font-size: 1.5em; }
    code { font-family: Consolas, monospace; background: #f1f5f9; padding: 2px 5px; border-radius: 4px; } pre { background: #0f172a; color: #e2e8f0; padding: 16px; border-radius: 8px; overflow-x: auto; } pre code { background: transparent; color: inherit; padding: 0; }
    blockquote { border-left: 4px solid #60a5fa; margin: 1em 0; padding: .2em 1em; color: #475569; background: #f8fafc; } table { border-collapse: collapse; width: 100%; margin: 1em 0; } th,td { border: 1px solid #cbd5e1; padding: 8px 10px; text-align: left; } th { background: #eff6ff; } a { color: #2563eb; }
    </style></head><body>""" + body + "</body></html>"


def search_document_text(path: Path) -> str:
    """Extract a deliberately small searchable text slice for responsive local search."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in PREVIEWABLE_TEXT:
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_SEARCH_TEXT_CHARS].lower()
    if suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            source = archive.read("word/document.xml")[:MAX_SEARCH_TEXT_CHARS * 4].decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", source).lower()[:MAX_SEARCH_TEXT_CHARS]
    if suffix == ".pdf":
        with fitz.open(path) as document:
            return " ".join(document.load_page(index).get_text() for index in range(min(3, document.page_count))).lower()[:MAX_SEARCH_TEXT_CHARS]
    if suffix == ".msg":
        return MsgReader(path).preview_text().lower()[:MAX_SEARCH_TEXT_CHARS]
    return ""


def render_pdf_first_page(path: Path, scale: float = PREVIEW_RASTER_SCALE) -> Image.Image:
    cached = cached_path(path, f".first-{scale:.2f}x.png")
    if cached.exists():
        return Image.open(cached).copy()
    with fitz.open(path) as document:
        if not document.page_count:
            raise ValueError("PDF has no pages")
        pixmap = document.load_page(0).get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    image.save(cached, "PNG", compress_level=1)
    cleanup_preview_cache()
    return image


def stitch_pages(pages: list[Image.Image]) -> Image.Image:
    width = max(page.width for page in pages)
    gap = 18
    height = sum(page.height for page in pages) + gap * (len(pages) - 1)
    canvas = Image.new("RGB", (width + 20, height + 20), "#e9edf2")
    position = 10
    for page in pages:
        canvas.paste(page, ((width - page.width) // 2 + 10, position))
        position += page.height + gap
    return canvas


def render_pdf_pages(path: Path, scale: float = PREVIEW_RASTER_SCALE, page_limit: int = MAX_PREVIEW_PAGES) -> Image.Image:
    cached = cached_path(path, f".pages-{scale:.2f}x-{page_limit}.png")
    if cached.exists():
        return Image.open(cached).copy()
    pages: list[Image.Image] = []
    with fitz.open(path) as document:
        for index in range(min(document.page_count, page_limit)):
            pixmap = document.load_page(index).get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            pages.append(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples))
    if not pages:
        raise ValueError("PDF has no pages")
    image = stitch_pages(pages)
    image.save(cached, "PNG", compress_level=1)
    cleanup_preview_cache()
    return image


def embedded_office_thumbnail(path: Path) -> Image.Image | None:
    try:
        with zipfile.ZipFile(path) as archive:
            for name in ("docProps/thumbnail.jpeg", "docProps/thumbnail.png"):
                if name in archive.namelist():
                    return Image.open(io.BytesIO(archive.read(name))).copy()
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def cached_path(path: Path, suffix: str) -> Path:
    fingerprint = f"{path.resolve()}|{path.stat().st_mtime_ns}".encode("utf-8")
    PREVIEW_CACHE.mkdir(exist_ok=True)
    return PREVIEW_CACHE / (hashlib.sha1(fingerprint).hexdigest() + suffix)


def cache_entry_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def cleanup_preview_cache() -> None:
    """Keep regenerated preview data bounded without touching source documents."""
    if not PREVIEW_CACHE.exists():
        return
    entries = []
    for item in PREVIEW_CACHE.iterdir():
        try:
            entries.append((item, cache_entry_size(item), item.stat().st_mtime))
        except OSError:
            continue
    total = sum(size for _, size, _ in entries)
    if total <= PREVIEW_CACHE_LIMIT:
        return
    for item, size, _ in sorted(entries, key=lambda value: value[2]):
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            total -= size
        except OSError:
            continue
        if total <= PREVIEW_CACHE_TARGET:
            break


def ensure_office_pdf(path: Path) -> Path:
    """Export a Word/PowerPoint file to a cached PDF (worker thread only).

    Returns the cached PDF path.  A leftover partial/corrupt render is
    removed so a broken export is never served as a valid preview.
    """
    suffix = path.suffix.lower()
    rendered_pdf = cached_path(path, ".pdf")
    if rendered_pdf.exists():
        try:
            with fitz.open(rendered_pdf) as document:
                if document.page_count < 1:
                    raise ValueError("empty PDF")
            return rendered_pdf
        except Exception:
            try:
                rendered_pdf.unlink(missing_ok=True)
            except OSError:
                pass
    kind = "word" if suffix in {".doc", ".docx"} else "powerpoint"
    run_office_renderer(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", str(OFFICE_RENDER_SCRIPT), "-Source", str(path), "-Output", str(rendered_pdf), "-Kind", kind],
    )
    return rendered_pdf


def run_office_renderer(command: list[str]) -> None:
    options: dict[str, object] = {"check": True, "capture_output": True, "text": True, "timeout": 90}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    before = office_process_ids()
    try:
        subprocess.run(command, **options)
        # Sweep up hidden Word/PowerPoint instances left behind after a
        # successful export so the machine stays clean.
        kill_new_office_processes(before)
    except Exception:
        # Remove the partial output PDF so a broken render is never served as valid.
        for argument in command:
            if isinstance(argument, str) and argument.lower().endswith(".pdf"):
                try:
                    Path(argument).unlink(missing_ok=True)
                except OSError:
                    pass
        kill_new_office_processes(before)
        raise


def office_process_ids() -> set[int]:
    """PIDs of running Word/PowerPoint processes (best effort, Windows only)."""
    if os.name != "nt":
        return set()
    ids: set[int] = set()
    for image in ("WINWORD.EXE", "POWERPNT.EXE"):
        try:
            output = subprocess.run(
                ["tasklist.exe", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in output.splitlines():
            parts = line.replace('"', "").split(",")
            if len(parts) >= 2 and parts[0].upper() == image and parts[1].isdigit():
                ids.add(int(parts[1]))
    return ids


def kill_new_office_processes(before: set[int]) -> None:
    """Kill Word/PowerPoint processes that appeared since a render started."""
    for pid in office_process_ids() - before:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def excel_color(color: object, default: str) -> str:
    value = getattr(color, "rgb", None)
    if isinstance(value, str) and len(value) >= 6:
        return "#" + value[-6:]
    return default


def excel_display(cell: object) -> str:
    value = getattr(cell, "value", None)
    if value is None:
        return ""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return str(value)[:220]
    number_format = str(getattr(cell, "number_format", "General") or "General")
    if "%" in number_format:
        before_percent = number_format.split("%", 1)[0]
        decimals = len(before_percent.rsplit(".", 1)[1]) if "." in before_percent else 0
        return f"{value * 100:,.{decimals}f}%"
    decimals = 0
    if "." in number_format:
        fractional = number_format.rsplit(".", 1)[1].split(";", 1)[0]
        decimals = sum(character in "0#" for character in fractional)
    formatted = f"{value:,.{decimals}f}" if "," in number_format else f"{value:.{decimals}f}"
    currency = next((symbol for symbol in ("$", "¥", "€", "£") if symbol in number_format), "")
    return currency + formatted


def excel_workbook_grid(path: Path) -> list[tuple[str, list[str], list[tuple[list[str], str, str, bool]]]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        result = []
        for sheet in workbook.worksheets[:20]:
            max_row = min(sheet.max_row or 1, 70)
            max_column = min(sheet.max_column or 1, 16)
            headers = [f"{index}" for index in range(1, max_column + 1)]
            rows = []
            for row in sheet.iter_rows(min_row=1, max_row=max_row, max_col=max_column):
                values = [excel_display(cell) for cell in row]
                styled = next((cell for cell in row if cell.value is not None), row[0])
                fill_object = getattr(styled, "fill", None)
                fill = excel_color(getattr(fill_object, "fgColor", None), "#ffffff") if getattr(fill_object, "fill_type", None) else "#ffffff"
                font_object = getattr(styled, "font", None)
                foreground = excel_color(getattr(font_object, "color", None), "#000000") if getattr(font_object, "color", None) else "#000000"
                rows.append((values, fill, foreground, bool(getattr(font_object, "bold", False))))
            result.append((sheet.title, headers, rows))
        return result
    finally:
        workbook.close()


class FileWorkbench(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("File Workbench")
        self.app_icon_path = ensure_app_icon()
        self.iconbitmap(str(self.app_icon_path))
        self.geometry("1680x1040")
        self.minsize(1180, 760)
        self.state("zoomed")
        self.settings = load_settings()
        threading.Thread(target=cleanup_preview_cache, daemon=True).start()
        previous = Path(str(self.settings.get("last_folder", Path.home())))
        self.current_folder = previous if previous.is_dir() else Path.home()
        self.folder_tabs = [Path(str(item)) for item in self.settings.get("tabs", [str(self.current_folder)]) if Path(str(item)).is_dir()]
        if not self.folder_tabs:
            self.folder_tabs = [self.current_folder]
        self.active_folder_tab = min(max(int(self.settings.get("active_tab", 0)), 0), len(self.folder_tabs) - 1)
        self.current_folder = self.folder_tabs[self.active_folder_tab]
        self.path_by_item: dict[str, Path] = {}
        self.preview_jobs: queue.Queue[tuple[int, str, object]] = queue.Queue()
        self.preview_lock = threading.Lock()
        self.preview_request: tuple[object, int] | None = None
        self.preview_worker_running = False
        self.preview_token = 0
        self.office_preview_job: str | None = None
        self.previewed_path: Path | None = None
        self.preview_pdf_path: Path | None = None
        self.zip_preview_path: Path | None = None
        self.native_preview_process: subprocess.Popen[bytes] | None = None
        self.native_preview_path: Path | None = None
        self.pdf_zoom_generation = 0
        self.inline_markdown_path: Path | None = None
        self.preview_font_size = 16
        self.markdown_zoom = 1.0
        self.folder_history = [self.current_folder]
        self.folder_history_index = 0
        self.pending_selection_name: str | None = None
        self.file_clipboard: list[Path] = []
        self.file_clipboard_mode: str | None = None
        self.folder_stamp: int | None = None
        self.search_jobs: queue.Queue[tuple[int, list[tuple[Path, str, int, int]], int, Path]] = queue.Queue()
        self.search_token = 0
        self.search_results_active = False
        self.search_content_cache: dict[str, tuple[int, int, str]] = {}
        self.file_column_fit_job: str | None = None
        self._build_ui()
        self.bind_all("<Control-plus>", self.increase_reading_size)
        self.bind_all("<Control-equal>", self.increase_reading_size)
        self.bind_all("<Control-minus>", self.decrease_reading_size)
        self.bind_all("<Alt-Left>", self.navigate_back)
        self.bind_all("<Alt-Right>", self.navigate_forward)
        self.bind_all("<Alt-Up>", self.navigate_up)
        self.bind_all("<Up>", self.route_file_list_arrow, add="+")
        self.bind_all("<Down>", self.route_file_list_arrow, add="+")
        self.bind_all("<Control-c>", self.route_copy, add="+")
        self.bind_all("<Control-v>", self.route_paste_files, add="+")
        self.bind_all("<Alt_L>", self.request_office_full_preview, add="+")
        self.bind_all("<Alt_R>", self.request_office_full_preview, add="+")
        self.after(300, self.guard_preview_focus)
        self.load_folder(self.current_folder)
        self.refresh_folder_tabs()
        self.after(120, self.poll_preview)
        self.after(140, self.poll_search)
        self.after(120, self.place_sashes)
        self.after(1500, self.watch_current_folder)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Workbench.TPanedwindow", sashwidth=3, background="#cbd5e1")
        self.configure(background="#f5f7fa")
        style.configure("TFrame", background="#f5f7fa")
        style.configure("TLabel", background="#f5f7fa", foreground="#1f2937")
        style.configure("TButton", font=("Microsoft YaHei", 10), padding=(10, 6))
        style.configure("Treeview", rowheight=34, font=("Microsoft YaHei", 11), background="#ffffff", fieldbackground="#ffffff", foreground="#000000", borderwidth=0)
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#0f172a")])
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), background="#edf2f7", foreground="#475569", relief="flat", padding=(8, 8))
        style.configure("Heading", font=("Segoe UI", 10, "bold"))

        self.file_icons = {
            "folder": make_file_icon("#f4b942", folder=True), "doc": make_file_icon("#2b6cb0"),
            "xls": make_file_icon("#2f855a"), "ppt": make_file_icon("#dd6b20"), "pdf": make_file_icon("#c53030"),
            "md": make_file_icon("#805ad5"), "msg": make_file_icon("#0e7490"), "img": make_file_icon("#16a34a"), "zip": make_file_icon("#b45309"), "file": make_file_icon("#718096"),
        }
        tab_outer = ttk.Frame(self, padding=(12, 8, 12, 0))
        tab_outer.pack(fill=X)
        self.folder_tab_bar = ttk.Frame(tab_outer)
        self.folder_tab_bar.pack(side=LEFT, fill=X, expand=True)
        ttk.Button(tab_outer, text="+", width=3, command=self.new_folder_tab).pack(side=RIGHT)

        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.pack(fill=X)
        ttk.Button(top, text="Open folder", command=self.choose_folder).pack(side=LEFT)
        ttk.Button(top, text="Up", command=self.go_up).pack(side=LEFT, padx=(6, 0))
        ttk.Button(top, text="Pin current folder", command=self.pin_current_folder).pack(side=LEFT, padx=(6, 0))
        self.auto_start_var = tk.BooleanVar(value=self.is_auto_start())
        ttk.Checkbutton(top, text="开机自启动", variable=self.auto_start_var, command=self.toggle_auto_start).pack(side=LEFT, padx=(10, 0))
        self.path_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.path_var)
        entry.pack(side=LEFT, fill=X, expand=True, padx=8)
        entry.bind("<Return>", lambda _event: self.load_folder(Path(self.path_var.get())))
        self.search_var = tk.StringVar()
        search = ttk.Entry(top, textvariable=self.search_var, width=30)
        search.pack(side=RIGHT)
        ttk.Button(top, text="Search all", command=self.start_global_search).pack(side=RIGHT, padx=(0, 6))
        search.bind("<KeyRelease>", self.filter_current_folder)
        search.bind("<Return>", self.start_global_search)

        self.body = ttk.Panedwindow(self, orient="horizontal", style="Workbench.TPanedwindow")
        self.body.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))

        left = ttk.Frame(self.body, width=280)
        favorites_bar = ttk.Frame(left)
        favorites_bar.pack(fill=X, pady=(0, 6))
        ttk.Label(favorites_bar, text="Pinned folders", font=("Segoe UI", 10, "bold")).pack(side=LEFT)
        ttk.Button(favorites_bar, text="Remove", command=self.unpin_selected_favorite).pack(side=RIGHT)
        self.favorites_list = tk.Listbox(left, height=6, activestyle="none", font=("Segoe UI", 10))
        self.favorites_list.pack(fill=X, pady=(0, 8))
        self.favorites_list.bind("<<ListboxSelect>>", self.select_favorite)
        ttk.Separator(left).pack(fill=X, pady=(0, 6))
        ttk.Label(left, text="Folders", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 3))
        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill=BOTH, expand=True)
        self.folder_tree = ttk.Treeview(tree_frame, show="tree")
        self.folder_tree.pack(side=LEFT, fill=BOTH, expand=True)
        left_scroll = ttk.Scrollbar(tree_frame, orient=VERTICAL, command=self.folder_tree.yview)
        left_scroll.pack(side=RIGHT, fill=Y)
        self.folder_tree.configure(yscrollcommand=left_scroll.set)
        self.folder_tree.bind("<<TreeviewOpen>>", self.expand_tree)
        self.folder_tree.bind("<<TreeviewSelect>>", self.select_tree_folder)
        self.folder_tree.bind("<ButtonRelease-1>", self.tree_row_click)
        self.body.add(left, weight=1)

        center = ttk.Frame(self.body, width=570)
        columns = ("type", "modified", "size")
        self.file_list = ttk.Treeview(center, columns=columns, show=("tree", "headings"), selectmode="extended")
        self.file_list.heading("#0", text="Name", command=lambda: self.sort_by("name"))
        self.file_list.column("#0", width=310, anchor="w")
        headings = {"type": "Type", "modified": "Modified", "size": "Size"}
        widths = {"type": 80, "modified": 135, "size": 80}
        for col in columns:
            self.file_list.heading(col, text=headings[col], command=lambda c=col: self.sort_by(c))
            self.file_list.column(col, width=widths[col], anchor="w")
        self.file_list.pack(side=LEFT, fill=BOTH, expand=True)
        list_scroll = ttk.Scrollbar(center, orient=VERTICAL, command=self.file_list.yview)
        list_scroll.pack(side=RIGHT, fill=Y)
        self.file_list.configure(yscrollcommand=list_scroll.set)
        self.file_list.bind("<<TreeviewSelect>>", self.preview_selected)
        self.file_list.bind("<Double-1>", lambda _event: self.open_selected())
        self.file_list.bind("<Return>", self.enter_selected)
        self.file_list.bind("<Down>", self.file_list_arrow)
        self.file_list.bind("<Up>", self.file_list_arrow)
        self.file_list.bind("<Control-c>", lambda _event: self.copy_selected_files())
        self.file_list.bind("<Control-x>", lambda _event: self.cut_selected_files())
        self.file_list.bind("<Control-v>", lambda _event: self.paste_files())
        self.file_list.bind("<F2>", self.rename_selected_file)
        self.file_list.bind("<Configure>", self.schedule_file_column_fit, add="+")
        self.body.add(center, weight=3)

        right = ttk.Frame(self.body, width=520)
        preview_bar = ttk.Frame(right)
        preview_bar.pack(fill=X)
        self.preview_title = tk.StringVar(value="Preview")
        ttk.Label(preview_bar, textvariable=self.preview_title, font=("Segoe UI", 11, "bold")).pack(side=LEFT)
        self.export_label = ttk.Label(preview_bar, text="正在导出中…", foreground="#2563eb")
        self.export_progress = ttk.Progressbar(preview_bar, mode="indeterminate", length=140)
        ttk.Button(preview_bar, text="Open", command=self.open_selected).pack(side=RIGHT)
        ttk.Button(preview_bar, text="Edit", command=self.edit_selected).pack(side=RIGHT, padx=(0, 6))
        self.save_markdown_button = ttk.Button(preview_bar, text="Save", command=self.save_inline_markdown)
        self.preview_host = ttk.Frame(right)
        self.preview_host.pack(fill=BOTH, expand=True, pady=(8, 0))
        self.preview = tk.Text(self.preview_host, wrap="word", font=("Microsoft YaHei", self.preview_font_size), padx=18, pady=16, state="disabled")
        self.preview_canvas = tk.Canvas(self.preview_host, highlightthickness=0, background="white")
        self.native_preview_host = tk.Frame(self.preview_host, background="white", highlightthickness=0)
        self.markdown_view = HtmlFrame(self.preview_host, messages_enabled=False, vertical_scrollbar=True, horizontal_scrollbar=False, stylesheets_enabled=True, on_link_click=self.handle_preview_link)
        self.excel_stack = ttk.Frame(self.preview_host)
        self.excel_content = ttk.Frame(self.excel_stack)
        self.excel_content.pack(fill=BOTH, expand=True)
        self.excel_tabs = ttk.Frame(self.excel_stack)
        self.excel_tabs.pack(fill=X, pady=(6, 0))
        self.preview_scroll = ttk.Scrollbar(self.preview_host, orient=VERTICAL, command=self.preview.yview)
        self.preview.pack(side=LEFT, fill=BOTH, expand=True)
        self.preview_scroll.pack(side=RIGHT, fill=Y)
        self.preview.configure(yscrollcommand=self.preview_scroll.set)
        self.preview_canvas.configure(yscrollcommand=self.preview_scroll.set)
        self.preview_image: ImageTk.PhotoImage | None = None
        self.preview_source: Image.Image | None = None
        self.preview_zoom = 1.0
        self.zoom_job: str | None = None
        self.preview_canvas.bind("<Control-MouseWheel>", self.zoom_preview)
        self.preview_canvas.bind("<MouseWheel>", self.scroll_preview)
        self.preview.bind("<MouseWheel>", self.scroll_preview)
        self.preview_host.bind("<Configure>", self.refit_preview)
        self.body.add(right, weight=3)

        self.status = tk.StringVar(value="Local read-only preview. Nothing is uploaded.")
        ttk.Label(self, textvariable=self.status, padding=(12, 0, 12, 8)).pack(fill=X)
        self.populate_tree()
        self.refresh_favorites()

    def place_sashes(self) -> None:
        # ttk may otherwise collapse the first pane on first launch.
        try:
            width = self.body.winfo_width()
            self.body.sashpos(0, max(160, int(width * 0.08)))
            self.body.sashpos(1, int(width * 0.49))
            self.after_idle(self.fit_file_columns)
        except tk.TclError:
            pass

    def save_settings(self) -> None:
        try:
            SETTINGS_PATH.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    # ---------- 开机自启动 ----------
    def _autostart_command(self) -> str:
        """返回写入注册表 Run 键的启动命令（跟随当前运行方式：正式版 exe 或开发版 pythonw）"""
        if getattr(sys, "frozen", False):
            exe = Path(sys.executable).resolve()
            if exe.suffix.lower() == ".exe":
                return f'"{exe}"'
        py = Path(sys.executable).resolve()
        pythonw = py.with_name("pythonw.exe")
        if not pythonw.exists():
            pythonw = py
        return f'"{pythonw}" "{Path(__file__).resolve()}"'

    def is_auto_start(self) -> bool:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
                value, _kind = winreg.QueryValueEx(key, AUTOSTART_VALUE)
            return bool(value)
        except OSError:
            return False

    def set_auto_start(self, enabled: bool) -> bool:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, AUTOSTART_VALUE, 0, winreg.REG_SZ, self._autostart_command())
                else:
                    try:
                        winreg.DeleteValue(key, AUTOSTART_VALUE)
                    except FileNotFoundError:
                        pass
            return True
        except OSError as exc:
            self.status.set(f"设置开机自启动失败：{exc}")
            return False

    def toggle_auto_start(self) -> None:
        enabled = bool(self.auto_start_var.get())
        if self.set_auto_start(enabled):
            self.status.set("已开启开机自启动（下次开机自动运行 File Workbench）" if enabled else "已关闭开机自启动")
        else:
            self.auto_start_var.set(self.is_auto_start())

    def refresh_folder_tabs(self) -> None:
        for child in self.folder_tab_bar.winfo_children():
            child.destroy()
        for index, folder in enumerate(self.folder_tabs):
            tab = ttk.Frame(self.folder_tab_bar, padding=(3, 0))
            tab.pack(side=LEFT, padx=(0, 4))
            label = folder.name or str(folder)
            ttk.Button(tab, text=("● " if index == self.active_folder_tab else "") + label, command=lambda selected=index: self.open_folder_tab(selected)).pack(side=LEFT)
            ttk.Button(tab, text="x", width=2, command=lambda selected=index: self.close_folder_tab(selected)).pack(side=LEFT)

    def persist_folder_tabs(self) -> None:
        self.settings["tabs"] = [str(folder) for folder in self.folder_tabs]
        self.settings["active_tab"] = self.active_folder_tab
        self.settings["last_folder"] = str(self.current_folder)
        self.save_settings()

    def open_folder_tab(self, index: int) -> None:
        if 0 <= index < len(self.folder_tabs):
            self.active_folder_tab = index
            self.load_folder(self.folder_tabs[index])

    def close_folder_tab(self, index: int) -> None:
        if not (0 <= index < len(self.folder_tabs)):
            return
        self.folder_tabs.pop(index)
        if not self.folder_tabs:
            root = PV_WORKING_ROOT if PV_WORKING_ROOT.is_dir() else Path.home()
            self.folder_tabs.append(root)
        self.active_folder_tab = min(self.active_folder_tab, len(self.folder_tabs) - 1)
        self.load_folder(self.folder_tabs[self.active_folder_tab])

    def new_folder_tab(self) -> None:
        start = PV_WORKING_ROOT if PV_WORKING_ROOT.is_dir() else self.current_folder
        chosen = filedialog.askdirectory(initialdir=str(start))
        if not chosen:
            return
        folder = Path(chosen)
        if folder in self.folder_tabs:
            self.open_folder_tab(self.folder_tabs.index(folder))
            return
        self.folder_tabs.append(folder)
        self.active_folder_tab = len(self.folder_tabs) - 1
        self.load_folder(folder)

    def refresh_favorites(self) -> None:
        self.favorites_list.delete(0, END)
        valid: list[str] = []
        for item in self.settings.get("favorites", []):
            path = Path(str(item))
            if path.is_dir() and str(path) not in valid:
                valid.append(str(path))
                self.favorites_list.insert(END, path.name or str(path))
        self.settings["favorites"] = valid
        self.save_settings()

    def pin_current_folder(self) -> None:
        favorites = list(self.settings.get("favorites", []))
        current = str(self.current_folder)
        if current not in favorites:
            favorites.append(current)
            self.settings["favorites"] = favorites
            self.refresh_favorites()
        self.status.set(f"Pinned: {self.current_folder}")

    def unpin_selected_favorite(self) -> None:
        selection = self.favorites_list.curselection()
        if not selection:
            return
        favorites = list(self.settings.get("favorites", []))
        favorites.pop(selection[0])
        self.settings["favorites"] = favorites
        self.refresh_favorites()

    def select_favorite(self, _event: object) -> None:
        selection = self.favorites_list.curselection()
        favorites = list(self.settings.get("favorites", []))
        if selection and selection[0] < len(favorites):
            self.show_favorite_in_tree(Path(str(favorites[selection[0]])))

    def show_favorite_in_tree(self, folder: Path) -> None:
        """Use a pinned folder as the root of the lower-left navigation tree."""
        self.folder_tree.delete(*self.folder_tree.get_children())
        root = self.folder_tree.insert("", END, text=folder.name or str(folder), values=(str(folder),), open=True)
        self.add_tree_children(root, folder)
        self.folder_tree.focus(root)
        self.status.set(f"Browse pinned folder from the left tree: {folder}")

    def populate_tree(self) -> None:
        root = self.folder_tree.insert("", END, text=str(Path.home()), values=(str(Path.home()),), open=True)
        self.add_tree_children(root, Path.home())

    def add_tree_children(self, parent: str, folder: Path) -> None:
        try:
            folders = sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: p.name.lower())[:120]
        except (OSError, PermissionError):
            return
        for child in folders:
            item = self.folder_tree.insert(parent, END, text=child.name, values=(str(child),))
            # Defer probing nested directories until the user opens this row.
            self.folder_tree.insert(item, END, text="...")

    def expand_tree(self, _event: object) -> None:
        item = self.folder_tree.focus()
        values = self.folder_tree.item(item, "values")
        if not values:
            return
        children = self.folder_tree.get_children(item)
        if len(children) == 1 and self.folder_tree.item(children[0], "text") == "...":
            self.folder_tree.delete(children[0])
            self.add_tree_children(item, Path(values[0]))

    def select_tree_folder(self, _event: object) -> None:
        item = self.folder_tree.focus()
        values = self.folder_tree.item(item, "values")
        if values:
            self.load_folder(Path(values[0]))

    def tree_row_click(self, event: tk.Event) -> None:
        """A click anywhere on a folder row both opens it and shows its contents."""
        item = self.folder_tree.identify_row(event.y)
        if not item:
            return
        values = self.folder_tree.item(item, "values")
        if not values:
            return
        if not bool(self.folder_tree.item(item, "open")):
            self.folder_tree.focus(item)
            self.folder_tree.item(item, open=True)
            self.expand_tree(event)
        self.load_folder(Path(values[0]))

    def choose_folder(self) -> None:
        self.new_folder_tab()

    def go_up(self) -> None:
        self.load_folder(self.current_folder.parent)

    def navigate_up(self, _event: object = None) -> str:
        self.go_up()
        return "break"

    def navigate_back(self, _event: object = None) -> str:
        if self.folder_history_index > 0:
            self.folder_history_index -= 1
            self.load_folder(self.folder_history[self.folder_history_index], record_history=False)
        return "break"

    def navigate_forward(self, _event: object = None) -> str:
        if self.folder_history_index < len(self.folder_history) - 1:
            self.folder_history_index += 1
            self.load_folder(self.folder_history[self.folder_history_index], record_history=False)
        return "break"

    def load_folder(self, folder: Path, record_history: bool = True) -> None:
        folder = folder.expanduser()
        if not folder.is_dir():
            messagebox.showwarning("Folder not found", str(folder))
            return
        previous_folder = self.current_folder
        self.search_token += 1
        self.search_results_active = False
        self.pending_selection_name = previous_folder.name if previous_folder.parent == folder else None
        if record_history and (not self.folder_history or self.folder_history[self.folder_history_index] != folder):
            self.folder_history = self.folder_history[:self.folder_history_index + 1]
            self.folder_history.append(folder)
            self.folder_history_index = len(self.folder_history) - 1
        self.current_folder = folder
        self.path_var.set(str(folder))
        if self.folder_tabs:
            self.folder_tabs[self.active_folder_tab] = folder
        self.persist_folder_tabs()
        self.refresh_folder_tabs()
        self.refresh_file_list()
        self.after_idle(self.file_list.focus_set)
        self.preview_title.set(folder.name or str(folder))
        self.preview_text(self.folder_preview(folder))

    def folder_preview(self, folder: Path) -> str:
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (OSError, PermissionError) as exc:
            return f"Cannot preview folder: {exc}"
        folders = [p for p in entries if p.is_dir()]
        files = [p for p in entries if p.is_file()]
        lines = [f"Folder: {folder}", "", f"{len(folders)} folder(s) | {len(files)} file(s)", "", "Contents"]
        for item in entries[:45]:
            prefix = "[Folder]" if item.is_dir() else (item.suffix.upper() or "[File]")
            lines.append(f"{prefix:10} {item.name}")
        if len(entries) > 45:
            lines.append(f"... plus {len(entries) - 45} more item(s)")
        return "\n".join(lines)

    def file_icon_for(self, entry: Path) -> ImageTk.PhotoImage:
        if entry.is_dir():
            return self.file_icons["folder"]
        suffix = entry.suffix.lower()
        if suffix in {".doc", ".docx"}:
            return self.file_icons["doc"]
        if suffix in {".xls", ".xlsx", ".xlsm", ".csv"}:
            return self.file_icons["xls"]
        if suffix in {".ppt", ".pptx"}:
            return self.file_icons["ppt"]
        if suffix == ".pdf":
            return self.file_icons["pdf"]
        if suffix == ".md":
            return self.file_icons["md"]
        if suffix in MSG_EXTENSIONS:
            return self.file_icons["msg"]
        if suffix in IMAGE_EXTENSIONS:
            return self.file_icons["img"]
        if suffix in ZIP_EXTENSIONS:
            return self.file_icons["zip"]
        return self.file_icons["file"]

    def refresh_file_list(self) -> None:
        self.file_list.delete(*self.file_list.get_children())
        self.path_by_item.clear()
        query = self.search_var.get().strip().lower()
        try:
            entries = sorted(os.scandir(self.current_folder), key=lambda entry: (not entry.is_dir(), entry.name.lower()))
        except (OSError, PermissionError) as exc:
            self.status.set(f"Cannot read folder: {exc}")
            return
        for entry in entries:
            if query and query not in entry.name.lower():
                continue
            try:
                stat = entry.stat()
                modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
                path = Path(entry.path)
                kind = "Folder" if entry.is_dir() else (path.suffix[1:].upper() or "File")
                size = "-" if entry.is_dir() else human_size(stat.st_size)
                item = self.file_list.insert("", END, text=entry.name, image=self.file_icon_for(path), values=(kind, modified, size))
                self.path_by_item[item] = path
            except OSError:
                continue
        if self.pending_selection_name:
            for item, path in self.path_by_item.items():
                if path.name == self.pending_selection_name:
                    self.file_list.selection_set(item)
                    self.file_list.focus(item)
                    self.file_list.see(item)
                    break
        self.pending_selection_name = None
        self.fit_file_columns()
        self.folder_stamp = self.directory_stamp(self.current_folder)
        self.status.set(f"{len(self.path_by_item)} item(s) | Local read-only preview")

    @staticmethod
    def directory_stamp(folder: Path) -> int | None:
        try:
            return folder.stat().st_mtime_ns
        except OSError:
            return None

    def watch_current_folder(self) -> None:
        try:
            current_stamp = self.directory_stamp(self.current_folder)
            if not self.search_results_active and self.folder_stamp is not None and current_stamp != self.folder_stamp:
                selected_names = {path.name for path in self.selected_paths()}
                self.refresh_file_list()
                for item, path in self.path_by_item.items():
                    if path.name in selected_names:
                        self.file_list.selection_add(item)
                self.status.set(f"Folder updated | {len(self.path_by_item)} item(s)")
        finally:
            self.after(1500, self.watch_current_folder)

    def filter_current_folder(self, event: tk.Event) -> None:
        if event.keysym != "Return":
            self.search_results_active = False
            self.refresh_file_list()

    def start_global_search(self, _event: object = None) -> str:
        query = self.search_var.get().strip()
        if not query:
            return "break"
        self.search_token += 1
        token = self.search_token
        root = self.current_folder
        self.search_results_active = True
        self.status.set(f"Searching names and document text under {root.name}...")
        threading.Thread(target=self.search_files, args=(root, query, token), daemon=True).start()
        return "break"

    def cached_search_text(self, path: Path, modified: int, size: int) -> str:
        key = str(path)
        cached = self.search_content_cache.get(key)
        if cached and cached[:2] == (modified, size):
            return cached[2]
        try:
            text = search_document_text(path)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, fitz.FileDataError):
            text = ""
        if len(self.search_content_cache) >= MAX_SEARCH_CACHE_ENTRIES:
            self.search_content_cache.pop(next(iter(self.search_content_cache)))
        self.search_content_cache[key] = (modified, size, text)
        return text

    def search_files(self, root: Path, query: str, token: int) -> None:
        terms = [term.lower() for term in re.split(r"\s+", query) if term]
        results: list[tuple[Path, str, int, int, int]] = []
        scanned = 0
        for folder, directories, filenames in os.walk(root, onerror=lambda _error: None):
            directories[:] = [name for name in directories if name not in {"preview_cache", ".git", "node_modules", "__pycache__"}]
            for directory in directories:
                path = Path(folder) / directory
                relative_name = str(path.relative_to(root)).lower()
                if all(term in relative_name for term in terms):
                    try:
                        stat = path.stat()
                        results.append((path, "Folder", stat.st_mtime_ns, 0, 10 - min(len(path.parts), 20)))
                    except OSError:
                        continue
            for filename in filenames:
                if token != self.search_token or scanned >= MAX_SEARCH_FILES:
                    break
                path = Path(folder) / filename
                try:
                    stat = path.stat()
                except OSError:
                    continue
                scanned += 1
                relative_name = str(path.relative_to(root)).lower()
                name_match = all(term in relative_name for term in terms)
                content_match = False
                if not name_match and path.suffix.lower() in SEARCHABLE_CONTENT_EXTENSIONS and stat.st_size <= MAX_SEARCH_FILE_BYTES:
                    content = self.cached_search_text(path, stat.st_mtime_ns, stat.st_size)
                    content_match = all(term in content for term in terms)
                if name_match or content_match:
                    score = (100 if name_match else 0) + (20 if content_match else 0) - min(len(path.parts), 20)
                    results.append((path, "Name + content" if name_match and content_match else ("Name" if name_match else "Document text"), stat.st_mtime_ns, stat.st_size, score))
            if token != self.search_token or scanned >= MAX_SEARCH_FILES:
                break
        results.sort(key=lambda item: (item[1] == "Folder", -item[4], item[0].name.lower()))
        self.search_jobs.put((token, [(path, match, modified, size) for path, match, modified, size, _score in results[:MAX_SEARCH_RESULTS]], scanned, root))

    def poll_search(self) -> None:
        try:
            while True:
                token, results, scanned, root = self.search_jobs.get_nowait()
                if token == self.search_token:
                    self.show_search_results(results)
                    self.status.set(f"Search: {len(results)} result(s) from {scanned} files under {root.name}")
        except queue.Empty:
            pass
        self.after(140, self.poll_search)

    def show_search_results(self, results: list[tuple[Path, str, int, int]]) -> None:
        self.file_list.delete(*self.file_list.get_children())
        self.path_by_item.clear()
        for path, match, modified, size in results:
            try:
                label = str(path.relative_to(self.current_folder))
            except ValueError:
                label = str(path)
            item = self.file_list.insert("", END, text=label, image=self.file_icon_for(path), values=(match, time.strftime("%Y-%m-%d %H:%M", time.localtime(modified / 1_000_000_000)), human_size(size)))
            self.path_by_item[item] = path
        self.fit_file_columns()

    def fit_file_columns(self) -> None:
        self.file_column_fit_job = None
        measure = tkfont.Font(family="Microsoft YaHei", size=11).measure
        items = self.file_list.get_children()[:600]

        def width(values: list[str], minimum: int, maximum: int, padding: int = 20) -> int:
            return min(maximum, max(minimum, max((measure(value) + padding for value in values), default=minimum)))

        names = [self.file_list.item(item, "text") for item in items]
        types = [str(self.file_list.item(item, "values")[0]) for item in items]
        modified = [str(self.file_list.item(item, "values")[1]) for item in items]
        sizes = [str(self.file_list.item(item, "values")[2]) for item in items]
        desired_type = width(types + ["Type"], 80, 120)
        desired_modified = width(modified + ["Modified"], 165, 210)
        desired_size = width(sizes + ["Size"], 80, 120)
        available = self.file_list.winfo_width() - 22
        if available < 580:
            type_width = min(desired_type, 90)
            modified_width = min(desired_modified, 165)
            size_width = min(desired_size, 95)
        else:
            type_width = desired_type
            modified_width = desired_modified
            size_width = desired_size
        # Name absorbs remaining width, keeping the table edge aligned with the pane splitter.
        name_width = max(120, available - type_width - modified_width - size_width)
        if name_width + type_width + modified_width + size_width > available:
            modified_width = max(135, available - name_width - type_width - size_width)
        self.file_list.column("#0", width=name_width, minwidth=120, stretch=False)
        self.file_list.column("type", width=type_width, minwidth=60, stretch=False)
        self.file_list.column("modified", width=modified_width, minwidth=110, stretch=False)
        self.file_list.column("size", width=size_width, minwidth=60, stretch=False)

    def schedule_file_column_fit(self, _event: tk.Event = None) -> None:
        if self.file_column_fit_job is not None:
            self.after_cancel(self.file_column_fit_job)
        self.file_column_fit_job = self.after(80, self.fit_file_columns)

    def selected_path(self) -> Path | None:
        selection = self.file_list.selection()
        return self.path_by_item.get(selection[0]) if selection else None

    def selected_paths(self) -> list[Path]:
        return [self.path_by_item[item] for item in self.file_list.selection() if item in self.path_by_item]

    def copy_files_to_clipboard(self, paths: list[Path]) -> None:
        """Expose the selected files on the Windows clipboard (CF_HDROP), like Explorer.

        This lets the selection be pasted into WeChat, mail clients, Explorer and
        other apps exactly as if the files had been copied there.
        """
        if os.name != "nt" or not paths:
            return
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            # 64-bit handles must be declared or ctypes truncates them to 32-bit.
            user32.OpenClipboard.argtypes = [wintypes.HWND]
            user32.OpenClipboard.restype = ctypes.c_int
            user32.EmptyClipboard.restype = ctypes.c_int
            user32.SetClipboardData.argtypes = [ctypes.c_uint, wintypes.HANDLE]
            user32.SetClipboardData.restype = wintypes.HANDLE
            user32.CloseClipboard.restype = ctypes.c_int
            kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
            kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
            kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
            kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
            CF_HDROP = 15
            files = "\0".join(str(path) for path in paths) + "\0\0"
            payload = files.encode("utf-16-le")
            total = 20 + len(payload)
            buffer = ctypes.create_string_buffer(total)
            buffer[0:4] = struct.pack("<I", 20)           # DROPFILES.pFiles
            buffer[16:20] = struct.pack("<I", 1)           # DROPFILES.fWide = 1
            buffer[20:20 + len(payload)] = payload
            if not user32.OpenClipboard(None):
                return
            try:
                user32.EmptyClipboard()
                handle = kernel32.GlobalAlloc(0x0042, total)  # GMEM_MOVEABLE | GMEM_ZEROINIT
                if not handle:
                    return
                locked = kernel32.GlobalLock(handle)
                if not locked:
                    kernel32.GlobalFree(handle)
                    return
                ctypes.memmove(ctypes.c_void_p(locked), buffer, total)
                kernel32.GlobalUnlock(handle)
                if not user32.SetClipboardData(CF_HDROP, handle):
                    kernel32.GlobalFree(handle)
            finally:
                user32.CloseClipboard()
        except Exception:
            pass

    def copy_selected_files(self) -> str:
        paths = self.selected_paths()
        if paths:
            self.file_clipboard = paths
            self.file_clipboard_mode = "copy"
            self.copy_files_to_clipboard(paths)
            self.status.set(f"Copied {len(paths)} item(s) to the clipboard. Ctrl+V pastes here or in any other app.")
        return "break"

    def cut_selected_files(self) -> str:
        paths = self.selected_paths()
        if paths:
            self.file_clipboard = paths
            self.file_clipboard_mode = "cut"
            self.status.set(f"Cut {len(paths)} item(s). Choose a folder and press Ctrl+V.")
        return "break"

    def paste_files(self) -> str:
        if not self.file_clipboard or self.file_clipboard_mode is None:
            self.status.set("Nothing to paste.")
            return "break"
        completed = skipped = 0
        errors: list[str] = []
        target_folder = self.current_folder
        for source in list(self.file_clipboard):
            if not source.exists():
                skipped += 1
                continue
            target = target_folder / source.name
            if self.file_clipboard_mode == "copy":
                target = self.copy_destination(target_folder, source)
            elif target == source or target.exists():
                skipped += 1
                continue
            try:
                if self.file_clipboard_mode == "copy":
                    if source.is_dir():
                        shutil.copytree(source, target)
                    else:
                        shutil.copy2(source, target)
                else:
                    if source.is_dir() and target_folder.resolve().is_relative_to(source.resolve()):
                        skipped += 1
                        continue
                    shutil.move(str(source), str(target))
                completed += 1
            except OSError as exc:
                skipped += 1
                errors.append(f"{source.name}: {exc}")
        if self.file_clipboard_mode == "cut" and completed:
            self.file_clipboard = [path for path in self.file_clipboard if path.exists()]
            if not self.file_clipboard:
                self.file_clipboard_mode = None
        self.refresh_file_list()
        suffix = f"; skipped {skipped} existing or unavailable item(s)" if skipped else ""
        self.status.set(f"Pasted {completed} item(s){suffix}.")
        if errors:
            messagebox.showwarning("Some items were not moved", "\n".join(errors[:3]))
        return "break"

    def copy_destination(self, target_folder: Path, source: Path) -> Path:
        candidate = target_folder / source.name
        if not candidate.exists() and candidate != source:
            return candidate
        suffix = source.suffix if source.is_file() else ""
        stem = source.name[:-len(suffix)] if suffix else source.name
        candidate = target_folder / f"{stem} - 副本{suffix}"
        number = 2
        while candidate.exists():
            candidate = target_folder / f"{stem} - 副本 ({number}){suffix}"
            number += 1
        return candidate

    def rename_selected_file(self, _event: object = None) -> str:
        path = self.selected_path()
        if path is None:
            return "break"
        name = simpledialog.askstring("Rename", "New name:", initialvalue=path.name, parent=self)
        if name is None or name == path.name:
            return "break"
        name = name.strip()
        if not name or any(character in name for character in '\\/:*?"<>|'):
            messagebox.showwarning("Invalid name", "Use a valid Windows file name.")
            return "break"
        target = path.with_name(name)
        if target.exists():
            messagebox.showwarning("Name already exists", f"{name} already exists in this folder.")
            return "break"
        try:
            path.rename(target)
            self.refresh_file_list()
            for item, entry in self.path_by_item.items():
                if entry == target:
                    self.file_list.selection_set(item)
                    self.file_list.focus(item)
                    self.file_list.see(item)
                    break
            self.status.set(f"Renamed to {name}.")
        except OSError as exc:
            messagebox.showerror("Rename failed", str(exc))
        return "break"

    def move_file_selection(self, delta: int) -> str:
        """Move the file-list selection by one row and keep previewing.

        This is safe to call no matter which widget currently has focus, so
        Up/Down keep switching files even while the preview pane is active.
        """
        items = self.file_list.get_children()
        if not items:
            return "break"
        selection = self.file_list.selection()
        current = selection[0] if selection else None
        if current in items:
            index = items.index(current)
        else:
            index = 0 if delta > 0 else len(items) - 1
        index = max(0, min(len(items) - 1, index + delta))
        item = items[index]
        self.file_list.selection_set(item)
        self.file_list.focus(item)
        self.file_list.see(item)
        return "break"

    def file_list_arrow(self, event: object) -> str | None:
        if self.file_list.selection():
            return None
        items = self.file_list.get_children()
        if not items:
            return "break"
        item = items[0] if getattr(event, "keysym", "") == "Down" else items[-1]
        self.file_list.selection_set(item)
        self.file_list.focus(item)
        self.file_list.see(item)
        return "break"

    def route_file_list_arrow(self, event: tk.Event) -> str | None:
        focused = self.focus_get()
        if focused is not None and focused.winfo_class() in {"Entry", "Text", "TEntry", "TCombobox", "Listbox"}:
            return None
        if focused in (self.file_list, self.folder_tree):
            return None  # The Treeview class binding already moves the selection.
        # Focus sits in a preview widget (Markdown/Excel/native PDF): still move
        # the file selection so arrows keep switching files while previewing.
        delta = 1 if getattr(event, "keysym", "") == "Down" else -1
        self.file_list.focus_set()
        return self.move_file_selection(delta)

    def route_copy(self, _event: tk.Event) -> str | None:
        focused = self.focus_get()
        if focused is not None and focused.winfo_class() in {"Entry", "Text", "TEntry", "TCombobox"}:
            return None
        return self.copy_selected_files()

    def route_paste_files(self, _event: tk.Event) -> str | None:
        focused = self.focus_get()
        if focused is not None and focused.winfo_class() in {"Entry", "Text", "TEntry", "TCombobox"}:
            return None
        return self.paste_files()

    def guard_preview_focus(self) -> None:
        """Keep the keyboard out of the native PDF child window.

        The WebView2 host is a separate native window, so Tk never sees arrow
        keys while it has focus.  When it steals focus, pull it back to the
        file list - but only while our own app (or the native preview host)
        is the foreground window.
        """
        process = self.native_preview_process
        if process is not None and process.poll() is None:
            if self.focus_get() is None and self.focus_displayof() is None:
                foreground = ctypes.windll.user32.GetForegroundWindow()
                owner_pid = wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(foreground, ctypes.byref(owner_pid))
                if owner_pid.value in (os.getpid(), process.pid):
                    self.file_list.focus_set()
        self.after(300, self.guard_preview_focus)

    def enter_selected(self, _event: object = None) -> str:
        self.open_selected()
        return "break"

    def preview_selected(self, _event: object = None) -> None:
        path = self.selected_path()
        if not path:
            return
        self.inline_markdown_path = None
        self.save_markdown_button.pack_forget()
        self.zip_preview_path = None
        if path.is_dir():
            self.stop_native_pdf_preview()
            self.previewed_path = None
            self.preview_pdf_path = None
            self.preview_title.set(path.name or str(path))
            self.preview_text(self.folder_preview(path))
            return
        if path.suffix.lower() != ".pdf":
            self.stop_native_pdf_preview()
        self.preview_token += 1
        token = self.preview_token
        self.previewed_path = path
        self.preview_pdf_path = path if path.suffix.lower() == ".pdf" else (
            cached_path(path, ".pdf") if path.suffix.lower() in {".doc", ".docx", ".ppt", ".pptx"} else None
        )
        self.preview_title.set(path.name)
        self.preview_text("Loading local preview...")
        # Keep keyboard focus on the file list so Up/Down keep switching files
        # even while the preview pane is rendering.
        self.after_idle(self.file_list.focus_set)
        if path.suffix.lower() == ".pdf" and NATIVE_PDF_PREVIEW.exists():
            self.after(20, lambda selected=path, selected_token=token: self.start_native_pdf_preview(selected, selected_token))
            return
        with self.preview_lock:
            self.preview_request = (path, token)
            if self.preview_worker_running:
                return
            self.preview_worker_running = True
        threading.Thread(target=self.preview_worker, daemon=True).start()

    def stop_native_pdf_preview(self) -> None:
        self.native_preview_host.pack_forget()
        process = self.native_preview_process
        self.native_preview_process = None
        self.native_preview_path = None
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=0.4)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass

    def start_native_pdf_preview(self, path: Path, token: int) -> None:
        if token != self.preview_token or not path.is_file() or not NATIVE_PDF_PREVIEW.exists():
            return
        if self.native_preview_process and self.native_preview_process.poll() is None:
            if self.send_native_pdf_path(path):
                self.native_preview_path = path
                self.status.set("Native PDF preview | instant switch | selectable text, continuous scroll, Ctrl+wheel zoom")
                return
        self.stop_native_pdf_preview()
        self.preview.pack_forget()
        self.preview_canvas.pack_forget()
        self.excel_stack.pack_forget()
        self.markdown_view.pack_forget()
        self.preview_scroll.pack_forget()
        self.native_preview_host.pack(fill=BOTH, expand=True)
        self.update_idletasks()
        options: dict[str, object] = {"cwd": str(NATIVE_PDF_PREVIEW.parent)}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            self.native_preview_process = subprocess.Popen(
                [str(NATIVE_PDF_PREVIEW), str(self.native_preview_host.winfo_id()), str(path)],
                **options,
            )
            self.native_preview_path = path
            self.status.set("Native PDF preview | selectable text, continuous scroll, Ctrl+wheel zoom")
        except OSError as exc:
            self.native_preview_host.pack_forget()
            self.preview_text(f"Native PDF preview unavailable: {exc}")

    def send_native_pdf_path(self, path: Path) -> bool:
        if os.name != "nt":
            return False
        pipe_name = rf"\\.\pipe\FileWorkbenchPdfPreview-{self.native_preview_host.winfo_id()}"
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        kernel32.WaitNamedPipeW.restype = wintypes.BOOL
        kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        if not kernel32.WaitNamedPipeW(pipe_name, 250):
            return False
        handle = kernel32.CreateFileW(pipe_name, 0x40000000, 0, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            return False
        payload = str(path).encode("utf-8")
        written = wintypes.DWORD()
        try:
            return bool(kernel32.WriteFile(handle, payload, len(payload), ctypes.byref(written), None))
        finally:
            kernel32.CloseHandle(handle)

    def preview_worker(self) -> None:
        while True:
            with self.preview_lock:
                request = self.preview_request
                self.preview_request = None
                if request is None:
                    self.preview_worker_running = False
                    return
            try:
                self.build_preview(*request)
            except Exception as exc:
                token = request[1]
                self.preview_jobs.put((token, "text", f"Preview unavailable: {exc}"))

    def request_office_full_preview(self, _event: object = None) -> str:
        """ALT: upgrade the current Word/PowerPoint quick preview to a full PDF."""
        path = self.selected_path()
        if not path or path.suffix.lower() not in {".doc", ".docx", ".ppt", ".pptx"}:
            return "break"
        # Debounce so Alt+arrow navigation does not export files the user is
        # only passing through; the selection is re-checked when it fires.
        if self.office_preview_job is not None:
            try:
                self.after_cancel(self.office_preview_job)
            except tk.TclError:
                pass
        self.office_preview_job = self.after(250, lambda selected=path: self.run_office_full_preview(selected))
        return "break"

    def run_office_full_preview(self, path: Path) -> None:
        self.office_preview_job = None
        if path != self.selected_path() or not path.is_file():
            return
        rendered_pdf = cached_path(path, ".pdf")
        if not rendered_pdf.exists():
            self.show_export_progress()
            threading.Thread(target=self.export_office_pdf, args=(path, self.preview_token), daemon=True).start()
            return
        self.preview_jobs.put((self.preview_token, "native_pdf", str(rendered_pdf)))

    def export_office_pdf(self, path: Path, token: int) -> None:
        try:
            ensure_office_pdf(path)
        except Exception as exc:
            self.preview_jobs.put((token, "export_done", None))
            self.preview_jobs.put((token, "text", f"Office 导出失败：{exc}\n\n可改用 Open 直接打开原文件。"))
            return
        self.preview_jobs.put((token, "export_done", None))
        if self.preview_is_current(token):
            rendered_pdf = cached_path(path, ".pdf")
            if rendered_pdf.exists():
                self.preview_jobs.put((token, "native_pdf", str(rendered_pdf)))

    def show_export_progress(self) -> None:
        self.export_label.pack(side=LEFT, padx=(10, 4))
        self.export_progress.pack(side=LEFT)
        self.export_progress.start(12)

    def hide_export_progress(self) -> None:
        self.export_progress.stop()
        self.export_progress.pack_forget()
        self.export_label.pack_forget()

    def preview_is_current(self, token: int) -> bool:
        with self.preview_lock:
            return self.preview_request is None and token == self.preview_token

    def build_preview(self, path: Path | tuple[Path, int], token: int, display_name: str | None = None) -> None:
        try:
            if isinstance(path, tuple):
                zip_path, entry_index = path
                self.build_zip_entry_preview(zip_path, entry_index, token)
                return
            suffix = path.suffix.lower()
            details = f"{display_name or path.name}\n{human_size(path.stat().st_size)}\n{path.parent}\n\n"
            if suffix == ".md":
                self.preview_jobs.put((token, "markdown", path.read_text(encoding="utf-8", errors="replace")))
                return
            if suffix in MSG_EXTENSIONS:
                self.preview_jobs.put((token, "text", details + MsgReader(path).preview_text()))
                return
            if suffix in ZIP_EXTENSIONS:
                self.preview_jobs.put((token, "zip", (path, self.zip_archive_entries(path))))
                return
            if suffix in IMAGE_EXTENSIONS:
                try:
                    self.preview_jobs.put((token, "image", Image.open(path).copy()))
                except (OSError, ValueError):
                    self.preview_jobs.put((token, "text", details + "Image preview unavailable."))
                return
            if suffix == ".pdf":
                self.preview_jobs.put((token, "image", render_pdf_first_page(path)))
                if not self.preview_is_current(token):
                    return
                self.preview_jobs.put((token, "image", render_pdf_pages(path)))
                return
            if suffix in {".xlsx", ".xlsm"}:
                # Instant text overview first; upgrade to the editable grid after.
                self.preview_jobs.put((token, "text", details + office_text(path)))
                if not self.preview_is_current(token):
                    return
                try:
                    grid = excel_workbook_grid(path)
                except Exception:
                    grid = None
                if grid is not None:
                    self.preview_jobs.put((token, "excel", grid))
                return
            if suffix in {".doc", ".docx", ".ppt", ".pptx"}:
                # Two-stage preview: show the embedded thumbnail or extracted
                # text instantly (plus cached first pages when a PDF already
                # exists).  The heavy Office export only runs when the user
                # presses ALT, so arrow navigation stays instant.
                thumbnail = embedded_office_thumbnail(path)
                if thumbnail is not None:
                    self.preview_jobs.put((token, "image", thumbnail))
                else:
                    quick = office_text(path)
                    if suffix in {".doc", ".ppt"}:
                        quick = "(旧版二进制格式，无法免 Office 提取正文)\n\n按 ALT 键可导出完整 PDF 预览。"
                    self.preview_jobs.put((token, "text", details + quick))
                if not self.preview_is_current(token):
                    return
                rendered_pdf = cached_path(path, ".pdf")
                if rendered_pdf.exists():
                    try:
                        quick_pages = render_pdf_pages(rendered_pdf, scale=OFFICE_QUICK_SCALE, page_limit=OFFICE_QUICK_PAGES)
                    except Exception:
                        quick_pages = None
                    if quick_pages is not None and self.preview_is_current(token):
                        self.preview_jobs.put((token, "image", quick_pages))
                return
            content = text_preview(path)
        except Exception as exc:  # Keep the UI responsive if a malformed document appears.
            content = f"Preview unavailable: {exc}"
        self.preview_jobs.put((token, "text", details + content))

    def zip_archive_entries(self, path: Path) -> str:
        path = Path(path)
        """Render a clickable HTML listing of the archive contents."""
        try:
            with zipfile.ZipFile(path) as archive:
                infos = sorted(archive.infolist(), key=lambda info: (info.is_dir(), info.filename.lower()))
        except (OSError, zipfile.BadZipFile) as exc:
            return f"<p>Cannot read zip archive: {escape(str(exc))}</p>"
        rows: list[str] = []
        for index, info in enumerate(infos[:600]):
            name = info.filename
            is_dir = info.is_dir()
            display = escape(name.rstrip("/") if is_dir else name)
            icon = "&#128193;" if is_dir else "&#128196;"
            size = "" if is_dir else escape(human_size(info.file_size))
            rows.append(
                "<a class='row' href='zip:%d'><span class='icon'>%s</span>"
                "<span class='name'>%s</span><span class='size'>%s</span></a>"
                % (index, icon, display, size)
            )
        if len(infos) > 600:
            rows.append("<p class='hint'>... plus %d more item(s)</p>" % (len(infos) - 600))
        css = (
            "<style>"
            "body{font-family:'Microsoft YaHei','Segoe UI',sans-serif;font-size:15px;background:#ffffff;color:#171717;margin:0;padding:14px 18px 40px;}"
            "p.hint{color:#64748b;font-size:13px;margin:4px 0 12px;}"
            "a.row{display:flex;align-items:center;gap:10px;padding:7px 8px;border-radius:6px;text-decoration:none;color:#171717;}"
            "a.row:hover{background:#eff6ff;}"
            "span.icon{width:22px;text-align:center;}"
            "span.name{flex:1;word-break:break-all;}"
            "span.size{color:#64748b;font-size:13px;white-space:nowrap;}"
            "</style>"
        )
        header = "<p class='hint'>Zip archive: %s &middot; %d item(s) &middot; click a file to preview it</p>" % (
            escape(path.name), len(infos))
        return "<!doctype html><html><head><meta charset='utf-8'>" + css + "</head><body>" + header + "".join(rows) + "</body></html>"

    def build_zip_entry_preview(self, zip_path: Path, entry_index: int, token: int) -> None:
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            if not (0 <= entry_index < len(infos)):
                return
            info = infos[entry_index]
            if info.is_dir():
                self.preview_jobs.put((token, "zip", (zip_path, self.zip_archive_entries(zip_path))))
                return
            name = info.filename
            data = archive.read(info)
        suffix = Path(name).suffix.lower()
        tmp = PREVIEW_CACHE / ("zip-" + hashlib.sha1(("%s|%s" % (zip_path, name)).encode("utf-8")).hexdigest()[:20] + suffix)
        try:
            tmp.write_bytes(data)
        except OSError:
            tmp = None
        if tmp is not None:
            self.build_preview(tmp, token, display_name=name)
            return
        content = name + "\n" + human_size(len(data)) + "\n\n"
        if suffix in PREVIEWABLE_TEXT or suffix in MSG_EXTENSIONS:
            self.preview_jobs.put((token, "text", content + "Preview unavailable for this entry."))
        else:
            self.preview_jobs.put((token, "text", content + "Preview unavailable for entries of this type inside a zip."))

    def preview_zip_listing(self, zip_path: Path, html: str) -> None:
        self.zip_preview_path = zip_path
        self.preview_title.set("%s | Zip contents" % zip_path.name)
        self.preview.pack_forget()
        self.native_preview_host.pack_forget()
        self.preview_canvas.pack_forget()
        self.excel_stack.pack_forget()
        self.preview_scroll.pack_forget()
        self.markdown_view.configure(zoom=self.markdown_zoom)
        self.markdown_view.load_html(html)
        self.markdown_view.pack(fill=BOTH, expand=True)

    def handle_preview_link(self, url: str) -> None:
        if self.zip_preview_path is None or not url.startswith("zip:"):
            return
        try:
            index = int(url[4:])
        except ValueError:
            return
        self.preview_token += 1
        token = self.preview_token
        self.previewed_path = self.zip_preview_path
        self.preview_pdf_path = None
        self.preview_title.set(self.zip_preview_path.name)
        self.preview_text("Loading archive entry...")
        with self.preview_lock:
            self.preview_request = ((self.zip_preview_path, index), token)
            if not self.preview_worker_running:
                self.preview_worker_running = True
                threading.Thread(target=self.preview_worker, daemon=True).start()

    def poll_preview(self) -> None:
        try:
            while True:
                token, kind, content = self.preview_jobs.get_nowait()
                if kind == "export_done":
                    self.hide_export_progress()
                    continue
                if token == self.preview_token:
                    if kind == "image":
                        if self.preview_pdf_path is None or self.preview_zoom == 1.0:
                            self.preview_picture(content)
                    elif kind == "pdf_zoom":
                        generation, picture = content
                        if generation == self.pdf_zoom_generation:
                            self.preview_picture(picture, preserve_zoom=True)
                    elif kind == "excel":
                        self.preview_excel(content)
                    elif kind == "markdown":
                        self.preview_markdown(str(content))
                    elif kind == "native_pdf":
                        self.start_native_pdf_preview(Path(str(content)), token)
                    elif kind == "zip":
                        zip_path, html = content
                        self.preview_zip_listing(Path(zip_path), str(html))
                    else:
                        self.preview_text(str(content))
        except queue.Empty:
            pass
        self.after(120, self.poll_preview)

    def preview_text(self, content: str) -> None:
        self.native_preview_host.pack_forget()
        self.preview_canvas.pack_forget()
        self.excel_stack.pack_forget()
        self.markdown_view.pack_forget()
        self.preview.pack(side=LEFT, fill=BOTH, expand=True)
        self.preview_scroll.pack(side=RIGHT, fill=Y)
        self.preview_scroll.configure(command=self.preview.yview)
        self.preview.configure(state="normal")
        self.preview.delete("1.0", END)
        self.preview.insert("1.0", content[:MAX_TEXT])
        self.preview.configure(state="disabled")

    def preview_picture(self, picture: object, preserve_zoom: bool = False) -> None:
        if not isinstance(picture, Image.Image):
            self.preview_text("Preview image unavailable.")
            return
        self.preview.pack_forget()
        self.native_preview_host.pack_forget()
        self.excel_stack.pack_forget()
        self.markdown_view.pack_forget()
        self.preview_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.preview_scroll.pack(side=RIGHT, fill=Y)
        self.preview_scroll.configure(command=self.preview_canvas.yview)
        # Keep only the worker's image; copying a multi-page preview can double memory use.
        self.preview_source = picture
        if not preserve_zoom:
            self.preview_zoom = 1.0
        self.draw_preview_picture(reset_scroll=not preserve_zoom)

    def draw_preview_picture(self, reset_scroll: bool = False) -> None:
        if self.preview_source is None:
            return
        picture = self.preview_source
        current_top = self.preview_canvas.yview()[0] if self.preview_canvas.winfo_ismapped() else 0.0
        available_width = max(self.preview_host.winfo_width() - 26, 420)
        scale = (available_width / picture.width) * self.preview_zoom
        rendered = picture.resize((max(1, int(picture.width * scale)), max(1, int(picture.height * scale))), Image.Resampling.LANCZOS)
        self.preview_image = ImageTk.PhotoImage(rendered)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(10, 10, anchor="nw", image=self.preview_image)
        self.preview_canvas.configure(scrollregion=(0, 0, rendered.width + 20, rendered.height + 20))
        self.preview_canvas.yview_moveto(0 if reset_scroll else current_top)

    def zoom_preview(self, event: tk.Event) -> str:
        if self.preview_source is None or not self.preview_canvas.winfo_ismapped():
            return "break"
        self.preview_zoom *= 1.16 if event.delta > 0 else 1 / 1.16
        self.preview_zoom = min(3.5, max(0.35, self.preview_zoom))
        if self.zoom_job is not None:
            self.after_cancel(self.zoom_job)
        self.zoom_job = self.after(110, self.apply_zoom)
        return "break"

    def apply_zoom(self) -> None:
        self.zoom_job = None
        self.draw_preview_picture()
        if self.preview_pdf_path is not None:
            self.pdf_zoom_generation += 1
            generation = self.pdf_zoom_generation
            scale = min(5.0, max(2.0, 1.8 * self.preview_zoom))
            page_limit = MAX_PREVIEW_PAGES if self.preview_zoom <= 1.25 else (8 if self.preview_zoom <= 2.2 else 4)
            threading.Thread(
                target=self.build_zoomed_pdf_preview,
                args=(self.preview_pdf_path, self.preview_token, generation, scale, page_limit),
                daemon=True,
            ).start()

    def build_zoomed_pdf_preview(self, path: Path, token: int, generation: int, scale: float, page_limit: int) -> None:
        try:
            picture = render_pdf_pages(path, scale=scale, page_limit=page_limit)
        except Exception:
            return
        self.preview_jobs.put((token, "pdf_zoom", (generation, picture)))

    def scroll_preview(self, event: tk.Event) -> str:
        if event.state & 0x0004:  # Ctrl is reserved for zoom.
            return "break"
        target = self.preview_canvas if self.preview_canvas.winfo_ismapped() else event.widget
        steps = max(1, abs(event.delta) // 40)
        target.yview_scroll(-steps if event.delta > 0 else steps, "units")
        return "break"

    def refit_preview(self, _event: tk.Event) -> None:
        if self.preview_source is not None and self.preview_canvas.winfo_ismapped() and self.preview_zoom == 1.0:
            self.draw_preview_picture()

    def increase_reading_size(self, _event: object = None) -> str:
        self.set_reading_size(1)
        return "break"

    def decrease_reading_size(self, _event: object = None) -> str:
        self.set_reading_size(-1)
        return "break"

    def set_reading_size(self, direction: int) -> None:
        self.preview_font_size = min(24, max(11, self.preview_font_size + direction))
        self.preview.configure(font=("Microsoft YaHei", self.preview_font_size))
        self.markdown_zoom = min(1.8, max(0.75, self.markdown_zoom + direction * 0.1))
        try:
            self.markdown_view.configure(zoom=self.markdown_zoom)
        except tk.TclError:
            pass
        self.status.set(f"Reading size: {self.preview_font_size}")

    def preview_excel(self, data: object) -> None:
        if not isinstance(data, list) or not data:
            self.preview_text("Excel preview unavailable.")
            return
        self.preview_title.set(f"{self.selected_path().name if self.selected_path() else 'Workbook'} | Sheets")
        self.preview.pack_forget()
        self.native_preview_host.pack_forget()
        self.preview_canvas.pack_forget()
        self.markdown_view.pack_forget()
        self.preview_scroll.pack_forget()
        for child in self.excel_content.winfo_children() + self.excel_tabs.winfo_children():
            child.destroy()
        sheets = []
        for sheet_name, headers, rows in data:
            frame = ttk.Frame(self.excel_content)
            values = [row_values for row_values, _fill, _foreground, _bold in rows]
            letters = [get_column_letter(index + 1) for index in range(len(headers))]
            widths = [min(300, max(80, max([len(letter)] + [len(row[index]) for row in values]) * 9 + 22)) for index, letter in enumerate(letters)]
            table = Sheet(
                frame,
                data=values,
                headers=letters,
                show_row_index=True,
                default_row_index="numbers",
                font=("Microsoft YaHei", 11, "normal"),
                header_font=("Microsoft YaHei", 11, "bold"),
                index_font=("Microsoft YaHei", 10, "normal"),
                default_column_width=120,
                table_wrap="",
                theme="light blue",
            )
            table.pack(fill=BOTH, expand=True)
            table.set_column_widths(widths)
            table.enable_bindings("single_select", "drag_select", "row_select", "column_select", "arrowkeys", "copy")
            table._fileworkbench_sheet = sheet_name
            for row_number, (_values, fill, foreground, _bold) in enumerate(rows):
                if fill != "#ffffff" or foreground != "#000000":
                    table.highlight_rows(row_number, bg=fill, fg=foreground, redraw=False)
            table.bind("<Double-Button-1>", lambda event, grid=table: self.edit_excel_cell(event, grid))
            table.bind("<<SheetSelect>>", lambda _event, grid=table: self.show_excel_selection(grid), add="+")
            sheets.append((sheet_name, frame))
        for index, (sheet_name, frame) in enumerate(sheets):
            ttk.Button(self.excel_tabs, text=sheet_name[:20], command=lambda selected=frame: self.select_excel_sheet(selected)).pack(side=LEFT, padx=(0, 5))
            if index == 0:
                frame.pack(fill=BOTH, expand=True)
        self.excel_stack.pack(fill=BOTH, expand=True)

    def edit_excel_cell(self, _event: tk.Event, table: Sheet) -> str:
        selected = table.get_currently_selected()
        path = self.previewed_path
        if not selected or not isinstance(selected.row, int) or not isinstance(selected.column, int) or not path or path.suffix.lower() not in {".xlsx", ".xlsm"}:
            return "break"
        row_number = selected.row + 1
        column_number = selected.column + 1
        previous = str(table.get_cell_data(selected.row, selected.column))
        coordinate = f"{get_column_letter(column_number)}{row_number}"
        value = simpledialog.askstring("Edit Excel cell", f"{table._fileworkbench_sheet}!{coordinate}", initialvalue=previous, parent=self)
        if value is None:
            return "break"
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.FileWorkbench-backup-{timestamp}{path.suffix}")
        try:
            shutil.copy2(path, backup)
            run_office_renderer([
                "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(EXCEL_EDIT_SCRIPT),
                "-Source", str(path), "-Sheet", table._fileworkbench_sheet, "-Row", str(row_number), "-Column", str(column_number), "-Value", value,
            ])
            self.status.set(f"Saved {table._fileworkbench_sheet}!{coordinate} | Backup: {backup.name}")
            self.preview_selected()
        except Exception as exc:
            messagebox.showerror("Excel edit failed", str(exc))
        return "break"

    def show_excel_selection(self, table: Sheet) -> None:
        selected = table.get_currently_selected()
        if not selected:
            return
        if isinstance(selected.row, int) and isinstance(selected.column, int):
            coordinate = f"{get_column_letter(selected.column + 1)}{selected.row + 1}"
            self.status.set(f"Selected {table._fileworkbench_sheet}!{coordinate} | Double-click to edit")

    def preview_markdown(self, source: str) -> None:
        self.preview.pack_forget()
        self.native_preview_host.pack_forget()
        self.preview_canvas.pack_forget()
        self.excel_stack.pack_forget()
        self.preview_scroll.pack_forget()
        self.markdown_view.configure(zoom=self.markdown_zoom)
        self.markdown_view.load_html(markdown_html(source))
        self.markdown_view.pack(fill=BOTH, expand=True)

    def edit_selected(self) -> None:
        path = self.previewed_path or self.selected_path()
        if not path or path.suffix.lower() != ".md":
            self.status.set("Editing is currently available for Markdown files only.")
            return
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            messagebox.showerror("Cannot open Markdown", str(exc))
            return
        self.inline_markdown_path = path
        self.stop_native_pdf_preview()
        self.preview_canvas.pack_forget()
        self.excel_stack.pack_forget()
        self.markdown_view.pack_forget()
        self.preview_scroll.pack(side=RIGHT, fill=Y)
        self.preview_scroll.configure(command=self.preview.yview)
        self.preview.pack(side=LEFT, fill=BOTH, expand=True)
        self.preview.configure(state="normal", font=("Microsoft YaHei", self.preview_font_size), undo=True)
        self.preview.delete("1.0", END)
        self.preview.tag_configure("md_heading_1", font=("Microsoft YaHei", 20, "bold"), foreground="#111111", spacing1=14, spacing3=7)
        self.preview.tag_configure("md_heading_2", font=("Microsoft YaHei", 17, "bold"), foreground="#111111", spacing1=12, spacing3=6)
        self.preview.tag_configure("md_heading_3", font=("Microsoft YaHei", 14, "bold"), foreground="#111111", spacing1=10, spacing3=4)
        self.preview.tag_configure("md_bullet", lmargin1=24, lmargin2=42)
        self.preview.tag_configure("md_quote", lmargin1=24, lmargin2=38, foreground="#334155")
        for line in source.splitlines():
            display, tag = self.markdown_editor_line(line)
            self.preview.insert(END, display + "\n", (tag,) if tag else ())
        self.preview.focus_set()
        self.preview.bind("<Control-s>", lambda _event: (self.save_inline_markdown(), "break")[1])
        self.save_markdown_button.pack(side=RIGHT, padx=(0, 6))
        self.status.set("Markdown edit mode | Parsed formatting is shown directly | Ctrl+S to save")

    @staticmethod
    def markdown_editor_line(line: str) -> tuple[str, str]:
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            return FileWorkbench.clean_markdown_inline(heading.group(2)), f"md_heading_{len(heading.group(1))}"
        bullet = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if bullet:
            return "• " + FileWorkbench.clean_markdown_inline(bullet.group(1)), "md_bullet"
        quote = re.match(r"^>\s?(.*)$", line)
        if quote:
            return FileWorkbench.clean_markdown_inline(quote.group(1)), "md_quote"
        return FileWorkbench.clean_markdown_inline(line), ""

    @staticmethod
    def clean_markdown_inline(text: str) -> str:
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        return re.sub(r"(\*\*|__|`|\*|_)", "", text)

    def save_inline_markdown(self) -> None:
        path = self.inline_markdown_path
        if path is None:
            return
        lines = []
        for line_number in range(1, int(self.preview.index("end-1c").split(".")[0]) + 1):
            text = self.preview.get(f"{line_number}.0", f"{line_number}.end")
            tags = self.preview.tag_names(f"{line_number}.0")
            if "md_heading_1" in tags:
                text = "# " + text
            elif "md_heading_2" in tags:
                text = "## " + text
            elif "md_heading_3" in tags:
                text = "### " + text
            elif "md_bullet" in tags:
                text = "- " + text.removeprefix("• ")
            elif "md_quote" in tags:
                text = "> " + text
            lines.append(text)
        try:
            source = "\n".join(lines).rstrip("\n") + "\n"
            path.write_text(source, encoding="utf-8")
            self.inline_markdown_path = None
            self.save_markdown_button.pack_forget()
            self.preview_markdown(source)
            self.status.set(f"Saved: {path.name}")
        except OSError as exc:
            messagebox.showerror("Cannot save Markdown", str(exc))

    def select_excel_sheet(self, selected: ttk.Frame) -> None:
        for frame in self.excel_content.winfo_children():
            frame.pack_forget()
        selected.pack(fill=BOTH, expand=True)

    def open_selected(self) -> None:
        path = self.selected_path()
        if not path:
            return
        if path.is_dir():
            self.load_folder(path)
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("Cannot open", str(exc))

    def sort_by(self, column: str) -> None:
        rows = list(self.file_list.get_children())
        if column == "name":
            rows.sort(key=lambda item: self.file_list.item(item, "text").lower())
        else:
            index = {"type": 0, "modified": 1, "size": 2}[column]
            rows.sort(key=lambda item: self.file_list.item(item, "values")[index].lower())
        for position, item in enumerate(rows):
            self.file_list.move(item, "", position)


if __name__ == "__main__":
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("jg.FileWorkbench")
    if os.environ.get("FILE_WORKBENCH_SELFTEST"):
        import traceback
        log_path = BASE_DIR / "selftest.log"
        try:
            from msg_reader import MsgReader  # noqa: F401  (must be bundled next to the app)
            missing = [str(path) for path in (OFFICE_RENDER_SCRIPT, EXCEL_EDIT_SCRIPT,
                                              APP_ICON_PATH, NATIVE_PDF_PREVIEW) if not path.exists()]
            if missing:
                log_path.write_text("SELFTEST MISSING: " + "; ".join(missing), encoding="utf-8")
                sys.exit(1)
            app = FileWorkbench()
            app.withdraw()
            app.update_idletasks()
            app.destroy()
            log_path.write_text("SELFTEST OK", encoding="utf-8")
            sys.exit(0)
        except Exception:
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
            sys.exit(1)
    FileWorkbench().mainloop()
