# OpenOS Server — USB-Installationsanleitung

Eine Schritt-für-Schritt-Anleitung, um OpenOS auf einem Server zu installieren.

## Was du brauchst

| Was | Details |
|-----|---------|
| **Server-PC** | Jeder PC mit x86_64-CPU (Intel/AMD). Mindestens 4 GB RAM, 16 GB Disk. |
| **USB-Stick** | Mindestens 2 GB. Wird überschrieben! |
| **Zweiter PC** | Zum Flashen des USB-Sticks und für den Setup-Wizard. |
| **Internet** | Der Server braucht Internet (Ethernet empfohlen). |

> **Hinweis:** Ein Mac mit ARM-Chip (M1/M2/M3) eignet sich nicht als Server — nimm einen normalen PC mit Intel/AMD-Prozessor. Dein Mac ist aber perfekt als Entwicklungsmaschine.

---

## Schritt 1: USB-Stick vorbereiten

Du hast zwei Optionen:

### Option A: OpenOS Installer ISO (empfohlen)

Wenn du Nix auf deinem Mac/PC installiert hast:

```bash
# Repository klonen
git clone https://github.com/fritte-MOOD/OpenOS-Server.git
cd OpenOS-Server

# ISO bauen
nix build .#packages.x86_64-linux.installer-iso

# Die ISO-Datei liegt jetzt unter:
ls result/iso/
# → openos-installer-*.iso
```

### Option B: Standard NixOS ISO

Wenn du kein Nix hast, lade die NixOS Minimal ISO herunter:
https://nixos.org/download#nixos-iso

---

## Schritt 2: ISO auf USB-Stick flashen

### macOS

```bash
# USB-Stick finden
diskutil list
# Suche deinen USB-Stick (z.B. /dev/disk4)

# USB-Stick unmounten
diskutil unmountDisk /dev/diskN

# ISO flashen (N durch deine Disk-Nummer ersetzen!)
sudo dd if=openos-installer-*.iso of=/dev/rdiskN bs=4m status=progress

# Fertig → USB-Stick auswerfen
diskutil eject /dev/diskN
```

### Linux

```bash
# USB-Stick finden
lsblk
# Suche deinen USB-Stick (z.B. /dev/sdb)

# ISO flashen
sudo dd if=openos-installer-*.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

### Windows

Benutze [Rufus](https://rufus.ie/) oder [balenaEtcher](https://etcher.balena.io/):
1. Programm öffnen
2. ISO-Datei auswählen
3. USB-Stick auswählen
4. "Flash" klicken

---

## Schritt 3: Server vom USB-Stick booten

1. **USB-Stick** in den Server stecken
2. **Server einschalten**
3. **Boot-Menü öffnen** — drücke beim Start die richtige Taste:

| Hersteller | Taste |
|-----------|-------|
| Die meisten PCs | **F12** |
| Dell | F12 |
| HP | F9 |
| Lenovo | F12 |
| ASUS | F8 oder Esc |
| Acer | F12 |
| MSI | F11 |
| Intel NUC | F10 |

4. **USB-Stick auswählen** im Boot-Menü
5. Warten bis NixOS gestartet ist

---

## Schritt 4: Installer starten

### Wenn du die OpenOS ISO benutzt:

Du siehst automatisch ein Menü:
```
Welcome to OpenOS Server Installer
===================================

  1) Install OpenOS (interactive)
  2) Install OpenOS (from network)
  3) Drop to shell

Choice [1]:
```

Drücke **Enter** (oder tippe `1`).

### Wenn du die Standard NixOS ISO benutzt:

Zuerst Internet verbinden, dann den Installer herunterladen:

```bash
# Ethernet: sollte automatisch funktionieren
# WiFi:
sudo systemctl start wpa_supplicant
wpa_cli
> add_network
> set_network 0 ssid "DeinWiFiName"
> set_network 0 psk "DeinWiFiPasswort"
> enable_network 0
> quit

# Installer starten
curl -sL https://raw.githubusercontent.com/fritte-MOOD/OpenOS-Server/main/scripts/net-install.sh | sudo bash
```

---

## Schritt 5: Installation durchführen

Der Installer fragt dich:

### 5.1 Disk auswählen

```
Available disks:
  sda   500G  Samsung SSD 870
  sdb    16G  USB Flash Drive

Enter the disk to install to (e.g. sda, nvme0n1): sda
```

**Wähle die Festplatte des Servers** (NICHT den USB-Stick!).

### 5.2 Bestätigen

```
WARNING: This will ERASE ALL DATA on /dev/sda
Type 'yes' to continue: yes
```

### 5.3 Warten

Der Installer:
- Partitioniert die Disk (Boot, System, Daten)
- Lädt das OpenOS Seed-System herunter
- Installiert NixOS

Das dauert **5-15 Minuten** je nach Internetgeschwindigkeit.

### 5.4 Fertig

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  OpenOS Seed installed successfully!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Remove the USB stick
  2. Reboot
  3. Wait for the server to boot (1-2 minutes)
  4. Open a browser and go to: http://<server-ip>
  5. The setup wizard will guide you through the rest
```

**USB-Stick entfernen** und **Enter drücken** zum Neustarten.

---

## Schritt 6: Setup-Wizard im Browser

Nach dem Neustart läuft der Server im **Seed-Modus** — ein minimales System mit einem Web-Panel.

### 6.1 IP-Adresse finden

Die IP-Adresse wird auf dem Server-Bildschirm angezeigt. Alternativ:
- Schau in deinem Router nach neuen Geräten
- Oder auf dem Server: `ip addr`

### 6.2 Browser öffnen

Öffne auf deinem Mac/PC: **http://\<server-ip\>**

Du siehst den OpenOS Setup-Wizard:

### 6.3 Server konfigurieren

**Schritt 1 — Server:**
- **Hostname:** Name deines Servers (z.B. `mein-server`)
- **Domain:** Deine Domain (z.B. `meinserver.example.com` oder `openos.local`)
- **Timezone:** Deine Zeitzone (z.B. `Europe/Berlin`)
- **Admin Passwort:** Sicheres Passwort wählen!

**Schritt 2 — Netzwerk:**
- **Headscale URL:** Wenn du einen Headscale-Server hast, trage die URL ein. Sonst leer lassen — kannst du später einrichten.

**Schritt 3 — Version:**
- **Repository:** Standard lassen (GitHub)
- **Channel:** `Stable` empfohlen für den Anfang

### 6.4 Installation starten

Klicke **"Install OpenOS"**.

Der Seed zieht jetzt die volle OpenOS-Version von GitHub und baut das System. Das dauert **10-30 Minuten**.

Du siehst den Fortschritt live im Browser.

### 6.5 Automatischer Neustart

Nach der Installation startet der Server automatisch neu. Jetzt läuft das **volle OpenOS** mit:
- PostgreSQL Datenbank
- Nginx Reverse Proxy
- OpenOS API
- Tailscale VPN
- Alle konfigurierten Apps

---

## Schritt 7: Server benutzen

### API testen

```bash
curl http://<server-ip>:8090/api/v1/status
```

Antwort:
```json
{
  "version": "v0.1.0",
  "hostname": "mein-server",
  "mode": "full",
  "healthy": true
}
```

### Apps installieren

```bash
# Verfügbare Apps anzeigen
curl http://<server-ip>:8090/api/v1/apps

# Nextcloud installieren
curl -X POST http://<server-ip>:8090/api/v1/apps/nextcloud/install
```

### Global Stack Client verbinden

1. Tailscale auf deinem Mac/PC installieren
2. Mit dem gleichen Headscale-Netzwerk verbinden
3. Global Stack öffnen → Server hinzufügen → Tailscale-IP eingeben

---

## Fehlerbehebung

### Server bootet nicht vom USB-Stick

- Prüfe im BIOS: **Secure Boot** deaktivieren
- Prüfe im BIOS: **Boot-Reihenfolge** → USB an erster Stelle
- Versuche einen anderen USB-Port (USB 2.0 statt 3.0)

### Kein Internet nach dem Booten

```bash
# Ethernet-Status prüfen
ip link
# Wenn das Interface "down" ist:
sudo ip link set enp0s3 up
sudo dhclient enp0s3
```

### Setup-Wizard nicht erreichbar

```bash
# Auf dem Server prüfen:
sudo systemctl status openos-seed-panel
sudo journalctl -u openos-seed-panel -f

# Firewall prüfen:
sudo iptables -L
```

### Installation schlägt fehl (Phase 2)

```bash
# SSH auf den Server:
ssh admin@<server-ip>

# Manuell neu versuchen:
sudo bash /etc/openos/seed-pull.sh \
  https://github.com/fritte-MOOD/OpenOS-Server.git \
  stable openos openos.local UTC deinpasswort
```

### Zurück zum Seed-Modus

Wenn etwas schiefgeht, kannst du immer zum Seed zurück:
1. Server neustarten
2. Im GRUB-Menü eine ältere Generation wählen
3. Oder: `sudo nixos-rebuild switch --rollback`

---

## Nächste Schritte

- [Vollständige Installationsreferenz](INSTALL.md) — alle Methoden und Optionen
- [Architektur](ARCHITECTURE.md) — wie OpenOS funktioniert
- [App-Entwicklung](APP_DEVELOPMENT.md) — eigene Apps bauen
