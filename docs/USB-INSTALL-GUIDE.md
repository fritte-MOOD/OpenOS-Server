# homeserver OS — USB-Installationsanleitung

Schritt-für-Schritt-Anleitung, um homeserver OS auf einem Server zu installieren.

## Was du brauchst

| Was | Details |
|-----|---------|
| **Server-PC** | x86_64-CPU (Intel/AMD). Mindestens 4 GB RAM, 16 GB Disk. |
| **USB-Stick** | Mindestens 2 GB. Wird überschrieben! |
| **Zweiter PC** | Zum Flashen des USB-Sticks und für das Admin Panel. |
| **Internet** | Ethernet empfohlen. |

> **Mac mit ARM-Chip?** Nimm einen PC mit Intel/AMD für den Server. Dein Mac ist perfekt als Entwicklungsmaschine.

---

## Schritt 1: USB-Stick vorbereiten

### Option A: homeserver OS Installer ISO (empfohlen)

```bash
git clone https://github.com/fritte-MOOD/OpenOS-Server.git
cd OpenOS-Server
nix build .#packages.x86_64-linux.installer-iso
ls result/iso/   # → homeserver-installer-*.iso
```

### Option B: Standard NixOS ISO

NixOS Minimal ISO herunterladen: https://nixos.org/download#nixos-iso

---

## Schritt 2: ISO auf USB-Stick flashen

### macOS
```bash
diskutil list                    # USB-Stick finden
diskutil unmountDisk /dev/diskN
sudo dd if=homeserver-installer-*.iso of=/dev/rdiskN bs=4m status=progress
diskutil eject /dev/diskN
```

### Linux
```bash
lsblk                           # USB-Stick finden
sudo dd if=homeserver-installer-*.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

### Windows
[Rufus](https://rufus.ie/) oder [balenaEtcher](https://etcher.balena.io/) benutzen.

---

## Schritt 3: Server vom USB-Stick booten

1. USB-Stick einstecken
2. Server einschalten
3. Boot-Menü öffnen:

| Hersteller | Taste |
|-----------|-------|
| Die meisten | **F12** |
| Dell | F12 |
| HP | F9 |
| Lenovo | F12 |
| ASUS | F8 / Esc |
| Acer | F12 |
| MSI | F11 |
| Intel NUC | F10 |

4. USB-Stick auswählen
5. Warten bis NixOS startet

---

## Schritt 4: Installer starten

### Mit homeserver OS ISO:

```
Welcome to homeserver OS Installer
===================================
  1) Install homeserver OS (interactive)
  2) Install homeserver OS (from network)
  3) Drop to shell
```

**Enter** drücken für Option 1.

### Mit Standard NixOS ISO:

```bash
# Internet verbinden (Ethernet: automatisch, WiFi: siehe unten)
# Dann:
curl -sL https://raw.githubusercontent.com/fritte-MOOD/OpenOS-Server/main/scripts/net-install.sh | sudo bash
```

---

## Schritt 5: Installation

### 5.1 Disk auswählen
```
Available disks:
  sda   500G  Samsung SSD 870
  sdb    16G  USB Flash Drive

Enter the disk to install to: sda
```
**Die Festplatte wählen, NICHT den USB-Stick!**

### 5.2 Bestätigen
```
WARNING: ALL DATA on /dev/sda will be ERASED
Type 'yes' to continue: yes
```

### 5.3 Warten
Der Installer partitioniert, lädt homeserver OS herunter und installiert das System. **5-15 Minuten.**

### 5.4 Fertig
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  homeserver OS installed successfully!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Remove the USB stick
  2. Reboot
  3. Open http://<server-ip> for setup
```

USB-Stick entfernen und Enter drücken.

---

## Schritt 6: Setup im Browser

Nach dem Neustart zeigt das Admin Panel einen **Setup-Wizard**.

### 6.1 IP-Adresse finden
Wird auf dem Server-Bildschirm angezeigt. Oder im Router nachschauen.

### 6.2 Browser öffnen
`http://<server-ip>`

### 6.3 Konfigurieren

**1. Server:**
- Hostname, Domain, Timezone, Admin-Passwort

**2. Tailscale:**
- Headscale URL (optional — kann später konfiguriert werden)

**3. Version:**
- Repository (Standard: GitHub)
- Channel: Stable / Beta / Nightly

### 6.4 Installieren
**"Install homeserver OS"** klicken. Das System baut sich selbst — dauert **10-30 Minuten**.

### 6.5 Automatischer Neustart
Danach startet der Server neu. Jetzt läuft das volle homeserver OS.

---

## Schritt 7: Server benutzen

### Admin Panel
Das Admin Panel ist **immer erreichbar** unter `http://<server-ip>`:
- System-Gesundheit anzeigen
- Tailscale-Status
- NixOS-Generationen verwalten
- Updates installieren und zurückrollen
- Terminal

### Apps installieren
```bash
curl http://<server-ip>:8090/api/v1/apps
curl -X POST http://<server-ip>:8090/api/v1/apps/nextcloud/install
```

### Global Stack Client verbinden
1. Tailscale auf deinem PC installieren
2. Mit dem gleichen Headscale-Netzwerk verbinden
3. Global Stack → Server hinzufügen → Tailscale-IP

---

## Was den Bootloader besonders macht

Das Admin Panel und Tailscale sind **Teil des Bootloaders** — sie laufen immer, egal was passiert:

- **Update bricht Apps kaputt?** → Admin Panel + Tailscale laufen weiter, Watchdog rollt automatisch zurück
- **Komplett kaputte Version gepusht?** → GRUB startet automatisch die letzte funktionierende Generation
- **Server aus der Ferne nicht erreichbar?** → Tailscale hat eigene Verbindung, unabhängig von Apps

Du kannst den Server **nie** durch ein Update unzugänglich machen.

---

## Fehlerbehebung

### Server bootet nicht vom USB
- BIOS: **Secure Boot** deaktivieren
- BIOS: **Boot-Reihenfolge** → USB an erster Stelle
- Anderen USB-Port probieren (2.0 statt 3.0)

### Kein Internet
```bash
ip link
sudo ip link set enp0s3 up
sudo dhclient enp0s3
```

### Admin Panel nicht erreichbar
```bash
ssh admin@<server-ip>
sudo systemctl status homeserver-admin-panel
sudo journalctl -u homeserver-admin-panel -f
```

### Update fehlgeschlagen
Der Watchdog handelt automatisch. Falls manuell nötig:
1. Admin Panel öffnen → vorherige Generation aktivieren
2. Oder: `sudo nixos-rebuild switch --rollback`
3. Oder: GRUB-Menü → ältere Generation wählen

---

## Nächste Schritte

- [Installationsreferenz](INSTALL.md) — alle Methoden
- [Architektur](ARCHITECTURE.md) — wie homeserver OS funktioniert
- [App-Entwicklung](APP_DEVELOPMENT.md) — eigene Apps bauen
