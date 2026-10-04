import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

// Each phone owns immutable batch files; no device overwrites the shared master log.
export class CloudInboxImporter {
  constructor(store, home, options = {}, logger = console) {
    this.store = store; this.logger = logger; this.options = options;
    this.root = path.join(store.dataRoot, 'inbox', 'android');
    this.statePath = path.join(home, 'state', 'cloud-inbox.json');
    this.seen = {}; this.timer = null; this.running = false;
    try { this.seen = JSON.parse(fs.readFileSync(this.statePath, 'utf8')); } catch {}
  }
  scan() {
    if (this.running || this.options.enabled === false) return 0;
    this.running = true;
    let saved = 0, changed = false;
    try {
      fs.mkdirSync(this.root, { recursive: true });
      const files = fs.readdirSync(this.root).filter(n => /^android-[a-f0-9]{64}\.jsonl$/.test(n));
      for (const name of files) {
        const file = path.join(this.root, name);
        const stat = fs.lstatSync(file);
        if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 300000) continue;
        const stamp = `${stat.size}:${stat.mtimeMs}`;
        if (this.seen[name] === stamp) continue;
        try {
          const body = fs.readFileSync(file, 'utf8');
          // Incomplete downloads and renamed/altered batches are never acknowledged.
          const hash = crypto.createHash('sha256').update(body).digest('hex');
          if (name !== `android-${hash}.jsonl`) continue;
          const records = body.trim().split(/\r?\n/).filter(Boolean).map(s => JSON.parse(s));
          if (!records.length || records.length > 20 || records.some(e =>
            !['android:notification', 'android:accessibility', 'android:manual'].includes(e.source)
            || typeof e.sourceId !== 'string' || !e.sourceId || e.sourceId.length > 500
            || typeof e.text !== 'string' || e.text.length > 32768
            || typeof (e.title ?? '') !== 'string' || (e.title ?? '').length > 32768)) continue;
          for (const record of records) {
            const { duplicate } = this.store.remember(record, { deferRender: true });
            if (!duplicate) saved++;
          }
          this.seen[name] = stamp; changed = true;
        } catch { /* Retain files and retry transient download/parse failures. */ }
      }
      if (saved) this.store.rebuildViews();
      if (changed) {
        const temp = `${this.statePath}.tmp`;
        fs.writeFileSync(temp, JSON.stringify(this.seen)); fs.renameSync(temp, this.statePath);
      }
      return saved;
    } finally { this.running = false; }
  }
  start() {
    this.scan();
    this.timer = setInterval(() => { try { this.scan(); } catch (e) { this.logger.error('Cloud inbox scan failed'); } }, this.options.intervalMs || 15000);
    this.timer.unref();
  }
  stop() { clearInterval(this.timer); }
}
