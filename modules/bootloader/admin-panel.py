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
MOUNTS_NIX = "/etc/openos/mounts.nix"
REGISTRY_JSON = "/etc/openos/registry.json"
DATA_DIR = "/data"

os.makedirs(STATE_DIR, exist_ok=True)

ENV_WITH_PATH = {**os.environ, "PATH": NIXOS_PATH + ":" + os.environ.get("PATH", "")}


def ensure_dns():
    """Make sure DNS resolution works before network ops.

    Tests resolution first; only intervenes if DNS is broken.
    /etc/resolv.conf is normally managed by NixOS (static nameservers),
    but we fix it at runtime if something overwrites it.
    """
    try:
        try:
            socket.setdefaulttimeout(5)
            socket.getaddrinfo("cache.nixos.org", 443, socket.AF_INET, socket.SOCK_STREAM)
            return
        except Exception:
            pass

        with open("/etc/resolv.conf", "w") as f:
            f.write("nameserver 8.8.8.8\nnameserver 1.1.1.1\n")

        subprocess.run(["systemctl", "restart", "nix-daemon"],
                       timeout=15, env=ENV_WITH_PATH, capture_output=True)
        time.sleep(2)

        try:
            socket.getaddrinfo("cache.nixos.org", 443, socket.AF_INET, socket.SOCK_STREAM)
        except Exception:
            subprocess.run(["systemctl", "restart", "nix-daemon"],
                           timeout=15, env=ENV_WITH_PATH, capture_output=True)
            time.sleep(3)
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


def get_storage():
    """Block devices, partitions, mounts, and usage."""
    disks = []
    try:
        r = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL,ROTA,RM"],
            capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        data = json.loads(r.stdout)
        for dev in data.get("blockdevices", []):
            if dev.get("type") not in ("disk", "loop"):
                continue
            if dev.get("name", "").startswith("loop"):
                continue
            disk = {
                "name": dev.get("name", ""),
                "size": dev.get("size", 0),
                "model": (dev.get("model") or "").strip(),
                "serial": (dev.get("serial") or "").strip(),
                "rotational": dev.get("rota", False),
                "removable": dev.get("rm", False),
                "partitions": [],
            }
            for child in dev.get("children", []):
                part = {
                    "name": child.get("name", ""),
                    "size": child.get("size", 0),
                    "fstype": child.get("fstype") or "",
                    "mountpoint": child.get("mountpoint") or "",
                }
                if part["mountpoint"]:
                    try:
                        st = os.statvfs(part["mountpoint"])
                        part["total"] = st.f_frsize * st.f_blocks
                        part["used"] = st.f_frsize * (st.f_blocks - st.f_bfree)
                        part["avail"] = st.f_frsize * st.f_bavail
                    except Exception:
                        pass
                disk["partitions"].append(part)
            if not disk["partitions"] and dev.get("mountpoint"):
                mp = dev.get("mountpoint")
                part = {"name": dev["name"], "size": dev.get("size", 0),
                        "fstype": dev.get("fstype") or "", "mountpoint": mp}
                try:
                    st = os.statvfs(mp)
                    part["total"] = st.f_frsize * st.f_blocks
                    part["used"] = st.f_frsize * (st.f_blocks - st.f_bfree)
                    part["avail"] = st.f_frsize * st.f_bavail
                except Exception:
                    pass
                disk["partitions"].append(part)
            disks.append(disk)
    except Exception as e:
        return {"error": str(e), "disks": []}
    return {"disks": disks}


def get_storage_health():
    """SMART health status for all disks."""
    results = []
    try:
        r = subprocess.run(
            ["lsblk", "-d", "-n", "-o", "NAME,TYPE"],
            capture_output=True, text=True, timeout=5, env=ENV_WITH_PATH)
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[1] != "disk":
                continue
            name = parts[0]
            info = {"name": name, "healthy": None, "temperature": None, "details": ""}
            try:
                sr = subprocess.run(
                    ["smartctl", "-H", "-A", "-j", "/dev/" + name],
                    capture_output=True, text=True, timeout=15, env=ENV_WITH_PATH)
                sdata = json.loads(sr.stdout)
                smart_status = sdata.get("smart_status", {})
                info["healthy"] = smart_status.get("passed", None)
                temp = sdata.get("temperature", {})
                if temp.get("current"):
                    info["temperature"] = temp["current"]
                attrs = sdata.get("ata_smart_attributes", {}).get("table", [])
                for attr in attrs:
                    if attr.get("name") == "Reallocated_Sector_Ct":
                        info["reallocated"] = attr.get("raw", {}).get("value", 0)
                    elif attr.get("name") == "Power_On_Hours":
                        info["power_on_hours"] = attr.get("raw", {}).get("value", 0)
            except Exception:
                info["details"] = "smartctl not available or disk does not support SMART"
            results.append(info)
    except Exception as e:
        return {"error": str(e), "disks": []}
    return {"disks": results}


def get_storage_usage():
    """Per-app and per-directory storage usage under /data."""
    usage = {"total": 0, "used": 0, "avail": 0, "apps": {}, "backups": 0, "shared": 0}
    try:
        st = os.statvfs(DATA_DIR)
        usage["total"] = st.f_frsize * st.f_blocks
        usage["used"] = st.f_frsize * (st.f_blocks - st.f_bfree)
        usage["avail"] = st.f_frsize * st.f_bavail
    except Exception:
        pass

    apps_dir = os.path.join(DATA_DIR, "apps")
    if os.path.isdir(apps_dir):
        for app in os.listdir(apps_dir):
            app_path = os.path.join(apps_dir, app)
            if os.path.isdir(app_path):
                try:
                    r = subprocess.run(
                        ["du", "-sb", app_path],
                        capture_output=True, text=True, timeout=30, env=ENV_WITH_PATH)
                    usage["apps"][app] = int(r.stdout.split()[0])
                except Exception:
                    usage["apps"][app] = 0

    for subdir in ["backups", "shared"]:
        path = os.path.join(DATA_DIR, subdir)
        if os.path.isdir(path):
            try:
                r = subprocess.run(
                    ["du", "-sb", path],
                    capture_output=True, text=True, timeout=30, env=ENV_WITH_PATH)
                usage[subdir] = int(r.stdout.split()[0])
            except Exception:
                pass

    return usage


def get_backup_status():
    """3-2-1 backup status check."""
    status = {
        "copy1_ok": False, "copy1_label": "Original data",
        "copy2_ok": False, "copy2_label": "Local backup disk",
        "copy3_ok": False, "copy3_label": "Offsite (not configured)",
    }
    if os.path.isdir(DATA_DIR):
        status["copy1_ok"] = True

    backup_dir = os.path.join(DATA_DIR, "backups", "daily")
    if os.path.isdir(backup_dir):
        try:
            files = sorted(os.listdir(backup_dir), reverse=True)
            sql_files = [f for f in files if f.startswith("postgres_") and f.endswith(".sql")]
            if sql_files:
                status["last_backup"] = sql_files[0]
                fname = sql_files[0].replace("postgres_", "").replace(".sql", "")
                try:
                    ts = time.strptime(fname, "%Y%m%d_%H%M%S")
                    age_hours = (time.time() - time.mktime(ts)) / 3600
                    status["backup_age_hours"] = round(age_hours, 1)
                except Exception:
                    pass
        except Exception:
            pass

    mounts = get_configured_mounts()
    for m in mounts:
        if m.get("role") == "backup":
            mp = m.get("mountpoint", "")
            if mp and os.path.ismount(mp):
                status["copy2_ok"] = True
                status["copy2_label"] = "Backup: %s" % mp

    return status


def get_configured_mounts():
    """Read /etc/openos/mounts.nix and return list of configured extra mounts."""
    mounts = []
    try:
        with open(MOUNTS_NIX) as f:
            content = f.read()
        for m in re.finditer(
            r'"([^"]+)"\s*=\s*\{\s*device\s*=\s*"([^"]+)";\s*fsType\s*=\s*"([^"]+)";\s*(?:options\s*=\s*\[([^\]]*)\];\s*)?(?:role\s*=\s*"([^"]*)";\s*)?',
            content
        ):
            mounts.append({
                "mountpoint": m.group(1),
                "device": m.group(2),
                "fsType": m.group(3),
                "options": [o.strip().strip('"') for o in (m.group(4) or "").split() if o.strip()],
                "role": m.group(5) or "data",
            })
    except FileNotFoundError:
        pass
    return mounts


def write_mounts_nix(mounts):
    """Write /etc/openos/mounts.nix from a list of mount dicts."""
    lines = ["{\n", "  fileSystems = {\n"]
    for m in mounts:
        opts = ""
        if m.get("options"):
            opts = '      options = [ %s ];\n' % " ".join('"%s"' % o for o in m["options"])
        lines.append('    "%s" = {\n' % m["mountpoint"])
        lines.append('      device = "%s";\n' % m["device"])
        lines.append('      fsType = "%s";\n' % m["fsType"])
        if opts:
            lines.append(opts)
        lines.append("    };\n")
    lines.append("  };\n")
    lines.append("}\n")
    with open(MOUNTS_NIX, "w") as f:
        f.writelines(lines)


def get_unmounted_partitions():
    """Find partitions that are not currently mounted (candidates for mounting)."""
    candidates = []
    try:
        r = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL"],
            capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        data = json.loads(r.stdout)
        for dev in data.get("blockdevices", []):
            children = dev.get("children", [])
            if not children:
                if dev.get("type") == "disk" and not dev.get("mountpoint") and dev.get("fstype"):
                    candidates.append({
                        "device": "/dev/" + dev["name"],
                        "size": dev.get("size", 0),
                        "fstype": dev.get("fstype", ""),
                        "model": (dev.get("model") or "").strip(),
                    })
                continue
            for child in children:
                if child.get("type") == "part" and not child.get("mountpoint") and child.get("fstype"):
                    candidates.append({
                        "device": "/dev/" + child["name"],
                        "size": child.get("size", 0),
                        "fstype": child.get("fstype", ""),
                        "model": (dev.get("model") or "").strip(),
                    })
    except Exception:
        pass
    return candidates


# ==================== ZFS POOL MANAGEMENT ====================

def get_available_disks():
    """Find whole disks not used by the system (no mounted partitions, not the boot disk)."""
    available = []
    try:
        r = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,SERIAL,ROTA,RM"],
            capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        data = json.loads(r.stdout)
        for dev in data.get("blockdevices", []):
            if dev.get("type") != "disk":
                continue
            name = dev.get("name", "")
            if name.startswith("loop") or name.startswith("sr") or name.startswith("fd"):
                continue
            in_use = False
            children = dev.get("children", [])
            if children:
                for child in children:
                    mp = child.get("mountpoint") or ""
                    if mp in ("/", "/boot", "/nix", "/nix/store"):
                        in_use = True
                        break
            else:
                mp = dev.get("mountpoint") or ""
                if mp in ("/", "/boot", "/nix", "/nix/store"):
                    in_use = True
            if in_use:
                continue
            # Check if disk is part of an existing zpool
            try:
                zr = subprocess.run(
                    ["zpool", "status", "-P"],
                    capture_output=True, text=True, timeout=5, env=ENV_WITH_PATH)
                if "/dev/" + name in zr.stdout:
                    in_use = True
            except Exception:
                pass
            if in_use:
                continue
            available.append({
                "device": "/dev/" + name,
                "name": name,
                "size": dev.get("size", 0),
                "model": (dev.get("model") or "").strip(),
                "serial": (dev.get("serial") or "").strip(),
                "rotational": dev.get("rota", False),
                "removable": dev.get("rm", False),
                "has_partitions": len(children) > 0,
            })
    except Exception:
        pass
    return available


def get_zfs_pools():
    """Get status of all ZFS pools."""
    pools = []
    try:
        r = subprocess.run(
            ["zpool", "list", "-H", "-o", "name,size,alloc,free,health,fragmentation,capacity"],
            capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        if r.returncode != 0:
            return pools
        for line in r.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 7:
                pools.append({
                    "name": parts[0],
                    "size": parts[1],
                    "allocated": parts[2],
                    "free": parts[3],
                    "health": parts[4],
                    "fragmentation": parts[5],
                    "capacity_pct": parts[6].rstrip("%"),
                })
    except Exception:
        pass

    for pool in pools:
        try:
            r = subprocess.run(
                ["zpool", "status", pool["name"]],
                capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
            pool["status_detail"] = r.stdout.strip()
            if "raidz1" in r.stdout:
                pool["type"] = "raidz1"
            elif "raidz2" in r.stdout:
                pool["type"] = "raidz2"
            elif "mirror" in r.stdout:
                pool["type"] = "mirror"
            else:
                pool["type"] = "stripe"
            disks = []
            for sline in r.stdout.splitlines():
                sline = sline.strip()
                if sline.startswith("/dev/") or (sline and sline.split()[0] in
                    [d for d in os.listdir("/dev") if d.startswith("sd") or d.startswith("nvme")]):
                    disks.append(sline.split()[0])
            pool["disks"] = disks
            pool["disk_count"] = len(disks)
        except Exception:
            pool["type"] = "unknown"
            pool["disks"] = []
            pool["disk_count"] = 0

    return pools


def get_zfs_datasets(pool_name=None):
    """List ZFS datasets with usage info."""
    datasets = []
    try:
        cmd = ["zfs", "list", "-H", "-o", "name,used,avail,refer,mountpoint,quota,compression"]
        if pool_name:
            cmd.append(pool_name)
            cmd.append("-r")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        if r.returncode != 0:
            return datasets
        for line in r.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 7:
                datasets.append({
                    "name": parts[0],
                    "used": parts[1],
                    "available": parts[2],
                    "referenced": parts[3],
                    "mountpoint": parts[4],
                    "quota": parts[5] if parts[5] != "none" else None,
                    "compression": parts[6],
                })
    except Exception:
        pass
    return datasets


def get_importable_pools():
    """Find ZFS pools that exist on disks but are not currently imported."""
    importable = []
    try:
        r = subprocess.run(
            ["zpool", "import"],
            capture_output=True, text=True, timeout=15, env=ENV_WITH_PATH)
        current_pool = None
        for line in r.stdout.splitlines():
            line_stripped = line.strip()
            if line_stripped.startswith("pool:"):
                current_pool = {"name": line_stripped.split(":", 1)[1].strip(), "state": "", "disks": []}
            elif current_pool and line_stripped.startswith("state:"):
                current_pool["state"] = line_stripped.split(":", 1)[1].strip()
            elif current_pool and line_stripped.startswith("config:"):
                pass
            elif current_pool and (line_stripped.startswith("/dev/") or
                  (line_stripped and line_stripped.split()[0].startswith("sd") or
                   line_stripped.split()[0].startswith("nvme"))):
                current_pool["disks"].append(line_stripped.split()[0])
            elif current_pool and line_stripped == "" and current_pool.get("name"):
                importable.append(current_pool)
                current_pool = None
        if current_pool and current_pool.get("name"):
            importable.append(current_pool)
    except Exception:
        pass
    return importable


def import_zfs_pool(pool_name, force=False):
    """Import an existing ZFS pool."""
    global task_log, task_running, task_done, task_name
    task_log = []
    task_running = True
    task_done = False
    task_name = "Import ZFS Pool '%s'" % pool_name

    def log(msg):
        task_log.append(msg)

    log("=== Importing ZFS Pool: %s ===" % pool_name)

    try:
        cmd = ["zpool", "import"]
        if force:
            cmd.append("-f")
        cmd.append(pool_name)

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=ENV_WITH_PATH)
        for line in iter(proc.stdout.readline, ""):
            log(line.rstrip())
        proc.wait()

        if proc.returncode != 0:
            log("")
            log("Retrying with -f (force)...")
            proc2 = subprocess.Popen(
                ["zpool", "import", "-f", pool_name],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=ENV_WITH_PATH)
            for line in iter(proc2.stdout.readline, ""):
                log(line.rstrip())
            proc2.wait()
            if proc2.returncode != 0:
                log("")
                log("=== Import FAILED ===" )
                task_running = False
                task_done = True
                return

        log("")
        log("Pool imported! Status:")
        r = subprocess.run(["zpool", "status", pool_name],
                           capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        for line in r.stdout.splitlines():
            log("  " + line)

        log("")
        r2 = subprocess.run(["zfs", "list", "-r", pool_name],
                            capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        log("Datasets:")
        for line in r2.stdout.splitlines():
            log("  " + line)

        log("")
        log("=== ZFS Pool '%s' imported successfully ===" % pool_name)

    except Exception as e:
        log("ERROR: %s" % e)

    task_running = False
    task_done = True


def create_zfs_pool(pool_name, disks, raid_type="raidz1"):
    """Create a ZFS pool with given disks and RAID type."""
    global task_log, task_running, task_done, task_name
    task_log = []
    task_running = True
    task_done = False
    task_name = "Create ZFS Pool '%s'" % pool_name

    def log(msg):
        task_log.append(msg)

    log("=== Creating ZFS Pool: %s (%s) ===" % (pool_name, raid_type))
    log("Disks: %s" % ", ".join(disks))
    log("")

    try:
        # Validate inputs
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*$', pool_name):
            log("ERROR: Invalid pool name. Use letters, numbers, hyphens, underscores.")
            task_running = False
            task_done = True
            return

        if raid_type == "raidz1" and len(disks) < 3:
            log("ERROR: RAIDZ1 requires at least 3 disks (got %d)." % len(disks))
            task_running = False
            task_done = True
            return
        elif raid_type == "raidz2" and len(disks) < 4:
            log("ERROR: RAIDZ2 requires at least 4 disks (got %d)." % len(disks))
            task_running = False
            task_done = True
            return
        elif raid_type == "mirror" and len(disks) < 2:
            log("ERROR: Mirror requires at least 2 disks (got %d)." % len(disks))
            task_running = False
            task_done = True
            return

        # Wipe partition tables
        for disk in disks:
            log("Wiping partition table on %s..." % disk)
            subprocess.run(
                ["wipefs", "--all", "--force", disk],
                capture_output=True, timeout=30, env=ENV_WITH_PATH)
            subprocess.run(
                ["sgdisk", "--zap-all", disk],
                capture_output=True, timeout=30, env=ENV_WITH_PATH)

        log("")
        log("Creating pool...")

        # Build zpool create command
        cmd = ["zpool", "create", "-f",
               "-o", "ashift=12",
               "-O", "atime=off",
               "-O", "compression=lz4",
               "-O", "xattr=sa",
               "-O", "acltype=posixacl",
               "-O", "mountpoint=/data",
               pool_name, raid_type] + disks

        if raid_type == "stripe":
            cmd = ["zpool", "create", "-f",
                   "-o", "ashift=12",
                   "-O", "atime=off",
                   "-O", "compression=lz4",
                   "-O", "xattr=sa",
                   "-O", "acltype=posixacl",
                   "-O", "mountpoint=/data",
                   pool_name] + disks

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=ENV_WITH_PATH)
        for line in iter(proc.stdout.readline, ""):
            log(line.rstrip())
        proc.wait()

        if proc.returncode != 0:
            log("")
            log("=== Pool creation FAILED (exit code %d) ===" % proc.returncode)
            task_running = False
            task_done = True
            return

        log("Pool created!")
        log("")

        # Create standard datasets
        log("Creating datasets...")
        datasets = [
            (pool_name + "/apps", "/data/apps"),
            (pool_name + "/shared", "/data/shared"),
            (pool_name + "/backups", "/data/backups"),
            (pool_name + "/postgres", "/data/postgres"),
        ]
        for ds_name, mp in datasets:
            subprocess.run(
                ["zfs", "create", "-o", "mountpoint=" + mp, ds_name],
                capture_output=True, timeout=10, env=ENV_WITH_PATH)
            log("  Created: %s -> %s" % (ds_name, mp))

        log("")
        log("Setting permissions...")
        os.chmod("/data/shared", 0o770)
        subprocess.run(["chown", "root:openos-data", "/data/shared"],
                       timeout=5, env=ENV_WITH_PATH)
        os.chmod("/data/postgres", 0o700)
        subprocess.run(["chown", "postgres:postgres", "/data/postgres"],
                       timeout=5, env=ENV_WITH_PATH)

        log("")
        log("Pool status:")
        r = subprocess.run(["zpool", "status", pool_name],
                           capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        for line in r.stdout.splitlines():
            log("  " + line)

        log("")
        log("=== ZFS Pool '%s' created successfully ===" % pool_name)

    except Exception as e:
        log("ERROR: %s" % e)

    task_running = False
    task_done = True


def create_zfs_dataset(pool_name, dataset_name, quota=None):
    """Create a ZFS dataset within a pool."""
    full_name = pool_name + "/" + dataset_name
    mountpoint = "/data/" + dataset_name
    cmd = ["zfs", "create", "-o", "mountpoint=" + mountpoint]
    if quota:
        cmd += ["-o", "quota=" + quota]
    cmd.append(full_name)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        if r.returncode == 0:
            return {"ok": True, "dataset": full_name, "mountpoint": mountpoint}
        return {"ok": False, "error": r.stderr.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def destroy_zfs_dataset(dataset_name):
    """Destroy a ZFS dataset."""
    try:
        r = subprocess.run(
            ["zfs", "destroy", dataset_name],
            capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        if r.returncode == 0:
            return {"ok": True}
        return {"ok": False, "error": r.stderr.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ==================== SAMBA SHARE MANAGEMENT ====================

SHARES_CONF = "/etc/openos/shares.json"


def get_shares():
    """Read configured Samba shares."""
    try:
        with open(SHARES_CONF) as f:
            return json.loads(f.read())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_shares(shares):
    """Write shares config and regenerate smb.conf includes."""
    with open(SHARES_CONF, "w") as f:
        f.write(json.dumps(shares, indent=2))
    # Write Samba share definitions
    conf_dir = "/etc/samba/shares.d"
    os.makedirs(conf_dir, exist_ok=True)
    # Clear old share files
    for fname in os.listdir(conf_dir):
        os.remove(os.path.join(conf_dir, fname))
    for share in shares:
        conf_path = os.path.join(conf_dir, share["name"] + ".conf")
        lines = [
            "[%s]" % share["name"],
            "   path = %s" % share["path"],
            "   browseable = yes",
            "   read only = %s" % ("yes" if share.get("readonly", False) else "no"),
            "   guest ok = %s" % ("yes" if share.get("guest", False) else "no"),
        ]
        if share.get("valid_users"):
            lines.append("   valid users = %s" % " ".join(share["valid_users"]))
        if share.get("write_list"):
            lines.append("   write list = %s" % " ".join(share["write_list"]))
        lines.append("   create mask = 0664")
        lines.append("   directory mask = 0775")
        lines.append("")
        with open(conf_path, "w") as f:
            f.write("\n".join(lines))
    # Reload samba
    subprocess.run(["systemctl", "reload", "smbd"],
                   capture_output=True, timeout=10, env=ENV_WITH_PATH)


def create_share(name, path, valid_users=None, write_list=None, readonly=False, guest=False):
    """Create a new Samba share."""
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*$', name):
        return {"ok": False, "error": "Invalid share name"}
    if not path.startswith("/data/"):
        return {"ok": False, "error": "Share path must be under /data/"}
    os.makedirs(path, exist_ok=True)
    os.chmod(path, 0o775)
    shares = get_shares()
    for s in shares:
        if s["name"] == name:
            return {"ok": False, "error": "Share '%s' already exists" % name}
    shares.append({
        "name": name,
        "path": path,
        "valid_users": valid_users or [],
        "write_list": write_list or [],
        "readonly": readonly,
        "guest": guest,
    })
    save_shares(shares)
    return {"ok": True}


def delete_share(name):
    """Delete a Samba share (does not delete files)."""
    shares = get_shares()
    shares = [s for s in shares if s["name"] != name]
    save_shares(shares)
    return {"ok": True}


def get_system_users():
    """Get list of non-system users (UID >= 1000)."""
    users = []
    try:
        with open("/etc/passwd") as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) >= 7:
                    uid = int(parts[2])
                    if uid >= 1000 and uid < 65000:
                        users.append({
                            "username": parts[0],
                            "uid": uid,
                            "home": parts[5],
                            "shell": parts[6],
                        })
    except Exception:
        pass
    return users


def create_system_user(username, password=None):
    """Create a system user and optionally set Samba password."""
    if not re.match(r'^[a-z][a-z0-9_-]{1,30}$', username):
        return {"ok": False, "error": "Invalid username (lowercase, 2-31 chars, start with letter)"}
    try:
        r = subprocess.run(
            ["useradd", "-m", "-G", "openos-data", "-s", "/bin/bash", username],
            capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        if r.returncode != 0 and "already exists" not in r.stderr:
            return {"ok": False, "error": r.stderr.strip()}
        if password:
            proc = subprocess.run(
                ["smbpasswd", "-a", "-s", username],
                input=password + "\n" + password + "\n",
                capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
            if proc.returncode != 0:
                return {"ok": False, "error": "User created but Samba password failed: " + proc.stderr.strip()}
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mount_disk(device, mountpoint, fstype, role="data"):
    """Add a mount to mounts.nix and trigger rebuild."""
    global task_log, task_running, task_done, task_name
    task_log = []
    task_running = True
    task_done = False
    task_name = "Mount %s" % device

    def log(msg):
        task_log.append(msg)

    log("=== Mounting %s at %s ===" % (device, mountpoint))

    try:
        ensure_dns()
        mounts = get_configured_mounts()
        mounts.append({
            "mountpoint": mountpoint,
            "device": device,
            "fsType": fstype,
            "options": ["nofail"],
            "role": role,
        })
        write_mounts_nix(mounts)
        log("Updated mounts.nix")

        os.makedirs(mountpoint, exist_ok=True)
        log("Created mountpoint directory")

        log("")
        ensure_dns()
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
            log("=== Mount configured successfully ===")
        else:
            log("")
            log("=== Mount FAILED (exit code %d) ===" % proc.returncode)
    except Exception as e:
        log("ERROR: %s" % e)

    task_running = False
    task_done = True


def unmount_disk(mountpoint):
    """Remove a mount from mounts.nix and trigger rebuild."""
    global task_log, task_running, task_done, task_name
    task_log = []
    task_running = True
    task_done = False
    task_name = "Unmount %s" % mountpoint

    def log(msg):
        task_log.append(msg)

    log("=== Removing mount %s ===" % mountpoint)

    try:
        ensure_dns()
        mounts = get_configured_mounts()
        mounts = [m for m in mounts if m["mountpoint"] != mountpoint]
        write_mounts_nix(mounts)
        log("Updated mounts.nix")

        log("")
        ensure_dns()
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
            log("=== Mount removed successfully ===")
        else:
            log("")
            log("=== Unmount FAILED (exit code %d) ===" % proc.returncode)
    except Exception as e:
        log("ERROR: %s" % e)

    task_running = False
    task_done = True


def get_network_info():
    """Network interfaces, IPs, and basic status."""
    interfaces = []
    try:
        r = subprocess.run(
            ["ip", "-j", "addr", "show"],
            capture_output=True, text=True, timeout=10, env=ENV_WITH_PATH)
        data = json.loads(r.stdout)
        for iface in data:
            name = iface.get("ifname", "")
            if name == "lo":
                continue
            addrs = []
            for ai in iface.get("addr_info", []):
                addrs.append({"addr": ai.get("local", ""), "family": ai.get("family", "")})
            kind = "ethernet"
            if name.startswith("tailscale") or name.startswith("ts"):
                kind = "tailscale"
            elif name.startswith("wl"):
                kind = "wifi"
            elif name.startswith("docker") or name.startswith("br-") or name.startswith("veth"):
                kind = "virtual"
            interfaces.append({
                "name": name,
                "state": iface.get("operstate", "UNKNOWN"),
                "mac": iface.get("address", ""),
                "addresses": addrs,
                "kind": kind,
            })
    except Exception as e:
        return {"error": str(e), "interfaces": []}

    dns = []
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                if line.strip().startswith("nameserver"):
                    dns.append(line.strip().split()[1])
    except Exception:
        pass

    return {"interfaces": interfaces, "dns": dns}


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
        ensure_dns()
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
        ensure_dns()
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
.bar-track{background:#222;border-radius:4px;height:20px;overflow:hidden;margin:.4rem 0}
.bar-fill{height:100%;border-radius:4px;transition:width .3s}
.bar-fill.green{background:#22c55e}.bar-fill.yellow{background:#eab308}.bar-fill.red{background:#ef4444}
.disk-card{background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:1rem;margin-bottom:.8rem}
.disk-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:.5rem}
.disk-name{font-weight:600;color:#fff;font-size:.95rem}
.disk-model{color:#666;font-size:.8rem}
.disk-size{color:#888;font-family:monospace;font-size:.85rem}
.part-row{display:flex;align-items:center;gap:.8rem;padding:.3rem 0;font-size:.85rem}
.part-name{min-width:80px;color:#aaa;font-family:monospace}
.part-bar{flex:1}
.part-info{min-width:180px;text-align:right;color:#888;font-family:monospace;font-size:.8rem}
.backup-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:.8rem;margin-top:.5rem}
.backup-item{background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:.8rem;text-align:center}
.backup-item.active{border-color:#22c55e40}
.backup-item.missing{border-color:#ef444440}
.backup-icon{font-size:1.5rem;margin-bottom:.3rem}
.backup-label{font-size:.8rem;color:#888;margin-bottom:.2rem}
.backup-status{font-size:.85rem;font-weight:600}
.usage-row{display:flex;align-items:center;gap:.6rem;padding:.3rem 0}
.usage-name{min-width:100px;color:#aaa;font-size:.85rem}
.usage-bar{flex:1}
.usage-size{min-width:80px;text-align:right;color:#888;font-family:monospace;font-size:.8rem}
.iface-card{background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:.8rem;margin-bottom:.6rem;display:flex;align-items:center;gap:1rem}
.iface-icon{font-size:1.3rem;width:32px;text-align:center}
.iface-info{flex:1}
.iface-name{font-weight:600;color:#fff;font-size:.9rem}
.iface-addrs{font-size:.8rem;color:#888;font-family:monospace}
.iface-state{font-size:.8rem;font-weight:600}
.modal-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.7);z-index:100;align-items:center;justify-content:center}
.modal-overlay.show{display:flex}
.modal{background:#1a1a1a;border:1px solid #444;border-radius:8px;padding:1.5rem;max-width:500px;width:90%}
.modal h3{color:#fff;margin-bottom:1rem}
.modal label{display:block;color:#aaa;font-size:.85rem;margin-bottom:.3rem}
.modal input,.modal select{width:100%;padding:.5rem;background:#0a0a0a;border:1px solid #444;border-radius:4px;color:#fff;font-size:.9rem;margin-bottom:.8rem}
.modal input:focus,.modal select:focus{outline:none;border-color:#f97316}
.modal-actions{display:flex;gap:.5rem;justify-content:flex-end;margin-top:.5rem}
</style></head><body>
<nav>
<h1>OpenOS</h1>
<a href="#dashboard" class="active" onclick="showPage('dashboard',this)">Dashboard</a>
<a href="#storage" onclick="showPage('storage',this)">Storage</a>
<a href="#network" onclick="showPage('network',this)">Network</a>
<a href="#apps" onclick="showPage('apps',this)">Apps</a>
<a href="#system" onclick="showPage('system',this)">System</a>
</nav>

<!-- ==================== DASHBOARD ==================== -->
<div class="page show" id="p-dashboard">
<p class="val" id="version" style="color:#888;margin-bottom:1rem;font-size:.85rem">Loading...</p>
<div class="card"><h2>System Health</h2><div id="health">Loading...</div></div>
<div class="card"><h2>Tailscale</h2><div id="ts">Loading...</div>
<div class="actions"><button class="btn btn-sm btn-gray" onclick="setupTS()">Configure Tailscale</button></div></div>
<div class="card"><h2>Storage Overview</h2><div id="dash-storage">Loading...</div></div>
<div class="card"><h2>Installed Apps</h2><div id="installed-apps" style="color:#888;font-size:.9rem">Loading...</div></div>
</div>

<!-- ==================== STORAGE ==================== -->
<div class="page" id="p-storage">
<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center">
<h2 style="margin:0">ZFS Pools</h2>
<div><button class="btn btn-sm btn-gray" onclick="showImportDialog()" style="margin-right:.5rem">Import Pool</button><button class="btn btn-sm" onclick="showPoolDialog()">Create Pool</button></div>
</div>
<div id="pool-list" style="margin-top:.8rem">Loading...</div>
</div>
<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center">
<h2 style="margin:0">Datasets</h2>
<button class="btn btn-sm btn-gray" onclick="showDatasetDialog()">New Dataset</button>
</div>
<div id="dataset-list" style="margin-top:.8rem">Loading...</div>
</div>
<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center">
<h2 style="margin:0">File Shares</h2>
<button class="btn btn-sm btn-gray" onclick="showShareDialog()">New Share</button>
</div>
<div id="share-list" style="margin-top:.8rem">Loading...</div>
</div>
<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center">
<h2 style="margin:0">Disks</h2>
<button class="btn btn-sm btn-gray" onclick="showMountDialog()">Mount Disk</button>
</div>
<div id="disk-list" style="margin-top:.8rem">Loading...</div>
</div>
<div class="card"><h2>Data Usage</h2><div id="data-usage">Loading...</div></div>
<div class="card"><h2>3-2-1 Backup Status</h2><div id="backup-status">Loading...</div></div>
<div class="card"><h2>Disk Health (SMART)</h2><div id="smart-status">Loading...</div></div>
<div id="storage-buildstatus" style="text-align:center;padding:.6rem;border-radius:6px;font-size:.9rem;font-weight:600;display:none;margin-bottom:1rem"></div>
<div id="storage-buildlog" class="log-box"></div>
</div>

<!-- ==================== NETWORK ==================== -->
<div class="page" id="p-network">
<div class="card"><h2>Network Interfaces</h2><div id="iface-list">Loading...</div></div>
<div class="card"><h2>Tailscale Nodes</h2><div id="ts-nodes">Loading...</div>
<div class="actions">
<button class="btn btn-sm btn-gray" onclick="setupTS()">Configure Tailscale</button>
</div></div>
<div class="card"><h2>DNS</h2><div id="dns-info">Loading...</div></div>
</div>

<!-- ==================== APPS ==================== -->
<div class="page" id="p-apps">
<div id="buildstatus"></div>
<div id="buildlog"></div>
<div class="app-grid" id="app-grid">
<div style="color:#888">Loading apps...</div>
</div>
</div>

<!-- ==================== SYSTEM ==================== -->
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

<!-- ==================== MOUNT DIALOG ==================== -->
<div class="modal-overlay" id="mount-modal">
<div class="modal">
<h3>Mount Disk</h3>
<label>Device</label>
<select id="mount-device"><option value="">Loading...</option></select>
<label>Mount Point</label>
<input id="mount-point" value="/data/extra" placeholder="/data/extra">
<label>Role</label>
<select id="mount-role">
<option value="data">Data (extra storage)</option>
<option value="backup">Backup (3-2-1 copy 2)</option>
</select>
<div class="modal-actions">
<button class="btn btn-gray" onclick="closeMountDialog()">Cancel</button>
<button class="btn" onclick="doMount()">Mount &amp; Apply</button>
</div>
</div>
</div>

<!-- ==================== CREATE POOL DIALOG ==================== -->
<div class="modal-overlay" id="pool-modal">
<div class="modal" style="max-width:600px">
<h3>Create ZFS Pool</h3>
<label>Pool Name</label>
<input id="pool-name" value="tank" placeholder="tank">
<label>RAID Type</label>
<select id="pool-type">
<option value="raidz1">RAIDZ1 (1 disk parity, min 3 disks)</option>
<option value="raidz2">RAIDZ2 (2 disk parity, min 4 disks)</option>
<option value="mirror">Mirror (min 2 disks)</option>
<option value="stripe">Stripe (no redundancy!)</option>
</select>
<label>Select Disks</label>
<div id="pool-disk-list" style="max-height:200px;overflow-y:auto;border:1px solid #333;border-radius:4px;padding:.5rem;margin-bottom:.8rem;background:#0a0a0a">
<span style="color:#666">Loading available disks...</span>
</div>
<p style="color:#f97316;font-size:.8rem;margin-bottom:.8rem">&#x26A0; All data on selected disks will be erased!</p>
<div class="modal-actions">
<button class="btn btn-gray" onclick="closePoolDialog()">Cancel</button>
<button class="btn" onclick="doCreatePool()">Create Pool</button>
</div>
</div>
</div>

<!-- ==================== CREATE DATASET DIALOG ==================== -->
<div class="modal-overlay" id="dataset-modal">
<div class="modal">
<h3>New Dataset</h3>
<label>Pool</label>
<select id="ds-pool"><option value="">Loading...</option></select>
<label>Dataset Name</label>
<input id="ds-name" placeholder="e.g. movies, photos, documents">
<label>Quota (optional)</label>
<input id="ds-quota" placeholder="e.g. 100G, 500G, none">
<div class="modal-actions">
<button class="btn btn-gray" onclick="closeDatasetDialog()">Cancel</button>
<button class="btn" onclick="doCreateDataset()">Create</button>
</div>
</div>
</div>

<!-- ==================== CREATE SHARE DIALOG ==================== -->
<div class="modal-overlay" id="share-modal">
<div class="modal">
<h3>New File Share</h3>
<label>Share Name</label>
<input id="share-name" placeholder="e.g. Filme, Fotos, Dokumente">
<label>Path</label>
<input id="share-path" placeholder="/data/shared/filme">
<label>Allowed Users (comma-separated, leave empty for all)</label>
<input id="share-users" placeholder="e.g. anna, max, jonas">
<label>Write Access (comma-separated, leave empty = same as allowed)</label>
<input id="share-writers" placeholder="e.g. anna, max">
<div style="margin-bottom:.8rem">
<label style="display:inline"><input type="checkbox" id="share-readonly"> Read-only</label>
<label style="display:inline;margin-left:1rem"><input type="checkbox" id="share-guest"> Guest access (no password)</label>
</div>
<div class="modal-actions">
<button class="btn btn-gray" onclick="closeShareDialog()">Cancel</button>
<button class="btn" onclick="doCreateShare()">Create Share</button>
</div>
</div>
</div>

<!-- ==================== CREATE USER DIALOG ==================== -->
<div class="modal-overlay" id="user-modal">
<div class="modal">
<h3>New User</h3>
<label>Username</label>
<input id="new-username" placeholder="e.g. anna">
<label>Password (for file share access)</label>
<input id="new-password" type="password" placeholder="Samba password">
<div class="modal-actions">
<button class="btn btn-gray" onclick="closeUserDialog()">Cancel</button>
<button class="btn" onclick="doCreateUser()">Create User</button>
</div>
</div>
</div>

<!-- ==================== IMPORT POOL DIALOG ==================== -->
<div class="modal-overlay" id="import-modal">
<div class="modal">
<h3>Import Existing ZFS Pool</h3>
<div id="import-pool-list" style="margin-bottom:1rem">
<span style="color:#666">Scanning for importable pools...</span>
</div>
<div class="modal-actions">
<button class="btn btn-gray" onclick="closeImportDialog()">Cancel</button>
</div>
</div>
</div>

<script>
(function(){
var icons={"tv":"\uD83D\uDCFA","cloud":"\u2601\uFE0F","brain":"\uD83E\uDDE0","sync":"\uD83D\uDD04","lock":"\uD83D\uDD12","git":"\uD83C\uDF3F","doc":"\uD83D\uDCDD","files":"\uD83D\uDCC1","music":"\uD83C\uDFB5"};
var appTimer=null,curApps=[],storageTimer=null;

function fmtBytes(b){
if(!b||b===0)return '0 B';
var u=['B','KB','MB','GB','TB'];var i=0;var v=b;
while(v>=1024&&i<u.length-1){v/=1024;i++;}
return v.toFixed(i>0?1:0)+' '+u[i];
}

function barColor(pct){return pct>90?'red':pct>70?'yellow':'green';}

window.showPage=function(id,el){
var pages=document.querySelectorAll('.page');
for(var i=0;i<pages.length;i++)pages[i].className='page';
document.getElementById('p-'+id).className='page show';
var links=document.querySelectorAll('nav a');
for(var i=0;i<links.length;i++)links[i].className='';
if(el)el.className='active';
if(id==='apps')loadApps();
if(id==='storage')loadStorage();
if(id==='network')loadNetwork();
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
fetch('/api/storage/usage').then(function(r){return r.json()}).then(function(d){
var el=document.getElementById('dash-storage');
if(d.total){
var pct=Math.round((d.used/d.total)*100);
el.innerHTML='<div class="row"><span class="lbl">/data</span><span class="val">'+fmtBytes(d.used)+' / '+fmtBytes(d.total)+' ('+pct+'%)</span></div>'
+'<div class="bar-track"><div class="bar-fill '+barColor(pct)+'" style="width:'+pct+'%"></div></div>';
}else{el.innerHTML='<span class="val" style="color:#888">Not available</span>';}
});
fetch('/api/apps').then(function(r){return r.json()}).then(function(apps){
var inst=apps.filter(function(a){return a.enabled;});
var el=document.getElementById('installed-apps');
if(!inst.length){el.innerHTML='No apps installed yet. <a href="#apps" onclick="showPage(\'apps\',document.querySelectorAll(\'nav a\')[3])" style="color:#f97316;text-decoration:underline">Browse apps</a>';return;}
var h='';
for(var i=0;i<inst.length;i++){var a=inst[i];
h=h+'<div class="row"><span class="lbl">'+(icons[a.icon]||'\u2699\uFE0F')+' '+a.name+'</span><span class="val ok">running</span></div>';
}
el.innerHTML=h;
});
}
load();setInterval(load,15000);

/* ==================== STORAGE ==================== */
function loadStorage(){
/* ZFS Pools */
fetch('/api/storage/pools').then(function(r){return r.json()}).then(function(pools){
var el=document.getElementById('pool-list');
if(!pools||!pools.length){el.innerHTML='<div style="color:#888;padding:.5rem 0">No ZFS pools found. Create one to get started.</div>';return;}
var h='';
for(var i=0;i<pools.length;i++){
var p=pools[i];
var pct=parseInt(p.capacity_pct)||0;
h+='<div class="disk-card"><div class="disk-header"><div><span class="disk-name">'+p.name+'</span> <span style="color:#666;font-size:.8rem;margin-left:.5rem">'+p.type.toUpperCase()+'</span> <span style="color:#666;font-size:.8rem;margin-left:.5rem">'+p.disk_count+' disks</span></div>';
h+='<div><span class="val '+(p.health==='ONLINE'?'ok':'fail')+'">'+p.health+'</span></div></div>';
h+='<div class="bar-track"><div class="bar-fill '+barColor(pct)+'" style="width:'+pct+'%"></div></div>';
h+='<div style="display:flex;justify-content:space-between;font-size:.8rem;color:#888;margin-top:.3rem"><span>Used: '+p.allocated+'</span><span>Free: '+p.free+'</span><span>Total: '+p.size+'</span><span>Frag: '+p.fragmentation+'</span></div>';
h+='</div>';
}
el.innerHTML=h;
});

/* ZFS Datasets */
fetch('/api/storage/datasets').then(function(r){return r.json()}).then(function(datasets){
var el=document.getElementById('dataset-list');
if(!datasets||!datasets.length){el.innerHTML='<div style="color:#888;padding:.5rem 0">No datasets. Create a pool first.</div>';return;}
var h='<table><thead><tr><th>Dataset</th><th>Used</th><th>Available</th><th>Mountpoint</th><th>Quota</th><th></th></tr></thead><tbody>';
for(var i=0;i<datasets.length;i++){
var d=datasets[i];
h+='<tr><td style="font-family:monospace;font-size:.85rem">'+d.name+'</td><td>'+d.used+'</td><td>'+d.available+'</td><td style="color:#888">'+d.mountpoint+'</td><td>'+(d.quota||'-')+'</td>';
h+='<td><button class="btn btn-sm btn-red" onclick="deleteDataset(\''+d.name+'\')">Del</button></td></tr>';
}
h+='</tbody></table>';
el.innerHTML=h;
});

/* Shares */
fetch('/api/storage/shares').then(function(r){return r.json()}).then(function(shares){
var el=document.getElementById('share-list');
if(!shares||!shares.length){el.innerHTML='<div style="color:#888;padding:.5rem 0">No shares configured. <a href="#" onclick="showShareDialog();return false" style="color:#f97316">Create one</a> | <a href="#" onclick="showUserDialog();return false" style="color:#f97316">Add user</a></div>';return;}
var h='<div style="margin-bottom:.5rem"><button class="btn btn-sm btn-gray" onclick="showUserDialog()">Add User</button></div>';
h+='<table><thead><tr><th>Share</th><th>Path</th><th>Users</th><th>Access</th><th></th></tr></thead><tbody>';
for(var i=0;i<shares.length;i++){
var s=shares[i];
var users=s.valid_users&&s.valid_users.length?s.valid_users.join(', '):'<span style="color:#888">everyone</span>';
var acc=s.readonly?'<span style="color:#f97316">read-only</span>':'<span class="ok">read/write</span>';
if(s.guest)acc+=' <span style="color:#888">(guest)</span>';
h+='<tr><td style="font-weight:600">'+s.name+'</td><td style="font-family:monospace;font-size:.85rem;color:#888">'+s.path+'</td><td>'+users+'</td><td>'+acc+'</td>';
h+='<td><button class="btn btn-sm btn-red" onclick="deleteShare(\''+s.name+'\')">Del</button></td></tr>';
}
h+='</tbody></table>';
el.innerHTML=h;
});

/* Physical Disks */
fetch('/api/storage').then(function(r){return r.json()}).then(function(d){
var el=document.getElementById('disk-list');
if(!d.disks||!d.disks.length){el.innerHTML='<div style="color:#888">No disks detected.</div>';return;}
var h='';
for(var i=0;i<d.disks.length;i++){
var dk=d.disks[i];
var dtype=dk.rotational?'HDD':'SSD';
if(dk.removable)dtype='USB';
h=h+'<div class="disk-card"><div class="disk-header"><div><span class="disk-name">/dev/'+dk.name+'</span> <span class="disk-model">'+dk.model+'</span></div><div><span class="disk-size">'+fmtBytes(dk.size)+'</span> <span style="color:#666;font-size:.75rem;margin-left:.5rem">'+dtype+'</span></div></div>';
if(dk.partitions&&dk.partitions.length){
for(var j=0;j<dk.partitions.length;j++){
var p=dk.partitions[j];
var pct=0,info='';
if(p.total&&p.total>0){pct=Math.round((p.used/p.total)*100);info=fmtBytes(p.used)+' / '+fmtBytes(p.total)+' ('+pct+'%)';}
else if(p.size){info=fmtBytes(p.size)+(p.mountpoint?'':' unmounted');}
h=h+'<div class="part-row"><span class="part-name">'+p.name+(p.fstype?' <span style="color:#555;font-size:.75rem">'+p.fstype+'</span>':'')+'</span>';
if(p.mountpoint){
h=h+'<div class="part-bar"><div class="bar-track"><div class="bar-fill '+barColor(pct)+'" style="width:'+pct+'%"></div></div></div>';
h=h+'<span class="part-info">'+p.mountpoint+' &mdash; '+info+'</span>';
}else{
h=h+'<div class="part-bar"></div><span class="part-info" style="color:#555">'+info+'</span>';
}
h=h+'</div>';
}
}
h=h+'</div>';
}
el.innerHTML=h;
});

fetch('/api/storage/usage').then(function(r){return r.json()}).then(function(d){
var el=document.getElementById('data-usage');
if(!d.total){el.innerHTML='<span style="color:#888">/data not mounted</span>';return;}
var pct=Math.round((d.used/d.total)*100);
var h='<div class="row"><span class="lbl">/data total</span><span class="val">'+fmtBytes(d.used)+' / '+fmtBytes(d.total)+' ('+pct+'%)</span></div>';
h=h+'<div class="bar-track"><div class="bar-fill '+barColor(pct)+'" style="width:'+pct+'%"></div></div>';
h=h+'<div style="margin-top:.8rem">';
var apps=d.apps||{};
var items=[];
for(var k in apps)items.push({name:k,size:apps[k]});
items.sort(function(a,b){return b.size-a.size;});
for(var i=0;i<items.length;i++){
var a=items[i];
var ap=d.total>0?Math.max(1,Math.round((a.size/d.total)*100)):0;
h=h+'<div class="usage-row"><span class="usage-name">'+(icons[a.name]||'\u2699\uFE0F')+' '+a.name+'</span><div class="usage-bar"><div class="bar-track" style="height:12px"><div class="bar-fill green" style="width:'+ap+'%"></div></div></div><span class="usage-size">'+fmtBytes(a.size)+'</span></div>';
}
if(d.shared){
var sp=d.total>0?Math.max(1,Math.round((d.shared/d.total)*100)):0;
h=h+'<div class="usage-row"><span class="usage-name">\uD83D\uDCC1 shared</span><div class="usage-bar"><div class="bar-track" style="height:12px"><div class="bar-fill green" style="width:'+sp+'%"></div></div></div><span class="usage-size">'+fmtBytes(d.shared)+'</span></div>';
}
if(d.backups){
var bp=d.total>0?Math.max(1,Math.round((d.backups/d.total)*100)):0;
h=h+'<div class="usage-row"><span class="usage-name">\uD83D\uDDC4\uFE0F backups</span><div class="usage-bar"><div class="bar-track" style="height:12px"><div class="bar-fill green" style="width:'+bp+'%"></div></div></div><span class="usage-size">'+fmtBytes(d.backups)+'</span></div>';
}
h=h+'</div>';
el.innerHTML=h;
});

fetch('/api/storage/backup-status').then(function(r){return r.json()}).then(function(d){
var el=document.getElementById('backup-status');
var h='<div class="backup-grid">';
h=h+'<div class="backup-item '+(d.copy1_ok?'active':'missing')+'"><div class="backup-icon">'+(d.copy1_ok?'\u2705':'\u274C')+'</div><div class="backup-label">Copy 1: Original</div><div class="backup-status '+(d.copy1_ok?'ok':'fail')+'">'+d.copy1_label+'</div></div>';
h=h+'<div class="backup-item '+(d.copy2_ok?'active':'missing')+'"><div class="backup-icon">'+(d.copy2_ok?'\u2705':'\u26A0\uFE0F')+'</div><div class="backup-label">Copy 2: Local Backup</div><div class="backup-status '+(d.copy2_ok?'ok':'warn')+'">'+d.copy2_label+'</div></div>';
h=h+'<div class="backup-item missing"><div class="backup-icon">\u2B50</div><div class="backup-label">Copy 3: Offsite</div><div class="backup-status" style="color:#555">'+d.copy3_label+'</div></div>';
h=h+'</div>';
if(d.last_backup){h=h+'<div style="margin-top:.8rem;font-size:.8rem;color:#888">Last backup: '+d.last_backup+(d.backup_age_hours!=null?' ('+d.backup_age_hours+'h ago)':'')+'</div>';}
el.innerHTML=h;
});

fetch('/api/storage/health').then(function(r){return r.json()}).then(function(d){
var el=document.getElementById('smart-status');
if(!d.disks||!d.disks.length){el.innerHTML='<span style="color:#888">No SMART data available.</span>';return;}
var h='';
for(var i=0;i<d.disks.length;i++){
var s=d.disks[i];
var hOk=s.healthy===true;var hFail=s.healthy===false;var hUnk=s.healthy===null;
h=h+'<div class="row"><span class="lbl">/dev/'+s.name+'</span><span class="val '+(hOk?'ok':hFail?'fail':'')+'">'+(hOk?'PASSED':hFail?'FAILING':s.details||'N/A')+'</span></div>';
if(s.temperature!=null)h=h+'<div class="row"><span class="lbl" style="padding-left:1rem">Temperature</span><span class="val">'+s.temperature+'\u00B0C</span></div>';
if(s.power_on_hours!=null)h=h+'<div class="row"><span class="lbl" style="padding-left:1rem">Power-on hours</span><span class="val">'+s.power_on_hours+'h</span></div>';
if(s.reallocated!=null&&s.reallocated>0)h=h+'<div class="row"><span class="lbl" style="padding-left:1rem">Reallocated sectors</span><span class="val warn">'+s.reallocated+'</span></div>';
}
el.innerHTML=h;
});
}

window.showMountDialog=function(){
document.getElementById('mount-modal').className='modal-overlay show';
fetch('/api/storage/unmounted').then(function(r){return r.json()}).then(function(parts){
var sel=document.getElementById('mount-device');
sel.innerHTML='';
if(!parts.length){sel.innerHTML='<option value="">No unmounted partitions found</option>';return;}
for(var i=0;i<parts.length;i++){
var p=parts[i];
sel.innerHTML=sel.innerHTML+'<option value="'+p.device+'">'+p.device+' ('+fmtBytes(p.size)+', '+p.fstype+(p.model?', '+p.model:'')+')</option>';
}
});
};
window.closeMountDialog=function(){document.getElementById('mount-modal').className='modal-overlay';};

window.doMount=function(){
var dev=document.getElementById('mount-device').value;
var mp=document.getElementById('mount-point').value.trim();
var role=document.getElementById('mount-role').value;
if(!dev){alert('Select a device');return;}
if(!mp||!mp.startsWith('/')){alert('Mount point must be an absolute path');return;}
closeMountDialog();
var log=document.getElementById('storage-buildlog');
var st=document.getElementById('storage-buildstatus');
log.style.display='block';log.textContent='';log.setAttribute('data-n','0');
st.style.display='block';st.className='working';st.style.background='#1a1000';st.style.color='#f97316';st.textContent='Mounting '+dev+' at '+mp+'...';
fetch('/api/storage/mount',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device:dev,mountpoint:mp,role:role})})
.then(function(r){return r.json()}).then(function(d){
if(d.ok){storageTimer=setInterval(pollStorageLog,2000);}
else{st.style.background='#2d0a0a';st.style.color='#ef4444';st.textContent='Error: '+(d.error||'unknown');}
});
};

window.doUnmount=function(mp){
if(!confirm('Remove mount '+mp+'? This will rebuild the system.'))return;
var log=document.getElementById('storage-buildlog');
var st=document.getElementById('storage-buildstatus');
log.style.display='block';log.textContent='';log.setAttribute('data-n','0');
st.style.display='block';st.className='working';st.style.background='#1a1000';st.style.color='#f97316';st.textContent='Removing mount '+mp+'...';
fetch('/api/storage/unmount',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mountpoint:mp})})
.then(function(r){return r.json()}).then(function(d){
if(d.ok){storageTimer=setInterval(pollStorageLog,2000);}
else{st.style.background='#2d0a0a';st.style.color='#ef4444';st.textContent='Error: '+(d.error||'unknown');}
});
};

function pollStorageLog(){
var log=document.getElementById('storage-buildlog');
var st=document.getElementById('storage-buildstatus');
var n=parseInt(log.getAttribute('data-n')||'0');
fetch('/api/task-log?from='+n).then(function(r){return r.json()}).then(function(d){
if(d.lines&&d.lines.length>0){
for(var i=0;i<d.lines.length;i++)log.textContent=log.textContent+d.lines[i]+'\n';
log.setAttribute('data-n',String(n+d.lines.length));log.scrollTop=log.scrollHeight;
}
if(d.done&&d.lines.length===0){
clearInterval(storageTimer);
var last=log.textContent.trim().split('\n').pop()||'';
if(last.indexOf('successfully')>=0){st.style.background='#052e16';st.style.color='#22c55e';st.textContent='Done!';}
else{st.style.background='#2d0a0a';st.style.color='#ef4444';st.textContent='Failed. Check log above.';}
loadStorage();
}
});
}

/* ZFS Pool Dialog */
window.showPoolDialog=function(){
document.getElementById('pool-modal').className='modal-overlay show';
fetch('/api/storage/available-disks').then(function(r){return r.json()}).then(function(disks){
var el=document.getElementById('pool-disk-list');
if(!disks||!disks.length){el.innerHTML='<span style="color:#ef4444">No available disks found. All disks are in use.</span>';return;}
var h='';
for(var i=0;i<disks.length;i++){
var d=disks[i];
var dtype=d.rotational?'HDD':'SSD';
if(d.removable)dtype='USB';
h+='<label style="display:flex;align-items:center;gap:.6rem;padding:.4rem;cursor:pointer;border-bottom:1px solid #222">';
h+='<input type="checkbox" class="pool-disk-cb" value="'+d.device+'">';
h+='<span style="font-family:monospace;min-width:80px">'+d.name+'</span>';
h+='<span style="color:#888;font-size:.85rem">'+fmtBytes(d.size)+'</span>';
h+='<span style="color:#555;font-size:.8rem">'+dtype+'</span>';
h+='<span style="color:#666;font-size:.8rem">'+d.model+'</span>';
h+='</label>';
}
el.innerHTML=h;
});
};
window.closePoolDialog=function(){document.getElementById('pool-modal').className='modal-overlay';};

window.doCreatePool=function(){
var name=document.getElementById('pool-name').value.trim();
var rtype=document.getElementById('pool-type').value;
var cbs=document.querySelectorAll('.pool-disk-cb:checked');
var disks=[];
for(var i=0;i<cbs.length;i++)disks.push(cbs[i].value);
if(!name){alert('Enter a pool name');return;}
if(!disks.length){alert('Select at least one disk');return;}
if(rtype==='raidz1'&&disks.length<3){alert('RAIDZ1 requires at least 3 disks (selected: '+disks.length+')');return;}
if(rtype==='raidz2'&&disks.length<4){alert('RAIDZ2 requires at least 4 disks (selected: '+disks.length+')');return;}
if(rtype==='mirror'&&disks.length<2){alert('Mirror requires at least 2 disks (selected: '+disks.length+')');return;}
if(!confirm('Create pool "'+name+'" ('+rtype+') with '+disks.length+' disks?\n\nALL DATA ON THESE DISKS WILL BE ERASED!')){return;}
closePoolDialog();
var log=document.getElementById('storage-buildlog');
var st=document.getElementById('storage-buildstatus');
log.style.display='block';log.textContent='';log.setAttribute('data-n','0');
st.style.display='block';st.style.background='#1a1000';st.style.color='#f97316';st.textContent='Creating ZFS pool...';
fetch('/api/storage/create-pool',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,type:rtype,disks:disks})})
.then(function(r){return r.json()}).then(function(d){
if(d.ok){storageTimer=setInterval(pollStorageLog,2000);}
else{st.style.background='#2d0a0a';st.style.color='#ef4444';st.textContent='Error: '+(d.error||'unknown');}
});
};

/* Import Pool Dialog */
window.showImportDialog=function(){
document.getElementById('import-modal').className='modal-overlay show';
var el=document.getElementById('import-pool-list');
el.innerHTML='<span style="color:#666">Scanning for importable pools...</span>';
fetch('/api/storage/importable-pools').then(function(r){return r.json()}).then(function(pools){
if(!pools||!pools.length){el.innerHTML='<div style="color:#888;padding:.5rem 0">No importable pools found. The disks might not contain a valid ZFS pool.</div>';return;}
var h='';
for(var i=0;i<pools.length;i++){
var p=pools[i];
h+='<div style="background:#111;border:1px solid #2a2a2a;border-radius:6px;padding:.8rem;margin-bottom:.5rem;display:flex;justify-content:space-between;align-items:center">';
h+='<div><span style="font-weight:600;color:#fff">'+p.name+'</span> <span style="color:#888;font-size:.85rem;margin-left:.5rem">'+p.state+'</span>';
if(p.disks&&p.disks.length)h+='<div style="font-size:.8rem;color:#555;margin-top:.2rem">Disks: '+p.disks.join(', ')+'</div>';
h+='</div>';
h+='<button class="btn btn-sm" onclick="doImportPool(\''+p.name+'\')">Import</button>';
h+='</div>';
}
el.innerHTML=h;
});
};
window.closeImportDialog=function(){document.getElementById('import-modal').className='modal-overlay';};

window.doImportPool=function(name){
if(!confirm('Import pool "'+name+'"?'))return;
closeImportDialog();
var log=document.getElementById('storage-buildlog');
var st=document.getElementById('storage-buildstatus');
log.style.display='block';log.textContent='';log.setAttribute('data-n','0');
st.style.display='block';st.style.background='#1a1000';st.style.color='#f97316';st.textContent='Importing pool "'+name+'"...';
fetch('/api/storage/import-pool',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,force:true})})
.then(function(r){return r.json()}).then(function(d){
if(d.ok){storageTimer=setInterval(pollStorageLog,2000);}
else{st.style.background='#2d0a0a';st.style.color='#ef4444';st.textContent='Error: '+(d.error||'unknown');}
});
};

/* Dataset Dialog */
window.showDatasetDialog=function(){
document.getElementById('dataset-modal').className='modal-overlay show';
fetch('/api/storage/pools').then(function(r){return r.json()}).then(function(pools){
var sel=document.getElementById('ds-pool');
sel.innerHTML='';
if(!pools||!pools.length){sel.innerHTML='<option value="">No pools available</option>';return;}
for(var i=0;i<pools.length;i++){sel.innerHTML+='<option value="'+pools[i].name+'">'+pools[i].name+'</option>';}
});
};
window.closeDatasetDialog=function(){document.getElementById('dataset-modal').className='modal-overlay';};

window.doCreateDataset=function(){
var pool=document.getElementById('ds-pool').value;
var name=document.getElementById('ds-name').value.trim();
var quota=document.getElementById('ds-quota').value.trim()||null;
if(!pool){alert('Select a pool');return;}
if(!name){alert('Enter a dataset name');return;}
fetch('/api/storage/create-dataset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pool:pool,name:name,quota:quota})})
.then(function(r){return r.json()}).then(function(d){
if(d.ok){closeDatasetDialog();loadStorage();}
else{alert('Error: '+(d.error||'unknown'));}
});
};

window.deleteDataset=function(name){
if(!confirm('Delete dataset "'+name+'"? This will destroy all data in it!')){return;}
fetch('/api/storage/delete-dataset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset:name})})
.then(function(r){return r.json()}).then(function(d){
if(d.ok){loadStorage();}else{alert('Error: '+(d.error||'unknown'));}
});
};

/* Share Dialog */
window.showShareDialog=function(){document.getElementById('share-modal').className='modal-overlay show';};
window.closeShareDialog=function(){document.getElementById('share-modal').className='modal-overlay';};

window.doCreateShare=function(){
var name=document.getElementById('share-name').value.trim();
var path=document.getElementById('share-path').value.trim();
var usersStr=document.getElementById('share-users').value.trim();
var writersStr=document.getElementById('share-writers').value.trim();
var readonly=document.getElementById('share-readonly').checked;
var guest=document.getElementById('share-guest').checked;
if(!name){alert('Enter a share name');return;}
if(!path){alert('Enter a path');return;}
var users=usersStr?usersStr.split(',').map(function(s){return s.trim()}).filter(Boolean):[];
var writers=writersStr?writersStr.split(',').map(function(s){return s.trim()}).filter(Boolean):[];
fetch('/api/storage/create-share',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,path:path,valid_users:users,write_list:writers,readonly:readonly,guest:guest})})
.then(function(r){return r.json()}).then(function(d){
if(d.ok){closeShareDialog();loadStorage();}
else{alert('Error: '+(d.error||'unknown'));}
});
};

window.deleteShare=function(name){
if(!confirm('Delete share "'+name+'"? (Files will NOT be deleted)')){return;}
fetch('/api/storage/delete-share',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name})})
.then(function(r){return r.json()}).then(function(d){
if(d.ok){loadStorage();}else{alert('Error: '+(d.error||'unknown'));}
});
};

/* User Dialog */
window.showUserDialog=function(){document.getElementById('user-modal').className='modal-overlay show';};
window.closeUserDialog=function(){document.getElementById('user-modal').className='modal-overlay';};

window.doCreateUser=function(){
var username=document.getElementById('new-username').value.trim();
var password=document.getElementById('new-password').value;
if(!username){alert('Enter a username');return;}
if(!password){alert('Enter a password');return;}
fetch('/api/storage/create-user',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:username,password:password})})
.then(function(r){return r.json()}).then(function(d){
if(d.ok){closeUserDialog();alert('User "'+username+'" created!');loadStorage();}
else{alert('Error: '+(d.error||'unknown'));}
});
};

/* ==================== NETWORK ==================== */
function loadNetwork(){
fetch('/api/network').then(function(r){return r.json()}).then(function(d){
var el=document.getElementById('iface-list');
if(!d.interfaces||!d.interfaces.length){el.innerHTML='<span style="color:#888">No interfaces found.</span>';return;}
var h='';
var kindIcon={"ethernet":"\uD83D\uDD0C","wifi":"\uD83D\uDCF6","tailscale":"\uD83D\uDD10","virtual":"\uD83D\uDD17"};
for(var i=0;i<d.interfaces.length;i++){
var iface=d.interfaces[i];
var ic=kindIcon[iface.kind]||'\uD83C\uDF10';
var up=iface.state==='UP';
var addrs=[];
for(var j=0;j<(iface.addresses||[]).length;j++){
var a=iface.addresses[j];
addrs.push(a.addr+' ('+a.family+')');
}
h=h+'<div class="iface-card"><span class="iface-icon">'+ic+'</span><div class="iface-info"><div class="iface-name">'+iface.name+' <span style="color:#555;font-size:.75rem;font-weight:400">'+iface.kind+'</span></div><div class="iface-addrs">'+(addrs.join(', ')||'no address')+'</div></div><span class="iface-state '+(up?'ok':'fail')+'">'+(up?'UP':'DOWN')+'</span></div>';
}
el.innerHTML=h;

var dnsEl=document.getElementById('dns-info');
var dh='';
if(d.dns&&d.dns.length){
for(var i=0;i<d.dns.length;i++)dh=dh+'<div class="row"><span class="lbl">Nameserver</span><span class="val">'+d.dns[i]+'</span></div>';
}else{dh='<span style="color:#888">No DNS servers configured.</span>';}
dnsEl.innerHTML=dh;
});

fetch('/api/tailscale').then(function(r){return r.json()}).then(function(d){
var el=document.getElementById('ts-nodes');
var h='<div class="row"><span class="lbl">Status</span><span class="val '+(d.connected?'ok':'fail')+'">'+(d.connected?'Connected':'Disconnected')+'</span></div>';
if(d.self)h=h+'<div class="row"><span class="lbl">This node</span><span class="val">'+d.self+'</span></div>';
if(d.ips&&d.ips.length)h=h+'<div class="row"><span class="lbl">IPs</span><span class="val">'+d.ips.join(', ')+'</span></div>';
if(d.tailnet)h=h+'<div class="row"><span class="lbl">Tailnet</span><span class="val">'+d.tailnet+'</span></div>';
el.innerHTML=h;
});
}

/* ==================== APPS ==================== */
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

/* ==================== SYSTEM ==================== */
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

var hash=location.hash.replace('#','');
var tabMap={'dashboard':0,'storage':1,'network':2,'apps':3,'system':4};
if(hash&&tabMap[hash]!=null){
showPage(hash,document.querySelectorAll('nav a')[tabMap[hash]]);
}
})();
</script></body></html>"""


class AdminHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/dashboard", "/apps", "/system", "/storage", "/network"):
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
        elif self.path == "/api/storage":
            self._json(get_storage())
        elif self.path == "/api/storage/health":
            self._json(get_storage_health())
        elif self.path == "/api/storage/usage":
            self._json(get_storage_usage())
        elif self.path == "/api/storage/backup-status":
            self._json(get_backup_status())
        elif self.path == "/api/storage/unmounted":
            self._json(get_unmounted_partitions())
        elif self.path == "/api/storage/mounts":
            self._json(get_configured_mounts())
        elif self.path == "/api/storage/available-disks":
            self._json(get_available_disks())
        elif self.path == "/api/storage/pools":
            self._json(get_zfs_pools())
        elif self.path == "/api/storage/importable-pools":
            self._json(get_importable_pools())
        elif self.path.startswith("/api/storage/datasets"):
            pool = None
            if "pool=" in self.path:
                pool = self.path.split("pool=")[1].split("&")[0]
            self._json(get_zfs_datasets(pool))
        elif self.path == "/api/storage/shares":
            self._json(get_shares())
        elif self.path == "/api/storage/users":
            self._json(get_system_users())
        elif self.path == "/api/network":
            self._json(get_network_info())
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
                    log("Ensuring DNS for nix-daemon...")
                    ensure_dns()
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

        elif self.path == "/api/storage/mount":
            device = body.get("device", "")
            mountpoint = body.get("mountpoint", "")
            role = body.get("role", "data")
            if not device or not mountpoint:
                self._json({"ok": False, "error": "device and mountpoint required"})
                return
            if not mountpoint.startswith("/"):
                self._json({"ok": False, "error": "mountpoint must be absolute path"})
                return
            if task_running:
                self._json({"ok": False, "error": "Task already running"})
                return
            try:
                r = subprocess.run(["blkid", "-o", "value", "-s", "TYPE", device],
                                   capture_output=True, text=True, timeout=5, env=ENV_WITH_PATH)
                fstype = r.stdout.strip() or "ext4"
            except Exception:
                fstype = "ext4"
            t = threading.Thread(target=mount_disk, args=(device, mountpoint, fstype, role), daemon=True)
            t.start()
            self._json({"ok": True})

        elif self.path == "/api/storage/unmount":
            mountpoint = body.get("mountpoint", "")
            if not mountpoint:
                self._json({"ok": False, "error": "mountpoint required"})
                return
            if task_running:
                self._json({"ok": False, "error": "Task already running"})
                return
            t = threading.Thread(target=unmount_disk, args=(mountpoint,), daemon=True)
            t.start()
            self._json({"ok": True})

        elif self.path == "/api/storage/import-pool":
            pool_name = body.get("name", "")
            force = body.get("force", False)
            if not pool_name:
                self._json({"ok": False, "error": "Pool name required"})
                return
            if task_running:
                self._json({"ok": False, "error": "Task already running"})
                return
            t = threading.Thread(target=import_zfs_pool, args=(pool_name, force), daemon=True)
            t.start()
            self._json({"ok": True})

        elif self.path == "/api/storage/create-pool":
            pool_name = body.get("name", "")
            disks = body.get("disks", [])
            raid_type = body.get("type", "raidz1")
            if not pool_name:
                self._json({"ok": False, "error": "Pool name required"})
                return
            if not disks or len(disks) < 1:
                self._json({"ok": False, "error": "At least one disk required"})
                return
            if raid_type not in ("raidz1", "raidz2", "mirror", "stripe"):
                self._json({"ok": False, "error": "Invalid RAID type"})
                return
            if task_running:
                self._json({"ok": False, "error": "Task already running"})
                return
            t = threading.Thread(target=create_zfs_pool, args=(pool_name, disks, raid_type), daemon=True)
            t.start()
            self._json({"ok": True})

        elif self.path == "/api/storage/create-dataset":
            pool = body.get("pool", "")
            dataset = body.get("name", "")
            quota = body.get("quota")
            if not pool or not dataset:
                self._json({"ok": False, "error": "pool and name required"})
                return
            self._json(create_zfs_dataset(pool, dataset, quota))

        elif self.path == "/api/storage/delete-dataset":
            dataset = body.get("dataset", "")
            if not dataset:
                self._json({"ok": False, "error": "dataset name required"})
                return
            self._json(destroy_zfs_dataset(dataset))

        elif self.path == "/api/storage/create-share":
            name = body.get("name", "")
            path = body.get("path", "")
            valid_users = body.get("valid_users", [])
            write_list = body.get("write_list", [])
            readonly = body.get("readonly", False)
            guest = body.get("guest", False)
            if not name or not path:
                self._json({"ok": False, "error": "name and path required"})
                return
            self._json(create_share(name, path, valid_users, write_list, readonly, guest))

        elif self.path == "/api/storage/delete-share":
            name = body.get("name", "")
            if not name:
                self._json({"ok": False, "error": "name required"})
                return
            self._json(delete_share(name))

        elif self.path == "/api/storage/create-user":
            username = body.get("username", "")
            password = body.get("password", "")
            if not username:
                self._json({"ok": False, "error": "username required"})
                return
            self._json(create_system_user(username, password or None))

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
