"""Application configuration."""
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment."""
    
    # API Settings
    app_name: str = "Metadata Agent API"
    app_version: str = "1.0.0"
    debug: bool = False
    
    # LLM Provider Selection
    # Options: 'openai', 'b-api-openai', 'b-api-academiccloud'
    llm_provider: str = "b-api-openai"
    
    # OpenAI Configuration (native OpenAI API)
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_api_base: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_temperature: float = 0.3
    
    # B-API Configuration (shared key for both b-api providers)
    b_api_key: str = Field(default="", alias="B_API_KEY")
    
    # B-API OpenAI (OpenAI-compatible endpoint via B-API)
    b_api_openai_base: str = "https://b-api.staging.openeduhub.net/api/v1/llm/openai"
    b_api_openai_model: str = "gpt-4.1-mini"
    
    # B-API AcademicCloud (AcademicCloud endpoint via B-API)
    b_api_academiccloud_base: str = "https://b-api.staging.openeduhub.net/api/v1/llm/academiccloud"
    b_api_academiccloud_model: str = "deepseek-r1"
    
    # General LLM Settings
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2000
    llm_max_retries: int = 3
    llm_retry_delay: float = 1.0
    
    # Worker Settings
    default_max_workers: int = 10
    request_timeout: int = 60
    
    # Default Schema Settings
    default_context: str = "default"
    default_version: str = "1.8.0"
    
    # Normalization Settings
    normalization_enabled: bool = True
    normalization_temperature: float = 0.1
    
    # Repository Settings (for NodeID input source)
    repository_prod_url: str = "https://redaktion.openeduhub.net/edu-sharing/rest"
    repository_staging_url: str = "https://repository.staging.openeduhub.net/edu-sharing/rest"
    repository_default: str = "staging"  # 'prod' or 'staging'
    
    # Text Extraction API Settings (for URL input source)
    text_extraction_api_url: str = "https://text-extraction.staging.openeduhub.net"
    text_extraction_default_method: str = "simple"  # 'simple' or 'browser'
    
    # WLO Repository Upload Credentials (for /upload endpoint)
    wlo_guest_username: str = Field(default="", alias="WLO_GUEST_USERNAME")
    wlo_guest_password: str = Field(default="", alias="WLO_GUEST_PASSWORD")
    wlo_repository_base_url: str = Field(default="", alias="WLO_REPOSITORY_BASE_URL")
    
    # WLO Repository Inbox IDs (where new nodes are created)
    wlo_inbox_id_staging: str = "21144164-30c0-4c01-ae16-264452197063"
    wlo_inbox_id_prod: str = "21144164-30c0-4c01-ae16-264452197063"
    
    # CORS Settings
    cors_origins: str = "*"  # Comma-separated origins, or '*' for all
    
    model_config = {
        "env_prefix": "METADATA_AGENT_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,  # Allow both alias and field name
        "extra": "ignore",
    }
    
    def get_llm_config(self, provider_override: Optional[str] = None, model_override: Optional[str] = None) -> dict:
        """
        Get LLM configuration based on selected provider.
        
        Args:
            provider_override: Override the default provider from .env
            model_override: Override the default model for the provider
        """
        provider = provider_override or self.llm_provider
        
        if provider == "b-api-openai":
            config = {
                "provider": "b-api-openai",
                "api_key": self.b_api_key,
                "api_base": self.b_api_openai_base,
                "model": model_override or self.b_api_openai_model,
                "temperature": self.llm_temperature,
                "requires_custom_header": True,  # X-API-KEY instead of Bearer
            }
        elif provider == "b-api-academiccloud":
            config = {
                "provider": "b-api-academiccloud",
                "api_key": self.b_api_key,
                "api_base": self.b_api_academiccloud_base,
                "model": model_override or self.b_api_academiccloud_model,
                "temperature": self.llm_temperature,
                "requires_custom_header": True,
            }
        else:  # openai (default)
            config = {
                "provider": "openai",
                "api_key": self.openai_api_key,
                "api_base": self.openai_api_base,
                "model": model_override or self.openai_model,
                "temperature": self.openai_temperature,
                "requires_custom_header": False,
            }
        
        return config


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
