#!/usr/bin/env python3
"""
OpenOS Seed Panel — Setup Wizard

Paths to binaries are passed via environment variables set by systemd.
"""

import http.server
import json
import os
import subprocess
import socket
import threading
import time

PORT = 80
STATE_DIR = "/var/lib/openos-seed"
REPO_URL = os.environ.get("OPENOS_REPO_URL", "https://github.com/fritte-MOOD/OpenOS-Server.git")
BASH = os.environ.get("OPENOS_BASH", "/run/current-system/sw/bin/bash")
SEED_PULL = os.environ.get("OPENOS_SEED_PULL", "/etc/openos/seed-pull.sh")
NIXOS_PATH = "/run/current-system/sw/bin"

os.makedirs(STATE_DIR, exist_ok=True)

ENV_WITH_PATH = {**os.environ, "PATH": NIXOS_PATH + ":" + os.environ.get("PATH", "")}

# Global install log shared between the install thread and SSE clients
install_log = []
install_running = False
install_done = False


def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def get_system_info():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    info["memory_gb"] = round(kb / 1024 / 1024, 1)
    except Exception:
        info["memory_gb"] = "?"

    try:
        result = subprocess.run(["lsblk", "-d", "-o", "NAME,SIZE,MODEL", "--json"],
                                capture_output=True, text=True, timeout=5, env=ENV_WITH_PATH)
        data = json.loads(result.stdout)
        info["disks"] = data.get("blockdevices", [])
    except Exception:
        info["disks"] = []

    try:
        result = subprocess.run(["uname", "-m"],
                                capture_output=True, text=True, timeout=5, env=ENV_WITH_PATH)
        info["arch"] = result.stdout.strip()
    except Exception:
        info["arch"] = "unknown"

    info["ip"] = get_ip()
    info["hostname"] = socket.gethostname()
    return info


def run_install_bg(config):
    """Run the install in a background thread, streaming output to install_log."""
    global install_log, install_running, install_done
    install_log = []
    install_running = True
    install_done = False

    hostname = config.get("hostname", "openos")
    domain = config.get("domain", "openos.local")
    timezone = config.get("timezone", "UTC")
    password = config.get("password", "")
    headscale_url = config.get("headscale_url", "")
    repo_url = config.get("repo_url", REPO_URL)
    channel = config.get("channel", "nightly")

    def log(msg):
        install_log.append(msg)

    log("=== OpenOS Installation ===")
    log("Hostname: %s" % hostname)
    log("Domain: %s" % domain)
    log("Channel: %s" % channel)
    log("")
    log("Running seed-pull script...")

    try:
        proc = subprocess.Popen(
            [BASH, SEED_PULL, repo_url, channel, hostname, domain, timezone, password, headscale_url],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
            env=ENV_WITH_PATH
        )

        for line in iter(proc.stdout.readline, ''):
            log(line.rstrip())

        proc.wait()

        if proc.returncode == 0:
            log("")
            log("=== Installation complete! ===")
            log("The server will reboot in 15 seconds...")
            subprocess.Popen([BASH, "-c", "sleep 15 && systemctl reboot -i || reboot -f"],
                             env=ENV_WITH_PATH)
        else:
            log("Installation FAILED with exit code %d" % proc.returncode)
    except Exception as e:
        log("ERROR: %s" % e)

    install_running = False
    install_done = True


SETUP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenOS Setup</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0a0a0a; color: #e5e5e5; min-height: 100vh;
         display: flex; align-items: center; justify-content: center; }
  .container { max-width: 640px; width: 100%; padding: 2rem; }
  h1 { font-size: 1.8rem; margin-bottom: 0.5rem; color: #fff; }
  .subtitle { color: #888; margin-bottom: 2rem; }
  .info-box { background: #1a1a1a; border: 1px solid #333; border-radius: 8px;
              padding: 1rem; margin-bottom: 1.5rem; }
  .info-row { display: flex; justify-content: space-between; padding: 0.3rem 0; }
  .info-label { color: #888; }
  .info-value { color: #fff; font-family: monospace; }
  .step { background: #1a1a1a; border: 1px solid #333; border-radius: 8px;
          padding: 1.5rem; margin-bottom: 1rem; }
  .step h2 { font-size: 1.1rem; margin-bottom: 1rem; color: #fff; }
  label { display: block; color: #aaa; font-size: 0.85rem; margin-bottom: 0.3rem; }
  input, select { width: 100%; padding: 0.6rem; background: #0a0a0a; border: 1px solid #444;
                  border-radius: 4px; color: #fff; font-size: 0.95rem; margin-bottom: 0.8rem; }
  input:focus, select:focus { outline: none; border-color: #f97316; }
  button { padding: 0.7rem 1.5rem; background: #f97316; color: #000; border: none;
           border-radius: 6px; font-size: 1rem; font-weight: 600; cursor: pointer;
           width: 100%; margin-bottom: 0.5rem; }
  button:hover { background: #fb923c; }
  button:disabled { background: #555; color: #888; cursor: not-allowed; }
  .btn-secondary { background: #333; color: #fff; }
  .btn-secondary:hover { background: #444; }
  #log { background: #000; border: 1px solid #333; border-radius: 4px;
         padding: 0.8rem; font-family: monospace; font-size: 0.8rem;
         height: 350px; overflow-y: auto; white-space: pre-wrap;
         color: #aaa; display: none; margin-top: 1rem; }
  .status { text-align: center; padding: 0.5rem; border-radius: 4px;
            margin-top: 0.5rem; font-size: 0.9rem; display: none; }
  .status.ok { background: #052e16; color: #22c55e; }
  .status.err { background: #2d0a0a; color: #ef4444; }
  .status.working { background: #1a1000; color: #f97316; }
  .terminal { display: none; margin-top: 1rem; }
  .terminal-output { background: #000; border: 1px solid #333; border-radius: 4px;
                     padding: 0.8rem; font-family: monospace; font-size: 0.85rem;
                     min-height: 150px; max-height: 300px; overflow-y: auto;
                     white-space: pre-wrap; color: #0f0; }
  .terminal-input { display: flex; gap: 0.5rem; margin-top: 0.5rem; }
  .terminal-input input { flex: 1; margin-bottom: 0; }
  .terminal-input button { width: auto; padding: 0.6rem 1rem; }
</style>
</head>
<body>
<div class="container">
  <h1>OpenOS Server Setup</h1>
  <p class="subtitle">Seed system is running. Configure your server below.</p>

  <div class="info-box">
    <div class="info-row"><span class="info-label">IP Address</span><span class="info-value" id="ip">...</span></div>
    <div class="info-row"><span class="info-label">Architecture</span><span class="info-value" id="arch">...</span></div>
    <div class="info-row"><span class="info-label">Memory</span><span class="info-value" id="memory">...</span></div>
  </div>

  <form id="setupForm">
    <div class="step">
      <h2>1. Server Configuration</h2>
      <label>Hostname</label>
      <input name="hostname" value="openos" required>
      <label>Domain</label>
      <input name="domain" value="openos.local">
      <label>Timezone</label>
      <input name="timezone" value="Europe/Berlin">
      <label>Admin Password</label>
      <input name="password" type="password" required minlength="8">
    </div>

    <div class="step">
      <h2>2. Network (Tailscale)</h2>
      <label>Headscale Server URL (optional)</label>
      <input name="headscale_url" placeholder="https://hs.example.com">
      <p style="color:#666;font-size:0.8rem;">Leave empty to configure later.</p>
    </div>

    <div class="step">
      <h2>3. OpenOS Version</h2>
      <label>Repository</label>
      <input name="repo_url" value="https://github.com/fritte-MOOD/OpenOS-Server.git">
      <label>Channel</label>
      <select name="channel">
        <option value="stable">Stable (recommended)</option>
        <option value="beta">Beta (release candidates)</option>
        <option value="nightly" selected>Nightly (latest)</option>
      </select>
    </div>

    <button type="submit" id="installBtn">Install OpenOS</button>
    <button type="button" class="btn-secondary" id="termBtn">Open Terminal</button>
  </form>

  <div id="log"></div>
  <div class="status" id="status"></div>

  <div class="terminal" id="terminal">
    <h2 style="color:#fff;margin-bottom:0.5rem;">Terminal</h2>
    <div class="terminal-output" id="termOutput">$ </div>
    <div class="terminal-input">
      <input type="text" id="termCmd" placeholder="Enter command...">
      <button id="termRun">Run</button>
    </div>
  </div>
</div>

<script>
(function(){
  var logEl = document.getElementById('log');
  var statusEl = document.getElementById('status');
  var pollTimer = null;

  fetch('/api/info').then(function(r){return r.json()}).then(function(d){
    document.getElementById('ip').textContent = d.ip || '?';
    document.getElementById('arch').textContent = d.arch || '?';
    document.getElementById('memory').textContent = (d.memory_gb || '?') + ' GB';
  });

  document.getElementById('termBtn').addEventListener('click', function(){
    var t = document.getElementById('terminal');
    t.style.display = t.style.display === 'none' ? 'block' : 'none';
  });

  function runCmd() {
    var input = document.getElementById('termCmd');
    var output = document.getElementById('termOutput');
    var cmd = input.value.trim();
    if (!cmd) return;
    input.value = '';
    output.textContent = output.textContent + cmd + '\n';
    fetch('/api/exec', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd: cmd})
    }).then(function(r){return r.json()}).then(function(data){
      if (data.stdout) output.textContent = output.textContent + data.stdout;
      if (data.stderr) output.textContent = output.textContent + data.stderr;
      output.textContent = output.textContent + '$ ';
      output.scrollTop = output.scrollHeight;
    }).catch(function(e){
      output.textContent = output.textContent + 'Error: ' + e.message + '\n$ ';
    });
  }

  document.getElementById('termRun').addEventListener('click', runCmd);
  document.getElementById('termCmd').addEventListener('keypress', function(e){
    if (e.key === 'Enter') runCmd();
  });

  function pollLog() {
    var linesSeen = parseInt(logEl.getAttribute('data-lines') || '0');
    fetch('/api/install-log?from=' + linesSeen)
      .then(function(r){ return r.json() })
      .then(function(data){
        if (data.lines && data.lines.length > 0) {
          for (var i = 0; i < data.lines.length; i++) {
            logEl.textContent = logEl.textContent + data.lines[i] + '\n';
          }
          logEl.setAttribute('data-lines', String(linesSeen + data.lines.length));
          logEl.scrollTop = logEl.scrollHeight;
        }
        if (data.done && data.lines.length === 0) {
          clearInterval(pollTimer);
          var lastLine = logEl.textContent.trim().split('\n').pop() || '';
          if (lastLine.indexOf('complete') >= 0) {
            statusEl.className = 'status ok';
            statusEl.style.display = 'block';
            statusEl.textContent = 'Installation complete! Server will reboot shortly.';
          } else if (lastLine.indexOf('FAILED') >= 0 || lastLine.indexOf('ERROR') >= 0) {
            statusEl.className = 'status err';
            statusEl.style.display = 'block';
            statusEl.textContent = 'Installation failed. Check the log above.';
            document.getElementById('installBtn').disabled = false;
            document.getElementById('installBtn').textContent = 'Retry';
          }
        }
      })
      .catch(function(){});
  }

  document.getElementById('setupForm').addEventListener('submit', function(e){
    e.preventDefault();
    var btn = document.getElementById('installBtn');
    btn.disabled = true;
    btn.textContent = 'Installing...';
    logEl.style.display = 'block';
    logEl.textContent = '';
    logEl.setAttribute('data-lines', '0');
    statusEl.style.display = 'block';
    statusEl.className = 'status working';
    statusEl.textContent = 'Installing... this may take 10-30 minutes. Do not close this page.';

    var fd = new FormData(e.target);
    var data = Object.fromEntries(fd.entries());

    fetch('/api/install', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)
    }).then(function(r){ return r.json() }).then(function(resp){
      if (resp.ok) {
        pollTimer = setInterval(pollLog, 2000);
      }
    }).catch(function(err){
      statusEl.className = 'status err';
      statusEl.textContent = 'Failed to start: ' + err.message;
      btn.disabled = false;
      btn.textContent = 'Retry';
    });
  });
})();
</script>
</body>
</html>"""


class SeedHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(SETUP_HTML.encode())

        elif self.path == "/api/info":
            self._json_response(get_system_info())

        elif self.path == "/api/status":
            mode = "seed"
            try:
                with open("/etc/openos/mode") as f:
                    mode = f.read().strip()
            except Exception:
                pass
            self._json_response({
                "mode": mode,
                "hostname": socket.gethostname(),
                "ip": get_ip(),
            })

        elif self.path.startswith("/api/install-log"):
            from_line = 0
            if "from=" in self.path:
                try:
                    from_line = int(self.path.split("from=")[1])
                except Exception:
                    pass
            lines = install_log[from_line:]
            self._json_response({
                "lines": lines,
                "total": len(install_log),
                "running": install_running,
                "done": install_done,
            })

        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/install":
            global install_running
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            if install_running:
                self._json_response({"ok": False, "error": "Install already running"})
                return

            t = threading.Thread(target=run_install_bg, args=(body,), daemon=True)
            t.start()

            self._json_response({"ok": True, "message": "Install started"})

        elif self.path == "/api/exec":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            cmd = body.get("cmd", "")

            result = {"stdout": "", "stderr": "", "code": 0}
            try:
                proc = subprocess.run(
                    [BASH, "-c", cmd],
                    capture_output=True, text=True, timeout=60,
                    cwd="/root", env=ENV_WITH_PATH
                )
                result["stdout"] = proc.stdout
                result["stderr"] = proc.stderr
                result["code"] = proc.returncode
            except subprocess.TimeoutExpired:
                result["stderr"] = "Command timed out (60s limit)\n"
                result["code"] = -1
            except Exception as e:
                result["stderr"] = "Error: %s\n" % e
                result["code"] = -1

            self._json_response(result)

        else:
            self.send_error(404)

    def _json_response(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print("OpenOS Seed Panel running on port %d" % PORT)
    print("Open http://%s/ in your browser" % get_ip())
    server = http.server.HTTPServer(("0.0.0.0", PORT), SeedHandler)
    server.serve_forever()
