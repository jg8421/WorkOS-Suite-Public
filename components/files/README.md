# jg File System

Windows local file workbench for navigating folders, previewing Office documents, searching project files, and managing common file operations.

## v1.4 Highlights

- **Unstuck arrow navigation:** the native PDF preview no longer steals keyboard focus (non-activating host + focus guard), so Up/Down keep switching files even while a PDF is on screen.
- **Two-stage Word/PPT preview:** selecting an Office file instantly shows a light preview (embedded thumbnail, extracted text, or cached first pages); press **ALT** to export the full readable PDF quietly in the background.
- **Export progress feedback:** an in-app progress bar shows "正在导出中" while Office exports - no terminal, no Word/PowerPoint window, nothing steals your focus.
- **More robust silent export:** window-less PowerPoint open (no WithWindow fallback), fixed PowerShell 5.1 MsoTriState binding, a Mark-of-the-Web (downloaded file) temp-copy workaround, and automatic cleanup of leftover hidden Office processes.

## v1.3 Highlights

- **Explorer-style copy:** Ctrl+C puts the selected files on the Windows clipboard (CF_HDROP), so multi-selected files can be pasted straight into WeChat, mail clients, Explorer and other apps. The file list now supports Ctrl+click multi-select, and Ctrl+C works even while the preview pane has focus.
- **Silent Office preview:** PowerPoint/Word previews render fully in the background — no application window flashes, no terminal pop-ups; the pane just shows "Loading".
- **Privacy hardening:** settings, preview cache and WebView2 data now live in `%APPDATA%\FileWorkbench`, keeping the release folder clean and safe to share.

## v1.2 Highlights

- **MSG email preview:** open Outlook `.msg` files directly — subject, sender, recipients, attachments, and body text are extracted locally (no Outlook needed).
- **Zip archive preview:** click a `.zip` to browse its contents as a clickable list; click any entry to preview it inline (text, images, Office files, PDFs, and more).
- **Arrow-key file switching:** Up/Down keep moving between files even while the preview pane is focused, so previewing no longer traps keyboard navigation.
- **Faster previews:** images and zip listings appear instantly; Excel, Word, and PowerPoint show quick text/thumbnail first and upgrade to full quality in the background; cache cleanup no longer blocks startup.
- **Bigger Markdown reading:** Markdown preview uses a 20px font, fuller window width, and relaxed line spacing by default; Ctrl+Plus / Ctrl+Minus still adjust reading size.

## v1.1 Highlights

- **Native PDF preview:** embedded Microsoft Edge WebView2 for smooth continuous scrolling, text selection/copy, search, and browser-quality zoom.
- **Higher-fidelity Office preview:** Word and PowerPoint use Microsoft Office's native PDF export instead of low-resolution slide images.
- **Faster switching:** the PDF engine remains alive between documents, avoiding repeated browser cold starts.
- **Long-document reading:** image-preview fallback now renders up to 16 pages at 2x resolution and keeps a 5 GB local cache for fast reopening.
- **Practical file work:** folder tabs, pinned locations, recursive search, keyboard navigation, copy/cut/paste, rename, Markdown preview/edit, and Excel cell selection/editing.

> Office preview and Excel editing require Microsoft Office installed locally. All previews stay on the local machine.

## Run

- **Release EXE:** open `release\FileWorkbench v1.4\FileWorkbench.exe` — portable, no Python needed.
- **From source:** open `Launch File Workbench.cmd` on Windows with Python 3.12 and Microsoft Office installed for Office rendering and Excel cell editing.
