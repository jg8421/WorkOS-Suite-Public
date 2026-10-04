import { spawn } from 'node:child_process';
import path from 'node:path';
import fs from 'node:fs';

export class DesktopCapture {
  constructor(store, home, options = {}, logger = console) {
    this.store = store; this.home = home; this.options = options; this.logger = logger;
    this.child = null; this.timer = null; this.stopped = false; this.lastPulse = 0; this.lastSaved = 0;
    this.seen = new Map(); this.buffer = ''; this.lastStateWrite = 0;
  }
  launch() {
    if (this.stopped || this.child || this.options.enabled !== true || process.platform !== 'win32') return;
    const executable = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
    this.lastPulse = Date.now(); this.buffer = '';
    const child = spawn(executable, ['-NoProfile', '-NonInteractive', '-Mta', '-ExecutionPolicy', 'Bypass', '-File',
      path.join(this.home, 'scripts', 'capture-desktop.ps1'), '-OwnerPid', String(process.pid)], { windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
    this.child = child;
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', text => {
      this.buffer += text;
      if (this.buffer.length > 100000) { this.buffer = ''; return; }
      let index;
      while ((index = this.buffer.indexOf('\n')) >= 0) {
        const line = this.buffer.slice(0, index); this.buffer = this.buffer.slice(index + 1);
        try {
          const item = JSON.parse(line); this.lastPulse = Date.now();
          if (this.lastPulse - this.lastStateWrite > 15000 || !item.heartbeat) {
            this.lastStateWrite = this.lastPulse;
            fs.writeFileSync(path.join(this.home, 'state', 'desktop-capture.json'), JSON.stringify({
              workerPid: child.pid, checkedAt: new Date().toISOString(), lastCapturedAt: this.lastSaved,
              status: item.heartbeat ? String(item.status || 'running') : 'observed', enabled: true
            }));
          }
          if (item.heartbeat || typeof item.text !== 'string' || item.text.length > 1600
            || typeof item.title !== 'string' || item.title.length > 300) continue;
          const signature = `${item.title}|${item.text}`; const now = Date.now();
          if (now - this.lastSaved < 15000 || now - (this.seen.get(signature) || 0) < 300000) continue;
          this.lastSaved = now; this.seen.set(signature, now);
          if (this.seen.size > 256) this.seen.delete(this.seen.keys().next().value);
          this.store.remember({ type: 'activity', source: 'windows:context', sourceId: `desktop-${now}`,
            title: item.title, text: item.text, timestamp: new Date(now).toISOString(), importance: 0.25,
            tags: ['windows', 'visible-context'], metadata: { process: String(item.process || '').slice(0, 80), privacyVersion: 1 } });
        } catch { /* Never log screen content or malformed worker output. */ }
      }
    });
    child.on('error', () => { if (this.child === child) this.child = null; });
    child.on('exit', () => { if (this.child === child) this.child = null; });
  }
  start() {
    this.launch();
    this.timer = setInterval(() => {
      if (this.child && Date.now() - this.lastPulse > 60000) this.child.kill();
      else this.launch();
    }, 10000); this.timer.unref();
  }
  stop() { this.stopped = true; clearInterval(this.timer); this.child?.kill(); }
}
