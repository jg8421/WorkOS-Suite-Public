import http from "node:http";

function json(response, status, body) {
  const payload = Buffer.from(JSON.stringify(body));
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": payload.length,
    "Cache-Control": "no-store"
  });
  response.end(payload);
}

function authorized(request, token) {
  const header = request.headers.authorization || "";
  return header === `Bearer ${token}`;
}

async function readJson(request, maxBytes) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > maxBytes) throw new Error("Request body is too large.");
    chunks.push(chunk);
  }
  if (size === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

export function startHttpServer(store, config, logger = console) {
  const server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url || "/", `http://${request.headers.host || "localhost"}`);
      if (request.method === "GET" && url.pathname === "/health") {
        return json(response, 200, { ok: true, service: "personal-memory" });
      }
      if (!authorized(request, config.token)) {
        response.setHeader("WWW-Authenticate", "Bearer");
        return json(response, 401, { error: "Unauthorized" });
      }
      if (request.method === "POST" && url.pathname === "/api/events") {
        const body = await readJson(request, config.maxBodyBytes);
        const inputs = Array.isArray(body) ? body : [body];
        const results = inputs.slice(0, 100).map((input) => store.remember(input, { deferRender: true }));
        store.rebuildViews();
        return json(response, 201, {
          saved: results.filter((item) => !item.duplicate).length,
          duplicates: results.filter((item) => item.duplicate).length,
          events: results.map((item) => item.event)
        });
      }
      if (request.method === "GET" && url.pathname === "/api/search") {
        return json(response, 200, {
          results: store.search(url.searchParams.get("q") || "", url.searchParams.get("limit") || 8)
        });
      }
      if (request.method === "GET" && url.pathname === "/api/recent") {
        return json(response, 200, {
          results: store.recent(url.searchParams.get("limit") || 20, url.searchParams.get("since") || undefined)
        });
      }
      if (request.method === "GET" && url.pathname === "/api/stats") {
        return json(response, 200, store.stats());
      }
      if (request.method === "GET" && url.pathname.startsWith("/api/memory/")) {
        const event = store.get(decodeURIComponent(url.pathname.slice("/api/memory/".length)));
        return event ? json(response, 200, event) : json(response, 404, { error: "Not found" });
      }
      if (request.method === "POST" && url.pathname === "/api/forget") {
        const body = await readJson(request, config.maxBodyBytes);
        return store.forget(String(body.id || ""))
          ? json(response, 200, { ok: true, id: body.id })
          : json(response, 404, { error: "Not found" });
      }
      return json(response, 404, { error: "Not found" });
    } catch (error) {
      logger.error?.(`HTTP error: ${error.message}`);
      return json(response, 400, { error: error.message });
    }
  });
  server.listen(config.port, config.host, () => {
    logger.log?.(`Personal Memory listening on http://${config.host}:${config.port}`);
  });
  return server;
}
