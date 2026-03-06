# Metadata Agent API — Installationsanleitung

Vollständige Anleitung zur Installation, Konfiguration und zum Betrieb der Metadata Agent API — lokal, im Docker-Container und mit automatisiertem CI/CD über GitHub Actions.

---

## Inhaltsverzeichnis

- [1. Voraussetzungen](#1-voraussetzungen)
- [2. Lokale Installation (ohne Docker)](#2-lokale-installation-ohne-docker)
- [3. Docker-Container](#3-docker-container)
- [4. GitHub Actions — Automatischer Docker-Build](#4-github-actions--automatischer-docker-build)
- [5. Umgebungsvariablen — Vollständige Referenz](#5-umgebungsvariablen--vollständige-referenz)
- [6. Deployment-Varianten](#6-deployment-varianten)
- [7. Troubleshooting](#7-troubleshooting)

---

## 1. Voraussetzungen

### Minimale Systemanforderungen

| Komponente | Minimum | Empfohlen |
|---|---|---|
| CPU | 1 Core | 2+ Cores |
| RAM | 512 MB | 1 GB+ (Playwright/Chromium benötigt ~300 MB) |
| Disk | 500 MB | 1 GB (inkl. Chromium-Browser) |
| OS | Linux (x64/ARM64), Windows 10+, macOS 12+ | Linux (Docker) |

### Software-Voraussetzungen

| Software | Version | Zweck |
|---|---|---|
| Python | 3.12+ | Runtime |
| pip | 23+ | Paketmanager |
| Docker | 24+ | Container-Betrieb (optional) |
| Docker Compose | v2+ | Multi-Container (optional) |
| Git | 2.x | Quellcode |

### API-Keys (mindestens einer erforderlich)

| Key | Quelle | Beschreibung |
|---|---|---|
| B-API Key | OpenEduHub / WLO-Team | Standard-Provider für LLM-Zugriff (OpenAI via B-API oder AcademicCloud) |
| OpenAI API Key | https://platform.openai.com | Alternativer direkter OpenAI-Zugriff |

---

## 2. Lokale Installation (ohne Docker)

### 2.1 Python-Umgebung einrichten

```bash
# Repository klonen
git clone https://github.com/openeduhub/metadata-agent-api.git
cd metadata-agent-api

# Virtual Environment erstellen
python -m venv venv

# Aktivieren
source venv/bin/activate        # Linux / macOS
venv\Scripts\activate           # Windows (cmd)
venv\Scripts\Activate.ps1       # Windows (PowerShell)
```

### 2.2 Dependencies installieren

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Enthaltene Pakete (requirements.txt):

| Paket | Version | Zweck |
|---|---|---|
| fastapi | ≥0.115.0 | Web-Framework |
| uvicorn[standard] | ≥0.32.0 | ASGI-Server |
| pydantic | ≥2.10.0 | Datenvalidierung |
| pydantic-settings | ≥2.6.0 | Umgebungsvariablen |
| httpx | ≥0.28.0 | HTTP-Client (async) |
| python-multipart | ≥0.0.18 | File-Uploads |
| aiofiles | ≥24.1.0 | Async File-I/O |
| openai | ≥1.57.0 | OpenAI API Client |
| tenacity | ≥9.0.0 | Retry-Logik |
| python-dotenv | ≥1.0.1 | .env-Dateien |
| playwright | ≥1.49.0 | Browser-Automatisierung für Screenshots |

### 2.3 Playwright installieren

Playwright wird für die datenschutzfreundliche Screenshot-Funktion (`screenshot_method=playwright`) benötigt. Die Screenshots werden lokal mit einem headless Chromium-Browser erstellt — keine Daten werden an externe Services gesendet.

> **Hinweis:** Playwright ist optional. Ohne Playwright steht nur die externe `pageshot`-Methode zur Verfügung, bei der die URL an einen externen Screenshot-Service gesendet wird.

**Schritt 1: Python-Paket installieren** (bereits in requirements.txt)

```bash
pip install playwright>=1.49.0
```

**Schritt 2: Chromium-Browser herunterladen**

```bash
# Nur Chromium installieren (empfohlen — spart ~500 MB gegenüber allen Browsern)
playwright install chromium

# Alternativ: Alle Browser (Chromium, Firefox, WebKit) — nicht nötig
# playwright install
```

**Schritt 3: System-Dependencies installieren (Linux)**

Auf Linux-Systemen benötigt Chromium zusätzliche Systembibliotheken:

```bash
# Automatisch alle benötigten Systembibliotheken installieren (Ubuntu/Debian)
playwright install-deps chromium

# Oder manuell (minimale Liste):
sudo apt-get update && sudo apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libx11-xcb1 fonts-liberation fonts-noto-color-emoji
```

> **Windows & macOS:** Keine zusätzlichen System-Dependencies nötig — `playwright install chromium` reicht.

**Schritt 4: Installation prüfen**

```bash
# Prüfen ob Playwright korrekt installiert ist
python -c "from playwright.async_api import async_playwright; print('Playwright OK')"

# Chromium-Pfad anzeigen
python -c "from playwright._impl._driver import compute_driver_executable; print(compute_driver_executable())"
```

**Playwright-Probleme beheben:**

| Problem | Lösung |
|---|---|
| `playwright._impl._errors.Error: Executable doesn't exist` | `playwright install chromium` ausführen |
| `Host system is missing dependencies to run browsers` | `playwright install-deps chromium` (Linux) |
| Screenshot-Timeout | `METADATA_AGENT_SCREENSHOT_DELAY=5000` erhöhen |
| Chromium startet nicht im Container | System-Dependencies prüfen (siehe Dockerfile) |

### 2.4 Konfiguration (.env)

```bash
# Template kopieren
cp .env.template .env
```

Minimale Konfiguration — nur B-API Key eintragen:

```env
# Pflicht: LLM-Zugriff
B_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
METADATA_AGENT_LLM_PROVIDER=b-api-openai

# Optional: Repository-Upload
# WLO_GUEST_USERNAME=upload-user
# WLO_GUEST_PASSWORD=upload-password
```

Alle verfügbaren Variablen sind in [Abschnitt 5](#5-umgebungsvariablen--vollständige-referenz) dokumentiert.

### 2.5 API starten

```bash
# Entwicklungsmodus (mit Auto-Reload)
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Produktionsmodus
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 2
```

### 2.6 Installation testen

```bash
# Health Check
curl http://localhost:8000/health
# → {"status": "healthy", "version": "2.0.0"}

# Einfacher Test
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"text": "Workshop KI in der Bildung am 15. März 2026 in Berlin"}'

# Screenshot-Test (Playwright)
curl -X POST http://localhost:8000/screenshot \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "method": "playwright"}'

# API-Dokumentation
# → http://localhost:8000/docs (Swagger UI)
# → http://localhost:8000/redoc (ReDoc)
```

---

## 3. Docker-Container

### 3.1 Übersicht

Das Docker-Image ist ein Multi-Stage Build auf Basis von `python:3.12-slim` und enthält:

- Python 3.12 Runtime
- Alle Python-Dependencies (FastAPI, OpenAI, Playwright, etc.)
- Playwright + Chromium für datenschutzfreundliche Screenshots
- System-Bibliotheken für headless Chromium
- Non-Root User (`appuser`, UID 1000) für Sicherheit
- Health Check über `/health`-Endpoint

**Image-Größe:** ~800 MB (davon ~400 MB Chromium + System-Libs)

### 3.2 Docker-Image bauen

```bash
cd metadata-agent-api

# Standard-Build
docker build -t metadata-agent-api .

# Mit Build-Argumenten
docker build -t metadata-agent-api:v2.0.0 .

# Build-Cache nutzen (schneller bei Rebuilds)
docker build --cache-from metadata-agent-api:latest -t metadata-agent-api .

# Ohne Cache (sauberer Build)
docker build --no-cache -t metadata-agent-api .

# Build-Fortschritt anzeigen
docker build --progress=plain -t metadata-agent-api .
```

### 3.3 Container starten

```bash
# Minimal (mit einzelnen Env-Vars)
docker run -d \
  --name metadata-agent-api \
  -p 8000:8000 \
  -e B_API_KEY=your-api-key \
  metadata-agent-api

# Mit .env-Datei (empfohlen)
docker run -d \
  --name metadata-agent-api \
  -p 8000:8000 \
  --env-file .env \
  --restart unless-stopped \
  metadata-agent-api

# Mit allen Optionen
docker run -d \
  --name metadata-agent-api \
  -p 8000:8000 \
  --env-file .env \
  --restart unless-stopped \
  --memory 1g \
  --cpus 2 \
  --health-cmd "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\"" \
  --health-interval 30s \
  --health-timeout 10s \
  --health-retries 3 \
  metadata-agent-api
```

### 3.4 Docker Compose (empfohlen)

Die mitgelieferte `docker-compose.yml` enthält die empfohlene Konfiguration:

```bash
# .env-Datei vorbereiten
cp .env.template .env
# B_API_KEY eintragen

# Starten
docker compose up -d

# Logs anzeigen
docker compose logs -f

# Stoppen
docker compose down

# Neubauen und starten (nach Code-Änderungen)
docker compose up -d --build

# Nur neubauen ohne zu starten
docker compose build
```

`docker-compose.yml`:

```yaml
services:
  metadata-agent-api:
    build: .
    container_name: metadata-agent-api
    ports:
      - "8000:8000"
    environment:
      # LLM Provider
      - METADATA_AGENT_LLM_PROVIDER=b-api-openai
      - B_API_KEY=${B_API_KEY:-}
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}

      # Repository Upload (optional)
      - WLO_GUEST_USERNAME=${WLO_GUEST_USERNAME:-}
      - WLO_GUEST_PASSWORD=${WLO_GUEST_PASSWORD:-}

      # Worker Settings
      - METADATA_AGENT_DEFAULT_MAX_WORKERS=10
      - METADATA_AGENT_REQUEST_TIMEOUT=60

      # App Settings
      - METADATA_AGENT_DEBUG=false
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
```

### 3.5 Dockerfile-Aufbau im Detail

Das Dockerfile nutzt einen Multi-Stage Build für optimale Image-Größe:

```
┌─────────────────────────────────────────────┐
│ Stage 1: builder (python:3.12-slim)         │
│                                             │
│  pip install -r requirements.txt            │
│  → Installiert alle Python-Pakete           │
│  → Wird am Ende verworfen (nur Pakete       │
│    werden kopiert)                           │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│ Stage 2: production (python:3.12-slim)      │
│                                             │
│  1. System-Dependencies (apt-get)           │
│     → libnss3, libgbm1, fonts, etc.         │
│     → Benötigt für headless Chromium        │
│                                             │
│  2. Python-Pakete (COPY --from=builder)     │
│     → site-packages + bin                   │
│                                             │
│  3. Playwright Chromium (playwright install) │
│     → Lädt Chromium-Browser herunter        │
│                                             │
│  4. App-Code (COPY src/ ./src/)             │
│     → Inklusive static/widget/ falls        │
│       vorhanden                             │
│                                             │
│  5. Non-Root User (appuser, UID 1000)       │
│                                             │
│  6. Healthcheck + CMD (uvicorn)             │
└─────────────────────────────────────────────┘
```

**Warum Multi-Stage?**
- Build-Tools (pip, gcc, etc.) landen nicht im finalen Image
- Kleinere Angriffsfläche (kein Compiler im Produktionsimage)
- Bessere Layer-Caching-Effizienz

### 3.6 Playwright im Docker-Container

Im Docker-Image wird Playwright vollständig vorinstalliert:

- **Python-Paket** — via `requirements.txt` im Builder-Stage
- **Chromium-Browser** — via `playwright install chromium` im Dockerfile
- **System-Dependencies** — via `apt-get install` (17 Pakete)

Wichtige System-Bibliotheken für Chromium:

| Paket | Zweck |
|---|---|
| `libnss3`, `libnspr4` | Network Security Services |
| `libdbus-1-3` | D-Bus Message Bus |
| `libatk1.0-0`, `libatk-bridge2.0-0` | Accessibility Toolkit |
| `libcups2` | CUPS Printing |
| `libdrm2` | DRM Rendering |
| `libxkbcommon0` | Keyboard |
| `libatspi2.0-0` | Accessibility |
| `libxcomposite1`, `libxdamage1`, `libxfixes3`, `libxrandr2` | X11 Extensions |
| `libgbm1` | Generic Buffer Management |
| `libpango-1.0-0`, `libcairo2` | Text/Grafik-Rendering |
| `libasound2` | Audio (wird benötigt, aber nicht genutzt) |
| `libx11-xcb1` | X11/XCB |
| `fonts-liberation`, `fonts-noto-color-emoji` | Schriften |

**Screenshot-Test im Container:**

```bash
# Testen ob Playwright im Container funktioniert
docker exec metadata-agent-api python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://example.com')
    print(f'Title: {page.title()}')
    browser.close()
    print('Playwright OK')
"

# Screenshot über API
curl -X POST http://localhost:8000/screenshot \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "method": "playwright"}'
```

### 3.7 Container-Management

```bash
# Status prüfen
docker ps
docker inspect metadata-agent-api

# Logs
docker logs metadata-agent-api
docker logs -f metadata-agent-api          # Live-Logs
docker logs --tail 100 metadata-agent-api  # Letzte 100 Zeilen

# Health Check manuell
docker inspect --format='{{.State.Health.Status}}' metadata-agent-api

# Container neustarten
docker restart metadata-agent-api

# Container stoppen und entfernen
docker stop metadata-agent-api && docker rm metadata-agent-api

# Shell im Container
docker exec -it metadata-agent-api bash

# Ressourcenverbrauch
docker stats metadata-agent-api
```

### 3.8 Volumes und Persistenz

Die API ist stateless — kein persistenter Speicher nötig. Optional können Konfigurationsdateien gemountet werden:

```yaml
# docker-compose.yml Ergänzung
services:
  metadata-agent-api:
    # ...
    volumes:
      # Eigene .env-Datei (statt environment-Block)
      - ./.env:/app/.env:ro

      # Optional: Eigene Schemata einbinden
      # - ./custom-schemata:/app/src/schemata/custom:ro
```

### 3.9 Multi-Container-Setup (mehrere LLM-Provider)

Die `docker-compose.yml` enthält auskommentierte Beispiele für parallele Container mit unterschiedlichen LLM-Providern:

```yaml
services:
  # Standard: B-API OpenAI (Port 8000)
  metadata-agent-api:
    build: .
    ports: ["8000:8000"]
    environment:
      - METADATA_AGENT_LLM_PROVIDER=b-api-openai
      - B_API_KEY=${B_API_KEY}

  # Alternativ: B-API AcademicCloud / DeepSeek (Port 8001)
  metadata-agent-api-academiccloud:
    build: .
    ports: ["8001:8000"]
    environment:
      - METADATA_AGENT_LLM_PROVIDER=b-api-academiccloud
      - B_API_KEY=${B_API_KEY}

  # Alternativ: Natives OpenAI (Port 8002)
  metadata-agent-api-openai:
    build: .
    ports: ["8002:8000"]
    environment:
      - METADATA_AGENT_LLM_PROVIDER=openai
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - METADATA_AGENT_OPENAI_MODEL=gpt-4o-mini
```

### 3.10 Docker-Image optimieren

`.dockerignore` erstellen:

```
.git
.github
.env
.env.*
*.md
docs/
tests/
__pycache__
*.pyc
.pytest_cache
venv/
.venv/
node_modules/
```

**Layer-Caching optimieren:**

Die aktuelle Reihenfolge im Dockerfile ist bereits optimal:

1. `requirements.txt` → selten geändert → gecacht
2. System-Dependencies → selten geändert → gecacht
3. `playwright install chromium` → selten geändert → gecacht
4. `COPY src/` → häufig geändert → schneller Rebuild

### 3.11 Troubleshooting Docker

| Problem | Ursache | Lösung |
|---|---|---|
| `playwright install chromium` schlägt fehl | Netzwerkproblem | `docker build --network host` |
| Container startet, aber Health Check schlägt fehl | Port nicht exposed | `docker run -p 8000:8000` prüfen |
| `permission denied` im Container | Root-User nötig | Im Dockerfile: Non-Root User prüfen |
| Image zu groß (>2 GB) | Kein Multi-Stage Build | Dockerfile prüfen, `.dockerignore` anlegen |
| Chromium-Crash im Container | Zu wenig Shared Memory | `docker run --shm-size=256m` |
| `Error: Could not find expected browser` | Playwright nicht installiert | `playwright install chromium` im Dockerfile prüfen |
| Container OOM (Out of Memory) | Chromium benötigt RAM | `--memory 1g` oder mehr |

---

## 4. GitHub Actions — Automatischer Docker-Build

### 4.1 Aktueller Workflow (Docker Hub)

Der Workflow `.github/workflows/docker-publish.yml` baut das Image und pusht es zu **Docker Hub**:

```yaml
name: Docker Build & Publish

on:
  push:
    branches: [ main ]
    tags: [ 'v*' ]
  pull_request:
    branches: [ main ]
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: azure/docker-login@v1
        with:
          username: ${{ github.repository_owner }}
          password: ${{ secrets.DOCKERHUB_PASSWORD }}
      - name: Inject slug/short variables
        uses: rlespinasse/github-slug-action@v4
      - name: Build Docker image
        run: docker build --tag ${{ github.repository }}:${{ env.GITHUB_REF_SLUG }} .
      - name: Publish to DockerHub
        run: docker push ${{ github.repository }}:${{ env.GITHUB_REF_SLUG }}
```

**Ergebnis:**
- Push auf `main` → Image-Tag `main`
- Git-Tag `v2.0.0` → Image-Tag `v2.0.0`
- Image auf Docker Hub: `openeduhub/metadata-agent-api`

**Docker Hub:** https://hub.docker.com/r/openeduhub/metadata-agent-api

**Pull:**

```bash
docker pull openeduhub/metadata-agent-api:main
```

### 4.2 GitHub Secrets einrichten

Navigiere zu **Repository → Settings → Secrets and variables → Actions** und erstelle:

| Secret | Wert | Zweck |
|---|---|---|
| `DOCKERHUB_PASSWORD` | Docker Hub Passwort oder Access Token | Push zu Docker Hub |

> **Hinweis:** Das `DOCKERHUB_PASSWORD` Secret wird als Organisations-Secret verwaltet und ist für alle Repos der `openeduhub`-Organisation verfügbar (gleicher Wert wie bei `metadata-agent-canvas`).

### 4.3 Workflow-Trigger

| Trigger | Tag | Beschreibung |
|---|---|---|
| Push auf `main` | `main` | Automatisch bei jedem Merge/Push |
| Git-Tag `v*` | z.B. `v2.0.0` | Für Releases: `git tag v2.0.0 && git push --tags` |
| Pull Request | — | Nur Build, kein Push |
| Manual Dispatch | Branch-Slug | Über GitHub Actions UI |

### 4.4 Release erstellen (neuen Tag pushen)

```bash
# Version taggen
git tag v2.0.0
git push origin v2.0.0

# → GitHub Actions baut und pusht: openeduhub/metadata-agent-api:v2.0.0
```

### 4.5 Webhook-basierter Auto-Redeploy

**Variante A: Watchtower (einfachste Lösung)**

```yaml
# docker-compose.yml auf dem Server
services:
  metadata-agent-api:
    image: openeduhub/metadata-agent-api:main
    ports: ["8000:8000"]
    env_file: .env
    restart: unless-stopped

  # Watchtower prüft regelmäßig auf Image-Updates
  watchtower:
    image: containrrr/watchtower
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - WATCHTOWER_POLL_INTERVAL=300       # Alle 5 Minuten prüfen
      - WATCHTOWER_CLEANUP=true            # Alte Images entfernen
      - WATCHTOWER_INCLUDE_STOPPED=false
      - WATCHTOWER_LABEL_ENABLE=false      # Alle Container überwachen
    restart: unless-stopped
```

**Variante B: SSH-Deploy nach Build**

Erweitere den Workflow um ein Deployment-Step:

```yaml
  deploy:
    needs: build-and-push
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'

    steps:
      - name: Deploy to server via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: ${{ secrets.DEPLOY_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            cd /opt/metadata-agent
            docker compose pull
            docker compose up -d --force-recreate
            docker image prune -f
```

Dafür zusätzliche GitHub Secrets anlegen:

| Secret | Wert |
|---|---|
| `DEPLOY_HOST` | IP oder Hostname des Servers |
| `DEPLOY_USER` | SSH-Benutzername |
| `DEPLOY_SSH_KEY` | SSH Private Key |

---

## 5. Umgebungsvariablen — Vollständige Referenz

Alle Variablen können in `.env`, als System-Umgebungsvariablen oder als Docker-Environment gesetzt werden. Prefix: `METADATA_AGENT_` (außer API-Keys).

### API-Keys (ohne Prefix)

| Variable | Erforderlich | Beschreibung |
|---|---|---|
| `B_API_KEY` | ✅¹ | B-API Key für OpenEduHub LLM-Zugriff |
| `OPENAI_API_KEY` | ✅¹ | OpenAI API Key (nur für `provider=openai`) |
| `WLO_GUEST_USERNAME` | für `/upload` | WLO Repository Upload-Benutzername |
| `WLO_GUEST_PASSWORD` | für `/upload` | WLO Repository Upload-Passwort |
| `WLO_REPOSITORY_BASE_URL` | optional | Custom Repository-URL (überschreibt Staging/Prod) |

> ¹ Mindestens einer der LLM-Keys ist erforderlich.

### LLM-Provider

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` | `b-api-openai`, `b-api-academiccloud`, `openai` |
| `METADATA_AGENT_LLM_TEMPERATURE` | `0.3` | Kreativität (0.0–1.0) |
| `METADATA_AGENT_LLM_MAX_TOKENS` | `2000` | Max Tokens pro LLM-Aufruf |
| `METADATA_AGENT_LLM_MAX_RETRIES` | `3` | Wiederholungsversuche bei Fehler |
| `METADATA_AGENT_LLM_RETRY_DELAY` | `1.0` | Wartezeit zwischen Retries (Sek.) |

### Provider-spezifische Einstellungen

| Variable | Default |
|---|---|
| `METADATA_AGENT_B_API_OPENAI_BASE` | `https://b-api.staging.openeduhub.net/api/v1/llm/openai` |
| `METADATA_AGENT_B_API_OPENAI_MODEL` | `gpt-4.1-mini` |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_BASE` | `https://b-api.staging.openeduhub.net/api/v1/llm/academiccloud` |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL` | `deepseek-r1` |
| `METADATA_AGENT_OPENAI_API_BASE` | `https://api.openai.com/v1` |
| `METADATA_AGENT_OPENAI_MODEL` | `gpt-4o-mini` |

### Worker & Performance

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_DEFAULT_MAX_WORKERS` | `10` | Parallele LLM-Aufrufe |
| `METADATA_AGENT_REQUEST_TIMEOUT` | `60` | HTTP-Timeout (Sekunden) |

### Schema

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_DEFAULT_CONTEXT` | `default` | Standard-Kontext (`default`, `mds_oeh`) |
| `METADATA_AGENT_DEFAULT_VERSION` | `1.8.1` | Standard-Version (`latest` oder z.B. `1.8.1`) |

### Repository & Crawler

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_REPOSITORY_PROD_URL` | `https://redaktion.openeduhub.net/edu-sharing/rest` | Prod-Repository |
| `METADATA_AGENT_REPOSITORY_STAGING_URL` | `https://repository.staging.openeduhub.net/edu-sharing/rest` | Staging-Repository |
| `METADATA_AGENT_REPOSITORY_DEFAULT` | `staging` | Standard-Repository: `staging` oder `prod` |
| `METADATA_AGENT_TEXT_EXTRACTION_API_URL` | `https://text-extraction.staging.openeduhub.net` | Text-Extraction API |
| `METADATA_AGENT_TEXT_EXTRACTION_DEFAULT_METHOD` | `simple` | `simple` oder `browser` |

### Screenshot

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_SCREENSHOT_METHOD` | `pageshot` | `pageshot` (extern) oder `playwright` (intern) |
| `METADATA_AGENT_SCREENSHOT_WIDTH` | `800` | Viewport-Breite |
| `METADATA_AGENT_SCREENSHOT_HEIGHT` | `500` | Viewport-Höhe |
| `METADATA_AGENT_SCREENSHOT_DELAY` | `2000` | Wartezeit vor Aufnahme (ms) |
| `METADATA_AGENT_PAGESHOT_API_URL` | `https://pageshot.site/v1/screenshot` | PageShot API URL |

### Sonstiges

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_CORS_ORIGINS` | `*` | CORS-Origins (kommasepariert oder `*`) |
| `METADATA_AGENT_NORMALIZATION_ENABLED` | `true` | Normalisierung aktiv |
| `METADATA_AGENT_DEBUG` | `false` | Debug-Modus |

---

## 6. Deployment-Varianten

### 6.1 Docker Hub (empfohlen für Kubernetes)

Das vorgefertigte Image von Docker Hub verwenden:

```bash
docker pull openeduhub/metadata-agent-api:main

docker run -d \
  --name metadata-agent-api \
  -p 8000:8000 \
  -e B_API_KEY=<key> \
  openeduhub/metadata-agent-api:main
```

Vorteile: Playwright/Chromium vorinstalliert, Health Check, einfaches Update, reproduzierbar.

### 6.2 Vercel (Serverless)

Das Projekt ist für Vercel vorkonfiguriert (`vercel.json`).

```bash
npm i -g vercel
vercel --prod
```

Einschränkungen:
- Playwright **nicht** verfügbar — nur `pageshot` (extern)
- Max. 60s Funktionslaufzeit
- Max. 50 MB Lambda-Größe
- Widget-Dateien müssen in `src/static/widget/dist/` committet sein

Umgebungsvariablen in **Vercel Dashboard → Settings → Environment Variables** konfigurieren.

### 6.3 Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: metadata-agent-api
  labels:
    app: metadata-agent-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: metadata-agent-api
  template:
    metadata:
      labels:
        app: metadata-agent-api
    spec:
      containers:
        - name: metadata-agent-api
          image: openeduhub/metadata-agent-api:main
          ports:
            - containerPort: 8000
          env:
            - name: B_API_KEY
              valueFrom:
                secretKeyRef:
                  name: metadata-agent-secrets
                  key: b-api-key
            - name: WLO_GUEST_USERNAME
              valueFrom:
                secretKeyRef:
                  name: metadata-agent-secrets
                  key: wlo-username
            - name: WLO_GUEST_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: metadata-agent-secrets
                  key: wlo-password
            - name: METADATA_AGENT_LLM_PROVIDER
              value: "b-api-openai"
            - name: METADATA_AGENT_DEFAULT_MAX_WORKERS
              value: "10"
            - name: METADATA_AGENT_SCREENSHOT_METHOD
              value: "playwright"
          resources:
            requests:
              cpu: 500m
              memory: 512Mi
            limits:
              cpu: 2000m
              memory: 2Gi
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: metadata-agent-api
spec:
  selector:
    app: metadata-agent-api
  ports:
    - port: 80
      targetPort: 8000
  type: ClusterIP
```

**Secrets anlegen:**

```bash
kubectl create secret generic metadata-agent-secrets \
  --from-literal=b-api-key='<B_API_KEY>' \
  --from-literal=wlo-username='<WLO_GUEST_USERNAME>' \
  --from-literal=wlo-password='<WLO_GUEST_PASSWORD>'
```

**Ressourcen-Empfehlung:**

| Ressource | Request | Limit |
|---|---|---|
| CPU | 500m | 2000m |
| Memory | 512Mi | 2Gi |

> Das Image enthält Playwright + Chromium (~400 MB). Wenn nur `pageshot` (externe API) genutzt wird, reicht weniger Memory.

---

## 7. Troubleshooting

### Allgemein

| Problem | Lösung |
|---|---|
| `ModuleNotFoundError: No module named 'src'` | Aus dem richtigen Verzeichnis starten (`metadata-agent-api/`) |
| `B_API_KEY not configured` | `.env` prüfen oder `B_API_KEY` setzen |
| CORS-Fehler im Browser | `METADATA_AGENT_CORS_ORIGINS` auf die Domain setzen oder `*` |
| Timeout bei LLM-Aufrufen | `METADATA_AGENT_REQUEST_TIMEOUT` erhöhen |
| Felder werden nicht extrahiert | Schema prüfen: `ai_fillable: true` im Schema? |

### Playwright / Screenshots

| Problem | Lösung |
|---|---|
| `Executable doesn't exist` | `playwright install chromium` ausführen |
| `Host system is missing dependencies` | `playwright install-deps chromium` (Linux) |
| Screenshot ist leer/weiß | `screenshot_delay` erhöhen (Seite braucht Ladezeit) |
| Chromium-Crash (SIGTERM) | `--shm-size=256m` bei Docker, oder RAM erhöhen |
| `playwright` nicht als Screenshot-Methode verfügbar | Auf Vercel nicht möglich, `pageshot` verwenden |

### Docker

| Problem | Lösung |
|---|---|
| Build schlägt bei `playwright install` fehl | Netzwerkzugriff im Build prüfen, `--network host` |
| Container startet, Port nicht erreichbar | `docker ps` → Port-Mapping prüfen |
| Container-Restart-Loop | `docker logs metadata-agent-api` → Fehlermeldung prüfen |
| `permission denied` auf Dateien | User-Ownership im Dockerfile prüfen |
| Sehr langsamer Build | `.dockerignore` erstellen (`.git`, `node_modules`, `venv`) |

### GitHub Actions

| Problem | Lösung |
|---|---|
| Push zu Docker Hub schlägt fehl | `DOCKERHUB_PASSWORD` Secret prüfen (Settings → Secrets → Actions) |
| Build-Cache funktioniert nicht | GitHub Actions Cache prüfen |
| Deploy-Step: SSH Connection refused | `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` prüfen |
