#!/usr/bin/env python3
"""
OpenOS Admin Panel — Bootloader Web UI

Two modes:
  - Setup mode:  first boot, no /etc/openos/configured yet
  - Normal mode: generation management, safe-update, rollback, health
"""

import http.server
import json
import os
import subprocess
import socket
import threading
import time

PORT = 80
FLAKE_DIR = os.environ.get("OPENOS_FLAKE_DIR", "/etc/openos/flake")
REPO_URL = os.environ.get("OPENOS_REPO_URL", "https://github.com/fritte-MOOD/OpenOS-Server.git")
BASH = os.environ.get("OPENOS_BASH", "/run/current-system/sw/bin/bash")
STATE_DIR = "/var/lib/openos"
NIXOS_PATH = "/run/current-system/sw/bin"

os.makedirs(STATE_DIR, exist_ok=True)

ENV_WITH_PATH = {**os.environ, "PATH": NIXOS_PATH + ":" + os.environ.get("PATH", "")}

task_log = []
task_running = False
task_done = False
task_name = ""


def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


def is_setup_mode():
    return not os.path.exists("/etc/openos/configured")


def get_system_info():
    info = {"ip": get_ip(), "hostname": socket.gethostname()}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    info["memory_gb"] = round(int(line.split()[1]) / 1024 / 1024, 1)
    except Exception:
        info["memory_gb"] = "?"
    try:
        r = subprocess.run(["uname", "-m"], capture_output=True, text=True, timeout=5, env=ENV_WITH_PATH)
        info["arch"] = r.stdout.strip()
    except Exception:
        info["arch"] = "unknown"
    try:
        r = subprocess.run(["lsblk", "-d", "-o", "NAME,SIZE,MODEL", "--json"],
                           capture_output=True, text=True, timeout=5, env=ENV_WITH_PATH)
        info["disks"] = json.loads(r.stdout).get("blockdevices", [])
    except Exception:
        info["disks"] = []
    info["setup_mode"] = is_setup_mode()
    try:
        with open("/etc/openos/version") as f:
            info["version"] = f.read().strip()
    except Exception:
        info["version"] = "unknown"
    return info


def get_generations():
    try:
        r = subprocess.run([BASH, "/etc/openos/list-generations.sh"],
                           capture_output=True, text=True, timeout=30, env=ENV_WITH_PATH)
        return json.loads(r.stdout)
    except Exception as e:
        return [{"error": str(e)}]


def get_tailscale_status():
    try:
        r = subprocess.run(["tailscale", "status", "--json"],
                           capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        data = json.loads(r.stdout)
        return {
            "connected": data.get("BackendState") == "Running",
            "self": data.get("Self", {}).get("DNSName", ""),
            "tailnet": data.get("MagicDNSSuffix", ""),
            "ips": data.get("Self", {}).get("TailscaleIPs", []),
        }
    except Exception:
        return {"connected": False, "self": "", "tailnet": "", "ips": []}


def get_health():
    services = {
        "tailscaled": False, "sshd": False,
        "openos-admin-panel": True,
        "postgresql": False, "nginx": False, "openos-api": False,
    }
    for svc in services:
        try:
            r = subprocess.run(["systemctl", "is-active", "--quiet", svc + ".service"],
                               timeout=5, env=ENV_WITH_PATH)
            services[svc] = r.returncode == 0
        except Exception:
            pass
    pending = None
    pfile = os.path.join(STATE_DIR, "pending-generation")
    if os.path.exists(pfile):
        try:
            with open(pfile) as f:
                pending = int(f.read().strip())
        except Exception:
            pass
    return {"services": services, "pending_generation": pending}


def run_task_bg(name, cmd_args):
    global task_log, task_running, task_done, task_name
    task_log = []
    task_running = True
    task_done = False
    task_name = name

    def log(msg):
        task_log.append(msg)

    log("=== %s ===" % name)
    try:
        proc = subprocess.Popen(
            cmd_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=ENV_WITH_PATH
        )
        for line in iter(proc.stdout.readline, ""):
            log(line.rstrip())
        proc.wait()
        if proc.returncode == 0:
            log("")
            log("=== %s completed successfully ===" % name)
        else:
            log("")
            log("=== %s FAILED (exit code %d) ===" % (name, proc.returncode))
    except Exception as e:
        log("ERROR: %s" % e)

    task_running = False
    task_done = True


def run_setup(config):
    global task_log, task_running, task_done, task_name
    task_log = []
    task_running = True
    task_done = False
    task_name = "Initial Setup"

    hostname = config.get("hostname", "openos")
    domain = config.get("domain", "openos.local")
    timezone = config.get("timezone", "UTC")
    password = config.get("password", "")
    headscale_url = config.get("headscale_url", "")
    repo_url = config.get("repo_url", REPO_URL)
    channel = config.get("channel", "stable")

    def log(msg):
        task_log.append(msg)

    log("=== OpenOS Initial Setup ===")
    log("Hostname: %s" % hostname)
    log("Domain: %s" % domain)
    log("Channel: %s" % channel)

    try:
        log("")
        log("Cloning OpenOS repository...")
        if os.path.exists(FLAKE_DIR + "/.git"):
            subprocess.run([BASH, "-c", "cd %s && git fetch --all --tags" % FLAKE_DIR],
                           env=ENV_WITH_PATH, timeout=120)
        else:
            subprocess.run(["git", "clone", repo_url, FLAKE_DIR],
                           env=ENV_WITH_PATH, timeout=300)
            subprocess.run([BASH, "-c", "cd %s && git fetch --all --tags" % FLAKE_DIR],
                           env=ENV_WITH_PATH, timeout=120)

        log("Selecting version for channel: %s" % channel)
        if channel == "stable":
            r = subprocess.run(
                [BASH, "-c", "cd %s && git tag -l 'v*' --sort=-v:refname | grep -v -E '(beta|rc|alpha|dev)' | head -1" % FLAKE_DIR],
                capture_output=True, text=True, env=ENV_WITH_PATH)
            tag = r.stdout.strip()
            target = tag if tag else "origin/main"
        elif channel == "beta":
            r = subprocess.run(
                [BASH, "-c", "cd %s && git tag -l 'v*' --sort=-v:refname | head -1" % FLAKE_DIR],
                capture_output=True, text=True, env=ENV_WITH_PATH)
            tag = r.stdout.strip()
            target = tag if tag else "origin/main"
        else:
            target = "origin/main"

        log("Target: %s" % target)
        subprocess.run([BASH, "-c", "cd %s && git checkout %s" % (FLAKE_DIR, target)],
                       env=ENV_WITH_PATH, timeout=60)

        log("")
        log("Writing host configuration...")
        try:
            r = subprocess.run(["mkpasswd", "-m", "sha-512", "-s"],
                               input=password, capture_output=True, text=True, env=ENV_WITH_PATH)
            pw_hash = r.stdout.strip()
        except Exception:
            pw_hash = password

        host_cfg = """{ config, pkgs, ... }:
{
  networking.hostName = "%s";
  time.timeZone = "%s";
  openos.domain = "%s";
  openos.adminEmail = "admin@%s";
  users.users.admin = {
    isNormalUser = true;
    extraGroups = [ "wheel" "openos-data" "networkmanager" ];
    hashedPassword = "%s";
  };
  openos.updates = {
    enable = true;
    channel = "%s";
    autoApply = false;
  };
}
""" % (hostname, timezone, domain, domain, pw_hash, channel)

        with open("/etc/openos/host-config.nix", "w") as f:
            f.write(host_cfg)

        if not os.path.exists("/etc/openos/apps.nix"):
            with open("/etc/openos/apps.nix", "w") as f:
                f.write("{\n}\n")

        if headscale_url:
            log("")
            log("Connecting to Tailscale (%s)..." % headscale_url)
            r = subprocess.run(
                ["tailscale", "up", "--login-server=" + headscale_url,
                 "--accept-dns", "--accept-routes", "--timeout=60s"],
                capture_output=True, text=True, timeout=90, env=ENV_WITH_PATH)
            if r.returncode == 0:
                log("Tailscale connected!")
                ts = subprocess.run(["tailscale", "ip"], capture_output=True, text=True, env=ENV_WITH_PATH)
                log("Tailscale IP: %s" % ts.stdout.strip())
            else:
                log("Tailscale warning: %s" % r.stderr.strip())
                log("You can set this up later via the admin panel.")

        log("")
        log("Building OpenOS system... (this may take 10-30 minutes)")

        arch_r = subprocess.run(["uname", "-m"], capture_output=True, text=True, env=ENV_WITH_PATH)
        arch = arch_r.stdout.strip()
        flake_target = "openos" if arch == "x86_64" else "openos-arm"

        proc = subprocess.Popen(
            [BASH, "-c",
             "nixos-rebuild boot --flake %s#%s --impure 2>&1" % (FLAKE_DIR, flake_target)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=ENV_WITH_PATH
        )
        for line in iter(proc.stdout.readline, ""):
            log(line.rstrip())
        proc.wait()

        if proc.returncode != 0:
            log("")
            log("=== Build FAILED (exit code %d) ===" % proc.returncode)
            task_running = False
            task_done = True
            return

        log("")
        log("Build successful! Marking as configured...")

        with open("/etc/openos/configured", "w") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S"))

        version = target if not target.startswith("origin/") else "main"
        with open("/etc/openos/version", "w") as f:
            f.write(version)

        log("")
        log("=== Initial setup complete! ===")
        log("Rebooting into full OpenOS in 10 seconds...")

        subprocess.Popen(
            [BASH, "-c", "sleep 10 && systemctl reboot -i || reboot -f"],
            env=ENV_WITH_PATH)

    except Exception as e:
        log("FATAL ERROR: %s" % e)

    task_running = False
    task_done = True


SETUP_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenOS Setup</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;min-height:100vh;display:flex;align-items:center;justify-content:center}
.c{max-width:640px;width:100%;padding:2rem}
h1{font-size:1.8rem;margin-bottom:.5rem;color:#fff}
.sub{color:#888;margin-bottom:2rem}
.box{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:1rem;margin-bottom:1.5rem}
.row{display:flex;justify-content:space-between;padding:.3rem 0}
.lbl{color:#888}.val{color:#fff;font-family:monospace}
.step{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:1.5rem;margin-bottom:1rem}
.step h2{font-size:1.1rem;margin-bottom:1rem;color:#fff}
label{display:block;color:#aaa;font-size:.85rem;margin-bottom:.3rem}
input,select{width:100%;padding:.6rem;background:#0a0a0a;border:1px solid #444;border-radius:4px;color:#fff;font-size:.95rem;margin-bottom:.8rem}
input:focus,select:focus{outline:none;border-color:#f97316}
.btn{padding:.7rem 1.5rem;background:#f97316;color:#000;border:none;border-radius:6px;font-size:1rem;font-weight:600;cursor:pointer;width:100%;margin-bottom:.5rem}
.btn:hover{background:#fb923c}.btn:disabled{background:#555;color:#888;cursor:not-allowed}
.btn2{background:#333;color:#fff}.btn2:hover{background:#444}
#log{background:#000;border:1px solid #333;border-radius:4px;padding:.8rem;font-family:monospace;font-size:.8rem;height:350px;overflow-y:auto;white-space:pre-wrap;color:#aaa;display:none;margin-top:1rem}
.st{text-align:center;padding:.5rem;border-radius:4px;margin-top:.5rem;font-size:.9rem;display:none}
.st.ok{background:#052e16;color:#22c55e}.st.err{background:#2d0a0a;color:#ef4444}.st.wk{background:#1a1000;color:#f97316}
</style></head><body>
<div class="c">
<h1>OpenOS Server Setup</h1>
<p class="sub">First-boot configuration. Set up your server below.</p>
<div class="box">
<div class="row"><span class="lbl">IP</span><span class="val" id="ip">...</span></div>
<div class="row"><span class="lbl">Arch</span><span class="val" id="arch">...</span></div>
<div class="row"><span class="lbl">Memory</span><span class="val" id="mem">...</span></div>
</div>
<form id="f">
<div class="step"><h2>1. Server</h2>
<label>Hostname</label><input name="hostname" value="openos" required>
<label>Domain</label><input name="domain" value="openos.local">
<label>Timezone</label><input name="timezone" value="Europe/Berlin">
<label>Admin Password</label><input name="password" type="password" required minlength="8">
</div>
<div class="step"><h2>2. Tailscale</h2>
<label>Headscale Server URL</label><input name="headscale_url" placeholder="https://hs.example.com">
<p style="color:#666;font-size:.8rem">Leave empty to configure later.</p>
</div>
<div class="step"><h2>3. Version</h2>
<label>Repository</label><input name="repo_url" value="https://github.com/fritte-MOOD/OpenOS-Server.git">
<label>Channel</label>
<select name="channel">
<option value="stable">Stable</option>
<option value="beta">Beta</option>
<option value="nightly" selected>Nightly</option>
</select></div>
<button type="submit" class="btn" id="btn">Install OpenOS</button>
</form>
<div id="log"></div>
<div class="st" id="st"></div>
</div>
<script>
(function(){
var log=document.getElementById('log'),st=document.getElementById('st'),timer=null;
fetch('/api/info').then(function(r){return r.json()}).then(function(d){
document.getElementById('ip').textContent=d.ip||'?';
document.getElementById('arch').textContent=d.arch||'?';
document.getElementById('mem').textContent=(d.memory_gb||'?')+' GB';
});
function poll(){
var n=parseInt(log.getAttribute('data-n')||'0');
fetch('/api/task-log?from='+n).then(function(r){return r.json()}).then(function(d){
if(d.lines&&d.lines.length>0){for(var i=0;i<d.lines.length;i++){log.textContent=log.textContent+d.lines[i]+'\n';}
log.setAttribute('data-n',String(n+d.lines.length));log.scrollTop=log.scrollHeight;}
if(d.done&&d.lines.length===0){clearInterval(timer);
var last=log.textContent.trim().split('\n').pop()||'';
if(last.indexOf('complete')>=0||last.indexOf('successful')>=0){st.className='st ok';st.style.display='block';st.textContent='Setup complete! Server will reboot shortly.';}
else{st.className='st err';st.style.display='block';st.textContent='Setup failed. Check log above.';document.getElementById('btn').disabled=false;document.getElementById('btn').textContent='Retry';}}
});
}
document.getElementById('f').addEventListener('submit',function(e){
e.preventDefault();var b=document.getElementById('btn');b.disabled=true;b.textContent='Installing...';
log.style.display='block';log.textContent='';log.setAttribute('data-n','0');
st.style.display='block';st.className='st wk';st.textContent='Installing... this may take 10-30 minutes.';
var fd=new FormData(e.target);var data=Object.fromEntries(fd.entries());
fetch('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
.then(function(r){return r.json()}).then(function(r){if(r.ok)timer=setInterval(poll,2000);})
.catch(function(err){st.className='st err';st.textContent='Error: '+err.message;b.disabled=false;b.textContent='Retry';});
});
})();
</script></body></html>"""


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenOS Admin</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;min-height:100vh;padding:2rem}
.wrap{max-width:900px;margin:0 auto}
h1{font-size:1.6rem;margin-bottom:.3rem;color:#fff}
.sub{color:#888;margin-bottom:1.5rem}
.card{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:1.2rem;margin-bottom:1rem}
.card h2{font-size:1rem;color:#fff;margin-bottom:.8rem}
.row{display:flex;justify-content:space-between;padding:.25rem 0}
.lbl{color:#888}.val{color:#fff;font-family:monospace;font-size:.9rem}
.ok{color:#22c55e}.fail{color:#ef4444}.warn{color:#f97316}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{text-align:left;color:#888;padding:.4rem;border-bottom:1px solid #333}
td{padding:.4rem;border-bottom:1px solid #222}
.cur{background:#0a2a0a}
.btn{padding:.5rem 1rem;background:#f97316;color:#000;border:none;border-radius:4px;font-size:.85rem;font-weight:600;cursor:pointer;margin-right:.5rem}
.btn:hover{background:#fb923c}.btn:disabled{background:#555;color:#888;cursor:not-allowed}
.btn-sm{padding:.3rem .7rem;font-size:.8rem}
.btn-red{background:#ef4444;color:#fff}.btn-red:hover{background:#dc2626}
.btn-gray{background:#333;color:#fff}.btn-gray:hover{background:#444}
#log{background:#000;border:1px solid #333;border-radius:4px;padding:.8rem;font-family:monospace;font-size:.8rem;max-height:400px;overflow-y:auto;white-space:pre-wrap;color:#aaa;display:none;margin-top:.8rem}
.actions{margin-top:.8rem;display:flex;flex-wrap:wrap;gap:.5rem}
.badge{display:inline-block;padding:.15rem .5rem;border-radius:3px;font-size:.75rem;font-weight:600}
.badge-ok{background:#052e16;color:#22c55e}.badge-fail{background:#2d0a0a;color:#ef4444}
.badge-pending{background:#1a1000;color:#f97316}
</style></head><body>
<div class="wrap">
<h1>OpenOS Admin Panel</h1>
<p class="sub" id="version">Loading...</p>

<div class="card" id="health-card"><h2>System Health</h2><div id="health">Loading...</div></div>

<div class="card" id="ts-card"><h2>Tailscale</h2><div id="ts">Loading...</div>
<div class="actions"><button class="btn btn-sm btn-gray" onclick="setupTS()">Configure Tailscale</button></div></div>

<div class="card"><h2>NixOS Generations</h2>
<table><thead><tr><th>#</th><th>Date</th><th>Status</th><th>Actions</th></tr></thead>
<tbody id="gens"><tr><td colspan="4">Loading...</td></tr></tbody></table>
</div>

<div class="card"><h2>Update</h2>
<div class="actions">
<button class="btn" id="updateBtn" onclick="doUpdate()">Check for Updates</button>
<button class="btn btn-gray" onclick="doFetch()">Fetch Latest</button>
</div>
<div id="log"></div>
</div>

<div class="card"><h2>Terminal</h2>
<div style="background:#000;border:1px solid #333;border-radius:4px;padding:.8rem;font-family:monospace;font-size:.85rem;min-height:100px;max-height:250px;overflow-y:auto;white-space:pre-wrap;color:#0f0" id="term">$ </div>
<div style="display:flex;gap:.5rem;margin-top:.5rem">
<input type="text" id="cmd" placeholder="Enter command..." style="flex:1;padding:.5rem;background:#0a0a0a;border:1px solid #444;border-radius:4px;color:#fff;font-size:.9rem">
<button class="btn btn-sm" onclick="runCmd()">Run</button>
</div></div>
</div>

<script>
(function(){
var logEl=document.getElementById('log'),timer=null;

function load(){
fetch('/api/info').then(function(r){return r.json()}).then(function(d){
document.getElementById('version').textContent='Version: '+(d.version||'?')+' | '+d.hostname+' | '+d.arch+' | '+(d.memory_gb||'?')+' GB RAM';
});
fetch('/api/health').then(function(r){return r.json()}).then(function(d){
var h='';var svcs=d.services||{};
for(var k in svcs){h=h+'<div class="row"><span class="lbl">'+k+'</span><span class="val '+(svcs[k]?'ok':'fail')+'">'+(svcs[k]?'running':'stopped')+'</span></div>';}
if(d.pending_generation){h=h+'<div class="row"><span class="lbl">Pending</span><span class="val warn">Generation '+d.pending_generation+' (testing)</span></div>';}
document.getElementById('health').innerHTML=h;
});
fetch('/api/tailscale').then(function(r){return r.json()}).then(function(d){
var t='<div class="row"><span class="lbl">Status</span><span class="val '+(d.connected?'ok':'fail')+'">'+(d.connected?'Connected':'Disconnected')+'</span></div>';
if(d.self)t=t+'<div class="row"><span class="lbl">Name</span><span class="val">'+d.self+'</span></div>';
if(d.ips&&d.ips.length)t=t+'<div class="row"><span class="lbl">IPs</span><span class="val">'+d.ips.join(', ')+'</span></div>';
document.getElementById('ts').innerHTML=t;
});
fetch('/api/generations').then(function(r){return r.json()}).then(function(gens){
if(!gens||!gens.length||gens[0].error){document.getElementById('gens').innerHTML='<tr><td colspan="4">No generations found</td></tr>';return;}
var h='';
for(var i=0;i<gens.length;i++){var g=gens[i];
h=h+'<tr'+(g.current?' class="cur"':'')+'><td>'+g.generation+'</td><td>'+g.date+'</td>';
h=h+'<td>'+(g.current?'<span class="badge badge-ok">active</span>':'')+'</td>';
h=h+'<td>'+(g.current?'':'<button class="btn btn-sm" onclick="rollback('+g.generation+')">Activate</button>')+'</td></tr>';
}
document.getElementById('gens').innerHTML=h;
});
}
load();setInterval(load,15000);

window.rollback=function(gen){
if(!confirm('Switch to generation '+gen+'? The server will reboot.'))return;
fetch('/api/rollback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({generation:gen})})
.then(function(r){return r.json()}).then(function(d){alert(d.message||JSON.stringify(d));});
};
window.setupTS=function(){
var url=prompt('Headscale server URL:');if(!url)return;
fetch('/api/tailscale-setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({headscale_url:url})})
.then(function(r){return r.json()}).then(function(d){alert(d.message||JSON.stringify(d));load();});
};
window.doUpdate=function(){
logEl.style.display='block';logEl.textContent='Starting update...\n';logEl.setAttribute('data-n','0');
fetch('/api/safe-update',{method:'POST'}).then(function(r){return r.json()}).then(function(d){
if(d.ok)timer=setInterval(pollLog,2000);else{logEl.textContent=logEl.textContent+JSON.stringify(d)+'\n';}
});
};
window.doFetch=function(){
fetch('/api/fetch',{method:'POST'}).then(function(r){return r.json()}).then(function(d){alert(d.message||JSON.stringify(d));});
};
function pollLog(){
var n=parseInt(logEl.getAttribute('data-n')||'0');
fetch('/api/task-log?from='+n).then(function(r){return r.json()}).then(function(d){
if(d.lines&&d.lines.length>0){for(var i=0;i<d.lines.length;i++)logEl.textContent=logEl.textContent+d.lines[i]+'\n';
logEl.setAttribute('data-n',String(n+d.lines.length));logEl.scrollTop=logEl.scrollHeight;}
if(d.done&&d.lines.length===0)clearInterval(timer);
});
}
window.runCmd=function(){
var inp=document.getElementById('cmd'),out=document.getElementById('term');
var c=inp.value.trim();if(!c)return;inp.value='';
out.textContent=out.textContent+c+'\n';
fetch('/api/exec',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd:c})})
.then(function(r){return r.json()}).then(function(d){
if(d.stdout)out.textContent=out.textContent+d.stdout;
if(d.stderr)out.textContent=out.textContent+d.stderr;
out.textContent=out.textContent+'$ ';out.scrollTop=out.scrollHeight;
});
};
document.getElementById('cmd').addEventListener('keypress',function(e){if(e.key==='Enter')runCmd();});
})();
</script></body></html>"""


class AdminHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            html = SETUP_HTML if is_setup_mode() else DASHBOARD_HTML
            self._html(html)
        elif self.path == "/api/info":
            self._json(get_system_info())
        elif self.path == "/api/health":
            self._json(get_health())
        elif self.path == "/api/tailscale":
            self._json(get_tailscale_status())
        elif self.path == "/api/generations":
            self._json(get_generations())
        elif self.path.startswith("/api/task-log"):
            fr = 0
            if "from=" in self.path:
                try:
                    fr = int(self.path.split("from=")[1])
                except Exception:
                    pass
            self._json({"lines": task_log[fr:], "total": len(task_log),
                         "running": task_running, "done": task_done, "task": task_name})
        else:
            self.send_error(404)

    def do_POST(self):
        body = self._read_body()

        if self.path == "/api/setup":
            if task_running:
                self._json({"ok": False, "error": "Task already running"})
                return
            t = threading.Thread(target=run_setup, args=(body,), daemon=True)
            t.start()
            self._json({"ok": True})

        elif self.path == "/api/safe-update":
            if task_running:
                self._json({"ok": False, "error": "Task already running"})
                return
            t = threading.Thread(
                target=run_task_bg,
                args=("Safe Update", [BASH, "/etc/openos/safe-update.sh", "HEAD"]),
                daemon=True)
            t.start()
            self._json({"ok": True})

        elif self.path == "/api/fetch":
            try:
                subprocess.run(
                    [BASH, "-c", "cd %s && git fetch --all --tags" % FLAKE_DIR],
                    timeout=120, env=ENV_WITH_PATH)
                self._json({"ok": True, "message": "Repository updated."})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif self.path == "/api/rollback":
            gen = body.get("generation")
            if not gen:
                self._json({"error": "generation required"})
                return
            try:
                r = subprocess.run(
                    [BASH, "/etc/openos/rollback-to.sh", str(gen)],
                    capture_output=True, text=True, timeout=300, env=ENV_WITH_PATH)
                self._json({"ok": True, "message": "Switched to generation %s. Output: %s" % (gen, r.stdout.strip())})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif self.path == "/api/tailscale-setup":
            url = body.get("headscale_url", "")
            if not url:
                self._json({"error": "headscale_url required"})
                return
            try:
                r = subprocess.run(
                    ["tailscale", "up", "--login-server=" + url,
                     "--accept-dns", "--accept-routes", "--timeout=60s"],
                    capture_output=True, text=True, timeout=90, env=ENV_WITH_PATH)
                if r.returncode == 0:
                    self._json({"ok": True, "message": "Tailscale connected!"})
                else:
                    self._json({"ok": False, "message": "Tailscale: " + r.stderr.strip()})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif self.path == "/api/exec":
            cmd = body.get("cmd", "")
            try:
                r = subprocess.run(
                    [BASH, "-c", cmd], capture_output=True, text=True,
                    timeout=60, cwd="/root", env=ENV_WITH_PATH)
                self._json({"stdout": r.stdout, "stderr": r.stderr, "code": r.returncode})
            except subprocess.TimeoutExpired:
                self._json({"stderr": "Timeout (60s)\n", "code": -1})
            except Exception as e:
                self._json({"stderr": str(e), "code": -1})

        else:
            self.send_error(404)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, content):
        body = content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print("OpenOS Admin Panel on port %d" % PORT)
    mode = "SETUP" if is_setup_mode() else "DASHBOARD"
    print("Mode: %s" % mode)
    print("URL: http://%s/" % get_ip())
    server = http.server.HTTPServer(("0.0.0.0", PORT), AdminHandler)
    server.serve_forever()
