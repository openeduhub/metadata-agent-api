# Metadata Agent API

REST-API zur automatischen Extraktion von Metadaten aus Texten mittels KI.

## Inhaltsverzeichnis

- [Installation](#installation)
- [API-Endpunkte](#api-endpunkte)
- [Nutzungsbeispiele](#nutzungsbeispiele)
- [Umgebungsvariablen](#umgebungsvariablen)
- [Deployment](#deployment)

---

## Installation

### Voraussetzungen

- Python 3.12+
- API-Key (B-API oder OpenAI)

### Option 1: Docker (empfohlen)

```bash
# Repository klonen
git clone https://github.com/your-org/metadata-agent-api.git
cd metadata-agent-api

# Umgebungsvariablen konfigurieren
cp .env.template .env
# .env bearbeiten und API-Keys eintragen

# Starten
docker-compose up -d

# Prüfen
curl http://localhost:8000/health
```

### Option 2: Lokal mit Python

```bash
# Repository klonen
git clone https://github.com/your-org/metadata-agent-api.git
cd metadata-agent-api

# Virtual Environment erstellen
python -m venv venv

# Aktivieren
source venv/bin/activate      # Linux/Mac
venv\Scripts\activate         # Windows

# Dependencies installieren
pip install -r requirements.txt

# Umgebungsvariablen konfigurieren
cp .env.template .env
# .env bearbeiten und API-Keys eintragen

# Starten
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### Option 3: Railway / Render / Fly.io (empfohlen für Serverless)

Diese Plattformen unterstützen komplexe Python-Projekte besser als Vercel:

**Railway:**
```bash
# Railway CLI installieren
npm i -g @railway/cli

# Projekt deployen
railway login
railway init
railway up
```

**Render:**
1. Repository mit Render verbinden
2. "Web Service" erstellen
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn src.main:app --host 0.0.0.0 --port $PORT`

> ⚠️ **Vercel:** Nur eingeschränkt nutzbar für diese API aufgrund der komplexen Projektstruktur.

---

## API-Endpunkte

Die interaktive Dokumentation ist unter `/docs` (Swagger) oder `/redoc` verfügbar.

### POST /generate

Extrahiert Metadaten aus Text oder anderen Quellen.

#### Request-Parameter

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| **Input (einer erforderlich)** ||||
| `text` | string | - | Direkter Text zur Analyse |
| `input_source` | enum | `text` | Eingabequelle: `text`, `url`, `node_id`, `node_url` |
| `source_url` | string | - | URL für `url` oder `node_url` Input |
| `node_id` | string | - | Repository Node-ID für `node_id` oder `node_url` |
| `repository` | enum | `staging` | Repository: `staging` oder `prod` |
| `extraction_method` | enum | `simple` | Text-Extraktion: `simple` oder `browser` |
| **Schema** ||||
| `context` | string | `default` | Schema-Kontext |
| `version` | string | `latest` | Schema-Version (`latest` oder z.B. `1.8.0`) |
| `schema_file` | string | `auto` | Schema-Datei oder `auto` für Erkennung |
| **Optionen** ||||
| `language` | string | `de` | Sprache: `de` oder `en` |
| `max_workers` | int | `10` | Parallele LLM-Aufrufe (1-20) |
| `include_core` | bool | `true` | Core-Felder (Titel, Keywords) einbeziehen |
| `enable_geocoding` | bool | `true` | Adressen zu Koordinaten konvertieren |
| `normalize_output` | bool | `true` | Ausgabe normalisieren |
| **LLM** ||||
| `llm_provider` | enum | aus .env | `openai`, `b-api-openai`, `b-api-academiccloud` |
| `llm_model` | string | aus .env | z.B. `gpt-4.1-mini`, `gpt-4o-mini`, `deepseek-r1` |
| **Updates** ||||
| `existing_metadata` | object | - | Bestehende Metadaten als Basis |
| `regenerate_fields` | array | - | Nur diese Felder neu extrahieren |
| `regenerate_empty` | bool | `false` | Nur leere Felder neu extrahieren |

#### Input-Quellen

| `input_source` | Beschreibung | Erforderliche Parameter |
|----------------|--------------|------------------------|
| `text` | Direkter Text | `text` |
| `url` | Text von URL extrahieren | `source_url`, optional `extraction_method` |
| `node_id` | Text aus Repository-Node | `node_id`, optional `repository` |
| `node_url` | Repository-Metadaten + URL-Text | `node_id`, optional `source_url` (aus `ccm:wwwurl`) |

#### Schema-Dateien

| Schema | Beschreibung |
|--------|--------------|
| `auto` | Automatische Erkennung |
| `core.json` | Nur Core-Felder (Titel, Beschreibung, Keywords) |
| `event.json` | Veranstaltungen, Workshops, Seminare |
| `learning_material.json` | Lernmaterialien, Arbeitsblätter |
| `education_offer.json` | Bildungsangebote, Kurse |
| `person.json` | Personen, Autoren |
| `organization.json` | Organisationen, Institutionen |
| `tool_service.json` | Tools und Dienste |
| `source.json` | Quellen |

#### Response

```json
{
  "contextName": "default",
  "schemaVersion": "1.8.0",
  "metadataset": "event.json",
  "language": "de",
  "exportedAt": "2026-01-23T08:00:00.000Z",
  "metadata": {
    "cclom:title": "Workshop KI in der Bildung",
    "cclom:general_description": "...",
    "cclom:general_keyword": ["KI", "Bildung"],
    "schema:startDate": "2026-03-15T09:00",
    "schema:location": [{"address": {...}, "geo": {"latitude": 52.52, "longitude": 13.40}}]
  },
  "processing": {
    "success": true,
    "fields_extracted": 15,
    "fields_total": 41,
    "processing_time_ms": 2500,
    "llm_provider": "b-api-openai",
    "llm_model": "gpt-4.1-mini",
    "errors": [],
    "warnings": []
  }
}
```

---

### POST /extract-field

Extrahiert ein einzelnes Feld aus Text.

#### Request-Parameter

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| `text` | string | - | Text zur Analyse (oder andere Input-Source) |
| `field_id` | string | - | Feld-ID (z.B. `cclom:title`, `schema:startDate`) |
| `context` | string | `default` | Schema-Kontext |
| `version` | string | `latest` | Schema-Version |
| `schema_file` | string | - | Schema-Datei |

#### Response

```json
{
  "field_id": "schema:startDate",
  "value": "2026-03-15T09:00",
  "success": true
}
```

---

### POST /detect-content-type

Erkennt den Inhaltstyp eines Textes.

#### Request-Parameter

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| `text` | string | - | Text zur Analyse |
| `context` | string | `default` | Schema-Kontext |
| `version` | string | `latest` | Schema-Version |

#### Response

```json
{
  "content_type": "event.json",
  "confidence": 0.95,
  "alternatives": [
    {"content_type": "education_offer.json", "confidence": 0.3}
  ]
}
```

---

### POST /validate

Validiert Metadaten gegen das Schema.

#### Request-Parameter

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| `metadata` | object | - | Metadaten-Objekt (vom /generate Endpoint) |
| `context` | string | `auto` | Schema-Kontext |
| `version` | string | `auto` | Schema-Version |
| `schema_file` | string | `auto` | Schema-Datei |

#### Validierungsprüfungen

- Pflichtfelder vorhanden
- Datentyp-Prüfung (string, number, boolean, array)
- Datum/Zeit-Formate (ISO 8601)
- URL-Formate
- Geo-Koordinaten-Bereiche
- Vokabular-Prüfung mit Fuzzy-Matching

#### Response

```json
{
  "valid": true,
  "errors": [],
  "warnings": [
    {
      "field": "oeh:eventType",
      "message": "Wert 'Worksho' nicht im Vokabular. Meinten Sie: 'Workshop'?"
    }
  ],
  "coverage": 85.5
}
```

---

### POST /export/markdown

Exportiert Metadaten als lesbares Markdown.

#### Request-Parameter

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| `metadata` | object | - | Metadaten-Objekt |
| `language` | string | `de` | Ausgabesprache |
| `include_empty` | bool | `false` | Leere Felder anzeigen |

#### Response

```json
{
  "markdown": "# Workshop KI in der Bildung\n\n**Datum:** 15.03.2026\n..."
}
```

---

### POST /upload

Lädt Metadaten ins WLO Repository hoch.

> Erfordert `WLO_GUEST_USERNAME` und `WLO_GUEST_PASSWORD` Umgebungsvariablen.

#### Request-Parameter

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| `metadata` | object | - | Metadaten-Objekt (vom /generate Endpoint) |
| `repository` | enum | `staging` | `staging` oder `prod` |
| `check_duplicates` | bool | `true` | Dublettenprüfung via `ccm:wwwurl` |
| `start_workflow` | bool | `true` | Review-Workflow starten |

#### Repositories

| Name | URL |
|------|-----|
| `staging` | https://repository.staging.openeduhub.net/edu-sharing |
| `prod` | https://redaktion.openeduhub.net/edu-sharing |

#### Response (Erfolg)

```json
{
  "success": true,
  "repository": "staging",
  "node": {
    "nodeId": "abc123-def456-...",
    "title": "Workshop KI in der Bildung",
    "wwwurl": "https://example.com/workshop",
    "repositoryUrl": "https://repository.staging.openeduhub.net/edu-sharing/components/render/abc123-..."
  }
}
```

#### Response (Dublette)

```json
{
  "success": false,
  "duplicate": true,
  "node": {"nodeId": "existing-id", "title": "..."},
  "error": "URL existiert bereits"
}
```

---

### GET /health

Health-Check für Monitoring.

```json
{"status": "healthy", "version": "1.0.0"}
```

---

### GET /info/schemata

Listet verfügbare Schema-Kontexte.

```json
{
  "contexts": {
    "default": {"name": "WLO/OEH Standard", "defaultVersion": "1.8.0"},
    "mds_oeh": {"name": "MDS OEH", "defaultVersion": "1.8.0"}
  },
  "defaultContext": "default"
}
```

---

### GET /info/schemas/{context}/{version}

Listet Schemas für einen Kontext/Version.

```json
{
  "schemas": ["core.json", "event.json", "learning_material.json", ...]
}
```

---

### GET /info/schema/{context}/{version}/{schema_file}

Gibt Schema-Definition zurück.

---

## Nutzungsbeispiele

### curl - Einfache Extraktion

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Workshop KI in der Bildung am 15. März 2026 in Berlin",
    "schema_file": "event.json"
  }'
```

### curl - Mit URL als Quelle

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
    "input_source": "url",
    "source_url": "https://example.com/event",
    "schema_file": "auto"
  }'
```

### curl - Mit Repository Node

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
    "input_source": "node_url",
    "node_id": "abc123-def456",
    "repository": "staging",
    "schema_file": "event.json"
  }'
```

### Python - Vollständiger Workflow

```python
import httpx

API = "http://localhost:8000"

# 1. Metadaten generieren
response = httpx.post(f"{API}/generate", json={
    "text": """
    Fortbildung: Digitale Werkzeuge im Unterricht
    
    Am 20. April 2026 in der Stadthalle Hamburg,
    Dammtorwall 10, 20355 Hamburg.
    
    Zielgruppe: Lehrkräfte aller Schulformen
    Kosten: 49 Euro
    """,
    "schema_file": "event.json",
    "enable_geocoding": True
})
result = response.json()

print(f"Schema: {result['metadataset']}")
print(f"Felder: {result['processing']['fields_extracted']}/{result['processing']['fields_total']}")

# 2. Validieren
validation = httpx.post(f"{API}/validate", json={
    "metadata": result
}).json()

print(f"Valid: {validation['valid']}, Coverage: {validation['coverage']}%")

# 3. Hochladen
if validation['valid']:
    upload = httpx.post(f"{API}/upload", json={
        "metadata": result,
        "repository": "staging"
    }).json()
    
    if upload['success']:
        print(f"Hochgeladen: {upload['node']['repositoryUrl']}")
```

### JavaScript/TypeScript

```typescript
const API = "http://localhost:8000";

// Metadaten generieren
const response = await fetch(`${API}/generate`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    text: "Workshop am 15.03.2026 in Berlin",
    schema_file: "event.json"
  })
});

const result = await response.json();
console.log(result.metadata["cclom:title"]);
```

---

## Umgebungsvariablen

Alle Variablen können in `.env` oder als System-Umgebungsvariablen gesetzt werden.

### API-Keys (ohne Prefix)

| Variable | Beschreibung |
|----------|--------------|
| `B_API_KEY` | B-API Key für OpenEduHub LLM-Zugriff |
| `OPENAI_API_KEY` | OpenAI API Key (für `provider=openai`) |
| `WLO_GUEST_USERNAME` | WLO Repository Upload Username |
| `WLO_GUEST_PASSWORD` | WLO Repository Upload Password |

### LLM-Konfiguration

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` | Provider: `openai`, `b-api-openai`, `b-api-academiccloud` |
| `METADATA_AGENT_LLM_TEMPERATURE` | `0.3` | Kreativität (0.0-1.0) |
| `METADATA_AGENT_LLM_MAX_TOKENS` | `2000` | Max Tokens pro Anfrage |
| `METADATA_AGENT_LLM_MAX_RETRIES` | `3` | Wiederholungsversuche |

### Provider-spezifisch

**OpenAI:**

| Variable | Default |
|----------|---------|
| `METADATA_AGENT_OPENAI_API_BASE` | `https://api.openai.com/v1` |
| `METADATA_AGENT_OPENAI_MODEL` | `gpt-4o-mini` |

**B-API OpenAI:**

| Variable | Default |
|----------|---------|
| `METADATA_AGENT_B_API_OPENAI_BASE` | `https://b-api.staging.openeduhub.net/api/v1/llm/openai` |
| `METADATA_AGENT_B_API_OPENAI_MODEL` | `gpt-4.1-mini` |

**B-API AcademicCloud:**

| Variable | Default |
|----------|---------|
| `METADATA_AGENT_B_API_ACADEMICCLOUD_BASE` | `https://b-api.staging.openeduhub.net/api/v1/llm/academiccloud` |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL` | `deepseek-r1` |

### Worker & Performance

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `METADATA_AGENT_DEFAULT_MAX_WORKERS` | `10` | Parallele LLM-Aufrufe |
| `METADATA_AGENT_REQUEST_TIMEOUT` | `60` | Timeout in Sekunden |

### Schema-Defaults

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `METADATA_AGENT_DEFAULT_CONTEXT` | `default` | Standard-Kontext |
| `METADATA_AGENT_DEFAULT_VERSION` | `latest` | Standard-Version |

### Input Sources

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `METADATA_AGENT_REPOSITORY_PROD_URL` | Prod URL | Repository Production |
| `METADATA_AGENT_REPOSITORY_STAGING_URL` | Staging URL | Repository Staging |
| `METADATA_AGENT_REPOSITORY_DEFAULT` | `staging` | Standard-Repository |
| `METADATA_AGENT_TEXT_EXTRACTION_API_URL` | Staging URL | Text-Extraktion API |
| `METADATA_AGENT_TEXT_EXTRACTION_DEFAULT_METHOD` | `simple` | `simple` oder `browser` |

### Sonstige

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `METADATA_AGENT_DEBUG` | `false` | Debug-Logging |
| `METADATA_AGENT_NORMALIZATION_ENABLED` | `true` | Ausgabe-Normalisierung |

---

## Deployment

### Docker

```bash
# Mit docker-compose
docker-compose up -d

# Manuell
docker build -t metadata-agent-api .
docker run -d -p 8000:8000 \
  -e B_API_KEY=your-key \
  -e METADATA_AGENT_LLM_PROVIDER=b-api-openai \
  metadata-agent-api
```

### Railway

```bash
railway login
railway init
railway up
```

### Render

1. Repository verbinden
2. Web Service erstellen
3. Start Command: `uvicorn src.main:app --host 0.0.0.0 --port $PORT`

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: metadata-agent-api
spec:
  replicas: 2
  template:
    spec:
      containers:
      - name: api
        image: metadata-agent-api:latest
        ports:
        - containerPort: 8000
        env:
        - name: B_API_KEY
          valueFrom:
            secretKeyRef:
              name: api-secrets
              key: b-api-key
```

---

## Lizenz

MIT
