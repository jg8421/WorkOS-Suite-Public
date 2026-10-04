# Authenticated public Android upload

The optional upload-only listener binds to loopback port 18766. `node scripts/enable-public-upload.mjs` creates a separate random token in local runtime config; never commit that config or tunnel credentials. Restart the installed service after deploying `upload-gateway.mjs` and `service.mjs`.

Expose only this listener through an HTTPS Cloudflare Tunnel hostname, not the full local memory API. Authenticated GET `/api/stats` returns health only; POST `/api/events` accepts bounded Android-source batches and acknowledges counts only. Search, recent, get, forget and other routes are not exposed. Request size, timeouts and connection/rate limits apply. Android records retain stable source IDs for retry deduplication.

Phone pairing must be explicitly confirmed on the phone. Use the HTTPS hostname and the **publicUpload.token**, not the local management token. Disable direct Graph upload when choosing this transport. Preserve offline queues and capture permission settings.

Deployment status on 2026-10-01: source tests passed and local gateway deployed. The user corrected the hostname DNS route, and the separate connector was then started. Live HTTPS checks returned anonymous 401, authenticated health 200 with `personal-memory-upload`, and authenticated recent-memory access 404. Existing DSH config was not edited. Phone pairing, an actual harmless phone record, and OneDrive cloud upload still require separate verification. The hidden `launch-upload-tunnel.vbs` wrapper can start the connector watchdog at Windows user login; it restarts only the connector using this exact configuration path.
