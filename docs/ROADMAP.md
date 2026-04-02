# OpenOS Server — Roadmap

> Stand: 24. Maerz 2026
> Ergebnisse der Architektur-Planung zwischen Projektinhaber und Entwicklung.

---

## Vision

OpenOS ist ein **Community-Server-Betriebssystem** basierend auf NixOS.
Jede Community (Sportverein, Wohnprojekt, NGO, Gemeinde) installiert OpenOS
auf eigener Hardware und hat damit eine souveraene digitale Infrastruktur:
Chat, Kalender, Aufgaben, Dateien, Medienstreaming, KI — alles selbst gehostet.

Die Benutzeroberflaeche fuer Community-Mitglieder ist **Global Stack**
(Next.js Workspace mit 10+ Apps). Die Server-Verwaltung laeuft ueber das
**Admin Panel** (Web-UI im Bootloader).

---

## Architektur-Entscheidungen

### Zwei getrennte Welten

| Wer | Interface | Zweck |
|-----|-----------|-------|
| **Server-Admin** (1 Person) | Admin Panel (:8080) | Hardware, Netzwerk, Apps, Updates, User |
| **Community-Mitglieder** (viele) | Global Stack (:3000) | Chat, Calendar, Tasks, Groups, Documents |

### Apps pro Community

- Jede Community (Group) kann eigene Apps aktiviert haben
- Apps laufen in **NixOS-Containern** (systemd-nspawn) — eine Instanz pro Community
- Perfekte Daten-Isolation: eigenes Filesystem, eigene DB, eigene Ports
- GPU-Apps (Ollama) koennen geteilt werden (shared instance)
- **Implementierung in Phase 6** — erstmal globale App-Instanzen (Phase 0-5)

### Speicherverwaltung

- **3-2-1 Backup-Schema** wird vom System forciert:
  - 3 Kopien der Daten (Original + 2 Backups)
  - 2 verschiedene Medien (interne SSD + externe Platte)
  - 1 offsite (spaeter: zweiter Server ueber Tailscale)
- Erstmal lokal (zwei Platten), Offsite in spaeterer Phase
- **Detaillierte Speicher-Uebersicht**: pro App, pro Community, pro Platte
- Dynamische Verwaltung: System verteilt Daten automatisch, Admin sieht nur Uebersicht

### Netzwerk

- Primaer ueber **Tailscale/Headscale** (VPN)
- Netzwerk-Isolation zwischen Usern moeglich ueber Headscale ACLs
  (konfiguriert auf dem Headscale-Server, nicht auf OpenOS)
- Admin Panel zeigt: Interfaces, IPs, Tailscale-Nodes, Verbindungsstatus

### Identitaet / SSO

- **Ein Account fuer alles** (Single Sign-On)
- Authelia als OIDC-Provider, alle Apps per OIDC angebunden
- User registriert sich in Global Stack, kann damit alle freigegebenen Apps nutzen
- **Implementierung in Phase 5** — erstmal App-eigene Logins

### Global Stack als installierbare App

- Global Stack ist NICHT fest eingebaut, sondern ein Service der installiert wird
- Im Admin Panel erscheint es wie Jellyfin oder Nextcloud
- Braucht PostgreSQL (shared mit OpenOS) und Node.js Runtime
- Prisma-Schema wird auf PostgreSQL migriert (aktuell SQLite)

---

## Phasenplan

```
Phase 0  Admin Panel Tabs (Navigation)           <- JETZT
Phase 1  Storage Tab (Festplatten, 3-2-1)        <- JETZT
Phase 2  Network Tab (Interfaces, Tailscale)
Phase 3  App-Konfiguration (Settings pro App)
Phase 4  Global Stack als App (NixOS-Modul)
Phase 5  SSO + User-Verwaltung (Authelia, OIDC)
Phase 6  Container pro Community (Multi-Instanz)
Phase 7  Connect & Federation (Multi-Server)
```

### Phase 0 — Admin Panel Tab-Redesign

**Status:** Offen
**Aufwand:** ~1 Session

Admin Panel bekommt 5 Tabs statt 3:

| Tab | Inhalt |
|-----|--------|
| Dashboard | Health, Tailscale, Speicher-Zusammenfassung, installierte Apps |
| Storage | Festplatten, Partitionen, Mounts, Belegung pro App/Community |
| Network | Interfaces, IPs, Tailscale-Nodes, DNS |
| Apps | App-Grid, Install/Remove, App-Settings |
| System | Updates, Generationen/Rollback, Terminal |

Aenderungen in: `modules/bootloader/admin-panel.py` (HTML + API)

### Phase 1 — Storage Tab

**Status:** Offen
**Aufwand:** ~2 Sessions

Endpunkte:
- `GET /api/storage` — Blockdevices, Partitionen, Mounts, Belegung
- `GET /api/storage/health` — SMART-Status
- `GET /api/storage/usage` — Speicher pro App, pro Community-Verzeichnis
- `POST /api/storage/mount` — Neue Platte einbinden

Neue Datei: `/etc/openos/mounts.nix` (analog zu `apps.nix`)

UI:
- Visuelle Balken pro Platte (belegt/frei)
- Aufschluesselung: welche App / welche Daten wie viel Platz nutzen
- "Mount Disk" Dialog
- SMART-Warnungen
- 3-2-1 Status-Anzeige:
  - Kopie 1 (Original): OK / Warnung
  - Kopie 2 (lokales Backup): OK / Nicht konfiguriert
  - Kopie 3 (Offsite): Spaeter

3-2-1 Enforcement:
- System prueft ob mindestens 2 Medien vorhanden sind
- Warnung im Dashboard wenn Backup-Platte fehlt
- Automatische taegliche Backups (existiert bereits: `openos-backup` Service)
- Backup-Ziel konfigurierbar auf zweite Platte

### Phase 2 — Network Tab

**Status:** Offen
**Aufwand:** ~1-2 Sessions

Endpunkte:
- `GET /api/network` — Interfaces mit IPs, Status
- `GET /api/network/tailscale` — Eigene IPs + alle verbundenen Nodes
- `POST /api/network/tailscale/connect` — Verbinden/Reconnect
- `POST /api/network/dns` — DNS-Server aendern

UI:
- Interface-Liste (Name, IP, Up/Down, Ethernet/WiFi/Tailscale)
- Tailscale: eigene IPs, verbundene Nodes (Name, IP, Online/Offline)
- Connect/Disconnect
- DNS-Config

### Phase 3 — App-Konfiguration

**Status:** Offen
**Aufwand:** ~1 Session

Erweitert den Apps Tab:
- Klick auf installierte App oeffnet Settings
- Konfigurierbare Optionen: Port, Domain, App-spezifisch
- "Save & Apply" schreibt in `apps.nix` und rebuildet

Erweitert `apps.nix` Format:
```nix
{
  openos.apps.jellyfin.enable = true;
  openos.apps.jellyfin.port = 8096;
  openos.apps.nextcloud.enable = true;
  openos.apps.nextcloud.domain = "files.meine-community.de";
}
```

### Phase 4 — Global Stack als App

**Status:** Offen
**Aufwand:** ~2-3 Sessions

Neues NixOS-Modul: `modules/apps/global-stack.nix`
- Clont Global Stack Repo, baut Next.js Standalone
- systemd-Service auf Port 3000
- Eigene PostgreSQL-Datenbank `globalstack`
- Nginx-Proxy

Aenderungen im Global Stack Repo:
- Prisma-Provider: SQLite -> PostgreSQL
- Connection-String aus Environment
- Seed-Daten fuer PostgreSQL

Im Admin Panel:
- Global Stack erscheint im App-Grid
- Install wie jede andere App
- Nach Install erreichbar ueber Tailscale-IP

### Phase 5 — SSO + User-Verwaltung

**Status:** Offen
**Aufwand:** ~2-3 Sessions

Authelia als OIDC-Provider:
- Neues Modul: `modules/base/authelia.nix`
- Nutzt PostgreSQL
- OIDC-Endpunkte fuer alle Apps

App-Anbindung:
| App | OIDC-Support | Aufwand |
|-----|-------------|---------|
| Global Stack | Custom (Prisma Session -> OIDC) | Mittel |
| Nextcloud | Ja (Plugin) | Niedrig |
| Gitea | Ja (eingebaut) | Niedrig |
| Jellyfin | Ja (Plugin) | Mittel |
| Vaultwarden | Ja (eingebaut) | Niedrig |
| HedgeDoc | Ja (eingebaut) | Niedrig |

Admin Panel bekommt "Users" Tab:
- Invite-Links generieren
- User-Liste
- Rollen (Server-Admin vs Community-Member)

### Phase 6 — Container pro Community

**Status:** Geplant (nicht sofort)
**Aufwand:** ~3-4 Sessions

Umstellung von globalen App-Instanzen auf NixOS-Container (systemd-nspawn):
- Jede Community bekommt eigene App-Container
- Daten unter `/data/communities/<slug>/<app>/`
- Eigene Ports, eigene Datenbanken
- GPU-Sharing fuer Ollama (shared instance)

Neue Datenstruktur:
```
/data/
  communities/
    sportverein/
      jellyfin/       <- eigene Jellyfin-Daten
      nextcloud/      <- eigene Nextcloud-Daten
    wohnprojekt/
      nextcloud/
      hedgedoc/
  shared/             <- server-weite geteilte Dateien
  postgres/           <- zentrale DB
  backups/
```

Admin Panel:
- Community erstellen/verwalten
- Apps pro Community aktivieren/deaktivieren
- Speicher-Uebersicht pro Community

### Phase 7 — Connect & Federation

**Status:** Fernziel
**Aufwand:** Unbestimmt

- Multi-Server: User verbindet mehrere OpenOS-Server in Global Stack
- Server-zu-Server: Communities teilen Inhalte ueber Tailscale
- Offsite-Backup auf zweiten Server (3-2-1 komplett)

---

## Beziehung zu Global Stack

OpenOS Server und Global Stack sind **zwei separate Projekte**:

| | OpenOS Server | Global Stack |
|---|---|---|
| **Repo** | `fritte-MOOD/OpenOS-Server` | `fritte-MOOD/Global_Stack` |
| **Sprache** | Nix + Go + Python | TypeScript (Next.js) |
| **Zweck** | Server-OS + Infrastruktur | Community-Workspace-UI |
| **DB** | PostgreSQL (zentral) | PostgreSQL (gleiche Instanz) |
| **Deploy** | NixOS (auf Hardware) | Als App auf OpenOS ODER Vercel |

Global Stack kann auch **ohne** OpenOS laufen (auf Vercel mit Turso/Supabase).
Aber auf OpenOS wird es zum nativen Community-Interface.

Die Verbindung:
- Gleiche PostgreSQL-Instanz (Global Stack Prisma-Schema + OpenOS-Tabellen)
- Go-API liest Global Stack Tabellen (`User`, `Session`, `Group`, `Membership`)
- Auth wird geteilt: Session-Token aus Global Stack wird von Go-API validiert
- Spaeter: Authelia als gemeinsamer OIDC-Provider

---

## Technologie-Stack

| Komponente | Technologie | Dateien |
|-----------|------------|---------|
| Betriebssystem | NixOS 24.11 | `flake.nix`, `hosts/`, `modules/` |
| Bootloader/Admin | Python HTTP-Server | `modules/bootloader/admin-panel.py` |
| Server-API | Go + pgx | `api/` |
| Datenbank | PostgreSQL 16 | `modules/base/postgresql.nix` |
| Reverse Proxy | Nginx | `modules/base/nginx.nix` |
| VPN | Tailscale + Headscale | `modules/network/` |
| App-Module | Nix | `modules/apps/*.nix` |
| Secrets | agenix | `secrets/` |
| Community-UI | Next.js 16 (Global Stack) | Separates Repo |
