# Personal Memory Assistant

Android + Windows + MCP 跨设备个人上下文记忆助手。**v0.4.0** 支持独立认证的 HTTPS 手机上传入口、Windows 后台服务与托盘、离线队列补传，以及可配置的 OneDrive 官方直传。

修复了旧 Android 客户端与公网上传回执不兼容的问题，以及连接按钮缺少就近进度和错误提示的问题。测试部署已验证 HTTPS 上传、重复去重、电脑入库及 OneDrive 云端文件更新；移动网络切换、长期续航与不同设备后台行为仍需各自验证。详见 [版本说明](CHANGELOG.md)、[公网入口配置](docs/public-upload.md)、[手机直传配置](docs/onedrive-direct-upload.md) 与 [电脑采集范围](docs/windows-context.md)。源码和 APK 不包含使用者的个人路径、域名、公司账户、实际记忆、运行令牌、微软授权凭据或签名私钥。APK 在 `releases/`。

## 运行与安装

需要 Node.js 22.15+、pnpm；Android 构建需要 JDK 17+ 和 Android SDK 35/build-tools 35.0.0。

```powershell
pnpm install
pnpm test
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install-service.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install-tray.ps1
node scripts/enable-desktop.mjs
```

首次安装时请配置本机 `%LOCALAPPDATA%/PersonalMemory/config.json` 的 `dataRoot` 为自己的 OneDrive 数据目录。运行启用脚本时也要针对安装目录的脚本执行，避免改动另一份运行配置。启用电脑采集后重启服务。MCP 配置样例中的 `<ABSOLUTE_INSTALL_DIRECTORY>` 必须替换为本机安装目录。遇到端口冲突时请使用未占用端口或配置本地探测地址。

下面保留 0.2.0 基础能力和已有测试记录；关于手机仅能连接电脑的旧说明只适用于未授权 OneDrive 直传的模式。

Personal memory for Android, Windows, Codex, and other MCP clients such as DeepSeek Harness (DSH). This guide describes Android 0.2.0 and the matching Windows service/tray behavior. The MCP package retains its own version.

## What is collected

| Source | Collection scope | Required action |
| --- | --- | --- |
| Android manual | Text notes and text from the system voice recognizer | Press Save in Personal Memory |
| Android share | Shared text or an attachment URI in the note field; attachment bytes are not imported | Review the populated note and press Save |
| Android notification | Permitted notification title and text, after filtering and deduplication | Enable notification capture and Android notification access |
| Android accessibility | App/window switches, accessible visible non-editable text, labeled clicks, and approximate foreground duration | Accept the in-app disclosure and grant system accessibility permission |
| Windows importers | Supported Codex/DSH user and assistant messages | Enable the corresponding importer; default polling is 15 seconds |
| Windows context (0.3) | Foreground app/window titles and exposed non-editable visible labels | Enable desktop capture; hidden 5-second sampler with bounded reads |
| MCP/manual API | Content explicitly sent through memory tools or authenticated API | Connect an authorized client |

Accessibility collection depends on what each app exposes to Android. It is not a complete log of every action or message. Custom-rendered views, image/video content, private databases, hidden history, and inaccessible UI may be absent. There is no screenshot/OCR collection, raw keyboard capture, editable-field text capture, or background microphone recording. Text already rendered as a normal message may be captured after sending if the app exposes it.

## Consent, filters, and pause

Both automatic Android capture modes default to off. Accessibility additionally requires a versioned in-app consent record. The app shows enabled/permission state, pending count, last queued time, last write to the PC, and sync errors. Its Pause button stops new notification and accessibility capture. Already queued items can still upload, and explicitly saved manual notes still work.

The optional status notification provides a Pause action and requires notification-display permission where applicable. An enabled toggle and a granted system permission alone do not prove that a new event reached the PC.

Known authenticator, password-manager, bank/payment, keyboard, system/permission apps and this app are excluded. Package/label and page-text heuristics also filter recognized authentication, login, payment, and private-browsing pages. Password/sensitive nodes cause the page to be skipped; editable text is not recorded. These heuristics can miss unfamiliar apps/pages or exclude harmless ones. Additional package exclusions apply to both notification and context capture. The notification allowlist is independent; `*` permits all apps that pass the filters.

Android applies secret redaction before queueing and again before upload; recognized authentication notifications are skipped. The PC adds common secret/OTP redaction. Window-package validation, screen/lock checks, deduplication, and rate limits bound context collection. App-use duration is an estimate, not a screen-time audit.

## Storage and synchronization

The current installation uses:

```text
Android -> Windows HTTP service -> local OneDrive folder -> OneDrive desktop sync -> Microsoft cloud
Codex/DSH importers -------------> local OneDrive folder
```

Configure your own OneDrive-backed data folder in local runtime configuration. Application files, pairing token, logs, and import cursors remain in `%LOCALAPPDATA%\PersonalMemory`; never commit that runtime directory.

In computer-mediated mode, use a reachable LAN address on a trusted network or configure the authenticated HTTPS upload-only gateway for cross-network access. The PC must be awake and connected. A successful phone sync means the PC acknowledged the events, not that OneDrive finished uploading; verify cloud updates separately. The tray's OneDrive indicator detects only the client process. Direct Graph upload is an optional alternative requiring an independent Microsoft app registration and consent.

The offline queue uses atomic writes, asynchronous I/O/network workers, bounded batch uploads, and stable identifiers for retry deduplication. Retry triggers include opening the app, new queued events, network availability callbacks, and a scheduled job with a nominal 15-minute interval. Android battery/background restrictions may delay that job beyond 15 minutes. Queue capacity is approximately 20 MiB; when full, new items can fail to save with an in-app error. Capture is not guaranteed lossless or instantaneous.

If you choose an organization's OneDrive location, captured content may also reach that cloud under its account policies. MCP clients may send retrieved snippets to their configured model. This supplies retrievable context; it does not train or alter model weights.

## Windows service and tray

The per-user service and tray start from `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` through hidden VBS launchers, without a console window. Installation migrates obsolete service/tray scheduled tasks.

Right-click the notification-area icon for counts/status, the data folder or `MEMORY.md`, pause, resume, restart, and exit. If Windows hides it, expand the taskbar overflow area.

While running, the tray checks every 20 seconds and attempts to restart an unexpectedly unavailable service at most once per minute. Explicit Pause writes `state/service.paused`, which automatic starts respect across login/restarts. Resume or an explicit start clears it. Pausing the PC service stops phone ingestion and PC importers; it does not pause Android capture or OneDrive itself. Exit tray leaves the service running but removes the restart watchdog until the tray starts again.

The installer creates the parent Run registry key only if absent, preserving existing startup registrations. Review your own startup settings when migrating from an earlier installation.

## Memory files and clients

- `events.jsonl`: append-only event ledger.
- `events/YYYY-MM-DD.jsonl`: daily shards.
- `memory/MEMORY.md` and `memory/daily/YYYY-MM-DD.md`: generated readable views.
- `deleted.json` and `tombstones.jsonl`: soft-deletion records.

`memory_forget` hides an event from active retrieval but leaves raw records recoverable in local/cloud copies. It is not physical erasure.

The stdio MCP server exposes `memory_search`, `memory_recent`, `memory_get`, `memory_stats`, `memory_remember`, and `memory_forget`. Codex and DSH use the same store. Running clients may need a normal restart to load new MCP configuration. Importers keep supported user/assistant messages rather than system/tool/runtime payloads.

## API and validation

- `GET /health`: unauthenticated health flag only.
- `POST /api/events`: save an event or batch.
- `GET /api/search?q=...`, `GET /api/recent`, `GET /api/stats`: retrieval/search/status.
- `GET /api/memory/:id`: retrieve an event.
- `POST /api/forget`: soft-delete by ID.

Every `/api/*` endpoint requires `Authorization: Bearer <token>`.

Run `pnpm test` for server/importer checks. `tests/android-smoke/check-privacy.ps1` runs pure-Java filter/rate-limit checks. `tests/android-smoke/build-fixture.ps1` builds a separate permission-free synthetic UI fixture; its README describes device checks. Building/signing the fixture or passing local checks does not itself verify on-device capture, permissions, upload, or OneDrive cloud completion.

On-device checks on 2026-09-30 confirmed that neutral fixture text reached the PC event ledger as `android:accessibility`; the editable/password form and synthetic typing produced no new fixture records; pausing blocked a 24-update burst; resuming restored capture. This verifies those tested paths, not every Android app or Microsoft cloud upload completion. Real OneDrive cloud completion was not verified.

The final 0.2.0 APK also passed a scoped offline test: stopping the Memory service and using the fixture left two pending records with a connection error. After restoring the service and explicitly triggering the existing JobScheduler job 18765, the queue fell from two to zero and both fixture events appeared in the canonical `events.jsonl`. This proves that tested queued-job retry path, not natural 15-minute scheduling or offline-reboot durability. Accessibility remained bound with no observed app crash. Smoke records contain synthetic test content, not facts about the user.
