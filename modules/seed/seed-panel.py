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

PORT = 80
STATE_DIR = "/var/lib/openos-seed"
REPO_URL = os.environ.get("OPENOS_REPO_URL", "https://github.com/fritte-MOOD/OpenOS-Server.git")
BASH = os.environ.get("OPENOS_BASH", "/run/current-system/sw/bin/bash")
SEED_PULL = os.environ.get("OPENOS_SEED_PULL", "/etc/openos/seed-pull.sh")
NIXOS_PATH = "/run/current-system/sw/bin"

os.makedirs(STATE_DIR, exist_ok=True)

ENV_WITH_PATH = {**os.environ, "PATH": NIXOS_PATH + ":" + os.environ.get("PATH", "")}


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
  .log { background: #000; border: 1px solid #333; border-radius: 4px;
         padding: 0.8rem; font-family: monospace; font-size: 0.8rem;
         max-height: 400px; overflow-y: auto; white-space: pre-wrap;
         color: #aaa; display: none; margin-top: 1rem; }
  .status { text-align: center; padding: 0.5rem; border-radius: 4px;
            margin-top: 0.5rem; font-size: 0.9rem; }
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
    <div class="step" id="step1">
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

    <div class="step" id="step2">
      <h2>2. Network (Tailscale)</h2>
      <label>Headscale Server URL (optional)</label>
      <input name="headscale_url" placeholder="https://hs.example.com">
      <p style="color:#666;font-size:0.8rem;">Leave empty to configure later.</p>
    </div>

    <div class="step" id="step3">
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

  <div class="log" id="log"></div>
  <div class="status" id="status" style="display:none;"></div>

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
  fetch('/api/info').then(function(r){return r.json()}).then(function(d){
    document.getElementById('ip').textContent=d.ip||'?';
    document.getElementById('arch').textContent=d.arch||'?';
    document.getElementById('memory').textContent=(d.memory_gb||'?')+' GB';
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
    if(e.key==='Enter') runCmd();
  });

  document.getElementById('setupForm').addEventListener('submit', function(e){
    e.preventDefault();
    var btn=document.getElementById('installBtn');
    var log=document.getElementById('log');
    var status=document.getElementById('status');
    btn.disabled=true; btn.textContent='Installing...';
    log.style.display='block'; log.textContent='Starting installation...\n';
    status.style.display='block'; status.className='status working';
    status.textContent='Installing... this may take 10-30 minutes.';

    var fd=new FormData(e.target);
    var data=Object.fromEntries(fd.entries());

    fetch('/api/install',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(data)
    }).then(function(res){
      var reader=res.body.getReader();
      var decoder=new TextDecoder();
      function pump(){
        return reader.read().then(function(result){
          if(result.done) return;
          log.textContent = log.textContent + decoder.decode(result.value);
          log.scrollTop=log.scrollHeight;
          return pump();
        });
      }
      return pump();
    }).then(function(){
      status.className='status ok';
      status.textContent='Installation complete! The server will reboot in 10 seconds.';
      btn.textContent='Done';
    }).catch(function(err){
      status.className='status err';
      status.textContent='Installation failed: '+err.message;
      btn.disabled=false; btn.textContent='Retry';
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
                chunk = "%x\r\n%s\r\n" % (len(line.encode()), line)
                self.wfile.write(chunk.encode())
                self.wfile.flush()

            try:
                self._run_install(body, send_line)
            except Exception as e:
                send_line("ERROR: %s" % e)

            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

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

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

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

        send("=== OpenOS Installation ===")
        send("Hostname: %s" % hostname)
        send("Domain: %s" % domain)
        send("Channel: %s" % channel)
        send("Bash: %s" % BASH)
        send("Script: %s" % SEED_PULL)
        send("")

        send("Pulling OpenOS from GitHub...")
        try:
            proc = subprocess.run(
                [BASH, SEED_PULL, repo_url, channel, hostname, domain, timezone, password, headscale_url],
                capture_output=True, text=True, timeout=3600,
                env=ENV_WITH_PATH
            )

            for line in proc.stdout.splitlines():
                send(line)
            if proc.stderr:
                for line in proc.stderr.splitlines():
                    send("[stderr] %s" % line)

            if proc.returncode == 0:
                send("")
                send("Installation complete!")
                send("The server will reboot in 10 seconds...")
                subprocess.Popen([BASH, "-c", "sleep 10 && systemctl reboot -i || reboot -f"],
                                 env=ENV_WITH_PATH)
            else:
                send("Installation failed with exit code %d" % proc.returncode)
        except Exception as e:
            send("ERROR: %s" % e)


if __name__ == "__main__":
    print("OpenOS Seed Panel running on port %d" % PORT)
    print("Open http://%s/ in your browser" % get_ip())
    print("Bash: %s" % BASH)
    print("Seed pull: %s" % SEED_PULL)
    server = http.server.HTTPServer(("0.0.0.0", PORT), SeedHandler)
    server.serve_forever()
