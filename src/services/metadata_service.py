"""Metadata extraction and processing service."""
import time
import re
import json
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import get_settings


def parse_update_text(text: str) -> tuple[str, Optional[dict[str, Any]]]:
    """
    Parse text for [IST-STAND]/[UPDATE] markers.
    
    Supported formats:
    - [IST-STAND] ... [UPDATE] ...
    - [CURRENT] ... [UPDATE] ...
    - [EXISTING] ... [NEW] ...
    
    Returns:
        tuple of (actual_text, existing_metadata)
        If no markers found, returns (original_text, None)
    """
    # Define marker patterns (case-insensitive)
    patterns = [
        # German: [IST-STAND] ... [UPDATE]
        (r'\[IST-STAND\]\s*(.*?)\s*\[UPDATE\]\s*(.*)', re.IGNORECASE | re.DOTALL),
        # English: [CURRENT] ... [UPDATE]
        (r'\[CURRENT\]\s*(.*?)\s*\[UPDATE\]\s*(.*)', re.IGNORECASE | re.DOTALL),
        # Alternative: [EXISTING] ... [NEW]
        (r'\[EXISTING\]\s*(.*?)\s*\[NEW\]\s*(.*)', re.IGNORECASE | re.DOTALL),
    ]
    
    for pattern, flags in patterns:
        match = re.match(pattern, text.strip(), flags)
        if match:
            existing_json_str = match.group(1).strip()
            update_text = match.group(2).strip()
            
            # Try to parse the existing metadata as JSON
            try:
                existing_metadata = json.loads(existing_json_str)
                if isinstance(existing_metadata, dict):
                    # Remove meta fields that shouldn't be passed through
                    meta_keys = ['contextName', 'schemaVersion', 'metadataset', 
                                 'language', 'exportedAt', 'processing']
                    for key in meta_keys:
                        existing_metadata.pop(key, None)
                    
                    return (update_text, existing_metadata)
            except json.JSONDecodeError:
                # If JSON parsing fails, return original text
                pass
    
    # No markers found or parsing failed
    return (text, None)


from ..utils.schema_loader import (
    load_schema,
    get_ai_fillable_fields,
    get_schema_fields,
    get_content_types,
    detect_schema_from_text,
)


def get_default_value_for_field(field: dict[str, Any]) -> Any:
    """
    Get the default empty value for a field based on its datatype.
    Matches the web component's output format.
    """
    system = field.get("system", {})
    datatype = system.get("datatype", "string")
    multiple = system.get("multiple", False)
    
    # Complex object types that need empty object arrays
    complex_types = {
        "schema:eventSchedule": [{}],
        "schema:openingHoursSpecification": [{}],
        "schema:location": [{}],
        "schema:organizer": [],
        "schema:actor": [],
        "schema:performer": [],
        "schema:attendee": [],
        "schema:offers": [{}],
        "schema:accessService": [],
        "schema:subEvent": [],
        "schema:recordedIn": [],
    }
    
    field_id = field.get("id", "")
    if field_id in complex_types:
        return complex_types[field_id]
    
    # Based on datatype
    if datatype == "array" or multiple:
        return []
    elif datatype == "object":
        return {}
    elif datatype == "number" or datatype == "integer":
        return None
    elif datatype == "boolean":
        return None
    else:
        # string, date, datetime, time, url, etc.
        return ""


from .llm_service import get_llm_service
from .geocoding_service import get_geocoding_service
from .output_normalizer import get_output_normalizer
from .field_normalizer import get_field_normalizer


class MetadataService:
    """Service for metadata extraction orchestration."""
    
    def __init__(self, llm_provider: Optional[str] = None, llm_model: Optional[str] = None):
        """
        Initialize metadata service.
        
        Args:
            llm_provider: Override default LLM provider
            llm_model: Override default LLM model
        """
        self.llm_service = get_llm_service(llm_provider=llm_provider, llm_model=llm_model)
        self.geocoding_service = get_geocoding_service()
        self.output_normalizer = get_output_normalizer()
        self.field_normalizer = get_field_normalizer()
        self.settings = get_settings()
    
    async def generate_metadata(
        self,
        text: str,
        context: str = "default",
        version: str = "1.8.0",
        schema_file: str = "auto",
        existing_metadata: Optional[dict[str, Any]] = None,
        language: str = "de",
        max_workers: int = 10,
        include_core: bool = True,
        enable_geocoding: bool = True,
        normalize_output: bool = True,
        normalize_vocabularies: bool = True,
        regenerate_fields: Optional[list[str]] = None,
        regenerate_empty: bool = False,
    ) -> dict[str, Any]:
        """
        Generate metadata from text.
        
        Args:
            text: Input text to extract metadata from
            context: Schema context
            version: Schema version
            schema_file: Schema file or 'auto' for detection
            existing_metadata: Existing metadata to update
            language: Extraction language
            max_workers: Parallel worker count
            include_core: Include core fields (title, description, etc.)
            enable_geocoding: Convert location addresses to coordinates
            normalize_output: Apply normalization to extracted values
            normalize_vocabularies: Normalize vocabulary values using fuzzy matching
            regenerate_fields: List of field IDs to regenerate (re-extract)
            regenerate_empty: Re-extract fields that are empty in existing_metadata
            
        Returns:
            Generated metadata with statistics
        """
        start_time = time.time()
        errors = []
        warnings = []
        
        # Parse text for [IST-STAND]/[UPDATE] markers
        actual_text, parsed_metadata = parse_update_text(text)
        
        # Merge parsed metadata with existing_metadata (parsed takes precedence)
        if parsed_metadata:
            if existing_metadata:
                # Merge: existing_metadata as base, parsed_metadata overwrites
                merged = {**existing_metadata, **parsed_metadata}
                existing_metadata = merged
            else:
                existing_metadata = parsed_metadata
            
            # Use the UPDATE text for extraction
            text = actual_text
            warnings.append("Text-basierte Metadaten-Übergabe erkannt: [IST-STAND]/[UPDATE]")
        
        # Get LLM config for response
        llm_config = self.settings.get_llm_config()
        
        # Detect schema if auto
        if schema_file == "auto":
            # Try LLM detection first
            content_types = get_content_types(context, version)
            if content_types:
                schema_file = await self.llm_service.detect_content_type(
                    text, content_types, language
                )
            else:
                # Fallback to keyword detection
                schema_file = detect_schema_from_text(text, context, version)
        
        # Load schema and get AI-fillable fields
        try:
            schema = load_schema(context, version, schema_file)
            fields = get_ai_fillable_fields(context, version, schema_file)
            
            # Load core fields if requested and schema is not core.json
            core_fields = []
            if include_core and schema_file != "core.json":
                try:
                    core_fields = get_ai_fillable_fields(context, version, "core.json")
                except Exception as e:
                    warnings.append(f"Could not load core fields: {str(e)}")
            
            # Combine fields (core first, then schema-specific)
            all_fields = core_fields + fields
            
            # Build field schema lookup for normalization
            field_schemas = {f.get("id"): f.get("system", {}) for f in all_fields}
            
        except Exception as e:
            processing_time = int((time.time() - start_time) * 1000)
            return self._build_error_response(
                context, version, schema_file, language, processing_time,
                llm_config, f"Schema loading failed: {str(e)}"
            )
        
        # Determine which fields to extract
        fields_to_extract = all_fields
        
        if regenerate_fields or regenerate_empty:
            # Filter fields based on regeneration options
            fields_to_extract = []
            for field in all_fields:
                field_id = field.get("id", "")
                
                # Explicit regenerate list
                if regenerate_fields and field_id in regenerate_fields:
                    fields_to_extract.append(field)
                    continue
                
                # Regenerate empty fields
                if regenerate_empty and existing_metadata:
                    existing_value = existing_metadata.get(field_id)
                    if existing_value is None or existing_value == "" or existing_value == []:
                        fields_to_extract.append(field)
                        continue
                
                # If not regenerating, skip fields that have existing values
                if existing_metadata and field_id in existing_metadata:
                    continue
                
                # New fields (not in existing) - always extract
                fields_to_extract.append(field)
        
        # Extract fields in parallel
        result = await self.llm_service.extract_fields_parallel(
            fields=fields_to_extract,
            text=text,
            existing_metadata=existing_metadata,
            language=language,
            max_workers=max_workers,
        )
        
        extracted_values = result.get("values", {})
        errors.extend(result.get("errors", []))
        
        # Build flat metadata (just field_id: value)
        flat_metadata = {}
        if existing_metadata:
            # Copy existing but exclude meta fields
            for key, value in existing_metadata.items():
                if not key.startswith("_"):
                    flat_metadata[key] = value
        
        # Add extracted values (overwrites existing)
        for field_id, value in extracted_values.items():
            if value is not None:
                flat_metadata[field_id] = value
        
        # Normalize field values based on schema
        if normalize_output:
            for field_id, value in flat_metadata.items():
                if value is not None and field_id in field_schemas:
                    try:
                        flat_metadata[field_id] = self.field_normalizer.normalize_field_value(
                            value, field_schemas[field_id], normalize_vocabularies
                        )
                    except Exception as e:
                        warnings.append(f"Field normalization warning for {field_id}: {str(e)}")
        
        # Geocode locations to add coordinates (runs at the end after all extraction)
        if enable_geocoding:
            try:
                flat_metadata = await self.geocoding_service.enrich_metadata_with_geocoding(
                    flat_metadata, language
                )
            except Exception as e:
                warnings.append(f"Geocoding failed: {str(e)}")
        
        # Normalize output structure to match Canvas web component
        try:
            flat_metadata = self.output_normalizer.normalize_output(flat_metadata)
        except Exception as e:
            warnings.append(f"Output normalization warning: {str(e)}")
        
        # Build ordered metadata with all schema fields (core first, then content-type)
        # This matches the web component's output format
        ordered_metadata = {}
        
        # Get all fields from schemas (not just AI-fillable) for complete output
        all_core_fields = []
        all_schema_fields = []
        
        if include_core and schema_file != "core.json":
            try:
                all_core_fields = get_schema_fields(context, version, "core.json")
            except Exception:
                pass
        
        try:
            all_schema_fields = get_schema_fields(context, version, schema_file)
        except Exception:
            pass
        
        # Add core fields first (in schema order)
        for field in all_core_fields:
            field_id = field.get("id", "")
            if field_id in flat_metadata:
                ordered_metadata[field_id] = flat_metadata[field_id]
            else:
                ordered_metadata[field_id] = get_default_value_for_field(field)
        
        # Add content-type specific fields (in schema order)
        for field in all_schema_fields:
            field_id = field.get("id", "")
            if field_id in flat_metadata:
                ordered_metadata[field_id] = flat_metadata[field_id]
            else:
                ordered_metadata[field_id] = get_default_value_for_field(field)
        
        # Add any remaining fields from flat_metadata that weren't in schemas
        for field_id, value in flat_metadata.items():
            if field_id not in ordered_metadata:
                ordered_metadata[field_id] = value
        
        processing_time = int((time.time() - start_time) * 1000)
        
        return {
            "contextName": context,
            "schemaVersion": version,
            "metadataset": schema_file,
            "language": language,
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "metadata": ordered_metadata,
            "processing": {
                "success": True,
                "fields_extracted": len([v for v in extracted_values.values() if v is not None]),
                "fields_total": len(all_fields),
                "processing_time_ms": processing_time,
                "llm_provider": self.settings.llm_provider,
                "llm_model": llm_config["model"],
                "errors": errors,
                "warnings": warnings,
            }
        }
    
    def _build_error_response(
        self,
        context: str,
        version: str,
        schema_file: str,
        language: str,
        processing_time: int,
        llm_config: dict,
        error_message: str,
    ) -> dict[str, Any]:
        """Build error response in the new format."""
        return {
            "contextName": context,
            "schemaVersion": version,
            "metadataset": schema_file,
            "language": language,
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "metadata": {},
            "processing": {
                "success": False,
                "fields_extracted": 0,
                "fields_total": 0,
                "processing_time_ms": processing_time,
                "llm_provider": self.settings.llm_provider,
                "llm_model": llm_config["model"],
                "errors": [error_message],
                "warnings": [],
            }
        }
    
    def validate_metadata(
        self,
        metadata: dict[str, Any],
        context: str = "default",
        version: str = "1.8.0",
        schema_file: str = "auto",
    ) -> dict[str, Any]:
        """
        Validate metadata against schema with extended validation.
        
        Performs:
        - Required field checks
        - Type validation (array, number, boolean, string)
        - Date/Datetime/Time format validation
        - URL format validation
        - Geo coordinate range validation
        - Closed vocabulary validation
        
        Returns validation results with errors and warnings.
        """
        import re
        
        errors = []
        warnings = []
        
        # Detect schema from metadata if auto
        if schema_file == "auto":
            schema_info = metadata.get("_schema", {})
            schema_file = schema_info.get("file", "learning_material.json")
        
        try:
            schema = load_schema(context, version, schema_file)
            fields = schema.get("fields", [])
        except Exception as e:
            return {
                "valid": False,
                "schema_used": schema_file,
                "errors": [{"field_id": "_schema", "message": str(e), "severity": "error"}],
                "warnings": [],
                "coverage": 0.0,
            }
        
        required_fields = []
        filled_required = 0
        
        for field in fields:
            field_id = field.get("id", "")
            system = field.get("system", {})
            is_required = system.get("required", False)
            datatype = system.get("datatype", "string")
            
            value = metadata.get(field_id)
            
            # Required field check
            if is_required:
                required_fields.append(field_id)
                if value is not None and value != "" and value != []:
                    filled_required += 1
                else:
                    errors.append({
                        "field_id": field_id,
                        "message": "Required field is empty",
                        "severity": "error",
                    })
            
            # Skip further validation if no value
            if value is None or value == "" or value == []:
                continue
            
            # Get values to validate (handle arrays)
            values_to_check = value if isinstance(value, list) else [value]
            
            # Type validation
            for v in values_to_check:
                # Basic type checks
                if datatype == "number" or datatype == "integer":
                    if not isinstance(v, (int, float)):
                        warnings.append({
                            "field_id": field_id,
                            "message": f"Expected number, got {type(v).__name__}: '{v}'",
                            "severity": "warning",
                        })
                
                elif datatype == "boolean":
                    if not isinstance(v, bool):
                        warnings.append({
                            "field_id": field_id,
                            "message": f"Expected boolean, got {type(v).__name__}: '{v}'",
                            "severity": "warning",
                        })
                
                # Date format validation (YYYY-MM-DD)
                elif datatype == "date":
                    if isinstance(v, str):
                        if not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
                            # Try to suggest correction
                            suggestion = self._suggest_date_format(v)
                            if suggestion:
                                warnings.append({
                                    "field_id": field_id,
                                    "message": f"Date '{v}' should be formatted as '{suggestion}'",
                                    "severity": "warning",
                                })
                            else:
                                warnings.append({
                                    "field_id": field_id,
                                    "message": f"Invalid date format '{v}' - expected YYYY-MM-DD",
                                    "severity": "warning",
                                })
                        else:
                            # Validate date values
                            try:
                                year, month, day = int(v[:4]), int(v[5:7]), int(v[8:10])
                                if not (1 <= month <= 12 and 1 <= day <= 31):
                                    warnings.append({
                                        "field_id": field_id,
                                        "message": f"Invalid date values in '{v}'",
                                        "severity": "warning",
                                    })
                            except ValueError:
                                pass
                
                # Datetime format validation (ISO 8601)
                elif datatype == "datetime":
                    if isinstance(v, str):
                        if not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?', v):
                            suggestion = self._suggest_datetime_format(v)
                            if suggestion:
                                warnings.append({
                                    "field_id": field_id,
                                    "message": f"Datetime '{v}' should be formatted as '{suggestion}'",
                                    "severity": "warning",
                                })
                            else:
                                warnings.append({
                                    "field_id": field_id,
                                    "message": f"Invalid datetime format '{v}' - expected YYYY-MM-DDTHH:MM:SS",
                                    "severity": "warning",
                                })
                
                # Time format validation (HH:MM:SS)
                elif datatype == "time":
                    if isinstance(v, str):
                        if not re.match(r'^\d{2}:\d{2}(:\d{2})?$', v):
                            suggestion = self._suggest_time_format(v)
                            if suggestion:
                                warnings.append({
                                    "field_id": field_id,
                                    "message": f"Time '{v}' should be formatted as '{suggestion}'",
                                    "severity": "warning",
                                })
                            else:
                                warnings.append({
                                    "field_id": field_id,
                                    "message": f"Invalid time format '{v}' - expected HH:MM:SS",
                                    "severity": "warning",
                                })
                
                # URL format validation
                elif datatype in ("uri", "url"):
                    if isinstance(v, str):
                        if not re.match(r'^https?://', v, re.IGNORECASE):
                            # Suggest adding https://
                            if re.match(r'^[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,}', v):
                                warnings.append({
                                    "field_id": field_id,
                                    "message": f"URL '{v}' missing protocol - should be 'https://{v}'",
                                    "severity": "warning",
                                })
                            else:
                                warnings.append({
                                    "field_id": field_id,
                                    "message": f"Invalid URL format '{v}' - must start with http:// or https://",
                                    "severity": "warning",
                                })
                
                # Number validation with German number word detection
                elif datatype in ("number", "integer") and isinstance(v, str):
                    suggestion = self._suggest_number_format(v)
                    if suggestion is not None:
                        warnings.append({
                            "field_id": field_id,
                            "message": f"Number '{v}' should be '{suggestion}'",
                            "severity": "warning",
                        })
            
            # Geo coordinate validation (detect by field_id)
            field_id_lower = field_id.lower()
            if 'latitude' in field_id_lower or 'lat' in field_id_lower:
                for v in values_to_check:
                    if isinstance(v, (int, float)):
                        if v < -90 or v > 90:
                            errors.append({
                                "field_id": field_id,
                                "message": f"Latitude {v} out of range (-90 to 90)",
                                "severity": "error",
                            })
            elif 'longitude' in field_id_lower or 'lon' in field_id_lower or 'lng' in field_id_lower:
                for v in values_to_check:
                    if isinstance(v, (int, float)):
                        if v < -180 or v > 180:
                            errors.append({
                                "field_id": field_id,
                                "message": f"Longitude {v} out of range (-180 to 180)",
                                "severity": "error",
                            })
            
            # Closed vocabulary validation
            vocabulary = system.get("vocabulary", {})
            if vocabulary and vocabulary.get("type") == "closed":
                concepts = vocabulary.get("concepts", [])
                valid_uris = {c.get("uri") for c in concepts}
                
                for v in values_to_check:
                    if v and v not in valid_uris:
                        # Try to find closest match for better error message
                        closest = self._find_closest_vocabulary_match(v, concepts)
                        if closest:
                            warnings.append({
                                "field_id": field_id,
                                "message": f"Value '{v}' not in vocabulary. Did you mean '{closest}'?",
                                "severity": "warning",
                            })
                        else:
                            warnings.append({
                                "field_id": field_id,
                                "message": f"Value '{v}' not in closed vocabulary",
                                "severity": "warning",
                            })
        
        coverage = (filled_required / len(required_fields) * 100) if required_fields else 100.0
        
        return {
            "valid": len(errors) == 0,
            "schema_used": schema_file,
            "errors": errors,
            "warnings": warnings,
            "coverage": round(coverage, 1),
        }
    
    def _find_closest_vocabulary_match(self, value: str, concepts: list) -> Optional[str]:
        """Find closest vocabulary match using Levenshtein distance."""
        if not isinstance(value, str):
            return None
        
        value_lower = value.lower()
        best_match = None
        best_distance = float('inf')
        
        for concept in concepts:
            uri = concept.get("uri", "")
            # Check URI similarity
            if value_lower in uri.lower():
                return uri
            
            # Check label similarity
            label = concept.get("label", {})
            for lang_label in label.values() if isinstance(label, dict) else [label]:
                if isinstance(lang_label, str):
                    dist = self._levenshtein_distance(value_lower, lang_label.lower())
                    if dist < best_distance and dist <= len(value) * 0.4:
                        best_distance = dist
                        best_match = uri
        
        return best_match
    
    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        """Calculate Levenshtein distance between two strings."""
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)
        
        if len(s2) == 0:
            return len(s1)
        
        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        return previous_row[-1]
    
    def _suggest_date_format(self, value: str) -> Optional[str]:
        """Try to parse date and suggest ISO format."""
        import re
        
        # German months
        german_months = {
            'januar': 1, 'jan': 1, 'februar': 2, 'feb': 2, 'märz': 3, 'mär': 3, 'mar': 3,
            'april': 4, 'apr': 4, 'mai': 5, 'juni': 6, 'jun': 6,
            'juli': 7, 'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'sept': 9,
            'oktober': 10, 'okt': 10, 'november': 11, 'nov': 11, 'dezember': 12, 'dez': 12
        }
        
        val = value.strip()
        
        # DD.MM.YYYY
        match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$', val)
        if match:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year}-{month:02d}-{day:02d}"
        
        # DD.MM.YY
        match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{2})$', val)
        if match:
            day, month = int(match.group(1)), int(match.group(2))
            year = int(match.group(3))
            year = 2000 + year if year < 50 else 1900 + year
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year}-{month:02d}-{day:02d}"
        
        # "15. März 2025" or "15 März 2025"
        match = re.match(r'^(\d{1,2})\.?\s*([a-zäöü]+)\s+(\d{4})$', val, re.IGNORECASE)
        if match:
            day = int(match.group(1))
            month_name = match.group(2).lower()
            year = int(match.group(3))
            if month_name in german_months and 1 <= day <= 31:
                return f"{year}-{german_months[month_name]:02d}-{day:02d}"
        
        # DD/MM/YYYY
        match = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', val)
        if match:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year}-{month:02d}-{day:02d}"
        
        return None
    
    def _suggest_datetime_format(self, value: str) -> Optional[str]:
        """Try to parse datetime and suggest ISO format."""
        import re
        
        val = value.strip()
        
        # Try date first, then add time
        date_suggestion = self._suggest_date_format(val.split()[0] if ' ' in val else val)
        if date_suggestion:
            # Check if there's a time part
            match = re.search(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', val)
            if match:
                hour, minute = int(match.group(1)), int(match.group(2))
                second = int(match.group(3)) if match.group(3) else 0
                return f"{date_suggestion}T{hour:02d}:{minute:02d}:{second:02d}"
            return f"{date_suggestion}T00:00:00"
        
        # German format: "15.03.2025 14:30"
        match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$', val)
        if match:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            hour, minute = int(match.group(4)), int(match.group(5))
            second = int(match.group(6)) if match.group(6) else 0
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"
        
        return None
    
    def _suggest_time_format(self, value: str) -> Optional[str]:
        """Try to parse time and suggest HH:MM:SS format."""
        import re
        
        val = value.strip()
        
        # H:MM or HH:MM (without seconds)
        match = re.match(r'^(\d{1,2}):(\d{2})$', val)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}:00"
        
        # German: "14 Uhr 30" or "14 Uhr"
        match = re.match(r'^(\d{1,2})\s*[Uu]hr\s*(\d{2})?$', val)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)) if match.group(2) else 0
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}:00"
        
        return None
    
    def _suggest_number_format(self, value: str) -> Optional[int]:
        """Try to parse German number words and suggest numeric format."""
        # Basic number words
        ones = {
            'null': 0, 'eins': 1, 'ein': 1, 'zwei': 2, 'drei': 3, 'vier': 4,
            'fünf': 5, 'sechs': 6, 'sieben': 7, 'acht': 8, 'neun': 9
        }
        teens = {
            'zehn': 10, 'elf': 11, 'zwölf': 12, 'dreizehn': 13, 'vierzehn': 14,
            'fünfzehn': 15, 'sechzehn': 16, 'siebzehn': 17, 'achtzehn': 18, 'neunzehn': 19
        }
        tens = {
            'zwanzig': 20, 'dreißig': 30, 'dreissig': 30, 'vierzig': 40,
            'fünfzig': 50, 'sechzig': 60, 'siebzig': 70, 'achtzig': 80, 'neunzig': 90
        }
        
        text = value.strip().lower()
        
        # Direct match
        if text in ones:
            return ones[text]
        if text in teens:
            return teens[text]
        if text in tens:
            return tens[text]
        if text == 'hundert':
            return 100
        if text == 'tausend':
            return 1000
        
        # Try to parse compound German numbers
        result = 0
        remaining = text
        
        if 'tausend' in remaining:
            parts = remaining.split('tausend', 1)
            prefix = parts[0].strip()
            if prefix == '' or prefix == 'ein':
                result += 1000
            elif prefix in ones:
                result += ones[prefix] * 1000
            remaining = parts[1].strip() if len(parts) > 1 else ''
        
        if 'hundert' in remaining:
            parts = remaining.split('hundert', 1)
            prefix = parts[0].strip()
            if prefix == '' or prefix == 'ein':
                result += 100
            elif prefix in ones:
                result += ones[prefix] * 100
            remaining = parts[1].strip() if len(parts) > 1 else ''
        
        if remaining:
            if remaining in ones:
                result += ones[remaining]
            elif remaining in teens:
                result += teens[remaining]
            elif remaining in tens:
                result += tens[remaining]
            elif 'und' in remaining:
                parts = remaining.split('und', 1)
                ones_part = parts[0].strip()
                tens_part = parts[1].strip()
                if ones_part in ones and tens_part in tens:
                    result += ones[ones_part] + tens[tens_part]
        
        return result if result > 0 else None
    
    def export_to_markdown(
        self,
        metadata: dict[str, Any],
        context: str = "default",
        version: str = "1.8.0",
        schema_file: str = "auto",
        language: str = "de",
        include_empty: bool = False,
    ) -> str:
        """
        Export metadata to human-readable Markdown.
        Includes both core fields and schema-specific fields.
        """
        # Detect schema from metadata if auto
        if schema_file == "auto":
            schema_info = metadata.get("_schema", {})
            schema_file = schema_info.get("file", "learning_material.json")
        
        # Load core schema for core fields
        core_schema = None
        try:
            core_schema = load_schema(context, version, "core.json")
        except Exception:
            pass
        
        # Load schema-specific fields
        schema = None
        try:
            if schema_file != "core.json":
                schema = load_schema(context, version, schema_file)
        except Exception:
            pass
        
        if not core_schema and not schema:
            return f"# Metadata\n\nSchema not found: {schema_file}"
        
        # Combine groups and fields from both schemas
        all_groups = []
        all_fields = []
        group_ids_seen = set()
        
        # Add core groups and fields first
        if core_schema:
            for group in core_schema.get("groups", []):
                if group.get("id") not in group_ids_seen:
                    all_groups.append(group)
                    group_ids_seen.add(group.get("id"))
            all_fields.extend(core_schema.get("fields", []))
        
        # Add schema-specific groups and fields
        if schema:
            for group in schema.get("groups", []):
                if group.get("id") not in group_ids_seen:
                    all_groups.append(group)
                    group_ids_seen.add(group.get("id"))
            all_fields.extend(schema.get("fields", []))
        
        # Get schema label
        schema_label = None
        if schema:
            schema_label = self._get_localized(schema.get("label", {}), language)
        
        # Group fields by group_id
        fields_by_group = {}
        for field in all_fields:
            group_id = field.get("group", "other")
            if group_id not in fields_by_group:
                fields_by_group[group_id] = []
            fields_by_group[group_id].append(field)
        
        # Build markdown
        lines = [f"# {schema_label or schema_file}", ""]
        
        for group in all_groups:
            group_id = group.get("id", "")
            group_label = self._get_localized(group.get("label", {}), language)
            group_fields = fields_by_group.get(group_id, [])
            
            if not group_fields:
                continue
            
            # Check if group has any non-empty values
            has_values = any(
                self._has_meaningful_value(metadata.get(f.get("id")))
                for f in group_fields
            )
            
            if not has_values and not include_empty:
                continue
            
            lines.append(f"## {group_label or group_id}")
            lines.append("")
            
            for field in group_fields:
                field_id = field.get("id", "")
                field_label = self._get_localized(field.get("label", {}), language)
                value = metadata.get(field_id)
                
                if not self._has_meaningful_value(value) and not include_empty:
                    continue
                
                # Format value
                formatted_value = self._format_value(value, field, language)
                lines.append(f"**{field_label}:** {formatted_value}")
            
            lines.append("")
        
        return "\n".join(lines)
    
    def _has_meaningful_value(self, value: Any) -> bool:
        """Check if a value is meaningful (not empty)."""
        if value is None:
            return False
        if value == "":
            return False
        if value == []:
            return False
        # Check for list of empty objects
        if isinstance(value, list):
            return any(
                item and (not isinstance(item, dict) or any(v for v in item.values()))
                for item in value
            )
        # Check for empty object
        if isinstance(value, dict):
            return any(v for v in value.values())
        return True
    
    def _get_localized(self, obj: dict[str, str], language: str) -> str:
        """Get localized string with fallback."""
        if isinstance(obj, str):
            return obj
        return obj.get(language, obj.get("de", obj.get("en", "")))
    
    def _format_value(
        self,
        value: Any,
        field: dict[str, Any],
        language: str,
    ) -> str:
        """Format a value for markdown output."""
        if value is None:
            return "_leer_" if language == "de" else "_empty_"
        
        if isinstance(value, list):
            if not value:
                return "_leer_" if language == "de" else "_empty_"
            
            # Check if vocabulary field
            vocabulary = field.get("system", {}).get("vocabulary", {})
            if vocabulary:
                concepts = {c.get("uri"): c for c in vocabulary.get("concepts", [])}
                formatted = []
                for v in value:
                    if v in concepts:
                        label = self._get_localized(concepts[v].get("label", {}), language)
                        formatted.append(label or v)
                    else:
                        formatted.append(self._format_single_value(v))
                return ", ".join(formatted)
            
            return ", ".join(self._format_single_value(v) for v in value)
        
        if isinstance(value, bool):
            return "Ja" if value else "Nein" if language == "de" else "Yes" if value else "No"
        
        # Check if vocabulary field for single value
        vocabulary = field.get("system", {}).get("vocabulary", {})
        if vocabulary:
            concepts = {c.get("uri"): c for c in vocabulary.get("concepts", [])}
            if value in concepts:
                return self._get_localized(concepts[value].get("label", {}), language) or str(value)
        
        return self._format_single_value(value)
    
    def _format_single_value(self, value: Any) -> str:
        """Format a single value, extracting from nested objects."""
        if value is None:
            return ""
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        if isinstance(value, dict):
            # Extract meaningful value from nested object
            if "name" in value:
                return str(value["name"])
            if "label" in value:
                return str(value["label"])
            if "uri" in value:
                return str(value["uri"])
            if "@value" in value:
                return str(value["@value"])
            # For complex objects, format key parts
            parts = []
            for key in ["streetAddress", "addressLocality", "postalCode"]:
                if key in value:
                    parts.append(str(value[key]))
            if parts:
                return ", ".join(parts)
            # Fallback: first non-empty string value
            for v in value.values():
                if isinstance(v, str) and v:
                    return v
        return str(value)


# Singleton instance (default config)
_metadata_service: Optional[MetadataService] = None


def get_metadata_service(
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None
) -> MetadataService:
    """
    Get metadata service instance.
    
    If LLM overrides are specified, creates a new instance.
    Otherwise returns the cached default instance.
    """
    global _metadata_service
    
    # If overrides specified, create new instance with those settings
    if llm_provider is not None or llm_model is not None:
        return MetadataService(llm_provider=llm_provider, llm_model=llm_model)
    
    # Return cached default instance
    if _metadata_service is None:
        _metadata_service = MetadataService()
    return _metadata_service
