"""FastAPI application for metadata extraction."""
import re
import json
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.utils import get_openapi
from contextlib import asynccontextmanager

from .config import get_settings
from .models.schemas import (
    GenerateRequest,
    GenerateResponse,
    ProcessingInfo,
    ValidateRequest,
    ValidateResponse,
    ExportMarkdownRequest,
    ExportMarkdownResponse,
    SchemataInfoResponse,
    ContextInfo,
    SchemaInfo,
    UploadRequest,
    UploadResponse,
    UploadedNodeInfo,
    DetectContentTypeRequest,
    DetectContentTypeResponse,
    ContentTypeInfo,
    LocalizedString,
    sanitize_text,
    ExtractFieldRequest,
    ExtractFieldResponse,
    InputSource,
    Repository,
    ExtractionMethod,
)
from .services.input_source_service import get_input_source_service
from .services.metadata_service import get_metadata_service
from .services.repository_service import get_repository_service
from .services.llm_service import get_llm_service
from .services.field_normalizer import get_field_normalizer
from .utils.schema_loader import (
    get_available_contexts,
    get_available_schemas,
    load_schema,
    get_content_types,
    get_latest_version,
)


def sanitize_json_string(raw_body: str) -> str:
    """
    Sanitize raw JSON string by escaping control characters in string values.
    This allows malformed JSON with literal newlines in strings to be parsed.
    Handles multi-line text input that wasn't properly escaped.
    """
    # Remove BOM if present
    raw_body = raw_body.lstrip('\ufeff')
    
    # First, try to parse as-is
    try:
        json.loads(raw_body)
        return raw_body  # Already valid JSON
    except json.JSONDecodeError:
        pass
    
    # Escape literal newlines and tabs inside JSON strings
    # This handles cases where users paste multi-line text without escaping
    result = []
    in_string = False
    escape_next = False
    
    for char in raw_body:
        if escape_next:
            result.append(char)
            escape_next = False
            continue
        
        if char == '\\':
            result.append(char)
            escape_next = True
            continue
        
        if char == '"':
            in_string = not in_string
            result.append(char)
            continue
        
        if in_string:
            # Escape control characters inside strings
            if char == '\n':
                result.append('\\n')
            elif char == '\r':
                result.append('\\r')
            elif char == '\t':
                result.append('\\t')
            elif ord(char) < 32:
                # Remove other control characters
                pass
            else:
                result.append(char)
        else:
            result.append(char)
    
    return ''.join(result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    settings = get_settings()
    llm_config = settings.get_llm_config()
    print(f"Starting {settings.app_name} v{settings.app_version}")
    print(f"LLM Provider: {settings.llm_provider}")
    print(f"LLM Model: {llm_config['model']}")
    print(f"LLM API Base: {llm_config['api_base']}")
    print(f"Default Workers: {settings.default_max_workers}")
    
    yield
    
    # Shutdown - close HTTP clients
    from .services.llm_service import _llm_service
    if _llm_service:
        await _llm_service.close()
    from .services.metadata_service import _metadata_service
    if _metadata_service and _metadata_service.llm_service:
        await _metadata_service.llm_service.close()
    from .services.input_source_service import _input_source_service
    if _input_source_service:
        await _input_source_service.close()
    print("Shutting down...")


# Create FastAPI app
settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="""API für die automatische Extraktion von Metadaten aus Texten mittels KI.

## Features

- **Automatische Schema-Erkennung**: Erkennt den passenden Inhaltstyp
- **Parallele Verarbeitung**: Bis zu 20 Worker für schnelle Extraktion
- **Mehrsprachig**: Deutsch und Englisch
- **Validierung**: Prüft extrahierte Metadaten gegen Schema
- **Markdown-Export**: Menschenlesbare Ausgabe

## Authentifizierung

API-Key wird über den Header `X-API-Key` oder als Bearer Token übergeben.
    """,
    lifespan=lifespan,
)

# Custom OpenAPI schema: inject request models that are referenced via $ref
# but not auto-registered because endpoints use raw Request instead of Pydantic params
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    # Inject missing request schemas
    schemas = openapi_schema.setdefault("components", {}).setdefault("schemas", {})
    for model in [GenerateRequest, DetectContentTypeRequest, ExtractFieldRequest,
                  ValidateRequest, ExportMarkdownRequest, UploadRequest]:
        model_schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        # Extract $defs (sub-schemas like enums) and merge into top-level schemas
        defs = model_schema.pop("$defs", {})
        for def_name, def_schema in defs.items():
            schemas.setdefault(def_name, def_schema)
        schemas[model.__name__] = model_schema
    app.openapi_schema = openapi_schema
    return openapi_schema

app.openapi = custom_openapi

# CORS middleware - origins configurable via METADATA_AGENT_CORS_ORIGINS env var
_cors_origins = [o.strip() for o in settings.cors_origins.split(",")] if settings.cors_origins != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gzip compression for all responses >= 500 bytes (widget JS/CSS + API JSON)
app.add_middleware(GZipMiddleware, minimum_size=500)

# ============================================================================
# Widget Static Files
# ============================================================================

# Mount widget dist files if they exist
_widget_dir = Path(__file__).parent / "static" / "widget"
if _widget_dir.exists():
    app.mount("/widget/assets", StaticFiles(directory=str(_widget_dir / "assets")), name="widget-assets") if (_widget_dir / "assets").exists() else None
    app.mount("/widget/examples", StaticFiles(directory=str(_widget_dir / "examples"), html=True), name="widget-examples") if (_widget_dir / "examples").exists() else None
    if (_widget_dir / "dist").exists():
        app.mount("/widget/dist", StaticFiles(directory=str(_widget_dir / "dist")), name="widget-dist")


@app.get(
    "/widget/i18n/{lang}.json",
    summary="Widget UI-Übersetzungen",
    description="Liefert die UI-Übersetzungstexte für die Webkomponente. Unterstützte Sprachen: `de`, `en`.",
    tags=["Widget"],
)
async def widget_i18n(lang: str):
    """Serve i18n translation files for the web component."""
    i18n_dir = Path(__file__).parent / "static" / "widget" / "assets" / "i18n"
    file_path = i18n_dir / f"{lang}.json"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Language '{lang}' not found. Available: de, en")
    return JSONResponse(
        content=json.loads(file_path.read_text(encoding="utf-8")),
        headers={"Cache-Control": "public, max-age=3600"}
    )


@app.get(
    "/widget/info",
    summary="Widget-Einbindung (Web Component)",
    description="""Informationen zur Einbindung der Metadata Agent Webkomponente in eigene Anwendungen.

Gibt alle verfügbaren Varianten, die benötigten Script-URLs und Beispiel-Code zurück.
""",
    tags=["Widget"],
)
async def widget_info(request: Request):
    """Return embedding info for the web component widget."""
    base = str(request.base_url).rstrip("/")
    dist_base = f"{base}/widget/dist"
    examples_base = f"{base}/widget/examples"
    
    return {
        "name": "metadata-agent-canvas",
        "version": settings.app_version,
        "description": "Angular Web Component zur Anzeige und Bearbeitung von Metadaten. "
                       "Kann als <metadata-agent-canvas> Tag in beliebige Webanwendungen eingebettet werden.",
        "dist_base_url": dist_base,
        "scripts": {
            "required": [
                f"{dist_base}/runtime.js",
                f"{dist_base}/polyfills.js",
                f"{dist_base}/main.js",
            ],
            "styles": [
                f"{dist_base}/styles.css",
            ],
            "fonts": [
                "https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500&display=swap",
                "https://fonts.googleapis.com/icon?family=Material+Icons",
            ],
        },
        "variants": {
            "full": {
                "description": "Volle Webkomponente mit Eingabebereich, KI-Extraktion, Statusbar und allen Layouts. "
                               "Für interaktive Metadaten-Erfassung und -Bearbeitung.",
                "example_url": f"{examples_base}/full.html",
                "snippet": (
                    '<link rel="stylesheet" href="' + dist_base + '/styles.css">\n'
                    '<script src="' + dist_base + '/runtime.js" defer></script>\n'
                    '<script src="' + dist_base + '/polyfills.js" defer></script>\n'
                    '<script src="' + dist_base + '/main.js" defer></script>\n\n'
                    '<metadata-agent-canvas\n'
                    '  api-url="' + base + '"\n'
                    '  layout="default"\n'
                    '  show-input="true"\n'
                    '  show-status-bar="true"\n'
                    '  show-controls="true">\n'
                    '</metadata-agent-canvas>'
                ),
                "attributes": {
                    "api-url": "URL der Metadata Agent API",
                    "layout": "Layout-Variante: default, compact, minimal, detail",
                    "context-name": "Schema-Kontext, z.B. 'default' oder 'redesign_26'",
                    "show-input": "Eingabebereich anzeigen (true/false)",
                    "show-status-bar": "Statusleiste anzeigen (true/false)",
                    "show-controls": "Floating Controls anzeigen (true/false)",
                    "show-core-fields": "Kernfelder anzeigen (true/false)",
                    "show-special-fields": "Spezialfelder anzeigen (true/false)",
                    "borderless": "Rahmenloser Modus (true/false)",
                    "readonly": "Nur-Lese-Modus (true/false)",
                    "highlight-ai": "KI-generierte Felder hervorheben (true/false)",
                    "node-id": "edu-sharing Node-ID für automatische Extraktion",
                    "source-url": "URL für automatische Text-Extraktion",
                    "content-type": "Inhaltstyp setzen (Schema-Dateiname, z.B. 'event.json'). Per JS: canvas.contentType = 'event.json'",
                    "metadata-input": "Vorab-Metadaten als JSON-Objekt. Per JS: canvas.metadataInput = {...}",
                },
            },
            "detail": {
                "description": "Nur-Lese Detailansicht. Mehrspaltig, ohne Eingabe. "
                               "Für Repository-Detailseiten und Metadaten-Vorschau.",
                "example_url": f"{examples_base}/detail.html",
                "snippet": (
                    '<link rel="stylesheet" href="' + dist_base + '/styles.css">\n'
                    '<script src="' + dist_base + '/runtime.js" defer></script>\n'
                    '<script src="' + dist_base + '/polyfills.js" defer></script>\n'
                    '<script src="' + dist_base + '/main.js" defer></script>\n\n'
                    '<metadata-agent-canvas\n'
                    '  api-url="' + base + '"\n'
                    '  layout="detail"\n'
                    '  node-id="DEINE-NODE-ID"\n'
                    '  readonly="true">\n'
                    '</metadata-agent-canvas>'
                ),
            },
            "minimal": {
                "description": "Kompakte Variante ohne Statusbar und Controls. "
                               "Für Einbettung in bestehende Formulare oder Sidebars.",
                "example_url": f"{examples_base}/minimal.html",
                "snippet": (
                    '<link rel="stylesheet" href="' + dist_base + '/styles.css">\n'
                    '<script src="' + dist_base + '/runtime.js" defer></script>\n'
                    '<script src="' + dist_base + '/polyfills.js" defer></script>\n'
                    '<script src="' + dist_base + '/main.js" defer></script>\n\n'
                    '<metadata-agent-canvas\n'
                    '  api-url="' + base + '"\n'
                    '  layout="compact"\n'
                    '  show-input="true"\n'
                    '  show-status-bar="false"\n'
                    '  show-controls="false"\n'
                    '  borderless="true">\n'
                    '</metadata-agent-canvas>'
                ),
            },
        },
        "events": {
            "metadataChange": "Wird ausgelöst wenn sich Metadaten ändern. event.detail enthält die aktualisierten Felder.",
            "metadataSubmit": "Wird ausgelöst wenn der Nutzer die Metadaten absendet. event.detail enthält alle Metadaten.",
            "extractionComplete": "Wird nach Abschluss der KI-Extraktion ausgelöst.",
            "contentTypeDetected": "Wird ausgelöst wenn der Inhaltstyp erkannt wurde.",
        },
        "examples": {
            "full": f"{examples_base}/full.html",
            "detail": f"{examples_base}/detail.html",
            "minimal": f"{examples_base}/minimal.html",
            "json_import": f"{examples_base}/json-import.html",
            "prueftisch": f"{examples_base}/prueftisch.html",
            "prueftisch_gross": f"{examples_base}/prueftisch-gross.html",
            "metadatenpruefdialog": f"{examples_base}/metadatenpruefdialog.html",
            "default": f"{examples_base}/default.html",
            "interactive_test": f"{examples_base}/test.html",
        },
        "example_data": {
            "metadata_json": f"{examples_base}/metadata-2026-02-11.json",
        },
    }


# ============================================================================
# Health & Info Endpoints
# ============================================================================

@app.get(
    "/health",
    summary="Health Check",
    description="Prüft ob die API läuft und gibt die aktuelle Version zurück."
)
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": settings.app_version}


@app.get(
    "/info/schemata", 
    response_model=SchemataInfoResponse,
    summary="Verfügbare Schema-Kontexte",
    description="""Listet alle verfügbaren Schema-Kontexte mit ihren Versionen auf.

## Response

- **contexts**: Liste aller Kontexte mit Namen, Versionen und Default-Version
- **default_context**: Der Standard-Kontext (normalerweise `default`)

## Verwendung

Nutze diese Info um gültige Werte für `context` und `version` in anderen Endpoints zu finden.
"""
)
async def get_schemata_info():
    """Get information about available schemata."""
    contexts = get_available_contexts()
    
    return SchemataInfoResponse(
        contexts=[
            ContextInfo(
                name=c["name"],
                display_name=c["display_name"],
                versions=c["versions"],
                default_version=c["default_version"],
            )
            for c in contexts
        ],
        default_context=settings.default_context,
    )


@app.get(
    "/info/schemas/{context}/{version}", 
    response_model=list[SchemaInfo],
    summary="Schemas für Kontext/Version",
    description="""Listet alle verfügbaren Schemas (Content-Types) für einen Kontext und Version.

## Path-Parameter

- **context**: Schema-Kontext, z.B. `default`
- **version**: Schema-Version, z.B. `1.8.0` oder `latest`

## Response

Liste von Schemas mit:
- **file**: Schema-Dateiname (z.B. `event.json`)
- **profile_id**: Profil-ID für edu-sharing
- **label**: Mehrsprachiges Label
- **groups**: Anzahl Feldgruppen
- **field_count**: Anzahl Felder
"""
)
async def get_schemas_for_version(context: str, version: str):
    """Get available schemas for a context and version."""
    try:
        schemas = get_available_schemas(context, version)
        return [
            SchemaInfo(
                file=s["file"],
                profile_id=s["profile_id"],
                label=s["label"],
                groups=s["groups"],
                field_count=s["field_count"],
            )
            for s in schemas
        ]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get(
    "/info/schema/{context}/{version}/{schema_file}",
    summary="Schema-Definition abrufen",
    description="""Gibt die vollständige Schema-Definition als JSON zurück.

## Path-Parameter

- **context**: Schema-Kontext, z.B. `default`
- **version**: Schema-Version, z.B. `1.8.0`
- **schema_file**: Schema-Datei, z.B. `event.json`, `core.json`

## Response

Vollständiges Schema mit:
- **fields**: Alle Felder mit ID, Label, Typ, Vokabular etc.
- **groups**: Feldgruppen für UI-Anzeige
- **metadata**: Schema-Metadaten
"""
)
async def get_schema_definition(context: str, version: str, schema_file: str):
    """Get full schema definition."""
    try:
        schema = load_schema(context, version, schema_file)
        return schema
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================================
# Content Type Detection
# ============================================================================

@app.post(
    "/detect-content-type",
    response_model=DetectContentTypeResponse,
    summary="Content-Type erkennen",
    description="""Analysiert Text und erkennt automatisch den passenden Content-Type (Schema) für die Metadaten-Extraktion.

## Input-Quellen (input_source)

| Wert | Beschreibung | Benötigte Felder |
|------|-------------|------------------|
| `text` | Direkter Text als Eingabe (Standard) | `text` |
| `url` | Text von URL via Crawler abrufen | `source_url`, optional `extraction_method` |
| `node_id` | Text + Metadaten von Repository-Node abrufen | `node_id`, `repository` |
| `node_url` | Repository-Node + Crawler-Fallback (URL aus ccm:wwwurl) | `node_id`, `repository` (source_url optional) |

## Extraction Method (extraction_method)

| Wert | Beschreibung |
|------|-------------|
| `simple` | Schnelle HTML-Extraktion (Standard) |
| `browser` | Vollständiges Browser-Rendering für JS-Seiten |

## Repository (repository)

| Wert | URL |
|------|-----|
| `staging` | repository.staging.openeduhub.net (Standard) |
| `prod` | redaktion.openeduhub.net |

## LLM-Optionen

- **llm_provider**: `b-api-openai` (Standard), `openai`, `b-api-academiccloud`
- **llm_model**: z.B. `gpt-4.1-mini` (Standard), `gpt-4o-mini`, `deepseek-r1`
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/DetectContentTypeRequest"},
                    "examples": {
                        "text_input": {
                            "summary": "1. Text-Eingabe",
                            "description": "Direkter Text als Eingabe.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.\nLernen Sie die Grundlagen der künstlichen Intelligenz kennen.",
                                "source_url": "",
                                "extraction_method": "simple",
                                "node_id": "",
                                "repository": "staging",
                                "context": "default",
                                "version": "latest",
                                "language": "de",
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        },
                        "url_input": {
                            "summary": "2. URL-Eingabe",
                            "description": "Text von URL abrufen.",
                            "value": {
                                "input_source": "url",
                                "text": "",
                                "source_url": "https://www.wirlernenonline.de",
                                "extraction_method": "simple",
                                "node_id": "",
                                "repository": "staging",
                                "context": "default",
                                "version": "latest",
                                "language": "de",
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        },
                        "node_id_input": {
                            "summary": "3. NodeID-Eingabe",
                            "description": "Text von Repository-Node abrufen.",
                            "value": {
                                "input_source": "node_id",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "simple",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "repository": "staging",
                                "context": "default",
                                "version": "latest",
                                "language": "de",
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        }
                    }
                }
            }
        }
    }
)
async def detect_content_type(req: DetectContentTypeRequest):
    """
    Detect content type from text using LLM.
    
    Returns the detected content type along with all available content types
    for the specified context and version.
    """
    start_time = time.time()
    
    # Handle input source
    text = req.text
    if req.input_source != InputSource.TEXT:
        input_service = get_input_source_service()
        try:
            if req.input_source == InputSource.URL:
                if not req.source_url:
                    raise HTTPException(status_code=400, detail="source_url required for input_source='url'")
                text = await input_service.fetch_from_url(req.source_url, req.extraction_method.value, lang=req.language)
            elif req.input_source == InputSource.NODE_ID:
                if not req.node_id:
                    raise HTTPException(status_code=400, detail="node_id required for input_source='node_id'")
                input_data = await input_service.fetch_from_node_id(req.node_id, req.repository.value)
                text = input_data.text
            elif req.input_source == InputSource.NODE_URL:
                if not req.node_id:
                    raise HTTPException(status_code=400, detail="node_id required for input_source='node_url'")
                input_data = await input_service.fetch_from_node_url(
                    req.node_id, req.repository.value, req.source_url or None, req.extraction_method.value,
                    lang=req.language
                )
                text = input_data.text
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch input: {str(e)}")
    
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    
    # Truncate excessively long text to prevent exceeding LLM context window
    MAX_TEXT_LENGTH = 500_000
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH]
    
    # Resolve "latest" to actual version
    version = req.version
    if version == "latest" or not version:
        version = get_latest_version(req.context)
    
    # Get available content types
    content_types = get_content_types(req.context, version)
    
    if not content_types:
        raise HTTPException(
            status_code=404,
            detail=f"No content types defined for context '{req.context}' version '{version}'"
        )
    
    # Build available list
    available = []
    for ct in content_types:
        schema_file = ct.get("schema_file", "")
        available.append(ContentTypeInfo(
            schema_file=schema_file,
            profile_id=ct.get("profile_id"),
            label=LocalizedString(
                de=ct.get("label", {}).get("de", schema_file),
                en=ct.get("label", {}).get("en", schema_file)
            ),
            confidence=None
        ))
    
    # Use LLM to detect content type (with optional overrides)
    service = get_metadata_service(
        llm_provider=req.llm_provider,
        llm_model=req.llm_model
    )
    detected_schema = await service.llm_service.detect_content_type(
        text, content_types, req.language
    )
    
    # Close non-default LLM service HTTP client to prevent leak
    if req.llm_provider is not None or req.llm_model is not None:
        await service.llm_service.close()
    
    # Find the detected content type info
    detected_info = None
    for ct in content_types:
        if ct.get("schema_file") == detected_schema:
            detected_info = ContentTypeInfo(
                schema_file=detected_schema,
                profile_id=ct.get("profile_id"),
                label=LocalizedString(
                    de=ct.get("label", {}).get("de", detected_schema),
                    en=ct.get("label", {}).get("en", detected_schema)
                ),
                confidence="high"
            )
            break
    
    if not detected_info:
        # Fallback if detection returned unknown schema
        detected_info = ContentTypeInfo(
            schema_file=detected_schema,
            profile_id=None,
            label=LocalizedString(de=detected_schema, en=detected_schema),
            confidence="low"
        )
    
    processing_time = int((time.time() - start_time) * 1000)
    
    return DetectContentTypeResponse(
        detected=detected_info,
        available=available,
        context=req.context,
        version=version,
        processing_time_ms=processing_time
    )


# ============================================================================
# Single Field Extraction
# ============================================================================

@app.post(
    "/extract-field",
    response_model=ExtractFieldResponse,
    summary="Einzelnes Feld extrahieren",
    description="""Extrahiert oder regeneriert einen einzelnen Feldwert mittels LLM. Nützlich um einzelne Felder zu korrigieren ohne alles neu zu extrahieren.

## Input-Quellen (input_source)

| Wert | Beschreibung | Benötigte Felder |
|------|-------------|------------------|
| `text` | Direkter Text als Eingabe (Standard) | `text` |
| `url` | Text von URL via Crawler abrufen | `source_url`, optional `extraction_method` |
| `node_id` | Text + Metadaten von Repository-Node abrufen | `node_id`, `repository` |
| `node_url` | Repository-Node + Crawler-Fallback (URL aus ccm:wwwurl) | `node_id`, `repository` (source_url optional) |

## Extraction Method (extraction_method)

| Wert | Beschreibung |
|------|-------------|
| `simple` | Schnelle HTML-Extraktion (Standard) |
| `browser` | Vollständiges Browser-Rendering für JS-Seiten |

## Repository (repository)

| Wert | URL |
|------|-----|
| `staging` | repository.staging.openeduhub.net (Standard) |
| `prod` | redaktion.openeduhub.net |

## Feld-Optionen

- **schema_file**: Schema-Datei die das Feld enthält, z.B. `event.json`, `core.json`
- **field_id**: Feld-ID zum Extrahieren, z.B. `schema:startDate`, `cclom:title`
- **existing_metadata**: Bestehende Metadaten als Kontext (optional)
- **normalize**: Normalisierung anwenden (Standard: `true`)

## LLM-Optionen

- **llm_provider**: `b-api-openai` (Standard), `openai`, `b-api-academiccloud`
- **llm_model**: z.B. `gpt-4.1-mini` (Standard), `gpt-4o-mini`, `deepseek-r1`
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ExtractFieldRequest"},
                    "examples": {
                        "text_input": {
                            "summary": "1. Text-Eingabe",
                            "description": "Extrahiert ein Feld aus direktem Text.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                                "source_url": "",
                                "extraction_method": "simple",
                                "node_id": "",
                                "repository": "staging",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "schema:startDate",
                                "existing_metadata": {},
                                "language": "de",
                                "normalize": True,
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        },
                        "url_input": {
                            "summary": "2. URL-Eingabe",
                            "description": "Extrahiert ein Feld aus Text einer URL.",
                            "value": {
                                "input_source": "url",
                                "text": "",
                                "source_url": "https://www.wirlernenonline.de",
                                "extraction_method": "simple",
                                "node_id": "",
                                "repository": "staging",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "cclom:title",
                                "existing_metadata": {},
                                "language": "de",
                                "normalize": True,
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        },
                        "node_id_input": {
                            "summary": "3. NodeID-Eingabe",
                            "description": "Extrahiert ein Feld aus Repository-Node Daten.",
                            "value": {
                                "input_source": "node_id",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "simple",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "repository": "staging",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "schema:startDate",
                                "existing_metadata": {},
                                "language": "de",
                                "normalize": True,
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        },
                        "feld_korrigieren": {
                            "summary": "4. Feld korrigieren",
                            "description": "Korrigiert ein Feld basierend auf existing_metadata.",
                            "value": {
                                "input_source": "text",
                                "text": "Der Workshop wurde auf den 20. März 2025 verschoben.",
                                "source_url": "",
                                "extraction_method": "simple",
                                "node_id": "",
                                "repository": "staging",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "schema:startDate",
                                "existing_metadata": {"schema:startDate": "2025-03-15T00:00"},
                                "language": "de",
                                "normalize": True,
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        }
                    }
                }
            }
        }
    }
)
async def extract_field(req: ExtractFieldRequest):
    """
    Extract a single field from text.
    
    Use this endpoint to:
    - Fix individual fields without full re-extraction
    - Test different LLM models on specific fields
    - Update a single field with new information
    
    The extraction uses the same LLM pipeline as /generate but for one field only.
    Normalization is applied by default (dates, vocabularies, etc.).
    """
    start_time = time.time()
    
    # Handle input source
    text = req.text
    existing_metadata = req.existing_metadata or {}
    
    if req.input_source != InputSource.TEXT:
        input_service = get_input_source_service()
        try:
            if req.input_source == InputSource.URL:
                if not req.source_url:
                    raise HTTPException(status_code=400, detail="source_url required for input_source='url'")
                extracted_text = await input_service.fetch_from_url(req.source_url, req.extraction_method.value, lang=req.language)
                text = f"Quell-URL / Source URL: {req.source_url}\n\n{extracted_text}"
            elif req.input_source == InputSource.NODE_ID:
                if not req.node_id:
                    raise HTTPException(status_code=400, detail="node_id required for input_source='node_id'")
                input_data = await input_service.fetch_from_node_id(req.node_id, req.repository.value)
                # Prepend source URL if available (from ccm:wwwurl) so LLM can use it
                if input_data.source_url:
                    text = f"Quell-URL / Source URL: {input_data.source_url}\n\n{input_data.text}"
                else:
                    text = input_data.text
                if input_data.existing_metadata:
                    existing_metadata = {**input_data.existing_metadata, **existing_metadata}
            elif req.input_source == InputSource.NODE_URL:
                if not req.node_id:
                    raise HTTPException(status_code=400, detail="node_id required for input_source='node_url'")
                input_data = await input_service.fetch_from_node_url(
                    req.node_id, req.repository.value, req.source_url or None, req.extraction_method.value,
                    lang=req.language
                )
                source_url_info = input_data.source_url or req.source_url
                if source_url_info:
                    text = f"Quell-URL / Source URL: {source_url_info}\n\n{input_data.text}"
                else:
                    text = input_data.text
                if input_data.existing_metadata:
                    existing_metadata = {**input_data.existing_metadata, **existing_metadata}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to fetch input: {str(e)}")
    
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    
    # Truncate excessively long text to prevent exceeding LLM context window
    MAX_TEXT_LENGTH = 500_000
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH]
    
    # Resolve "latest" to actual version
    version = req.version
    if version == "latest" or not version:
        version = get_latest_version(req.context)
    
    # Load schema to get field definition
    try:
        schema = load_schema(req.context, version, req.schema_file)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    # Find the field in the schema
    field_def = None
    for field in schema.get("fields", []):
        if field.get("id") == req.field_id:
            field_def = field
            break
    
    if not field_def:
        # Also check core.json if not found
        try:
            core_schema = load_schema(req.context, version, "core.json")
            for field in core_schema.get("fields", []):
                if field.get("id") == req.field_id:
                    field_def = field
                    break
        except Exception:
            pass
    
    if not field_def:
        raise HTTPException(
            status_code=404,
            detail=f"Field '{req.field_id}' not found in schema '{req.schema_file}' or core.json"
        )
    
    # Get LLM service with optional overrides
    llm_service = get_llm_service(
        llm_provider=req.llm_provider,
        llm_model=req.llm_model
    )
    
    # Get existing value from existing_metadata if provided
    existing_value = None
    if existing_metadata:
        existing_value = existing_metadata.get(req.field_id)
    
    # Extract the field (returns tuple: field_id, value, error)
    _, raw_value, error = await llm_service.extract_field(
        field=field_def,
        text=text,
        existing_value=existing_value,
        language=req.language,
    )
    
    # Close non-default LLM service HTTP client to prevent leak
    if req.llm_provider is not None or req.llm_model is not None:
        await llm_service.close()
    
    if error:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {error}")
    
    # Apply normalization if requested
    normalized = False
    value = raw_value
    
    if req.normalize and raw_value is not None:
        normalizer = get_field_normalizer()
        normalized_value = normalizer.normalize_field_value(
            value=raw_value,
            field_schema=field_def.get("system", field_def),
            normalize_vocabularies=True
        )
        if normalized_value != raw_value:
            value = normalized_value
            normalized = True
    
    # Determine if value changed
    changed = value != existing_value
    
    # Get field label
    field_label = field_def.get("label", {})
    if isinstance(field_label, dict):
        field_label = field_label.get(req.language, field_label.get("de", req.field_id))
    
    processing_time = int((time.time() - start_time) * 1000)
    
    return ExtractFieldResponse(
        field_id=req.field_id,
        field_label=field_label,
        value=value,
        raw_value=raw_value if normalized else None,
        previous_value=existing_value,
        changed=changed,
        normalized=normalized,
        context=req.context,
        version=version,
        schema_file=req.schema_file,
        processing={
            "llm_provider": llm_service.provider,
            "llm_model": llm_service.model,
            "processing_time_ms": processing_time
        }
    )


# ============================================================================
# Main Endpoints
# ============================================================================

@app.post(
    "/generate", 
    response_model=GenerateResponse,
    summary="Metadaten generieren",
    description="""Generiert vollständige Metadaten aus Text, URL oder Repository-Node mittels LLM.

## Input-Quellen (input_source)

| Wert | Beschreibung | Benötigte Felder |
|------|-------------|------------------|
| `text` | Direkter Text als Eingabe (Standard) | `text` |
| `url` | Text von URL via Crawler abrufen | `source_url`, optional `extraction_method` |
| `node_id` | Metadaten + hinterlegte Volltexte von Repository-Node | `node_id`, `repository` |
| `node_url` | Repository-Node + Crawler-Fallback (URL aus ccm:wwwurl) | `node_id`, `repository` (source_url optional, wird aus Metadaten geholt) |

## Extraction Method (extraction_method)

| Wert | Beschreibung |
|------|-------------|
| `simple` | Schnelle HTML-Extraktion (Standard) |
| `browser` | Vollständiges Browser-Rendering für JS-Seiten |

## Repository (repository)

| Wert | URL |
|------|-----|
| `staging` | repository.staging.openeduhub.net (Standard) |
| `prod` | redaktion.openeduhub.net |

## Schema-Optionen

- **context**: Schema-Kontext, z.B. `default`, `mds_oeh`
- **version**: Schema-Version, `latest` (Standard) oder spezifisch z.B. `1.8.0`
- **schema_file**: `auto` (automatische Erkennung), oder spezifisch z.B. `event.json`

## Extraktions-Optionen

- **language**: Sprache für Extraktion (`de` oder `en`)
- **max_workers**: Parallele LLM-Worker (1-20, Standard: 10)
- **include_core**: Core-Felder einbeziehen (Standard: `true`)
- **enable_geocoding**: Adressen zu Koordinaten umwandeln (Standard: `true`)
- **normalize**: Normalisierung für Datum, Boolean, Vokabulare (Standard: `true`)

## Regenerations-Optionen

- **existing_metadata**: Bestehende Metadaten als Basis (werden erweitert)
- **regenerate_fields**: Liste von Feld-IDs die neu extrahiert werden sollen
- **regenerate_empty**: Leere Felder in existing_metadata neu extrahieren (Standard: `false`)

## LLM-Optionen

- **llm_provider**: `b-api-openai` (Standard), `openai`, `b-api-academiccloud`
- **llm_model**: z.B. `gpt-4.1-mini` (Standard), `gpt-4o-mini`, `deepseek-r1`
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/GenerateRequest"},
                    "examples": {
                        "text_input": {
                            "summary": "1. Text-Eingabe (Standard)",
                            "description": "Direkter Text als Eingabe. Schema wird automatisch erkannt.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.\nLernen Sie die Grundlagen der künstlichen Intelligenz kennen.",
                                "source_url": "",
                                "extraction_method": "simple",
                                "node_id": "",
                                "repository": "staging",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "max_workers": 10,
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        },
                        "url_input": {
                            "summary": "2. URL-Eingabe (Crawler)",
                            "description": "Text von URL abrufen via Text-Extraction-API. extraction_method: 'simple' (schnell) oder 'browser' (JS-Rendering).",
                            "value": {
                                "input_source": "url",
                                "text": "",
                                "source_url": "https://www.wirlernenonline.de",
                                "extraction_method": "simple",
                                "node_id": "",
                                "repository": "staging",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "max_workers": 10,
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        },
                        "node_id_input": {
                            "summary": "3. NodeID-Eingabe (Repository)",
                            "description": "Metadaten + hinterlegte Volltexte von Repository-Node abrufen. repository: 'staging' oder 'prod'.",
                            "value": {
                                "input_source": "node_id",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "simple",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "repository": "staging",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "max_workers": 10,
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        },
                        "node_url_input": {
                            "summary": "4. NodeID+URL (kombiniert)",
                            "description": "Repository-Metadaten + Volltext nutzen. Falls kein Volltext: URL aus ccm:wwwurl holen und Crawler nutzen.",
                            "value": {
                                "input_source": "node_url",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "simple",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "repository": "staging",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "max_workers": 10,
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        },
                        "mit_existing_metadata": {
                            "summary": "5. Mit bestehenden Metadaten",
                            "description": "existing_metadata als Basis, nur bestimmte Felder neu extrahieren.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                                "source_url": "",
                                "extraction_method": "simple",
                                "node_id": "",
                                "repository": "staging",
                                "existing_metadata": {
                                    "cclom:title": "Mein Workshop",
                                    "cclom:general_keyword": ["KI", "Bildung"]
                                },
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "language": "de",
                                "max_workers": 10,
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": ["schema:startDate"],
                                "regenerate_empty": True,
                                "llm_provider": "b-api-openai",
                                "llm_model": "gpt-4.1-mini"
                            }
                        }
                    }
                },
                "text/plain": {
                    "schema": {"type": "string"},
                    "examples": {
                        "mehrzeiliger_text": {
                            "summary": "Mehrzeiliger Text (ohne JSON)",
                            "description": "Für mehrzeilige Texte: Einfach den Text einfügen. Schema wird automatisch erkannt.",
                            "value": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.\n\nLernen Sie die Grundlagen der künstlichen Intelligenz kennen.\n\nZielgruppe: Lehrkräfte\nKosten: 49 Euro"
                        }
                    }
                }
            }
        }
    }
)
async def generate_metadata(request: Request):
    """
    Generate metadata from text.
    
    Extracts metadata from text using AI. Control characters are auto-sanitized.
    
    **Output Format:**
    - Header: contextName, schemaVersion, metadataset, language, exportedAt
    - metadata: Flat key-value pairs (field_id: value)
    - processing: Success status, stats, LLM info, errors/warnings
    """
    # Check content type to handle both JSON and plain text
    content_type = request.headers.get("content-type", "application/json")
    
    try:
        raw_body = await request.body()
        body_str = raw_body.decode('utf-8')
        
        if "text/plain" in content_type:
            # Plain text mode: text is the body, use defaults for other params
            data = {
                "text": body_str,
                "context": "default",
                "version": "latest",
                "schema_file": "auto",
                "language": "de",
                "include_core": True,
                "max_workers": 10,
            }
        else:
            # JSON mode: parse and sanitize
            sanitized = sanitize_json_string(body_str)
            
            try:
                data = json.loads(sanitized)
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid JSON: {str(e)}. Tip: Use Content-Type 'text/plain' for multi-line text input."
                )
        
        # Validate with Pydantic
        try:
            req = GenerateRequest(**data)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Request parsing error: {str(e)}")
    
    # Handle different input sources
    text = req.text
    existing_metadata = req.existing_metadata or {}
    
    # Extract _origins from existing_metadata (passed by web component)
    origins = existing_metadata.pop("_origins", None) if existing_metadata else None
    
    if req.input_source == InputSource.TEXT:
        # Direct text input
        if not text or not text.strip():
            raise HTTPException(status_code=400, detail="Text is required when input_source='text'")
    
    elif req.input_source == InputSource.URL:
        # Fetch text from URL via text extraction API
        if not req.source_url:
            raise HTTPException(status_code=400, detail="source_url is required when input_source='url'")
        try:
            input_service = get_input_source_service()
            extracted_text = await input_service.fetch_from_url(
                url=req.source_url,
                method=req.extraction_method.value,
                lang=req.language
            )
            # Prepend source URL to text so LLM can use it for ccm:wwwurl field
            text = f"Quell-URL / Source URL: {req.source_url}\n\n{extracted_text}"
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch text from URL: {str(e)}")
    
    elif req.input_source == InputSource.NODE_ID:
        # Fetch from repository by NodeID
        if not req.node_id:
            raise HTTPException(status_code=400, detail="node_id is required when input_source='node_id'")
        try:
            input_service = get_input_source_service()
            input_data = await input_service.fetch_from_node_id(
                node_id=req.node_id,
                repository=req.repository.value
            )
            # Prepend source URL if available (from ccm:wwwurl) so LLM can use it
            if input_data.source_url:
                text = f"Quell-URL / Source URL: {input_data.source_url}\n\n{input_data.text}"
            else:
                text = input_data.text
            # Merge fetched metadata with provided existing_metadata (provided takes precedence)
            existing_metadata = {**input_data.existing_metadata, **existing_metadata} if input_data.existing_metadata else existing_metadata
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch from repository: {str(e)}")
    
    elif req.input_source == InputSource.NODE_URL:
        # Fetch from repository + URL fallback (URL from ccm:wwwurl if not provided)
        if not req.node_id:
            raise HTTPException(status_code=400, detail="node_id is required when input_source='node_url'")
        try:
            input_service = get_input_source_service()
            input_data = await input_service.fetch_from_node_url(
                node_id=req.node_id,
                repository=req.repository.value,
                source_url=req.source_url or None,
                extraction_method=req.extraction_method.value,
                lang=req.language
            )
            # Prepend source URL to text so LLM can use it for ccm:wwwurl field
            source_url_info = input_data.source_url or req.source_url
            if source_url_info:
                text = f"Quell-URL / Source URL: {source_url_info}\n\n{input_data.text}"
            else:
                text = input_data.text
            # Merge fetched metadata with provided existing_metadata (provided takes precedence)
            existing_metadata = {**input_data.existing_metadata, **existing_metadata} if input_data.existing_metadata else existing_metadata
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch input data: {str(e)}")
    
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="No text content available from input source")
    
    # Truncate excessively long text to prevent exceeding LLM context window
    MAX_TEXT_LENGTH = 500_000  # ~125k tokens — generous limit for long web pages
    if len(text) > MAX_TEXT_LENGTH:
        original_len = len(text)
        text = text[:MAX_TEXT_LENGTH]
        print(f"⚠️ Text truncated from {original_len} to {MAX_TEXT_LENGTH} characters")
    
    # Resolve "latest" to actual version
    version = req.version
    if version == "latest" or not version:
        version = get_latest_version(req.context)
    
    # Get service with optional LLM overrides
    service = get_metadata_service(
        llm_provider=req.llm_provider,
        llm_model=req.llm_model
    )
    
    result = await service.generate_metadata(
        text=text,
        context=req.context,
        version=version,
        schema_file=req.schema_file,
        existing_metadata=existing_metadata,
        language=req.language,
        max_workers=req.max_workers,
        include_core=req.include_core,
        enable_geocoding=req.enable_geocoding,
        normalize_output=req.normalize,
        normalize_vocabularies=req.normalize,
        regenerate_fields=req.regenerate_fields,
        regenerate_empty=req.regenerate_empty,
        origins=origins,
    )
    
    # Close non-default LLM service HTTP client to prevent leak
    if req.llm_provider is not None or req.llm_model is not None:
        await service.llm_service.close()
    
    # Build flat response (metadata fields at top level)
    response = {
        "contextName": result["contextName"],
        "schemaVersion": result["schemaVersion"],
        "metadataset": result["metadataset"],
        "language": result["language"],
        "exportedAt": result["exportedAt"],
    }
    
    # Add metadata fields directly (flat) - skip empty default values
    for key, value in result.get("metadata", {}).items():
        if value is not None and value != "" and value != [] and value != {}:
            response[key] = value
    
    # Add _origins tracking (ai/user per field)
    if result.get("_origins"):
        response["_origins"] = result["_origins"]
    
    # Add processing info at the end
    response["processing"] = result["processing"]
    
    return JSONResponse(content=response)


@app.post(
    "/validate", 
    response_model=ValidateResponse,
    summary="Metadaten validieren",
    description="""Validiert Metadaten gegen das Schema und prüft Pflichtfelder, Datentypen und Vokabular-Werte.

## Einfache Nutzung

Kopiere einfach den kompletten Output von `/generate` direkt hier rein – Context, Version und Schema werden automatisch erkannt.

## Validierungsprüfungen

- **Pflichtfelder**: Sind alle required Felder ausgefüllt?
- **Datentypen**: Stimmen die Werte mit den erwarteten Typen überein?
- **Vokabulare**: Sind nur erlaubte Vocabulary-Werte verwendet?
- **Format**: Sind Datumsangaben, URLs etc. korrekt formatiert?

## Response

- **valid**: `true` wenn alle Prüfungen bestanden
- **errors**: Liste der Validierungsfehler mit Feld-ID und Beschreibung
- **warnings**: Nicht-kritische Hinweise
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "examples": {
                        "direkt": {
                            "summary": "Direkter Output von /generate",
                            "description": "Kopiere einfach den kompletten Output von /generate hier rein",
                            "value": {
                                "contextName": "default",
                                "schemaVersion": "1.8.0",
                                "metadataset": "event.json",
                                "language": "de",
                                "cclom:title": "Workshop KI in der Bildung",
                                "cclom:general_description": "Ein Workshop über KI...",
                                "schema:actor": [{"name": "Max Mustermann"}]
                            }
                        }
                    }
                }
            }
        }
    }
)
async def validate_metadata(request: Request):
    """
    Validate metadata against schema.
    
    **Einfache Nutzung:** Kopiere einfach den kompletten Output von `/generate` direkt hier rein.
    
    Checks if the provided metadata conforms to the schema rules,
    including required fields, data types, and vocabulary constraints.
    
    **Auto-Detection**: Context, version, and schema are automatically detected from:
    - `contextName`, `schemaVersion`, `metadataset` (new flat format)
    """
    # Parse JSON body - accept both direct metadata or wrapped in "metadata" field
    try:
        raw_body = await request.body()
        body_str = raw_body.decode('utf-8')
        sanitized = sanitize_json_string(body_str)
        data = json.loads(sanitized)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    # If direct metadata (no "metadata" wrapper), wrap it for Pydantic model
    if "metadata" not in data or not isinstance(data.get("metadata"), dict):
        data = {"metadata": data}
    
    # Validate with Pydantic model
    try:
        req = ValidateRequest(**data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Validation error: {str(e)}")
    
    # Use get_effective_params for clean parameter extraction
    context, version, schema_file, metadata = req.get_effective_params()
    
    # Normalize version: strip leading "v" if present
    if version.startswith("v"):
        version = version[1:]
    # Resolve "latest" to actual version
    if version == "latest":
        version = get_latest_version(context)
    
    service = get_metadata_service()
    result = service.validate_metadata(
        metadata=metadata,
        context=context,
        version=version,
        schema_file=schema_file,
    )
    
    return ValidateResponse(**result)


@app.post(
    "/export/markdown", 
    response_model=ExportMarkdownResponse,
    summary="Metadaten als Markdown exportieren",
    description="""Konvertiert Metadaten in ein lesbares Markdown-Dokument mit Labels und Struktur.

## Einfache Nutzung

Kopiere einfach den kompletten Output von `/generate` direkt hier rein.

## Export-Format

Das generierte Markdown enthält:
- **Titel** des Inhalts
- **Feldgruppen** mit deutschen Labels
- **Werte** formatiert nach Feldtyp (Datum, Liste, Text etc.)
- **Vokabular-Labels** statt technischer IDs

## Verwendung

- Zur Dokumentation
- Zur menschlichen Review
- Zum Teilen mit Nicht-Technikern
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "examples": {
                        "direkt": {
                            "summary": "Direkter Output von /generate",
                            "description": "Kopiere einfach den kompletten Output von /generate hier rein",
                            "value": {
                                "contextName": "default",
                                "schemaVersion": "1.8.0",
                                "metadataset": "event.json",
                                "language": "de",
                                "cclom:title": "Workshop KI in der Bildung",
                                "cclom:general_description": "Ein Workshop über KI...",
                                "schema:actor": [{"name": "Max Mustermann"}]
                            }
                        }
                    }
                }
            }
        }
    }
)
async def export_markdown(request: Request):
    """
    Export metadata to human-readable Markdown.
    
    **Einfache Nutzung:** Kopiere einfach den kompletten Output von `/generate` direkt hier rein.
    
    Converts the metadata JSON to a formatted Markdown document
    with proper labels and structure.
    """
    # Parse JSON body - accept both direct metadata or wrapped in "metadata" field
    try:
        raw_body = await request.body()
        body_str = raw_body.decode('utf-8')
        sanitized = sanitize_json_string(body_str)
        data = json.loads(sanitized)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    # If direct metadata (no "metadata" wrapper), wrap it for Pydantic model
    if "metadata" not in data or not isinstance(data.get("metadata"), dict):
        data = {"metadata": data}
    
    # Validate with Pydantic model
    try:
        req = ExportMarkdownRequest(**data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Validation error: {str(e)}")
    
    # Use get_effective_params for clean parameter extraction
    context, version, schema_file, language, metadata = req.get_effective_params()
    
    # Normalize version: strip leading "v" if present
    if version.startswith("v"):
        version = version[1:]
    # Resolve "latest" to actual version
    if version == "latest":
        version = get_latest_version(context)
    
    service = get_metadata_service()
    markdown = service.export_to_markdown(
        metadata=metadata,
        context=context,
        version=version,
        schema_file=schema_file,
        language=language,
        include_empty=req.include_empty,
    )
    
    return ExportMarkdownResponse(
        markdown=markdown,
        schema_used=schema_file,
    )


# ============================================================================
# Repository Upload Endpoint
# ============================================================================

@app.post(
    "/upload", 
    response_model=UploadResponse,
    summary="Metadaten ins Repository hochladen",
    description="""Lädt Metadaten ins WLO edu-sharing Repository hoch und erstellt einen neuen Node.

## Einfache Nutzung

Kopiere einfach den kompletten Output von `/generate` direkt hier rein.

## Optionale Parameter

| Parameter | Werte | Beschreibung |
|-----------|-------|-------------|
| `repository` | `staging` (Standard), `prod` | Ziel-Repository (staging = repository.staging.openeduhub.net, prod = redaktion.openeduhub.net) |
| `check_duplicates` | `true` (Standard), `false` | Duplikat-Prüfung via ccm:wwwurl |
| `start_workflow` | `true` (Standard), `false` | Review-Workflow starten |

## Workflow

1. **Duplikat-Check** (optional): Prüft ob URL bereits existiert
2. **Node erstellen**: Legt neuen Node mit Basisdaten an
3. **Metadaten setzen**: Überträgt alle Metadaten
4. **Collections**: Fügt Node zu Collections hinzu (falls angegeben)
5. **Workflow starten** (optional): Startet Review-Prozess

## Response

- **success**: `true` bei erfolgreichem Upload
- **duplicate**: `true` wenn URL bereits existiert
- **node**: Node-Info mit ID und URL
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "examples": {
                        "direkt": {
                            "summary": "Direkter Output von /generate",
                            "description": "Kopiere den Output von /generate hier rein. Optional: repository, check_duplicates, start_workflow",
                            "value": {
                                "contextName": "default",
                                "schemaVersion": "1.8.0",
                                "metadataset": "event.json",
                                "cclom:title": "Workshop KI in der Bildung",
                                "ccm:wwwurl": "https://example.com/workshop",
                                "repository": "staging",
                                "check_duplicates": True,
                                "start_workflow": True
                            }
                        }
                    }
                }
            }
        }
    }
)
async def upload_to_repository(request: Request):
    """
    Upload metadata to WLO edu-sharing repository.
    
    **Einfache Nutzung:** Kopiere einfach den kompletten Output von `/generate` direkt hier rein.
    
    Optional kannst du Optionen mit übergeben:
    - `repository`: "staging" (default) oder "production"
    - `check_duplicates`: true (default) oder false
    - `start_workflow`: true (default) oder false
    
    **Workflow:**
    1. Check for duplicates by ccm:wwwurl (optional)
    2. Create node with minimal data
    3. Set full metadata
    4. Add to collections (if specified)
    5. Start review workflow (optional)
    """
    # Parse JSON body
    try:
        raw_body = await request.body()
        body_str = raw_body.decode('utf-8')
        sanitized = sanitize_json_string(body_str)
        data = json.loads(sanitized)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    # If direct metadata (no "metadata" wrapper), wrap it for Pydantic model
    if "metadata" not in data or not isinstance(data.get("metadata"), dict):
        # Extract upload options before wrapping
        repository = data.pop("repository", "staging")
        check_duplicates = data.pop("check_duplicates", True)
        start_workflow = data.pop("start_workflow", True)
        data = {
            "metadata": data,
            "repository": repository,
            "check_duplicates": check_duplicates,
            "start_workflow": start_workflow,
        }
    
    # Validate with Pydantic model
    try:
        req = UploadRequest(**data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Validation error: {str(e)}")
    
    repo_service = get_repository_service()
    
    if not repo_service:
        raise HTTPException(
            status_code=503,
            detail="Repository service not configured. Set WLO_GUEST_USERNAME and WLO_GUEST_PASSWORD environment variables."
        )
    
    if req.repository not in ("staging", "prod", "production"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid repository: {req.repository}. Use 'staging' or 'prod'."
        )
    
    result = await repo_service.upload_metadata(
        metadata=req.metadata,
        repository=req.repository,
        check_duplicates=req.check_duplicates,
        start_workflow=req.start_workflow,
    )
    
    # Convert nested node dict to UploadedNodeInfo if present
    node_info = None
    if result.get("node"):
        node_info = UploadedNodeInfo(**result["node"])
    
    return UploadResponse(
        success=result.get("success", False),
        duplicate=result.get("duplicate"),
        repository=result.get("repository"),
        node=node_info,
        error=result.get("error"),
        step=result.get("step"),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
