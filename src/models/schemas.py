"""Pydantic models for API request/response schemas."""
import re
from enum import Enum
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field, field_validator


class InputSource(str, Enum):
    """Input source for metadata generation."""
    TEXT = "text"           # Direct text input (default)
    URL = "url"             # Fetch text via text extraction API
    NODE_ID = "node_id"     # Fetch from repository by NodeID
    NODE_URL = "node_url"   # Use NodeID + URL (prefers stored data, falls back to crawler)


class Repository(str, Enum):
    """Repository selection for NodeID lookups."""
    PROD = "prod"
    STAGING = "staging"


class ExtractionMethod(str, Enum):
    """Text extraction method for URL input."""
    SIMPLE = "simple"
    BROWSER = "browser"


def sanitize_text(text: str) -> str:
    """
    Sanitize text input by normalizing control characters.
    - Preserves newlines (\n), tabs (\t), and carriage returns (\r)
    - Removes other control characters (0x00-0x1F except \t, \n, \r)
    - Normalizes various whitespace characters to regular spaces
    """
    if not text:
        return text
    
    # Remove NULL bytes and other problematic control characters
    # Keep: \t (0x09), \n (0x0A), \r (0x0D)
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    
    # Normalize various Unicode whitespace to regular spaces
    text = re.sub(r'[\u00A0\u2000-\u200B\u202F\u205F\u3000]', ' ', text)
    
    # Normalize Windows line endings to Unix
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Remove excessive whitespace (more than 2 consecutive newlines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


class LocalizedString(BaseModel):
    """Localized string with de/en support."""
    de: Optional[str] = None
    en: Optional[str] = None


class GenerateRequest(BaseModel):
    """Request model for metadata generation."""
    
    # Input source selection
    input_source: InputSource = Field(
        default=InputSource.TEXT,
        description="Input source: 'text' (direct input), 'url' (fetch via crawler), 'node_id' (fetch from repository), 'node_url' (repository + crawler fallback)"
    )
    
    # Text input (required for input_source='text')
    text: Optional[str] = Field(
        default=None,
        description="Input text to extract metadata from. Required when input_source='text'."
    )
    
    # URL input (required for input_source='url' or 'node_url')
    source_url: Optional[str] = Field(
        default=None,
        description="URL to fetch text from via text extraction API. Required when input_source='url' or 'node_url'."
    )
    extraction_method: ExtractionMethod = Field(
        default=ExtractionMethod.SIMPLE,
        description="Text extraction method: 'simple' (fast, basic HTML parsing) or 'browser' (full browser rendering, slower)"
    )
    
    # NodeID input (required for input_source='node_id' or 'node_url')
    node_id: Optional[str] = Field(
        default=None,
        description="Repository NodeID to fetch metadata and text from. Required when input_source='node_id' or 'node_url'."
    )
    repository: Repository = Field(
        default=Repository.STAGING,
        description="Repository to use for NodeID lookup: 'prod' (redaktion.openeduhub.net) or 'staging' (repository.staging.openeduhub.net)"
    )
    
    # Common options
    existing_metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Existing metadata JSON to use as base (will be updated/enriched). For node_id/node_url sources, fetched metadata is merged."
    )
    context: str = Field(
        default="default",
        description="Schema context to use (e.g., 'default', 'mds_oeh')"
    )
    version: str = Field(
        default="latest",
        description="Schema version to use ('latest' for newest version, or specific like '1.8.0')"
    )
    schema_file: str = Field(
        default="auto",
        description="Schema file to use ('auto' for automatic detection, or specific like 'event.json')"
    )
    language: str = Field(
        default="de",
        description="Primary language for extraction (de/en)"
    )
    max_workers: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Maximum number of parallel LLM workers (1-20)"
    )
    include_core: bool = Field(
        default=True,
        description="Include core fields (title, description, keywords, etc.) in extraction"
    )
    enable_geocoding: bool = Field(
        default=True,
        description="Enable geocoding to convert location addresses to coordinates (uses Photon API)"
    )
    
    # Normalization option
    normalize: bool = Field(
        default=True,
        description="Apply normalization to extracted values (dates, booleans, vocabularies, etc.)"
    )
    
    # Field regeneration options
    regenerate_fields: Optional[list[str]] = Field(
        default=None,
        description="List of field IDs to regenerate (re-extract from text). Other fields use existing_metadata."
    )
    regenerate_empty: bool = Field(
        default=False,
        description="Re-extract fields that are empty/null in existing_metadata"
    )
    
    # LLM options (override defaults from .env)
    llm_provider: Optional[str] = Field(
        default=None,
        description="LLM provider to use. Options: 'openai' (native OpenAI API), 'b-api-openai' (OpenAI via B-API, default), 'b-api-academiccloud' (DeepSeek via B-API). If not set, uses METADATA_AGENT_LLM_PROVIDER from environment."
    )
    llm_model: Optional[str] = Field(
        default=None,
        description="LLM model to use. Examples: 'gpt-4.1-mini' (default for b-api-openai), 'gpt-4o-mini' (for openai), 'deepseek-r1' (for b-api-academiccloud). If not set, uses the provider's default model from environment."
    )
    
    @field_validator('text', mode='before')
    @classmethod
    def sanitize_text_input(cls, v: Any) -> str:
        """Sanitize text input before validation."""
        if isinstance(v, str):
            return sanitize_text(v)
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin. Lernen Sie die Grundlagen der künstlichen Intelligenz kennen.",
                    "existing_metadata": {"cclom:title": "Mein Workshop"},
                    "context": "default",
                    "version": "latest",
                    "schema_file": "auto",
                    "language": "de",
                    "include_core": True,
                    "max_workers": 10,
                    "llm_provider": "b-api-openai",
                    "llm_model": "gpt-4.1-mini"
                }
            ]
        }
    }


class ProcessingInfo(BaseModel):
    """Processing statistics and debug info."""
    success: bool
    fields_extracted: int = Field(description="Number of fields with values")
    fields_total: int = Field(description="Total number of fields in schema")
    processing_time_ms: int = Field(description="Processing time in milliseconds")
    llm_provider: str = Field(description="LLM provider used")
    llm_model: str = Field(description="LLM model used")
    errors: list[str] = Field(default_factory=list, description="Any errors encountered")
    warnings: list[str] = Field(default_factory=list, description="Any warnings")


class GenerateResponse(BaseModel):
    """
    Response model for metadata generation.
    
    The response contains header info, then flat metadata fields directly,
    followed by processing info at the end.
    """
    # Meta information (header)
    contextName: str = Field(description="Schema context name")
    schemaVersion: str = Field(description="Schema version used")
    metadataset: str = Field(description="Schema file that was used")
    language: str = Field(description="Language used for extraction")
    exportedAt: str = Field(description="ISO timestamp of generation")
    
    # Flat metadata - stored internally but expanded in serialization
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Generated metadata as flat key-value pairs. Fields appear at top level in response."
    )
    
    # Processing info (separate)
    processing: ProcessingInfo = Field(description="Processing statistics and debug info")
    
    def model_dump(self, **kwargs) -> dict[str, Any]:
        """Custom serialization to flatten metadata into response."""
        # Get base dict with header and processing
        result = {
            "contextName": self.contextName,
            "schemaVersion": self.schemaVersion,
            "metadataset": self.metadataset,
            "language": self.language,
            "exportedAt": self.exportedAt,
        }
        
        # Add flattened metadata fields directly
        for key, value in self.metadata.items():
            if value is not None:
                result[key] = value
        
        # Add processing info at the end
        result["processing"] = self.processing.model_dump() if self.processing else {}
        
        return result


class ValidateRequest(BaseModel):
    """
    Request model for metadata validation.
    
    All parameters default to 'auto' and are automatically detected from the metadata:
    - contextName → context
    - schemaVersion → version  
    - metadataset → schema_file
    
    You can override any parameter by providing an explicit value.
    """
    # The metadata to validate - can be full export or just fields
    metadata: dict[str, Any] = Field(..., description="Metadata JSON to validate (full export or nested)")
    
    # Parameters with auto-detection (set to 'auto' to read from metadata)
    context: str = Field(default="auto", description="Schema context ('auto' = read from contextName in metadata)")
    version: str = Field(default="auto", description="Schema version ('auto' = read from schemaVersion in metadata)")
    schema_file: str = Field(default="auto", description="Schema file ('auto' = read from metadataset in metadata)")
    
    def get_effective_params(self) -> tuple[str, str, str, dict[str, Any]]:
        """
        Extract effective context, version, schema_file and clean metadata.
        Returns: (context, version, schema_file, clean_metadata)
        """
        metadata = self.metadata.copy()
        
        # Extract values from metadata
        meta_context = metadata.pop("contextName", None)
        meta_version = metadata.pop("schemaVersion", None)
        meta_schema = metadata.pop("metadataset", None)
        
        # Remove other meta fields
        metadata.pop("language", None)
        metadata.pop("exportedAt", None)
        metadata.pop("processing", None)
        
        # Also check old nested _schema format
        if "_schema" in metadata:
            schema_info = metadata.pop("_schema")
            meta_context = meta_context or schema_info.get("context")
            meta_version = meta_version or schema_info.get("version")
            meta_schema = meta_schema or schema_info.get("file")
        
        # Use explicit value if not 'auto', otherwise use detected value, otherwise fallback
        context = meta_context if self.context == "auto" else self.context
        version = meta_version if self.version == "auto" else self.version
        schema_file = meta_schema if self.schema_file == "auto" else self.schema_file
        
        # Final fallbacks if still None
        context = context or "default"
        version = version or "1.8.0"
        schema_file = schema_file or "auto"
        
        return context, version, schema_file, metadata


class ValidationError(BaseModel):
    """Single validation error."""
    field_id: str
    message: str
    severity: str = Field(description="error, warning, or info")


class ValidateResponse(BaseModel):
    """Response model for metadata validation."""
    valid: bool
    schema_used: str
    errors: list[ValidationError] = Field(default_factory=list)
    warnings: list[ValidationError] = Field(default_factory=list)
    coverage: float = Field(description="Percentage of required fields filled")


class ExportMarkdownRequest(BaseModel):
    """
    Request model for markdown export.
    
    Like ValidateRequest, all parameters default to 'auto' for auto-detection.
    Simply pass the complete output from /generate directly as `metadata`.
    """
    metadata: dict[str, Any] = Field(..., description="Complete output from /generate endpoint - paste directly")
    context: str = Field(default="auto", description="Schema context ('auto' = read from metadata)")
    version: str = Field(default="auto", description="Schema version ('auto' = read from metadata)")
    schema_file: str = Field(default="auto", description="Schema file ('auto' = read from metadata)")
    language: str = Field(default="auto", description="Output language ('auto' = read from metadata, fallback: de)")
    include_empty: bool = Field(default=False, description="Include empty fields")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "metadata": {
                        "contextName": "default",
                        "schemaVersion": "1.8.0",
                        "metadataset": "event.json",
                        "language": "de",
                        "cclom:title": "Workshop KI in der Bildung",
                        "cclom:general_description": "Ein Workshop über KI...",
                        "schema:actor": [{"name": "Max Mustermann"}],
                        "schema:location": [{"name": "Berlin", "address": {"addressLocality": "Berlin"}}]
                    }
                }
            ]
        }
    }
    
    def get_effective_params(self) -> tuple[str, str, str, str, dict[str, Any]]:
        """
        Extract effective parameters and clean metadata.
        Returns: (context, version, schema_file, language, clean_metadata)
        """
        metadata = self.metadata.copy()
        
        # Extract values from metadata
        meta_context = metadata.pop("contextName", None)
        meta_version = metadata.pop("schemaVersion", None)
        meta_schema = metadata.pop("metadataset", None)
        meta_language = metadata.pop("language", None)
        
        # Remove other meta fields
        metadata.pop("exportedAt", None)
        metadata.pop("processing", None)
        
        # Also check old nested _schema format
        if "_schema" in metadata:
            schema_info = metadata.pop("_schema")
            meta_context = meta_context or schema_info.get("context")
            meta_version = meta_version or schema_info.get("version")
            meta_schema = meta_schema or schema_info.get("file")
        
        # Use explicit value if not 'auto', otherwise use detected value
        context = meta_context if self.context == "auto" else self.context
        version = meta_version if self.version == "auto" else self.version
        schema_file = meta_schema if self.schema_file == "auto" else self.schema_file
        language = meta_language if self.language == "auto" else self.language
        
        # Final fallbacks
        context = context or "default"
        version = version or "1.8.0"
        schema_file = schema_file or "auto"
        language = language or "de"
        
        return context, version, schema_file, language, metadata


class ExportMarkdownResponse(BaseModel):
    """Response model for markdown export."""
    markdown: str
    schema_used: str


class SchemaInfo(BaseModel):
    """Information about a schema."""
    file: str
    profile_id: str
    label: LocalizedString
    groups: list[str]
    field_count: int


class ContextInfo(BaseModel):
    """Information about a context."""
    name: str
    display_name: str
    versions: list[str]
    default_version: str


class SchemataInfoResponse(BaseModel):
    """Response with available schemata information."""
    contexts: list[ContextInfo]
    default_context: str


class UploadRequest(BaseModel):
    """
    Request model for uploading metadata to WLO repository.
    
    Accepts the JSON output from /generate endpoint.
    """
    metadata: dict[str, Any] = Field(
        ...,
        description="Metadata dict from /generate endpoint (with contextName, schemaVersion, etc.)"
    )
    repository: str = Field(
        default="staging",
        description="Target repository: 'staging' or 'production'"
    )
    check_duplicates: bool = Field(
        default=True,
        description="Check for duplicates by ccm:wwwurl before uploading"
    )
    start_workflow: bool = Field(
        default=True,
        description="Start review workflow after upload"
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [{
                "metadata": {
                    "contextName": "default",
                    "schemaVersion": "1.8.0",
                    "metadataset": "event.json",
                    "cclom:title": "Example Event",
                    "cclom:general_description": "Description...",
                    "ccm:wwwurl": "https://example.com/event"
                },
                "repository": "staging",
                "check_duplicates": True,
                "start_workflow": True
            }]
        }
    }


class UploadedNodeInfo(BaseModel):
    """Information about uploaded or existing node."""
    nodeId: str
    title: Optional[str] = None
    description: Optional[str] = None
    wwwurl: Optional[str] = None
    repositoryUrl: Optional[str] = None


class UploadResponse(BaseModel):
    """Response from repository upload."""
    success: bool
    duplicate: Optional[bool] = None
    repository: Optional[str] = None
    node: Optional[UploadedNodeInfo] = None
    error: Optional[str] = None
    step: Optional[str] = None


class DetectContentTypeRequest(BaseModel):
    """Request model for content type detection."""
    
    # Input source selection
    input_source: InputSource = Field(
        default=InputSource.TEXT,
        description="Input source: 'text' (direct input), 'url' (fetch via crawler), 'node_id' (fetch from repository), 'node_url' (repository + crawler fallback)"
    )
    
    # Text input (required for input_source='text')
    text: Optional[str] = Field(
        default=None,
        description="Input text to analyze. Required when input_source='text'."
    )
    
    # URL input (required for input_source='url' or 'node_url')
    source_url: Optional[str] = Field(
        default=None,
        description="URL to fetch text from via text extraction API. Required when input_source='url' or 'node_url'."
    )
    extraction_method: ExtractionMethod = Field(
        default=ExtractionMethod.SIMPLE,
        description="Text extraction method: 'simple' (fast, basic HTML parsing) or 'browser' (full browser rendering, slower)"
    )
    
    # NodeID input (required for input_source='node_id' or 'node_url')
    node_id: Optional[str] = Field(
        default=None,
        description="Repository NodeID to fetch metadata and text from. Required when input_source='node_id' or 'node_url'."
    )
    repository: Repository = Field(
        default=Repository.STAGING,
        description="Repository to use for NodeID lookup: 'prod' (redaktion.openeduhub.net) or 'staging' (repository.staging.openeduhub.net)"
    )
    
    # Detection options
    context: str = Field(default="default", description="Schema context to use")
    version: str = Field(default="latest", description="Schema version to use ('latest' for newest)")
    language: str = Field(default="de", description="Language for detection (de/en)")
    
    # LLM options (override defaults from .env)
    llm_provider: Optional[str] = Field(
        default=None,
        description="LLM provider to use. Options: 'openai' (native OpenAI API), 'b-api-openai' (OpenAI via B-API, default), 'b-api-academiccloud' (DeepSeek via B-API). If not set, uses METADATA_AGENT_LLM_PROVIDER from environment."
    )
    llm_model: Optional[str] = Field(
        default=None,
        description="LLM model to use. Examples: 'gpt-4.1-mini' (default for b-api-openai), 'gpt-4o-mini' (for openai), 'deepseek-r1' (for b-api-academiccloud). If not set, uses the provider's default model from environment."
    )
    
    @field_validator('text', mode='before')
    @classmethod
    def sanitize_text_input(cls, v: Any) -> str:
        """Sanitize text input before validation."""
        if isinstance(v, str):
            return sanitize_text(v)
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "input_source": "text",
                    "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
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
            ]
        }
    }


class ContentTypeInfo(BaseModel):
    """Information about a detected content type."""
    schema_file: str = Field(description="Schema file name (e.g., 'event.json')")
    profile_id: Optional[str] = Field(default=None, description="Profile ID if available")
    label: LocalizedString = Field(description="Localized label for the content type")
    confidence: Optional[str] = Field(default=None, description="Detection confidence (high/medium/low)")


class DetectContentTypeResponse(BaseModel):
    """Response model for content type detection."""
    detected: ContentTypeInfo = Field(description="Detected content type")
    available: list[ContentTypeInfo] = Field(description="All available content types for this context/version")
    context: str = Field(description="Schema context used")
    version: str = Field(description="Schema version used")
    processing_time_ms: int = Field(description="Processing time in milliseconds")


class ExtractFieldRequest(BaseModel):
    """Request model for single field extraction."""
    
    # Input source selection
    input_source: InputSource = Field(
        default=InputSource.TEXT,
        description="Input source: 'text' (direct input), 'url' (fetch via crawler), 'node_id' (fetch from repository), 'node_url' (repository + crawler fallback)"
    )
    
    # Text input (required for input_source='text')
    text: Optional[str] = Field(
        default=None,
        description="Input text to extract the field value from. Required when input_source='text'."
    )
    
    # URL input (required for input_source='url' or 'node_url')
    source_url: Optional[str] = Field(
        default=None,
        description="URL to fetch text from via text extraction API. Required when input_source='url' or 'node_url'."
    )
    extraction_method: ExtractionMethod = Field(
        default=ExtractionMethod.SIMPLE,
        description="Text extraction method: 'simple' (fast, basic HTML parsing) or 'browser' (full browser rendering, slower)"
    )
    
    # NodeID input (required for input_source='node_id' or 'node_url')
    node_id: Optional[str] = Field(
        default=None,
        description="Repository NodeID to fetch metadata and text from. Required when input_source='node_id' or 'node_url'."
    )
    repository: Repository = Field(
        default=Repository.STAGING,
        description="Repository to use for NodeID lookup: 'prod' (redaktion.openeduhub.net) or 'staging' (repository.staging.openeduhub.net)"
    )
    
    # Field-specific options
    context: str = Field(default="default", description="Schema context to use")
    version: str = Field(default="latest", description="Schema version to use ('latest' for newest)")
    schema_file: str = Field(..., description="Schema file containing the field (e.g., 'event.json', 'core.json')")
    field_id: str = Field(..., description="Field ID to extract (e.g., 'schema:startDate', 'cclom:title')")
    existing_metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Existing metadata JSON with current field values (for context in update scenarios). For node_id/node_url sources, fetched metadata is merged."
    )
    language: str = Field(default="de", description="Language for extraction (de/en)")
    normalize: bool = Field(
        default=True,
        description="Apply normalization to extracted value (dates, vocabularies, etc.)"
    )
    
    # LLM options (override defaults from .env)
    llm_provider: Optional[str] = Field(
        default=None,
        description="LLM provider to use. Options: 'openai' (native OpenAI API), 'b-api-openai' (OpenAI via B-API, default), 'b-api-academiccloud' (DeepSeek via B-API). If not set, uses METADATA_AGENT_LLM_PROVIDER from environment."
    )
    llm_model: Optional[str] = Field(
        default=None,
        description="LLM model to use. Examples: 'gpt-4.1-mini' (default for b-api-openai), 'gpt-4o-mini' (for openai), 'deepseek-r1' (for b-api-academiccloud). If not set, uses the provider's default model from environment."
    )
    
    @field_validator('text', mode='before')
    @classmethod
    def sanitize_text_input(cls, v: Any) -> str:
        """Sanitize text input before validation."""
        if isinstance(v, str):
            return sanitize_text(v)
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "context": "default",
                    "version": "latest",
                    "schema_file": "event.json",
                    "field_id": "schema:startDate",
                    "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                    "language": "de",
                    "llm_model": "gpt-4.1-mini",
                    "llm_provider": "b-api-openai",
                    "normalize": True
                },
                {
                    "context": "default",
                    "version": "latest",
                    "schema_file": "event.json",
                    "field_id": "schema:startDate",
                    "text": "Der Workshop wurde auf den 20. März 2025 verschoben.",
                    "existing_metadata": {"schema:startDate": "2025-03-15T00:00"},
                    "language": "de",
                    "llm_model": "gpt-4.1-mini",
                    "llm_provider": "b-api-openai",
                    "normalize": True
                }
            ]
        }
    }


class ExtractFieldResponse(BaseModel):
    """Response model for single field extraction."""
    field_id: str = Field(description="Field ID that was extracted")
    field_label: Optional[str] = Field(default=None, description="Human-readable field label")
    value: Any = Field(description="Extracted (and normalized) value")
    raw_value: Optional[Any] = Field(default=None, description="Value before normalization (if different)")
    previous_value: Optional[Any] = Field(default=None, description="Previous value if provided")
    changed: bool = Field(description="Whether the value changed from previous")
    normalized: bool = Field(description="Whether normalization was applied")
    context: str = Field(description="Schema context used")
    version: str = Field(description="Schema version used")
    schema_file: str = Field(description="Schema file used")
    processing: dict[str, Any] = Field(description="Processing info (provider, model, time)")
