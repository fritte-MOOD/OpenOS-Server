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
import re
import subprocess
import socket
import threading
import time

PORT = 8080
FLAKE_DIR = os.environ.get("OPENOS_FLAKE_DIR", "/etc/openos/flake")
REPO_URL = os.environ.get("OPENOS_REPO_URL", "https://github.com/fritte-MOOD/OpenOS-Server.git")
BASH = os.environ.get("OPENOS_BASH", "/run/current-system/sw/bin/bash")
STATE_DIR = "/var/lib/openos"
NIXOS_PATH = "/run/current-system/sw/bin"
APPS_NIX = "/etc/openos/apps.nix"
REGISTRY_JSON = "/etc/openos/registry.json"

os.makedirs(STATE_DIR, exist_ok=True)

ENV_WITH_PATH = {**os.environ, "PATH": NIXOS_PATH + ":" + os.environ.get("PATH", "")}


def ensure_dns():
    """Make sure /etc/resolv.conf has working nameservers before network ops."""
    try:
        has_ns = False
        try:
            with open("/etc/resolv.conf") as f:
                for line in f:
                    if line.strip().startswith("nameserver") and not line.strip().endswith("127.0.0.53"):
                        has_ns = True
                        break
        except FileNotFoundError:
            pass
        if not has_ns:
            with open("/etc/resolv.conf", "w") as f:
                f.write("nameserver 8.8.8.8\nnameserver 1.1.1.1\n")
            subprocess.run(["systemctl", "restart", "nix-daemon"],
                           timeout=15, env=ENV_WITH_PATH)
    except Exception:
        pass

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
    return not os.path.exists("/var/lib/openos/configured")


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
    for vpath in ["/var/lib/openos/version", "/etc/openos/version"]:
        try:
            with open(vpath) as f:
                info["version"] = f.read().strip()
                break
        except Exception:
            pass
    if "version" not in info:
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


ICON_MAP = {
    "tv": "&#x1F4FA;", "cloud": "&#x2601;", "brain": "&#x1F9E0;",
    "sync": "&#x1F504;", "lock": "&#x1F512;", "git": "&#x1F33F;",
    "doc": "&#x1F4DD;", "files": "&#x1F4C1;", "music": "&#x1F3B5;",
}


def get_enabled_apps():
    """Parse /etc/openos/apps.nix and return set of enabled app names."""
    enabled = set()
    try:
        with open(APPS_NIX) as f:
            for line in f:
                m = re.search(r'openos\.apps\.(\w+)\.enable\s*=\s*true', line)
                if m:
                    enabled.add(m.group(1))
    except FileNotFoundError:
        pass
    return enabled


def write_apps_nix(enabled_set):
    """Write /etc/openos/apps.nix from a set of app names."""
    lines = ["{\n"]
    for app in sorted(enabled_set):
        lines.append("  openos.apps.%s.enable = true;\n" % app)
    lines.append("}\n")
    with open(APPS_NIX, "w") as f:
        f.writelines(lines)


def get_apps():
    """Return list of apps with metadata and enabled status."""
    enabled = get_enabled_apps()
    apps = []
    try:
        with open(REGISTRY_JSON) as f:
            registry = json.loads(f.read())
    except Exception:
        registry = {}

    hardcoded = {
        "jellyfin": {"name": "Jellyfin", "description": "Stream movies, music, and shows", "icon": "tv", "category": "media", "ports": [8096]},
        "nextcloud": {"name": "Nextcloud", "description": "File sync and collaboration", "icon": "cloud", "category": "files", "ports": [443]},
        "ollama": {"name": "Ollama + Open WebUI", "description": "Run local LLMs on your server", "icon": "brain", "category": "ai", "ports": [11434, 3000]},
        "syncthing": {"name": "Syncthing", "description": "Continuous file synchronization", "icon": "sync", "category": "files", "ports": [8384]},
        "vaultwarden": {"name": "Vaultwarden", "description": "Password manager (Bitwarden compatible)", "icon": "lock", "category": "security", "ports": [8222]},
        "gitea": {"name": "Gitea", "description": "Self-hosted Git service", "icon": "git", "category": "development", "ports": [3000]},
        "hedgedoc": {"name": "HedgeDoc", "description": "Collaborative markdown editor", "icon": "doc", "category": "tools", "ports": [3000]},
    }

    seen = set()
    for key, meta in registry.items():
        seen.add(key)
        apps.append({
            "id": key,
            "name": meta.get("name", key),
            "description": meta.get("description", ""),
            "icon": meta.get("icon", ""),
            "category": meta.get("category", "tools"),
            "ports": meta.get("ports", []),
            "enabled": key in enabled,
        })

    for key, meta in hardcoded.items():
        if key not in seen:
            apps.append({
                "id": key,
                "name": meta["name"],
                "description": meta["description"],
                "icon": meta["icon"],
                "category": meta["category"],
                "ports": meta["ports"],
                "enabled": key in enabled,
            })

    apps.sort(key=lambda a: (0 if a["enabled"] else 1, a["name"]))
    return apps


def install_app(app_id):
    """Enable an app and trigger rebuild."""
    global task_log, task_running, task_done, task_name
    task_log = []
    task_running = True
    task_done = False
    task_name = "Install %s" % app_id

    def log(msg):
        task_log.append(msg)

    log("=== Installing %s ===" % app_id)

    try:
        ensure_dns()
        enabled = get_enabled_apps()
        enabled.add(app_id)
        write_apps_nix(enabled)
        log("Updated apps.nix: %s" % ", ".join(sorted(enabled)))
        log("")
        log("Building system... (this may take several minutes)")

        arch_r = subprocess.run(["uname", "-m"], capture_output=True, text=True, env=ENV_WITH_PATH)
        arch = arch_r.stdout.strip()
        flake_target = "openos" if arch == "x86_64" else "openos-arm"

        proc = subprocess.Popen(
            [BASH, "-c",
             "nixos-rebuild switch --flake %s#%s --impure 2>&1" % (FLAKE_DIR, flake_target)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=ENV_WITH_PATH
        )
        for line in iter(proc.stdout.readline, ""):
            log(line.rstrip())
        proc.wait()

        if proc.returncode == 0:
            log("")
            log("=== %s installed successfully ===" % app_id)
        else:
            log("")
            log("=== Install FAILED (exit code %d) ===" % proc.returncode)
            log("Rolling back apps.nix...")
            enabled.discard(app_id)
            write_apps_nix(enabled)
    except Exception as e:
        log("ERROR: %s" % e)

    task_running = False
    task_done = True


def uninstall_app(app_id):
    """Disable an app and trigger rebuild."""
    global task_log, task_running, task_done, task_name
    task_log = []
    task_running = True
    task_done = False
    task_name = "Uninstall %s" % app_id

    def log(msg):
        task_log.append(msg)

    log("=== Uninstalling %s ===" % app_id)

    try:
        ensure_dns()
        enabled = get_enabled_apps()
        enabled.discard(app_id)
        write_apps_nix(enabled)
        log("Updated apps.nix: %s" % (", ".join(sorted(enabled)) or "(none)"))
        log("")
        log("Rebuilding system...")

        arch_r = subprocess.run(["uname", "-m"], capture_output=True, text=True, env=ENV_WITH_PATH)
        arch = arch_r.stdout.strip()
        flake_target = "openos" if arch == "x86_64" else "openos-arm"

        proc = subprocess.Popen(
            [BASH, "-c",
             "nixos-rebuild switch --flake %s#%s --impure 2>&1" % (FLAKE_DIR, flake_target)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=ENV_WITH_PATH
        )
        for line in iter(proc.stdout.readline, ""):
            log(line.rstrip())
        proc.wait()

        if proc.returncode == 0:
            log("")
            log("=== %s uninstalled successfully ===" % app_id)
        else:
            log("")
            log("=== Uninstall FAILED (exit code %d) ===" % proc.returncode)
    except Exception as e:
        log("ERROR: %s" % e)

    task_running = False
    task_done = True


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
        ensure_dns()
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

        os.makedirs("/var/lib/openos", exist_ok=True)
        with open("/var/lib/openos/configured", "w") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S"))

        version = target if not target.startswith("origin/") else "main"
        with open("/var/lib/openos/version", "w") as f:
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
<label>Headscale Server URL</label><input name="headscale_url" value="https://tuktuk.redirectme.net">
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
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;min-height:100vh}
nav{background:#111;border-bottom:1px solid #333;padding:0 2rem;display:flex;align-items:center;height:56px;position:sticky;top:0;z-index:10}
nav h1{font-size:1.1rem;color:#fff;margin-right:2rem}
nav a{color:#888;text-decoration:none;padding:.5rem .8rem;font-size:.9rem;border-radius:4px;margin-right:.25rem}
nav a:hover{color:#fff;background:#222}
nav a.active{color:#f97316;background:#1a1000}
.page{display:none;max-width:960px;margin:0 auto;padding:1.5rem 2rem}
.page.show{display:block}
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
.actions{margin-top:.8rem;display:flex;flex-wrap:wrap;gap:.5rem}
.badge{display:inline-block;padding:.15rem .5rem;border-radius:3px;font-size:.75rem;font-weight:600}
.badge-ok{background:#052e16;color:#22c55e}.badge-fail{background:#2d0a0a;color:#ef4444}
.badge-pending{background:#1a1000;color:#f97316}
.app-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:.8rem}
.app-card{background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:1rem;display:flex;flex-direction:column;transition:border-color .2s}
.app-card:hover{border-color:#444}
.app-card.installed{border-color:#22c55e40}
.app-top{display:flex;align-items:center;gap:.7rem;margin-bottom:.6rem}
.app-icon{font-size:1.5rem;width:36px;text-align:center}
.app-name{font-size:.95rem;font-weight:600;color:#fff}
.app-cat{font-size:.7rem;color:#666;text-transform:uppercase;letter-spacing:.5px}
.app-desc{font-size:.8rem;color:#999;flex:1;margin-bottom:.8rem;line-height:1.4}
.app-bottom{display:flex;align-items:center;justify-content:space-between}
.app-ports{font-size:.7rem;color:#555;font-family:monospace}
.app-btn{padding:.4rem .9rem;border:none;border-radius:4px;font-size:.8rem;font-weight:600;cursor:pointer}
.app-btn.install{background:#f97316;color:#000}
.app-btn.install:hover{background:#fb923c}
.app-btn.remove{background:#2a2a2a;color:#ef4444;border:1px solid #ef444440}
.app-btn.remove:hover{background:#3a1a1a}
.app-btn:disabled{background:#333;color:#666;cursor:not-allowed;border:none}
#buildlog{background:#000;border:1px solid #333;border-radius:8px;padding:1rem;font-family:monospace;font-size:.8rem;max-height:400px;overflow-y:auto;white-space:pre-wrap;color:#aaa;display:none;margin-bottom:1rem}
#buildstatus{text-align:center;padding:.6rem;border-radius:6px;font-size:.9rem;font-weight:600;display:none;margin-bottom:1rem}
#buildstatus.working{background:#1a1000;color:#f97316}
#buildstatus.success{background:#052e16;color:#22c55e}
#buildstatus.error{background:#2d0a0a;color:#ef4444}
.log-box{background:#000;border:1px solid #333;border-radius:4px;padding:.8rem;font-family:monospace;font-size:.8rem;max-height:400px;overflow-y:auto;white-space:pre-wrap;color:#aaa;display:none;margin-top:.8rem}
</style></head><body>
<nav>
<h1>OpenOS</h1>
<a href="#dashboard" class="active" onclick="showPage('dashboard',this)">Dashboard</a>
<a href="#apps" onclick="showPage('apps',this)">Apps</a>
<a href="#system" onclick="showPage('system',this)">System</a>
</nav>

<div class="page show" id="p-dashboard">
<p class="val" id="version" style="color:#888;margin-bottom:1rem;font-size:.85rem">Loading...</p>
<div class="card"><h2>System Health</h2><div id="health">Loading...</div></div>
<div class="card"><h2>Tailscale</h2><div id="ts">Loading...</div>
<div class="actions"><button class="btn btn-sm btn-gray" onclick="setupTS()">Configure Tailscale</button></div></div>
<div class="card"><h2>Installed Apps</h2><div id="installed-apps" style="color:#888;font-size:.9rem">Loading...</div></div>
</div>

<div class="page" id="p-apps">
<div id="buildstatus"></div>
<div id="buildlog"></div>
<div class="app-grid" id="app-grid">
<div style="color:#888">Loading apps...</div>
</div>
</div>

<div class="page" id="p-system">
<div class="card"><h2>NixOS Generations</h2>
<table><thead><tr><th>#</th><th>Date</th><th>Status</th><th>Actions</th></tr></thead>
<tbody id="gens"><tr><td colspan="4">Loading...</td></tr></tbody></table>
</div>
<div class="card"><h2>Update</h2>
<p style="color:#888;font-size:.8rem;margin-bottom:.8rem">Pull the latest version from GitHub and apply it live — no reboot needed.</p>
<div class="actions">
<button class="btn" onclick="doApply()">Update &amp; Apply Now</button>
<button class="btn btn-gray" onclick="doFetch()">Fetch Only</button>
<button class="btn btn-gray" onclick="doUpdate()">Safe Update (reboot)</button>
</div>
<div class="log-box" id="updatelog"></div>
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
var icons={"tv":"\uD83D\uDCFA","cloud":"\u2601\uFE0F","brain":"\uD83E\uDDE0","sync":"\uD83D\uDD04","lock":"\uD83D\uDD12","git":"\uD83C\uDF3F","doc":"\uD83D\uDCDD","files":"\uD83D\uDCC1","music":"\uD83C\uDFB5"};
var appTimer=null,curApps=[];

window.showPage=function(id,el){
var pages=document.querySelectorAll('.page');
for(var i=0;i<pages.length;i++)pages[i].className='page';
document.getElementById('p-'+id).className='page show';
var links=document.querySelectorAll('nav a');
for(var i=0;i<links.length;i++)links[i].className='';
if(el)el.className='active';
if(id==='apps')loadApps();
};

function load(){
fetch('/api/info').then(function(r){return r.json()}).then(function(d){
document.getElementById('version').textContent='v'+(d.version||'?')+' | '+d.hostname+' | '+d.arch+' | '+(d.memory_gb||'?')+' GB RAM';
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
fetch('/api/apps').then(function(r){return r.json()}).then(function(apps){
var inst=apps.filter(function(a){return a.enabled;});
var el=document.getElementById('installed-apps');
if(!inst.length){el.innerHTML='No apps installed yet. <a href="#apps" onclick="showPage(\'apps\',document.querySelectorAll(\'nav a\')[1])" style="color:#f97316;text-decoration:underline">Browse apps</a>';return;}
var h='';
for(var i=0;i<inst.length;i++){var a=inst[i];
h=h+'<div class="row"><span class="lbl">'+(icons[a.icon]||'\u2699\uFE0F')+' '+a.name+'</span><span class="val ok">running</span></div>';
}
el.innerHTML=h;
});
}
load();setInterval(load,15000);

function loadApps(){
fetch('/api/apps').then(function(r){return r.json()}).then(function(apps){
curApps=apps;renderApps();
});
}

function renderApps(){
var grid=document.getElementById('app-grid');
if(!curApps.length){grid.innerHTML='<div style="color:#888">No apps available.</div>';return;}
var h='';
for(var i=0;i<curApps.length;i++){
var a=curApps[i];
var ic=icons[a.icon]||'\u2699\uFE0F';
var ports=a.ports&&a.ports.length?'Port '+ a.ports.join(', '):'';
h=h+'<div class="app-card'+(a.enabled?' installed':'')+'">';
h=h+'<div class="app-top"><span class="app-icon">'+ic+'</span><div><div class="app-name">'+a.name+'</div><div class="app-cat">'+a.category+'</div></div></div>';
h=h+'<div class="app-desc">'+a.description+'</div>';
h=h+'<div class="app-bottom"><span class="app-ports">'+ports+'</span>';
if(a.enabled){
h=h+'<button class="app-btn remove" onclick="appAction(\'uninstall\',\''+a.id+'\')">Remove</button>';
}else{
h=h+'<button class="app-btn install" onclick="appAction(\'install\',\''+a.id+'\')">Install</button>';
}
h=h+'</div></div>';
}
grid.innerHTML=h;
}

window.appAction=function(action,appId){
var label=action==='install'?'Install':'Remove';
if(action==='uninstall'&&!confirm('Remove '+appId+'? This will rebuild the system.'))return;
var log=document.getElementById('buildlog');
var st=document.getElementById('buildstatus');
log.style.display='block';log.textContent='';log.setAttribute('data-n','0');
st.style.display='block';st.className='working';st.textContent=(action==='install'?'Installing':'Removing')+' '+appId+'...';
var btns=document.querySelectorAll('.app-btn');
for(var i=0;i<btns.length;i++)btns[i].disabled=true;
fetch('/api/apps/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({app:appId})})
.then(function(r){return r.json()}).then(function(d){
if(d.ok){appTimer=setInterval(pollAppLog,2000);}
else{st.className='error';st.textContent='Error: '+(d.error||'unknown');enableAppBtns();}
}).catch(function(e){st.className='error';st.textContent='Error: '+e.message;enableAppBtns();});
};

function enableAppBtns(){var btns=document.querySelectorAll('.app-btn');for(var i=0;i<btns.length;i++)btns[i].disabled=false;}

function pollAppLog(){
var log=document.getElementById('buildlog');
var st=document.getElementById('buildstatus');
var n=parseInt(log.getAttribute('data-n')||'0');
fetch('/api/task-log?from='+n).then(function(r){return r.json()}).then(function(d){
if(d.lines&&d.lines.length>0){
for(var i=0;i<d.lines.length;i++)log.textContent=log.textContent+d.lines[i]+'\n';
log.setAttribute('data-n',String(n+d.lines.length));log.scrollTop=log.scrollHeight;
}
if(d.done&&d.lines.length===0){
clearInterval(appTimer);
var last=log.textContent.trim().split('\n').pop()||'';
if(last.indexOf('successfully')>=0){st.className='success';st.textContent='Done!';}
else{st.className='error';st.textContent='Failed. Check the log above.';}
enableAppBtns();
loadApps();
}
});
}

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
window.doApply=function(){
var logEl=document.getElementById('updatelog');
logEl.style.display='block';logEl.textContent='Pulling and applying...\n';logEl.setAttribute('data-n','0');
fetch('/api/apply',{method:'POST'}).then(function(r){return r.json()}).then(function(d){
if(d.ok){var t=setInterval(function(){
var nn=parseInt(logEl.getAttribute('data-n')||'0');
fetch('/api/task-log?from='+nn).then(function(r){return r.json()}).then(function(dd){
if(dd.lines&&dd.lines.length>0){for(var i=0;i<dd.lines.length;i++)logEl.textContent=logEl.textContent+dd.lines[i]+'\n';
logEl.setAttribute('data-n',String(nn+dd.lines.length));logEl.scrollTop=logEl.scrollHeight;}
if(dd.done&&dd.lines.length===0)clearInterval(t);
});
},2000);}else{logEl.textContent=logEl.textContent+(d.error||JSON.stringify(d))+'\n';}
});
};
window.doUpdate=function(){
var logEl=document.getElementById('updatelog');
logEl.style.display='block';logEl.textContent='Starting update...\n';logEl.setAttribute('data-n','0');
fetch('/api/safe-update',{method:'POST'}).then(function(r){return r.json()}).then(function(d){
if(d.ok){var t=setInterval(function(){
var nn=parseInt(logEl.getAttribute('data-n')||'0');
fetch('/api/task-log?from='+nn).then(function(r){return r.json()}).then(function(dd){
if(dd.lines&&dd.lines.length>0){for(var i=0;i<dd.lines.length;i++)logEl.textContent=logEl.textContent+dd.lines[i]+'\n';
logEl.setAttribute('data-n',String(nn+dd.lines.length));logEl.scrollTop=logEl.scrollHeight;}
if(dd.done&&dd.lines.length===0)clearInterval(t);
});
},2000);}else{logEl.textContent=logEl.textContent+JSON.stringify(d)+'\n';}
});
};
window.doFetch=function(){
fetch('/api/fetch',{method:'POST'}).then(function(r){return r.json()}).then(function(d){alert(d.message||JSON.stringify(d));});
};
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

if(location.hash==='#apps')showPage('apps',document.querySelectorAll('nav a')[1]);
else if(location.hash==='#system')showPage('system',document.querySelectorAll('nav a')[2]);
})();
</script></body></html>"""


class AdminHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/dashboard", "/apps", "/system"):
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
        elif self.path == "/api/apps":
            self._json(get_apps())
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
            ensure_dns()
            t = threading.Thread(
                target=run_task_bg,
                args=("Safe Update", [BASH, "/etc/openos/safe-update.sh", "HEAD"]),
                daemon=True)
            t.start()
            self._json({"ok": True})

        elif self.path == "/api/fetch":
            try:
                ensure_dns()
                subprocess.run(
                    [BASH, "-c", "cd %s && git pull origin main && git fetch --tags" % FLAKE_DIR],
                    timeout=120, env=ENV_WITH_PATH)
                self._json({"ok": True, "message": "Repository updated."})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif self.path == "/api/apply":
            if task_running:
                self._json({"ok": False, "error": "Task already running"})
                return

            def apply_now():
                global task_log, task_running, task_done, task_name
                task_log = []
                task_running = True
                task_done = False
                task_name = "Apply Update"

                def log(msg):
                    task_log.append(msg)

                log("=== Applying update (live, no reboot) ===")
                try:
                    ensure_dns()
                    log("Pulling latest from GitHub...")
                    r = subprocess.run(
                        [BASH, "-c", "cd %s && git pull origin main && git fetch --tags" % FLAKE_DIR],
                        capture_output=True, text=True, timeout=120, env=ENV_WITH_PATH)
                    if r.stdout.strip():
                        log(r.stdout.strip())

                    arch_r = subprocess.run(["uname", "-m"], capture_output=True, text=True, env=ENV_WITH_PATH)
                    arch = arch_r.stdout.strip()
                    flake_target = "openos" if arch == "x86_64" else "openos-arm"

                    log("")
                    log("Building and switching... (this may take a few minutes)")
                    proc = subprocess.Popen(
                        [BASH, "-c",
                         "nixos-rebuild switch --flake %s#%s --impure 2>&1" % (FLAKE_DIR, flake_target)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1, env=ENV_WITH_PATH
                    )
                    for line in iter(proc.stdout.readline, ""):
                        log(line.rstrip())
                    proc.wait()

                    if proc.returncode == 0:
                        log("")
                        log("=== Update applied successfully ===")
                        log("Refresh the page to see changes.")
                    else:
                        log("")
                        log("=== Update FAILED (exit code %d) ===" % proc.returncode)
                except Exception as e:
                    log("ERROR: %s" % e)

                task_running = False
                task_done = True

            t = threading.Thread(target=apply_now, daemon=True)
            t.start()
            self._json({"ok": True})

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

        elif self.path == "/api/apps/install":
            app_id = body.get("app", "")
            if not app_id or not re.match(r'^[a-z][a-z0-9_-]*$', app_id):
                self._json({"ok": False, "error": "Invalid app id"})
                return
            if task_running:
                self._json({"ok": False, "error": "Task already running"})
                return
            t = threading.Thread(target=install_app, args=(app_id,), daemon=True)
            t.start()
            self._json({"ok": True})

        elif self.path == "/api/apps/uninstall":
            app_id = body.get("app", "")
            if not app_id or not re.match(r'^[a-z][a-z0-9_-]*$', app_id):
                self._json({"ok": False, "error": "Invalid app id"})
                return
            if task_running:
                self._json({"ok": False, "error": "Task already running"})
                return
            t = threading.Thread(target=uninstall_app, args=(app_id,), daemon=True)
            t.start()
            self._json({"ok": True})

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
