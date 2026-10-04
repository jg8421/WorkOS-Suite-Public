import fs from "node:fs";
import path from "node:path";
import { loadConfig } from "./config.mjs";
import { MemoryStore } from "./store.mjs";
import { startHttpServer } from "./http.mjs";
import { CodexImporter } from "./codex-importer.mjs";
import { DshImporter } from "./dsh-importer.mjs";
import { CloudInboxImporter } from './cloud-inbox.mjs';
import { DesktopCapture } from './desktop-capture.mjs';
import { startUploadGateway } from './upload-gateway.mjs';

const { home, config } = loadConfig();
const logPath = path.join(home, "logs", "service.log");
const writeLog = (level, values) => {
  const text = values.map((value) => value instanceof Error ? value.stack || value.message : String(value)).join(" ");
  fs.appendFileSync(logPath, `${new Date().toISOString()} ${level} ${text}\n`, "utf8");
};
const logger = {
  log: (...values) => writeLog("INFO", values),
  error: (...values) => writeLog("ERROR", values)
};

const store = new MemoryStore(home, config);
store.rebuildViews();
const server = startHttpServer(store, config, logger);
const uploadGateway = startUploadGateway(store, config, logger);
const importer = new CodexImporter(store, home, config.codexImport, logger);
const dshImporter = new DshImporter(store, home, config.dshImport, logger);
await importer.start();
await dshImporter.start();
const cloudInbox = new CloudInboxImporter(store, home, config.cloudInbox, logger);
cloudInbox.start();
const desktopCapture = new DesktopCapture(store, home, config.desktopCapture, logger);
desktopCapture.start();

function shutdown() {
  uploadGateway?.close();
  importer.stop();
  dshImporter.stop();
  cloudInbox.stop();
  desktopCapture.stop();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 3000).unref();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
process.on("uncaughtException", (error) => logger.error(error));
process.on("unhandledRejection", (error) => logger.error(error));
