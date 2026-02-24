"""Schema loader utility for loading and caching schema definitions."""
import json
from pathlib import Path
from typing import Any, Optional
from functools import lru_cache


SCHEMATA_PATH = Path(__file__).parent.parent / "schemata"


@lru_cache(maxsize=1)
def load_context_registry() -> dict[str, Any]:
    """Load the context registry."""
    registry_path = SCHEMATA_PATH / "context-registry.json"
    with open(registry_path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=10)
def load_manifest(context: str) -> dict[str, Any]:
    """Load manifest for a context."""
    registry = load_context_registry()
    context_info = registry.get("contexts", {}).get(context)
    if not context_info:
        raise ValueError(f"Unknown context: {context}")
    
    context_path = context_info.get("path", context)
    manifest_path = SCHEMATA_PATH / context_path / "manifest.json"
    
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_version(context: str, version: str) -> str:
    """Resolve 'latest' to actual version number. Strips 'v' prefix if present."""
    if version.lower() == "latest":
        return get_latest_version(context)
    # Strip 'v' prefix — directory path already adds it
    return version.lstrip("v")


@lru_cache(maxsize=100)
def load_schema(context: str, version: str, schema_file: str) -> dict[str, Any]:
    """Load a specific schema definition."""
    # Resolve 'latest' to actual version
    resolved_version = resolve_version(context, version)
    
    registry = load_context_registry()
    context_info = registry.get("contexts", {}).get(context)
    if not context_info:
        raise ValueError(f"Unknown context: {context}")
    
    context_path = context_info.get("path", context)
    schema_path = SCHEMATA_PATH / context_path / f"v{resolved_version}" / schema_file
    
    if not schema_path.exists():
        raise ValueError(f"Schema not found: {schema_path}")
    
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_latest_version(context: str = "default") -> str:
    """
    Get the latest (default) version for a context.
    
    Checks for:
    1. Version marked as isDefault in manifest
    2. defaultVersion in context-registry
    3. Highest semantic version number as fallback
    """
    try:
        registry = load_context_registry()
        context_info = registry.get("contexts", {}).get(context, {})
        manifest = load_manifest(context)
        versions = manifest.get("versions", {})
        
        # 1. Check for version with isDefault=true
        for version, info in versions.items():
            if info.get("isDefault", False):
                return version
        
        # 2. Use defaultVersion from context-registry
        if context_info.get("defaultVersion"):
            return context_info["defaultVersion"]
        
        # 3. Fallback: highest semantic version
        if versions:
            def _parse_version(v: str) -> list[int]:
                try:
                    return [int(x) for x in v.split(".")]
                except ValueError:
                    return [0]
            
            sorted_versions = sorted(
                versions.keys(),
                key=_parse_version,
                reverse=True
            )
            return sorted_versions[0]
        
        return "1.0.0"
    except Exception:
        return "1.8.1"  # Hardcoded fallback


def get_available_contexts() -> list[dict[str, Any]]:
    """Get list of available contexts with their versions."""
    registry = load_context_registry()
    contexts = []
    
    for context_name, context_info in registry.get("contexts", {}).items():
        manifest = load_manifest(context_name)
        versions = list(manifest.get("versions", {}).keys())
        default_version = get_latest_version(context_name)
        
        contexts.append({
            "name": context_name,
            "display_name": context_info.get("name", context_name),
            "versions": versions,
            "default_version": default_version,
        })
    
    return contexts


def get_available_schemas(context: str, version: str) -> list[dict[str, Any]]:
    """Get list of available schemas for a context/version."""
    # Resolve 'latest' to actual version
    resolved_version = resolve_version(context, version)
    
    manifest = load_manifest(context)
    version_info = manifest.get("versions", {}).get(resolved_version)
    
    if not version_info:
        raise ValueError(f"Unknown version: {version} (resolved: {resolved_version})")
    
    schemas = []
    for schema_file in version_info.get("schemas", []):
        try:
            schema = load_schema(context, version, schema_file)
            schemas.append({
                "file": schema_file,
                "profile_id": schema.get("profileId", ""),
                "label": schema.get("label", {"de": schema_file, "en": schema_file}),
                "groups": [g.get("id") for g in schema.get("groups", [])],
                "field_count": len(schema.get("fields", [])),
            })
        except Exception:
            continue
    
    return schemas


def get_content_types(context: str, version: str) -> list[dict[str, Any]]:
    """Get content types from core.json for schema detection."""
    try:
        core_schema = load_schema(context, version, "core.json")
        
        # Find the content type field (ccm:oeh_flex_lrt or similar)
        for field in core_schema.get("fields", []):
            vocab = field.get("system", {}).get("vocabulary", {})
            concepts = vocab.get("concepts", [])
            
            content_types = []
            for concept in concepts:
                if concept.get("schema_file"):
                    content_types.append({
                        "uri": concept.get("uri", ""),
                        "label": concept.get("label", {}),
                        "schema_file": concept.get("schema_file"),
                    })
            
            if content_types:
                return content_types
        
        return []
    except Exception:
        return []


def detect_schema_from_text(text: str, context: str, version: str) -> str:
    """Detect the most appropriate schema based on text content."""
    text_lower = text.lower()
    
    # Keywords for schema detection
    schema_keywords = {
        "event.json": [
            "veranstaltung", "event", "workshop", "seminar", "konferenz",
            "tagung", "webinar", "schulung", "kurs", "fortbildung",
            "datum", "uhrzeit", "anmeldung", "teilnehmer"
        ],
        "person.json": [
            "person", "autor", "author", "referent", "dozent",
            "lehrer", "professor", "experte", "speaker"
        ],
        "organization.json": [
            "organisation", "organization", "firma", "company",
            "verein", "institution", "hochschule", "universität",
            "schule", "unternehmen"
        ],
        "education_offer.json": [
            "bildungsangebot", "kursangebot", "lehrgang", "ausbildung",
            "studiengang", "weiterbildung", "zertifikat", "abschluss"
        ],
        "learning_material.json": [
            "material", "arbeitsblatt", "unterrichtsmaterial",
            "lernmaterial", "ressource", "dokument", "präsentation",
            "video", "podcast", "buch", "artikel"
        ],
        "tool_service.json": [
            "tool", "software", "app", "anwendung", "dienst",
            "service", "plattform", "programm"
        ],
    }
    
    # Count keyword matches
    scores = {}
    for schema_file, keywords in schema_keywords.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[schema_file] = score
    
    # Return schema with highest score, or default to learning_material
    if scores:
        return max(scores, key=scores.get)
    
    return "learning_material.json"


def get_schema_fields(context: str, version: str, schema_file: str) -> list[dict[str, Any]]:
    """Get all fields from a schema with their configurations."""
    schema = load_schema(context, version, schema_file)
    return schema.get("fields", [])


def get_ai_fillable_fields(context: str, version: str, schema_file: str) -> list[dict[str, Any]]:
    """Get only AI-fillable fields from a schema."""
    fields = get_schema_fields(context, version, schema_file)
    return [
        f for f in fields
        if f.get("system", {}).get("ai_fillable", True)
    ]


def get_repo_fields(context: str, version: str, schema_file: Optional[str] = None) -> set[str]:
    """
    Get all field IDs that have repo_field=true from core.json + optional special schema.
    
    Args:
        context: Schema context (e.g. 'default')
        version: Schema version (e.g. '1.8.1' or 'latest')
        schema_file: Optional special schema (e.g. 'event.json')
    
    Returns:
        Set of field IDs (system.path) that should be written to the repository.
    """
    repo_field_ids: set[str] = set()
    
    # Always load core.json
    try:
        core_fields = get_schema_fields(context, version, "core.json")
        for field in core_fields:
            system = field.get("system", {})
            if system.get("repo_field", False):
                field_id = system.get("path") or field.get("id")
                if field_id:
                    repo_field_ids.add(field_id)
    except Exception:
        pass
    
    # Load special schema if provided (and not core.json itself)
    if schema_file and schema_file != "core.json":
        try:
            special_fields = get_schema_fields(context, version, schema_file)
            for field in special_fields:
                system = field.get("system", {})
                if system.get("repo_field", False):
                    field_id = system.get("path") or field.get("id")
                    if field_id:
                        repo_field_ids.add(field_id)
        except Exception:
            pass
    
    return repo_field_ids
