# Metadata Agent API

REST-API zur automatischen Extraktion von Metadaten aus Texten mittels KI (LLM).

Generiert strukturierte Metadaten nach dem [WLO/OEH-Schema](https://wirlernenonline.de) aus beliebigen Texten, URLs oder Repository-Nodes — inklusive Normalisierung, Geocoding, Validierung und Repository-Upload.

## Inhaltsverzeichnis

- [Quickstart](#quickstart)
- [Architektur](#architektur)
- [API-Endpunkte](#api-endpunkte)
  - [POST /generate](#post-generate)
  - [POST /extract-field](#post-extract-field)
  - [POST /detect-content-type](#post-detect-content-type)
  - [POST /validate](#post-validate)
  - [POST /export/markdown](#post-exportmarkdown)
  - [POST /upload](#post-upload)
  - [POST /upload/verify/{node_id}](#post-uploadverifynodeid)
  - [Info-Endpunkte](#info-endpunkte)
- [Nutzungsbeispiele](#nutzungsbeispiele)
- [Umgebungsvariablen](#umgebungsvariablen)
- [Widget / Webkomponente](#widget--webkomponente)
- [Deployment](#deployment)

---

## Quickstart

### Voraussetzungen

- Python 3.12+
- API-Key: **B-API Key** (Standard) oder **OpenAI API Key**

### Lokal starten

```bash
# Repository klonen
git clone <repo-url>
cd metadata-agent-api

# Virtual Environment
python -m venv venv
source venv/bin/activate      # Linux/Mac
venv\Scripts\activate         # Windows

# Dependencies
pip install -r requirements.txt

# API-Key konfigurieren
echo "B_API_KEY=dein-key-hier" > .env

# Starten
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### Mit Docker

```bash
docker-compose up -d
# oder manuell:
docker build -t metadata-agent-api .
docker run -d -p 8000:8000 -e B_API_KEY=dein-key metadata-agent-api
```

### Testen

```bash
curl http://localhost:8000/health
# → {"status": "healthy", "version": "1.0.0"}

curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"text": "Workshop KI in der Bildung am 15. März 2026 in Berlin"}'
```

Die interaktive API-Dokumentation ist unter **`/docs`** (Swagger UI) oder **`/redoc`** verfügbar.

---

## Architektur

```
Request → Input Source → LLM Extraction → Normalization → Response
              │                │                │
              ├─ Text          ├─ Parallel       ├─ Field Normalizer
              ├─ URL/Crawler   │  Field          ├─ Output Normalizer
              ├─ Node ID       │  Extraction     ├─ Geocoding
              └─ Node+URL      └─ (Semaphore)    └─ Vocabulary Matching
```

**Kernkomponenten:**

| Komponente | Beschreibung |
|------------|--------------|
| `main.py` | FastAPI-Endpunkte, Request-Routing, Input-Handling |
| `llm_service.py` | LLM-API-Aufrufe, Prompt-Building, Retry-Logik, Wert-Normalisierung |
| `metadata_service.py` | Orchestrierung: Schema-Erkennung, Extraktion, Validierung, Export |
| `input_source_service.py` | Text-Beschaffung aus URL, Repository-Node, Crawler |
| `field_normalizer.py` | Typ-basierte Normalisierung (Datum, Boolean, Vokabular, etc.) |
| `output_normalizer.py` | Strukturanpassung für Canvas-Webkomponente |
| `geocoding_service.py` | Adressen → Koordinaten via Photon/Komoot API |
| `repository_service.py` | Upload ins WLO edu-sharing Repository (Aspects, VCARD, Geo) |
| `schema_loader.py` | Schema-Laden, Caching, Versions-Auflösung |

---

## API-Endpunkte

### POST /generate

Generiert vollständige Metadaten aus Text, URL oder Repository-Node.

> **Tipp:** Akzeptiert auch `Content-Type: text/plain` — einfach mehrzeiligen Text senden, alle Parameter werden auf Defaults gesetzt.

#### Request (JSON)

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| **Input** ||||
| `input_source` | enum | `text` | `text`, `url`, `node_id`, `node_url` |
| `text` | string | — | Direkter Text (bei `input_source=text`) |
| `source_url` | string | — | URL (bei `input_source=url` oder `node_url`) |
| `node_id` | string | — | Repository Node-ID (bei `node_id` oder `node_url`) |
| `repository` | enum | `staging` | `staging` oder `prod` |
| `extraction_method` | enum | `browser` | `browser` (JS-Rendering, Standard) oder `simple` (schnell) |
| `output_format` | enum | `markdown` | `markdown` (Standard), `txt` (Klartext), `html` (rohes HTML) |
| **Schema** ||||
| `context` | string | `default` | Schema-Kontext |
| `version` | string | `latest` | Schema-Version (`latest` oder z.B. `1.8.0`) |
| `schema_file` | string | `auto` | Schema-Datei oder `auto` für LLM-Erkennung |
| **Optionen** ||||
| `language` | string | `de` | Sprache: `de` oder `en` |
| `max_workers` | int | `10` | Parallele LLM-Aufrufe (1–20) |
| `include_core` | bool | `true` | Core-Felder (Titel, Keywords) einbeziehen |
| `enable_geocoding` | bool | `true` | Adressen zu Koordinaten konvertieren |
| `normalize` | bool | `true` | Normalisierung (Datum, Boolean, Vokabular, Struktur) |
| **LLM-Overrides** ||||
| `llm_provider` | string | aus `.env` | `openai`, `b-api-openai`, `b-api-academiccloud` |
| `llm_model` | string | aus `.env` | z.B. `gpt-4.1-mini`, `gpt-4o-mini`, `deepseek-r1` |
| **Regeneration** ||||
| `existing_metadata` | object | — | Bestehende Metadaten als Basis |
| `regenerate_fields` | array | — | Nur diese Feld-IDs neu extrahieren |
| `regenerate_empty` | bool | `false` | Leere Felder in `existing_metadata` neu extrahieren |

#### Input-Quellen

| `input_source` | Beschreibung | Benötigt |
|----------------|--------------|----------|
| `text` | Direkter Text (Standard) | `text` |
| `url` | Text von URL via Crawler | `source_url` |
| `node_id` | Volltext + Metadaten aus Repository | `node_id` |
| `node_url` | Repository-Daten, Crawler-Fallback via `ccm:wwwurl` | `node_id` |

#### Response (flaches Format)

Die Metadaten-Felder liegen **direkt auf Top-Level** (nicht in einem `metadata`-Objekt):

```json
{
  "contextName": "default",
  "schemaVersion": "1.8.0",
  "metadataset": "event.json",
  "language": "de",
  "exportedAt": "2026-01-23T08:00:00+00:00",

  "cclom:title": "Workshop KI in der Bildung",
  "cclom:general_description": "Ein Workshop über den Einsatz von KI...",
  "cclom:general_keyword": ["KI", "Bildung", "Workshop"],
  "schema:startDate": "2026-03-15T09:00",
  "schema:location": [
    {
      "name": "Berlin",
      "address": {
        "streetAddress": "",
        "postalCode": "",
        "addressLocality": "Berlin",
        "addressRegion": "Berlin",
        "addressCountry": "DE"
      },
      "geo": { "latitude": 52.5200066, "longitude": 13.404954 }
    }
  ],

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

> **Hinweis:** Leere Default-Werte (`""`, `[]`, `{}`, `null`) werden aus der Response gefiltert — nur Felder mit tatsächlichen Werten erscheinen.

#### Verfügbare Schemas

| Schema | Beschreibung |
|--------|--------------|
| `auto` | Automatische Erkennung via LLM |
| `core.json` | Nur Core-Felder (Titel, Beschreibung, Keywords, Fach, Bildungsstufe) |
| `event.json` | Veranstaltungen, Workshops, Seminare, Konferenzen |
| `learning_material.json` | Lernmaterialien, Arbeitsblätter, Videos |
| `education_offer.json` | Bildungsangebote, Kurse, Studiengänge |
| `person.json` | Personen, Autoren, Referenten |
| `organization.json` | Organisationen, Institutionen, Vereine |
| `tool_service.json` | Tools, Software, Dienste |
| `source.json` | Quellen, Datenbanken |
| `didactic_planning_tools.json` | Didaktische Planungsinstrumente, Methoden, Unterrichtsphasen |
| `occupation.json` | Berufe, Qualifikationen, Fähigkeiten |
| `prompt.json` | KI-Prompts, Eingabe-/Ausgabeformate, Szenarien |

#### Schema-Kontexte

| Kontext | Beschreibung | Schemas |
|---------|--------------|--------|
| `default` | WLO/OEH Standard | 11 Schemas (alle) |
| `redesign_26` | Redesign 2026 | 11 Schemas (alle, angepasste Felder/Gruppen) |
| `mds_oeh` | OEH Metadatenset (kompakt) | 5 Schemas (core, event, education_offer, organization, person) |

---

### POST /extract-field

Extrahiert oder regeneriert ein einzelnes Feld. Nützlich um einzelne Felder zu korrigieren ohne alles neu zu extrahieren.

#### Request

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| `input_source` | enum | `text` | `text`, `url`, `node_id`, `node_url` |
| `text` | string | — | Text zur Analyse |
| `source_url` | string | — | URL (bei `url`/`node_url`) |
| `node_id` | string | — | Node-ID (bei `node_id`/`node_url`) |
| `repository` | enum | `staging` | `staging` oder `prod` |
| `extraction_method` | enum | `browser` | `browser` (Standard) oder `simple` |
| `output_format` | enum | `markdown` | `markdown`, `txt`, `html` |
| `schema_file` | string | **erforderlich** | Schema-Datei (z.B. `event.json`, `core.json`) |
| `field_id` | string | **erforderlich** | Feld-ID (z.B. `schema:startDate`, `cclom:title`) |
| `existing_metadata` | object | — | Bestehende Werte als Kontext |
| `context` | string | `default` | Schema-Kontext |
| `version` | string | `latest` | Schema-Version |
| `language` | string | `de` | Sprache |
| `normalize` | bool | `true` | Normalisierung anwenden |
| `llm_provider` | string | — | LLM-Provider Override |
| `llm_model` | string | — | LLM-Model Override |

#### Response

```json
{
  "field_id": "schema:startDate",
  "field_label": "Startdatum",
  "value": "2026-03-15T09:00",
  "raw_value": null,
  "previous_value": "2026-03-10T09:00",
  "changed": true,
  "normalized": false,
  "context": "default",
  "version": "1.8.0",
  "schema_file": "event.json",
  "processing": {
    "llm_provider": "b-api-openai",
    "llm_model": "gpt-4.1-mini",
    "processing_time_ms": 450
  }
}
```

| Feld | Beschreibung |
|------|--------------|
| `value` | Extrahierter (und normalisierter) Wert |
| `raw_value` | Wert vor Normalisierung (nur wenn normalisiert) |
| `previous_value` | Vorheriger Wert aus `existing_metadata` |
| `changed` | Ob sich der Wert geändert hat |
| `normalized` | Ob Normalisierung angewendet wurde |

---

### POST /detect-content-type

Erkennt den Inhaltstyp (Schema) eines Textes via LLM.

#### Request

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| `input_source` | enum | `text` | `text`, `url`, `node_id`, `node_url` |
| `text` | string | — | Text zur Analyse |
| `source_url` | string | — | URL |
| `node_id` | string | — | Node-ID |
| `repository` | enum | `staging` | Repository |
| `extraction_method` | enum | `browser` | `browser` (Standard) oder `simple` |
| `output_format` | enum | `markdown` | `markdown`, `txt`, `html` |
| `context` | string | `default` | Schema-Kontext |
| `version` | string | `latest` | Schema-Version |
| `language` | string | `de` | Sprache |
| `llm_provider` | string | — | LLM-Provider Override |
| `llm_model` | string | — | LLM-Model Override |

#### Response

```json
{
  "detected": {
    "schema_file": "event.json",
    "profile_id": "event",
    "label": { "de": "Veranstaltung", "en": "Event" },
    "confidence": "high"
  },
  "available": [
    {
      "schema_file": "event.json",
      "profile_id": "event",
      "label": { "de": "Veranstaltung", "en": "Event" },
      "confidence": null
    },
    {
      "schema_file": "learning_material.json",
      "profile_id": "learning_material",
      "label": { "de": "Lernmaterial", "en": "Learning Material" },
      "confidence": null
    }
  ],
  "context": "default",
  "version": "1.8.0",
  "processing_time_ms": 800
}
```

---

### POST /validate

Validiert Metadaten gegen das Schema. Prüft Core-Felder und Schema-spezifische Felder.

> **Einfache Nutzung:** Den kompletten Output von `/generate` direkt als Body senden — Context, Version und Schema werden automatisch erkannt.

#### Request

Der Body kann **direkt der `/generate`-Output** sein (flaches Format) oder ein Objekt mit `metadata`-Wrapper:

```bash
# Variante 1: Direkter /generate-Output
curl -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"contextName":"default","schemaVersion":"1.8.0","metadataset":"event.json",
       "cclom:title":"Mein Workshop","schema:startDate":"2026-03-15"}'

# Variante 2: Explizit mit metadata-Wrapper
curl -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" \
  -d '{"metadata": {...}, "context": "default", "version": "1.8.0"}'
```

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| `metadata` | object | **erforderlich** | Metadaten (oder direkt als Body) |
| `context` | string | `auto` | Schema-Kontext (`auto` = aus `contextName` lesen) |
| `version` | string | `auto` | Schema-Version (`auto` = aus `schemaVersion` lesen) |
| `schema_file` | string | `auto` | Schema-Datei (`auto` = aus `metadataset` lesen) |

#### Validierungsprüfungen

- **Pflichtfelder** — Sind alle `required`-Felder ausgefüllt? (Core + Schema)
- **Datentypen** — Stimmen Werte mit erwarteten Typen überein? (number, boolean, array)
- **Datumsformate** — ISO 8601 (`YYYY-MM-DD`, `YYYY-MM-DDTHH:MM:SS`)
- **Zeitformate** — `HH:MM:SS` oder `HH:MM`
- **URL-Formate** — Protokoll vorhanden (`http://` / `https://`)
- **Geo-Koordinaten** — Latitude (−90 bis 90), Longitude (−180 bis 180)
- **Vokabular** — Geschlossene Vokabulare mit Fuzzy-Matching und Vorschlägen

#### Response

```json
{
  "valid": true,
  "schema_used": "event.json",
  "errors": [],
  "warnings": [
    {
      "field_id": "oeh:eventType",
      "message": "Value 'Worksho' not in vocabulary. Did you mean 'http://w3id.org/openeduhub/vocabs/eventType/workshop'?",
      "severity": "warning"
    }
  ],
  "coverage": 85.7
}
```

| Feld | Beschreibung |
|------|--------------|
| `valid` | `true` wenn keine Errors |
| `schema_used` | Verwendete Schema-Datei |
| `errors` | Kritische Fehler (z.B. fehlende Pflichtfelder) |
| `warnings` | Hinweise (z.B. falsche Formate, ungültige Vokabular-Werte) |
| `coverage` | Prozent der ausgefüllten Pflichtfelder |

---

### POST /export/markdown

Exportiert Metadaten als menschenlesbares Markdown-Dokument.

> **Einfache Nutzung:** Den kompletten `/generate`-Output direkt als Body senden.

#### Request

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| `metadata` | object | **erforderlich** | Metadaten (oder direkt als Body) |
| `context` | string | `auto` | Schema-Kontext |
| `version` | string | `auto` | Schema-Version |
| `schema_file` | string | `auto` | Schema-Datei |
| `language` | string | `auto` | Ausgabesprache (`auto` = aus Metadaten, Fallback: `de`) |
| `include_empty` | bool | `false` | Leere Felder anzeigen |

#### Response

```json
{
  "markdown": "# Veranstaltung\n\n## Allgemein\n\n**Titel:** Workshop KI in der Bildung\n**Beschreibung:** Ein Workshop über...\n**Schlagwörter:** KI, Bildung\n\n## Veranstaltungsdetails\n\n**Startdatum:** 2026-03-15T09:00\n...",
  "schema_used": "event.json"
}
```

---

### POST /upload

Lädt Metadaten ins WLO edu-sharing Repository hoch.

> Erfordert `WLO_GUEST_USERNAME` und `WLO_GUEST_PASSWORD` Umgebungsvariablen.
>
> **Einfache Nutzung:** Den kompletten `/generate`-Output direkt als Body senden. Optional `repository`, `check_duplicates`, `start_workflow` mit übergeben.

#### Request

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| `metadata` | object | **erforderlich** | Metadaten (oder direkt als Body) |
| `repository` | string | `staging` | `staging` oder `prod` |
| `check_duplicates` | bool | `true` | Dublettenprüfung via `ccm:wwwurl` |
| `start_workflow` | bool | `true` | Review-Workflow starten |
| `source` | string | — | Bezugsquelle / Publisher-Override. Überschreibt `ccm:oeh_publisher_combined` |

#### Repositories

| Name | URL |
|------|-----|
| `staging` | https://repository.staging.openeduhub.net/edu-sharing |
| `prod` | https://redaktion.openeduhub.net/edu-sharing |

#### Upload-Workflow

1. **Duplikat-Check** — Prüft ob `ccm:wwwurl` bereits existiert (optional)
2. **Node erstellen** — Legt neuen Node mit Basisdaten an
3. **Aspects setzen** — Fügt benötigte Alfresco-Aspects hinzu (siehe unten)
4. **Metadaten setzen** — Überträgt alle Metadaten-Felder (`obeyMds=false`)
5. **Collections** — Fügt Node zu Collections hinzu (falls in Metadaten)
6. **Workflow starten** — Startet Review-Prozess (optional)

#### Automatische Transformationen beim Upload

Die API führt vor dem Schreiben automatisch folgende Transformationen durch:

| Transformation | Beschreibung |
|----------------|-------------|
| **VCARD Author** | `cm:author: ["Max Müller"]` → `ccm:lifecyclecontributer_author: ["BEGIN:VCARD\nFN:Max Müller\nN:Müller;Max\nVERSION:3.0\nEND:VCARD"]` |
| **Geo-Extraktion** | `schema:location[].geo.latitude/longitude` → `cm:latitude` / `cm:longitude` (String-Arrays) |
| **Geo-Fallback** | `schema:geo.latitude/longitude` (organization.json) → `cm:latitude` / `cm:longitude` |
| **Lizenz** | `ccm:custom_license` URI → `ccm:commonlicense_key` + `ccm:commonlicense_cc_version` |
| **obeyMds=false** | Umgeht den MDS-Filter, damit auch Felder wie `cm:latitude`, `ccm:oeh_event_begin` geschrieben werden |

#### Aspects

Nach Node-Erstellung werden automatisch Aspects hinzugefügt, die für bestimmte Properties benötigt werden:

| Aspect | Trigger | Ermöglicht |
|--------|---------|------------|
| `cm:geographic` | Geo-Daten in `schema:location` oder `schema:geo` | `cm:latitude`, `cm:longitude` |
| `cm:author` | `cm:author` in Metadaten | `ccm:lifecyclecontributer_author` |

#### Response (Erfolg)

```json
{
  "success": true,
  "repository": "staging",
  "fields_written": 12,
  "node": {
    "nodeId": "abc123-def456-...",
    "title": "Workshop KI in der Bildung",
    "description": "Ein Workshop über...",
    "wwwurl": "https://example.com/workshop",
    "repositoryUrl": "https://repository.staging.openeduhub.net/edu-sharing/components/render/abc123-..."
  },
  "field_errors": []
}
```

#### Response (Dublette)

```json
{
  "success": false,
  "duplicate": true,
  "repository": "staging",
  "node": { "nodeId": "existing-id", "title": "Existierender Workshop" },
  "error": "URL existiert bereits: \"Existierender Workshop\""
}
```

#### Response (Teilerfolg mit Feldfehlern)

```json
{
  "success": true,
  "repository": "staging",
  "fields_written": 10,
  "node": { "nodeId": "abc123-..." },
  "field_errors": [
    { "field_id": "ccm:oeh_event_begin", "error": "Invalid date format" }
  ]
}
```

---

### POST /upload/verify/{node_id}

Prüft hochgeladene Metadaten gegen die tatsächlichen Werte im Repository (SOLL/IST-Vergleich).

#### Request

| Parameter | Typ | Default | Beschreibung |
|-----------|-----|---------|--------------|
| `node_id` | string (URL-Pfad) | **erforderlich** | Node-ID des hochgeladenen Objekts |
| `expected_metadata` | object | — | Erwartete Metadaten (z.B. Output von `/generate`). Für SOLL/IST-Diff |
| `repository` | string | `staging` | `staging` oder `prod` |

#### Response

```json
{
  "success": true,
  "node_id": "abc123-def456-...",
  "repository": "staging",
  "actual_metadata": { "cclom:title": ["Workshop KI"], ... },
  "diff": [
    { "field_id": "cclom:title", "status": "match", "expected": "Workshop KI", "actual": ["Workshop KI"] },
    { "field_id": "schema:startDate", "status": "mismatch", "expected": "2026-03-15T09:00", "actual": ["1742166000000"] },
    { "field_id": "cclom:general_keyword", "status": "missing_in_repo", "expected": ["KI", "Bildung"], "actual": null }
  ],
  "summary": {
    "match": 8,
    "mismatch": 2,
    "missing_in_repo": 1,
    "extra_in_repo": 3,
    "not_written": 0
  }
}
```

| Status | Bedeutung |
|--------|----------|
| `match` | SOLL = IST |
| `mismatch` | SOLL ≠ IST (Wert wurde geschrieben, aber unterscheidet sich) |
| `missing_in_repo` | Feld im SOLL, aber nicht im Repository |
| `extra_in_repo` | Feld im Repository, aber nicht im SOLL |
| `not_written` | Feld hat kein `repo_field` — wird nicht ins Repository geschrieben |

---

### Info-Endpunkte

#### GET /health

```json
{ "status": "healthy", "version": "1.0.0" }
```

#### GET /info/schemata

Listet alle verfügbaren Schema-Kontexte mit Versionen.

```json
{
  "contexts": [
    {
      "name": "default",
      "display_name": "WLO/OEH Standard",
      "versions": ["1.8.0"],
      "default_version": "1.8.0"
    },
    {
      "name": "redesign_26",
      "display_name": "Redesign 2026",
      "versions": ["1.8.0"],
      "default_version": "1.8.0"
    },
    {
      "name": "mds_oeh",
      "display_name": "OEH Metadatenset",
      "versions": ["1.8.0"],
      "default_version": "1.8.0"
    }
  ],
  "default_context": "default"
}
```

#### GET /info/schemas/{context}/{version}

Listet alle Schemas für einen Kontext und Version. Version kann `latest` sein.

```json
[
  {
    "file": "core.json",
    "profile_id": "core",
    "label": { "de": "Core-Felder", "en": "Core Fields" },
    "groups": ["general", "classification"],
    "field_count": 12
  },
  {
    "file": "event.json",
    "profile_id": "event",
    "label": { "de": "Veranstaltung", "en": "Event" },
    "groups": ["event_details", "location", "organization"],
    "field_count": 29
  }
]
```

#### GET /info/schema/{context}/{version}/{schema_file}

Gibt die vollständige Schema-Definition als JSON zurück (Felder, Gruppen, Vokabulare, Datentypen).

---

## Nutzungsbeispiele

### curl — Einfache Textextraktion

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Workshop KI in der Bildung am 15. März 2026 in Berlin. Zielgruppe: Lehrkräfte.",
    "schema_file": "event.json"
  }'
```

### curl — Mehrzeiliger Text (ohne JSON)

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: text/plain" \
  -d 'Workshop KI in der Bildung
Am 15. März 2026 in Berlin.
Zielgruppe: Lehrkräfte aller Schulformen.
Kosten: 49 Euro'
```

### curl — URL als Quelle

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
    "input_source": "url",
    "source_url": "https://example.com/event-page",
    "extraction_method": "browser",
    "output_format": "markdown",
    "schema_file": "auto"
  }'
```

### curl — Repository-Node

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{
    "input_source": "node_url",
    "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
    "repository": "staging",
    "extraction_method": "browser",
    "schema_file": "auto"
  }'
```

### curl — Einzelnes Feld extrahieren

```bash
curl -X POST http://localhost:8000/extract-field \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Der Workshop wurde auf den 20. März 2026 verschoben.",
    "schema_file": "event.json",
    "field_id": "schema:startDate",
    "existing_metadata": {"schema:startDate": "2026-03-15T09:00"}
  }'
```

### curl — Validieren (direkter /generate-Output)

```bash
# Generieren und direkt validieren
curl -s http://localhost:8000/generate -H "Content-Type: application/json" \
  -d '{"text": "Workshop am 15.03.2026"}' | \
  curl -X POST http://localhost:8000/validate \
  -H "Content-Type: application/json" -d @-
```

### Python — Vollständiger Workflow

```python
import httpx

API = "http://localhost:8000"

# 1. Metadaten generieren
result = httpx.post(f"{API}/generate", json={
    "text": """
    Fortbildung: Digitale Werkzeuge im Unterricht
    Am 20. April 2026 in der Stadthalle Hamburg,
    Dammtorwall 10, 20355 Hamburg.
    Zielgruppe: Lehrkräfte aller Schulformen
    Kosten: 49 Euro
    """,
    "schema_file": "event.json",
    "enable_geocoding": True
}).json()

print(f"Titel: {result.get('cclom:title')}")
print(f"Felder: {result['processing']['fields_extracted']}/{result['processing']['fields_total']}")

# 2. Validieren (ganzer Output direkt rein)
validation = httpx.post(f"{API}/validate", json=result).json()
print(f"Valid: {validation['valid']}, Coverage: {validation['coverage']}%")

# 3. Markdown-Export
md = httpx.post(f"{API}/export/markdown", json=result).json()
print(md["markdown"])

# 4. Hochladen (ganzer Output + Optionen)
if validation["valid"]:
    upload_body = {**result, "repository": "staging", "check_duplicates": True}
    upload = httpx.post(f"{API}/upload", json=upload_body).json()
    if upload["success"]:
        print(f"Hochgeladen: {upload['node']['repositoryUrl']}")
```

### JavaScript/TypeScript

```typescript
const API = "http://localhost:8000";

// 1. Generieren
const result = await fetch(`${API}/generate`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    text: "Workshop am 15.03.2026 in Berlin",
    schema_file: "event.json",
  }),
}).then((r) => r.json());

console.log(result["cclom:title"]);       // "Workshop ..."
console.log(result["schema:startDate"]);   // "2026-03-15T09:00"

// 2. Validieren (direkter Output)
const validation = await fetch(`${API}/validate`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(result),
}).then((r) => r.json());

console.log(`Valid: ${validation.valid}, Coverage: ${validation.coverage}%`);
```

---

## Umgebungsvariablen

Alle Variablen können in `.env` oder als System-Umgebungsvariablen gesetzt werden.
Prefix `METADATA_AGENT_` wird automatisch vorangestellt (außer API-Keys).

### API-Keys (ohne Prefix)

| Variable | Beschreibung |
|----------|--------------|
| `B_API_KEY` | B-API Key für OpenEduHub LLM-Zugriff |
| `OPENAI_API_KEY` | OpenAI API Key (nur für `provider=openai`) |
| `WLO_GUEST_USERNAME` | WLO Repository Upload Username |
| `WLO_GUEST_PASSWORD` | WLO Repository Upload Password |

### LLM-Konfiguration

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` | `openai`, `b-api-openai`, `b-api-academiccloud` |
| `METADATA_AGENT_LLM_TEMPERATURE` | `0.3` | Kreativität (0.0–1.0) |
| `METADATA_AGENT_LLM_MAX_TOKENS` | `2000` | Max Tokens pro LLM-Aufruf |
| `METADATA_AGENT_LLM_MAX_RETRIES` | `3` | Wiederholungsversuche bei Fehler |
| `METADATA_AGENT_LLM_RETRY_DELAY` | `1.0` | Wartezeit zwischen Retries (Sekunden) |

### Provider-spezifische Einstellungen

**B-API OpenAI (Standard):**

| Variable | Default |
|----------|---------|
| `METADATA_AGENT_B_API_OPENAI_BASE` | `https://b-api.staging.openeduhub.net/api/v1/llm/openai` |
| `METADATA_AGENT_B_API_OPENAI_MODEL` | `gpt-4.1-mini` |

**B-API AcademicCloud:**

| Variable | Default |
|----------|---------|
| `METADATA_AGENT_B_API_ACADEMICCLOUD_BASE` | `https://b-api.staging.openeduhub.net/api/v1/llm/academiccloud` |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL` | `deepseek-r1` |

**OpenAI (nativ):**

| Variable | Default |
|----------|---------|
| `METADATA_AGENT_OPENAI_API_BASE` | `https://api.openai.com/v1` |
| `METADATA_AGENT_OPENAI_MODEL` | `gpt-4o-mini` |

### Worker & Performance

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `METADATA_AGENT_DEFAULT_MAX_WORKERS` | `10` | Standard parallele LLM-Aufrufe |
| `METADATA_AGENT_REQUEST_TIMEOUT` | `60` | HTTP-Timeout in Sekunden |

### Schema-Defaults

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `METADATA_AGENT_DEFAULT_CONTEXT` | `default` | Standard-Kontext |
| `METADATA_AGENT_DEFAULT_VERSION` | `1.8.0` | Standard-Version |

### Repository & Crawler

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `METADATA_AGENT_REPOSITORY_PROD_URL` | `https://redaktion.openeduhub.net/edu-sharing/rest` | Prod-Repository |
| `METADATA_AGENT_REPOSITORY_STAGING_URL` | `https://repository.staging.openeduhub.net/edu-sharing/rest` | Staging-Repository |
| `METADATA_AGENT_TEXT_EXTRACTION_API_URL` | `https://text-extraction.staging.openeduhub.net` | Text-Extraction API |
| `METADATA_AGENT_TEXT_EXTRACTION_DEFAULT_METHOD` | `browser` | `browser` (Standard) oder `simple` |

### Sonstige

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `METADATA_AGENT_CORS_ORIGINS` | `*` | CORS-Origins (komma-separiert oder `*`) |
| `METADATA_AGENT_DEBUG` | `false` | Debug-Modus |

### `.env` Beispiel

```env
# LLM Provider (Standard: B-API)
B_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
METADATA_AGENT_LLM_PROVIDER=b-api-openai

# Optional: OpenAI direkt
# OPENAI_API_KEY=sk-proj-...
# METADATA_AGENT_LLM_PROVIDER=openai

# Optional: Repository Upload
# WLO_GUEST_USERNAME=upload-user
# WLO_GUEST_PASSWORD=upload-password
```

---

## Widget / Webkomponente

Die API liefert eine einbettbare Angular-Webkomponente (`<metadata-agent-canvas>`) als statische Dateien mit aus. Damit können andere Anwendungen die Metadaten-Erfassung oder -Anzeige ohne eigenen Build einbinden.

### Widget bereitstellen

```powershell
# Angular-Projekt bauen und dist-Dateien in die API kopieren
.\scripts\deploy-widget.ps1

# Nur kopieren (ohne Neubau, z.B. wenn dist schon aktuell ist)
.\scripts\deploy-widget.ps1 -SkipBuild
```

### Einbindung in eigene Anwendungen

```html
<!-- Fonts (alle drei werden benötigt) -->
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/icon?family=Material+Icons|Material+Icons+Outlined" rel="stylesheet">

<!-- Widget -->
<link rel="stylesheet" href="https://DEINE-API-URL/widget/dist/styles.css">
<script src="https://DEINE-API-URL/widget/dist/runtime.js" defer></script>
<script src="https://DEINE-API-URL/widget/dist/polyfills.js" defer></script>
<script src="https://DEINE-API-URL/widget/dist/main.js" defer></script>

<!-- Nutzung -->
<metadata-agent-canvas
  api-url="https://DEINE-API-URL"
  layout="default"
  show-input-area="true"
  show-status-bar="true"
  show-floating-controls="true">
</metadata-agent-canvas>
```

### Layouts

Jedes Layout ist eine eigenständige Angular-Komponente mit eigener Darstellung.

| Layout | Beschreibung |
|--------|-------------|
| `default` | Vollständige Bearbeitung — Eingabe, Statusbar, Floating Controls, Footer |
| `plugin` | Kompakte Sidebar für Browser-Extension — Eingabe, Fortschrittsbalken |
| `dialog` | Review-Dialog für Modals — kein Eingabebereich, schwebende Speichern/Abbrechen-Buttons |
| `detail` | Mehrspaltige (1–4) Nur-Lese-Vorschau — Standard: readonly |
| `metadatenpruefdialog` | Metadaten-Prüfdialog mit Fortschrittsbalken |
| `prueftisch` | 1-spaltige Prüftabelle mit gruppierten Karten |
| `prueftisch-gross` | 2-spaltige Prüftabelle (gleiche Komponente wie prueftisch, andere Variante) |

> **Hinweis:** `readonly` ist kein Layout, sondern ein universelles Attribut, kombinierbar mit jedem Layout via `readonly="true"`.

### Alle Attribute

Alle Attribute können auch per JavaScript gesetzt werden: `canvas.layout = 'detail'`

#### Konfiguration

| Attribut | Werte | Beschreibung |
|----------|-------|-------------|
| `api-url` | URL | URL der Metadata Agent API **(Pflicht)** |
| `layout` | siehe oben | Layout-Variante |
| `context-name` | `default`, `redesign_26` | Schema-Kontext |
| `schema-version` | `1.8.0`, `latest` | Schema-Version |
| `language` | `de`, `en` | Sprache (i18n) |
| `columns` | `1`–`4` | Spaltenanzahl (nur detail-Layout) |
| `background-color` | CSS-Farbe | Hintergrundfarbe, z.B. `#f5f5f5` |
| `input-mode` | `text`, `url`, `nodeId` | Eingabemodus |

#### Sichtbarkeit (true/false)

| Attribut | Beschreibung |
|----------|-------------|
| `show-input-area` | Eingabebereich anzeigen |
| `show-status-bar` | Statusleiste mit Fortschritt |
| `show-core-fields` | Kernfelder (Titel, Beschreibung, Keywords) |
| `show-special-fields` | Spezialfelder (Fach, Bildungsstufe etc.) |
| `show-footer` | Fußzeile |
| `show-floating-controls` | Floating Controls (Content-Type-Selector) |
| `show-field-actions` | Feld-Aktionsbuttons (Bearbeiten, KI-Generieren) |
| `show-upload-button` | Upload-Button in Floating Controls |
| `show-page-mode` | Seitenmodus-Umschalter (Plugin: „Webseite laden") |
| `show-content-type-only` | Nur Content-Type-Selector in Controls |
| `controls` | Alias für `show-floating-controls` (OEH-Kompatibilität) |

#### Verhalten

| Attribut | Beschreibung |
|----------|-------------|
| `readonly` | Nur-Lese-Modus |
| `viewer-mode` | Alias für `readonly` (Rückwärtskompatibilität) |
| `borderless` | Rahmenloser Modus |
| `highlight-ai` | KI-generierte Felder farblich hervorheben (Standard: `true`) |
| `auto-extract` | Automatisch extrahieren nach Laden |

#### Daten direkt setzen

| Attribut | Beschreibung |
|----------|-------------|
| `text` | Text direkt als Eingabe |
| `url` | URL als Eingabe (löst URL-Modus aus) |
| `node-id` | edu-sharing Node-ID für automatische Extraktion |
| `metadata-input` | JSON-Objekt mit vorausgefüllten Metadaten (per JavaScript) |
| `content-type` | Inhaltstyp setzen (Schema-Dateiname, z.B. `event.json`) |

### Events

```javascript
const canvas = document.querySelector('metadata-agent-canvas');

// Metadaten wurden geändert (bei jeder Feldänderung)
canvas.addEventListener('metadataChange', (e) => console.log(e.detail));

// Metadaten abgesendet (Upload/Submit-Button geklickt)
canvas.addEventListener('metadataSubmit', (e) => console.log(e.detail));

// KI-Extraktion abgeschlossen
canvas.addEventListener('extractionComplete', (e) => console.log(e.detail));

// Inhaltstyp erkannt
canvas.addEventListener('contentTypeDetected', (e) => console.log(e.detail));

// Upload-Ergebnis (Erfolg oder Fehler)
canvas.addEventListener('uploadResult', (e) => console.log(e.detail));

// Nutzer hat "Seite neu laden" geklickt (Plugin-Modus)
canvas.addEventListener('reloadFromPage', (e) => console.log('reload'));
```

### Beispiel-Seiten

Unter `/widget/examples/` sind interaktive Beispiele verfügbar:

| Seite | Beschreibung |
|-------|-------------|
| `full.html` | Vollständige Webkomponente mit allen Attributen |
| `default.html` | Standard-Layout |
| `detail.html` | Detail-Ansicht (mehrspaltig, readonly) |
| `minimal.html` | Minimale Einbindung |
| `prueftisch.html` | 1-spaltige Prüftabelle |
| `prueftisch-gross.html` | 2-spaltige Prüftabelle |
| `metadatenpruefdialog.html` | Prüfdialog mit Fortschritt |
| `json-import.html` | JSON-Import mit Layout-Switcher |
| `test.html` | Test-Seite mit Toggle-Controls |

### API-Endpunkt

- **`GET /widget/info`** — Gibt alle Script-URLs, Layouts und Beispiel-Snippets als JSON zurück

---

## Deployment

### Docker (empfohlen)

```bash
# Mit docker-compose
docker-compose up -d

# Manuell
docker build -t metadata-agent-api .
docker run -d -p 8000:8000 \
  -e B_API_KEY=dein-key \
  -e METADATA_AGENT_LLM_PROVIDER=b-api-openai \
  metadata-agent-api
```

### Railway

```bash
npm i -g @railway/cli
railway login
railway init
railway up
```

### Render

1. Repository mit Render verbinden
2. Web Service erstellen
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn src.main:app --host 0.0.0.0 --port $PORT`
5. Environment Variables konfigurieren

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: metadata-agent-api
spec:
  replicas: 2
  selector:
    matchLabels:
      app: metadata-agent-api
  template:
    metadata:
      labels:
        app: metadata-agent-api
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
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 30
```

---

## Lizenz

MIT
