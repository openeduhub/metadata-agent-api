"""Service for fetching input data from various sources (URL, NodeID, etc.)."""
import httpx
import logging
from typing import Any, Optional
from dataclasses import dataclass

from ..config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class InputData:
    """Result from input source fetch."""
    text: str
    existing_metadata: Optional[dict[str, Any]] = None
    source_url: Optional[str] = None
    node_id: Optional[str] = None
    repository: Optional[str] = None


class InputSourceService:
    """Service for fetching input data from various sources."""
    
    def __init__(self):
        self.settings = get_settings()
        self.timeout = httpx.Timeout(30.0, connect=10.0)
        self.http_client = httpx.AsyncClient(timeout=self.timeout)
    
    async def close(self):
        """Close the shared HTTP client."""
        if self.http_client:
            await self.http_client.aclose()
    
    def _get_repository_base_url(self, repository: str) -> str:
        """Get the base URL for the specified repository."""
        if repository == "prod":
            return self.settings.repository_prod_url
        return self.settings.repository_staging_url
    
    async def fetch_from_url(
        self,
        url: str,
        method: str = "browser",
        lang: str = "auto",
        output_format: str = "markdown"
    ) -> str:
        """
        Fetch text content from URL via text extraction API.
        
        Args:
            url: URL to fetch content from
            method: Extraction method ('simple' or 'browser')
            lang: Language hint ('auto', 'de', 'en')
            
        Returns:
            Extracted text content
        """
        api_url = f"{self.settings.text_extraction_api_url}/from-url"
        
        payload = {
            "url": url,
            "method": method,
            "browser_location": None,
            "lang": lang,
            "output_format": output_format,
            "preference": "none"
        }
        
        logger.info(f"Fetching text from URL: {url} (method: {method})")
        
        response = await self.http_client.post(
            api_url,
            json=payload,
            headers={"Content-Type": "application/json", "accept": "application/json"}
        )
        response.raise_for_status()
        
        data = response.json()
        text = data.get("text", "")
        
        if not text:
            raise ValueError(f"No text content extracted from URL: {url}")
        
        logger.info(f"Extracted {len(text)} characters from URL")
        return text
    
    async def fetch_node_metadata(
        self,
        node_id: str,
        repository: str = "staging"
    ) -> dict[str, Any]:
        """
        Fetch metadata for a node from the repository.
        
        Args:
            node_id: The NodeID to fetch
            repository: 'prod' or 'staging'
            
        Returns:
            Node metadata including properties
        """
        base_url = self._get_repository_base_url(repository)
        api_url = f"{base_url}/node/v1/nodes/-home-/{node_id}/metadata?propertyFilter=-all-"
        
        logger.info(f"Fetching node metadata: {node_id} from {repository}")
        
        response = await self.http_client.get(
            api_url,
            headers={"accept": "application/json"}
        )
        response.raise_for_status()
        
        data = response.json()
        return data.get("node", {})
    
    async def fetch_node_text_content(
        self,
        node_id: str,
        repository: str = "staging"
    ) -> Optional[str]:
        """
        Fetch text content for a node from the repository.
        
        Args:
            node_id: The NodeID to fetch
            repository: 'prod' or 'staging'
            
        Returns:
            Text content if available, None otherwise
        """
        base_url = self._get_repository_base_url(repository)
        api_url = f"{base_url}/node/v1/nodes/-home-/{node_id}/textContent"
        
        logger.info(f"Fetching node text content: {node_id} from {repository}")
        
        response = await self.http_client.get(
            api_url,
            headers={"accept": "application/json"}
        )
        response.raise_for_status()
        
        data = response.json()
        
        # Try text, then html, then raw
        text = data.get("text") or data.get("html") or data.get("raw")
        
        if text:
            logger.info(f"Fetched {len(text)} characters of text content from node")
        else:
            logger.info("No text content available for node")
        
        return text
    
    def _convert_node_properties_to_metadata(self, properties: dict[str, Any]) -> dict[str, Any]:
        """
        Convert repository node properties to flat metadata format.
        
        The repository returns properties as arrays (e.g., {"cclom:title": ["My Title"]}).
        We convert single-item arrays to scalar values.
        """
        metadata = {}
        
        # cm: properties to keep (valid metadata fields)
        CM_KEEP = {"cm:author"}
        
        for key, value in properties.items():
            # Skip internal/system properties
            if key.startswith("sys:") or key.startswith("virtual:"):
                continue
            if key.startswith("cm:") and key not in CM_KEEP:
                continue
            
            # Skip DISPLAYNAME variants (we want the URI values)
            if key.endswith("_DISPLAYNAME"):
                continue
            
            if isinstance(value, list):
                if len(value) == 1:
                    metadata[key] = value[0]
                elif len(value) > 1:
                    metadata[key] = value
                # Skip empty lists
            else:
                metadata[key] = value
        
        return metadata
    
    async def fetch_from_node_id(
        self,
        node_id: str,
        repository: str = "staging"
    ) -> InputData:
        """
        Fetch input data from repository by NodeID.
        
        Args:
            node_id: The NodeID to fetch
            repository: 'prod' or 'staging'
            
        Returns:
            InputData with text and existing metadata
        """
        # Fetch metadata
        node_data = await self.fetch_node_metadata(node_id, repository)
        properties = node_data.get("properties", {})
        
        # Convert properties to flat metadata
        existing_metadata = self._convert_node_properties_to_metadata(properties)
        
        # Get the source URL from properties
        source_url = None
        wwwurl = properties.get("ccm:wwwurl", [])
        if wwwurl:
            source_url = wwwurl[0] if isinstance(wwwurl, list) else wwwurl
        
        # Fetch text content
        text = await self.fetch_node_text_content(node_id, repository)
        
        if not text:
            # Try to build text from title and description
            title = existing_metadata.get("cclom:title", "")
            description = existing_metadata.get("cclom:general_description", "")
            keywords = existing_metadata.get("cclom:general_keyword", [])
            
            if isinstance(keywords, list):
                keywords_text = ", ".join(keywords)
            else:
                keywords_text = keywords or ""
            
            text_parts = []
            if title:
                text_parts.append(title)
            if description:
                text_parts.append(description)
            if keywords_text:
                text_parts.append(f"Keywords: {keywords_text}")
            
            text = "\n\n".join(text_parts)
            
            if not text:
                raise ValueError(f"No text content available for node {node_id}")
            
            logger.info(f"Built text from metadata: {len(text)} characters")
        
        return InputData(
            text=text,
            existing_metadata=existing_metadata,
            source_url=source_url,
            node_id=node_id,
            repository=repository
        )
    
    async def fetch_from_node_url(
        self,
        node_id: str,
        repository: str = "staging",
        source_url: Optional[str] = None,
        extraction_method: str = "browser",
        lang: str = "auto",
        output_format: str = "markdown"
    ) -> InputData:
        """
        Fetch input data using NodeID for metadata and URL for text (fallback only).
        
        Logic:
        1. Fetch metadata from repository
        2. Get URL from ccm:wwwurl if not provided
        3. Check if stored fulltext exists in repository
        4. If fulltext exists: use metadata + fulltext (no crawler)
        5. If no fulltext: use crawler to fetch text from URL
        
        Args:
            node_id: The NodeID to fetch metadata from
            repository: 'prod' or 'staging'
            source_url: Optional URL - if not provided, uses ccm:wwwurl from metadata
            extraction_method: 'simple' or 'browser' for URL extraction
            
        Returns:
            InputData with text and existing metadata
        """
        # Step 1: Fetch metadata
        node_data = await self.fetch_node_metadata(node_id, repository)
        properties = node_data.get("properties", {})
        existing_metadata = self._convert_node_properties_to_metadata(properties)
        
        # Step 2: Get URL from metadata if not provided
        if not source_url:
            wwwurl = properties.get("ccm:wwwurl", [])
            if wwwurl:
                source_url = wwwurl[0] if isinstance(wwwurl, list) else wwwurl
                logger.info(f"Using ccm:wwwurl from metadata: {source_url}")
        
        # Step 3: Check for stored fulltext in repository
        stored_text = await self.fetch_node_text_content(node_id, repository)
        
        if stored_text and stored_text.strip():
            # Step 4: Fulltext exists - use it (no crawler needed)
            logger.info(f"Using stored fulltext from repository ({len(stored_text)} chars)")
            text = stored_text
        else:
            # Step 5: No fulltext - use crawler as fallback
            if not source_url:
                raise ValueError(f"No stored fulltext and no URL available for node {node_id}")
            logger.info(f"No stored fulltext, fetching from URL: {source_url}")
            try:
                text = await self.fetch_from_url(source_url, extraction_method, lang=lang, output_format=output_format)
            except Exception as e:
                # Final fallback: build text from metadata
                logger.warning(f"URL extraction failed: {e}")
                title = existing_metadata.get("cclom:title", "")
                description = existing_metadata.get("cclom:general_description", "")
                keywords = existing_metadata.get("cclom:general_keyword", [])
                
                if isinstance(keywords, list):
                    keywords_text = ", ".join(keywords)
                else:
                    keywords_text = keywords or ""
                
                text_parts = []
                if title:
                    text_parts.append(title)
                if description:
                    text_parts.append(description)
                if keywords_text:
                    text_parts.append(f"Keywords: {keywords_text}")
                
                text = "\n\n".join(text_parts)
                
                if not text:
                    raise ValueError(f"No text content available for node {node_id} and URL extraction failed")
                
                logger.info(f"Using fallback text from metadata: {len(text)} chars")
        
        return InputData(
            text=text,
            existing_metadata=existing_metadata,
            source_url=source_url,
            node_id=node_id,
            repository=repository
        )


_input_source_service: Optional[InputSourceService] = None


def get_input_source_service() -> InputSourceService:
    """Get singleton instance of input source service."""
    global _input_source_service
    if _input_source_service is None:
        _input_source_service = InputSourceService()
    return _input_source_service
