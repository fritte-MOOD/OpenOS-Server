#!/usr/bin/env python3
"""
OpenOS Seed Panel — Setup Wizard

A lightweight HTTP server that runs on the seed system (port 80).
It provides a web-based setup wizard to:
  1. Configure hostname, domain, timezone, admin password
  2. Set up Tailscale connection
  3. Select an OpenOS version to install
  4. Pull the full system from GitHub and rebuild

Once the full system is installed, this panel is replaced by the
real openos-api daemon + Nginx.
"""

import http.server
import json
import os
import subprocess
import socket
import html
import urllib.parse

PORT = 80
STATE_DIR = "/var/lib/openos-seed"
FLAKE_DIR = "/etc/openos/flake"
REPO_URL = "https://github.com/openos-project/openos-server.git"

os.makedirs(STATE_DIR, exist_ok=True)


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
                                capture_output=True, text=True, timeout=5)
        data = json.loads(result.stdout)
        info["disks"] = data.get("blockdevices", [])
    except Exception:
        info["disks"] = []

    try:
        result = subprocess.run(["uname", "-m"], capture_output=True, text=True, timeout=5)
        info["arch"] = result.stdout.strip()
    except Exception:
        info["arch"] = "unknown"

    info["ip"] = get_ip()
    info["hostname"] = socket.gethostname()
    return info


SETUP_HTML = """<!DOCTYPE html>
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
  .container { max-width: 600px; width: 100%; padding: 2rem; }
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
  .step.active { border-color: #f97316; }
  .step.done { border-color: #22c55e; opacity: 0.7; }
  label { display: block; color: #aaa; font-size: 0.85rem; margin-bottom: 0.3rem; }
  input, select { width: 100%; padding: 0.6rem; background: #0a0a0a; border: 1px solid #444;
                  border-radius: 4px; color: #fff; font-size: 0.95rem; margin-bottom: 0.8rem; }
  input:focus, select:focus { outline: none; border-color: #f97316; }
  button { padding: 0.7rem 1.5rem; background: #f97316; color: #000; border: none;
           border-radius: 6px; font-size: 1rem; font-weight: 600; cursor: pointer;
           width: 100%; }
  button:hover { background: #fb923c; }
  button:disabled { background: #555; color: #888; cursor: not-allowed; }
  .log { background: #000; border: 1px solid #333; border-radius: 4px;
         padding: 0.8rem; font-family: monospace; font-size: 0.8rem;
         max-height: 300px; overflow-y: auto; white-space: pre-wrap;
         color: #aaa; display: none; margin-top: 1rem; }
  .status { text-align: center; padding: 0.5rem; border-radius: 4px;
            margin-top: 0.5rem; font-size: 0.9rem; }
  .status.ok { background: #052e16; color: #22c55e; }
  .status.err { background: #2d0a0a; color: #ef4444; }
  .status.working { background: #1a1000; color: #f97316; }
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
    <div class="step active" id="step1">
      <h2>1. Server Configuration</h2>
      <label>Hostname</label>
      <input name="hostname" value="openos" required>
      <label>Domain</label>
      <input name="domain" value="openos.local">
      <label>Timezone</label>
      <input name="timezone" value="UTC">
      <label>Admin Password</label>
      <input name="password" type="password" required minlength="8">
    </div>

    <div class="step" id="step2">
      <h2>2. Network (Tailscale)</h2>
      <label>Headscale Server URL (optional)</label>
      <input name="headscale_url" placeholder="https://hs.example.com">
      <p style="color:#666;font-size:0.8rem;">Leave empty to configure later.</p>
    </div>

    <div class="step" id="step3">
      <h2>3. OpenOS Version</h2>
      <label>Repository</label>
      <input name="repo_url" value="https://github.com/openos-project/openos-server.git">
      <label>Channel</label>
      <select name="channel">
        <option value="stable" selected>Stable (recommended)</option>
        <option value="beta">Beta (release candidates)</option>
        <option value="nightly">Nightly (latest, may break)</option>
      </select>
    </div>

    <button type="submit" id="installBtn">Install OpenOS</button>
  </form>

  <div class="log" id="log"></div>
  <div class="status" id="status" style="display:none;"></div>
</div>

<script>
fetch('/api/info').then(r=>r.json()).then(d=>{
  document.getElementById('ip').textContent=d.ip||'?';
  document.getElementById('arch').textContent=d.arch||'?';
  document.getElementById('memory').textContent=(d.memory_gb||'?')+' GB';
});

document.getElementById('setupForm').addEventListener('submit', async(e)=>{
  e.preventDefault();
  const btn=document.getElementById('installBtn');
  const log=document.getElementById('log');
  const status=document.getElementById('status');
  btn.disabled=true; btn.textContent='Installing...';
  log.style.display='block'; log.textContent='Starting installation...\\n';
  status.style.display='block'; status.className='status working';
  status.textContent='Installing... this may take 10-30 minutes.';

  const fd=new FormData(e.target);
  const data=Object.fromEntries(fd.entries());

  try {
    const res=await fetch('/api/install',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const reader=res.body.getReader();
    const decoder=new TextDecoder();
    while(true){
      const{done,value}=await reader.read();
      if(done)break;
      log.textContent+=decoder.decode(value);
      log.scrollTop=log.scrollHeight;
    }
    status.className='status ok';
    status.textContent='Installation complete! The server will reboot in 10 seconds.';
    btn.textContent='Done';
  } catch(err){
    status.className='status err';
    status.textContent='Installation failed: '+err.message;
    btn.disabled=false; btn.textContent='Retry';
  }
});
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
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(get_system_info()).encode())

        elif self.path == "/api/status":
            mode = "seed"
            try:
                with open("/etc/openos/mode") as f:
                    mode = f.read().strip()
            except Exception:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "mode": mode,
                "hostname": socket.gethostname(),
                "ip": get_ip(),
            }).encode())

        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/install":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def send_line(msg):
                line = msg + "\n"
                chunk = f"{len(line.encode()):x}\r\n{line}\r\n"
                self.wfile.write(chunk.encode())
                self.wfile.flush()

            try:
                self._run_install(body, send_line)
            except Exception as e:
                send_line(f"ERROR: {e}")

            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        else:
            self.send_error(404)

    def _run_install(self, config, send):
        hostname = config.get("hostname", "openos")
        domain = config.get("domain", "openos.local")
        timezone = config.get("timezone", "UTC")
        password = config.get("password", "")
        headscale_url = config.get("headscale_url", "")
        repo_url = config.get("repo_url", REPO_URL)
        channel = config.get("channel", "stable")

        send(f"=== OpenOS Installation ===")
        send(f"Hostname: {hostname}")
        send(f"Domain: {domain}")
        send(f"Channel: {channel}")
        send("")

        send("Pulling OpenOS from GitHub...")
        proc = subprocess.run(
            ["bash", "/etc/openos/seed-pull.sh",
             repo_url, channel, hostname, domain, timezone, password, headscale_url],
            capture_output=True, text=True, timeout=1800
        )

        for line in proc.stdout.splitlines():
            send(line)
        if proc.stderr:
            for line in proc.stderr.splitlines():
                send(f"[stderr] {line}")

        if proc.returncode == 0:
            send("")
            send("Installation complete!")
            send("The server will reboot in 10 seconds...")
            subprocess.Popen(["bash", "-c", "sleep 10 && reboot"])
        else:
            send(f"Installation failed with exit code {proc.returncode}")


if __name__ == "__main__":
    print(f"OpenOS Seed Panel running on port {PORT}")
    print(f"Open http://{get_ip()}/ in your browser")
    server = http.server.HTTPServer(("0.0.0.0", PORT), SeedHandler)
    server.serve_forever()
