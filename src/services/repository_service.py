"""Repository service for uploading metadata to WLO edu-sharing repository."""
import base64
from typing import Any, Optional
import httpx


# Repository configurations
REPOSITORY_CONFIGS = {
    "staging": {
        "base_url": "https://repository.staging.openeduhub.net/edu-sharing",
        "inbox_id": "21144164-30c0-4c01-ae16-264452197063",  # WLO-Uploadmanager Inbox
    },
    "prod": {
        "base_url": "https://redaktion.openeduhub.net/edu-sharing",
        "inbox_id": "21144164-30c0-4c01-ae16-264452197063",  # Same inbox ID
    },
    # Alias for backwards compatibility
    "production": {
        "base_url": "https://redaktion.openeduhub.net/edu-sharing",
        "inbox_id": "21144164-30c0-4c01-ae16-264452197063",
    }
}


class RepositoryService:
    """
    Service for uploading metadata to WLO edu-sharing repository.
    
    Workflow:
    1. Check for duplicates (by ccm:wwwurl)
    2. Create node with minimal data
    3. Set full metadata
    4. Add to collections (optional)
    5. Start review workflow
    """
    
    def __init__(self, username: str, password: str):
        """
        Initialize repository service with credentials.
        
        Args:
            username: WLO guest upload username
            password: WLO guest upload password
        """
        self.username = username
        self.password = password
        self._auth_header = self._create_auth_header()
    
    def _create_auth_header(self) -> str:
        """Create Basic Auth header."""
        credentials = f"{self.username}:{self.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"
    
    async def upload_metadata(
        self,
        metadata: dict[str, Any],
        repository: str = "staging",
        check_duplicates: bool = True,
        start_workflow: bool = True,
    ) -> dict[str, Any]:
        """
        Upload metadata to WLO repository.
        
        Args:
            metadata: Metadata dict from /generate endpoint
            repository: "staging" or "production"
            check_duplicates: Check for duplicates by ccm:wwwurl
            start_workflow: Start review workflow after upload
            
        Returns:
            Upload result with nodeId, success status, etc.
        """
        config = REPOSITORY_CONFIGS.get(repository)
        if not config:
            return {
                "success": False,
                "error": f"Unknown repository: {repository}. Use 'staging' or 'production'."
            }
        
        base_url = config["base_url"]
        inbox_id = config["inbox_id"]
        
        # Extract metadata fields (remove processing info, etc.)
        clean_metadata = self._extract_metadata_fields(metadata)
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # 1. Check for duplicates
                if check_duplicates:
                    url = clean_metadata.get("ccm:wwwurl")
                    if url:
                        duplicate = await self._check_duplicate(client, base_url, url)
                        if duplicate.get("exists"):
                            node_id = duplicate.get("nodeId")
                            return {
                                "success": False,
                                "duplicate": True,
                                "repository": repository,
                                "node": {
                                    "nodeId": node_id,
                                    "title": duplicate.get("title"),
                                    "description": duplicate.get("description"),
                                    "wwwurl": url,
                                    "repositoryUrl": f"{base_url}/components/render/{node_id}"
                                },
                                "error": f"URL existiert bereits: \"{duplicate.get('title')}\""
                            }
                
                # 2. Create node with minimal data
                node_result = await self._create_node(client, base_url, inbox_id, clean_metadata)
                if not node_result.get("success"):
                    return node_result
                
                node_id = node_result["nodeId"]
                print(f"✅ Created node: {node_id}")
                
                # 3. Set full metadata
                metadata_result = await self._set_metadata(client, base_url, node_id, clean_metadata)
                if not metadata_result.get("success"):
                    return {
                        "success": False,
                        "nodeId": node_id,
                        "error": metadata_result.get("error"),
                        "step": "setMetadata"
                    }
                
                # 4. Set collections if present
                collection_ids = self._extract_collection_ids(clean_metadata)
                if collection_ids:
                    await self._set_collections(client, base_url, node_id, collection_ids)
                
                # 5. Start workflow
                if start_workflow:
                    await self._start_workflow(client, base_url, node_id)
                
                # Extract key metadata for response
                title = clean_metadata.get("cclom:title")
                if isinstance(title, list):
                    title = title[0] if title else None
                description = clean_metadata.get("cclom:general_description")
                if isinstance(description, list):
                    description = description[0] if description else None
                wwwurl = clean_metadata.get("ccm:wwwurl")
                if isinstance(wwwurl, list):
                    wwwurl = wwwurl[0] if wwwurl else None
                
                return {
                    "success": True,
                    "repository": repository,
                    "node": {
                        "nodeId": node_id,
                        "title": title,
                        "description": description[:200] + "..." if description and len(description) > 200 else description,
                        "wwwurl": wwwurl,
                        "repositoryUrl": f"{base_url}/components/render/{node_id}"
                    }
                }
                
        except Exception as e:
            print(f"❌ Repository upload failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _extract_metadata_fields(self, metadata: dict) -> dict:
        """Extract only metadata fields, removing processing info."""
        excluded_keys = {
            "contextName", "schemaVersion", "metadataset", 
            "language", "exportedAt", "processing"
        }
        return {k: v for k, v in metadata.items() if k not in excluded_keys}
    
    def _extract_collection_ids(self, metadata: dict) -> list[str]:
        """Extract collection IDs from metadata."""
        ids = []
        
        # Primary collection
        primary = metadata.get("virtual:collection_id_primary")
        if primary:
            ids.append(self._extract_id_from_url(primary))
        
        # Additional collections
        additional = metadata.get("ccm:collection_id", [])
        if isinstance(additional, list):
            for coll in additional:
                ids.append(self._extract_id_from_url(coll))
        
        return [id for id in ids if id]
    
    def _extract_id_from_url(self, value: Any) -> str:
        """Extract ID from URL or return as-is."""
        if isinstance(value, str) and "/" in value:
            return value.split("/")[-1]
        return str(value) if value else ""
    
    async def _check_duplicate(
        self, 
        client: httpx.AsyncClient, 
        base_url: str, 
        url: str
    ) -> dict:
        """Check if URL already exists in repository."""
        try:
            search_url = f"{base_url}/rest/search/v1/queries/-home-/mds_oeh/ngsearch"
            
            response = await client.post(
                search_url,
                headers={
                    "Authorization": self._auth_header,
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },
                json={
                    "criteria": [{
                        "property": "ccm:wwwurl",
                        "values": [url]
                    }],
                    "facettes": []
                }
            )
            
            if response.status_code != 200:
                print(f"⚠️ Duplicate check failed: {response.status_code}")
                return {"exists": False, "warning": "Duplicate check failed"}
            
            data = response.json()
            
            if data.get("nodes") and len(data["nodes"]) > 0:
                node = data["nodes"][0]
                props = node.get("properties", {})
                return {
                    "exists": True,
                    "nodeId": node["ref"]["id"],
                    "title": node.get("title") or props.get("cclom:title", [""])[0],
                    "description": props.get("cclom:general_description", [""])[0] if props.get("cclom:general_description") else None
                }
            
            return {"exists": False}
            
        except Exception as e:
            print(f"⚠️ Duplicate check error: {e}")
            return {"exists": False, "warning": str(e)}
    
    async def _create_node(
        self, 
        client: httpx.AsyncClient, 
        base_url: str,
        inbox_id: str,
        metadata: dict
    ) -> dict:
        """Create node with minimal essential fields."""
        create_url = f"{base_url}/rest/node/v1/nodes/-home-/{inbox_id}/children?type=ccm:io&renameIfExists=true&versionComment=MAIN_FILE_UPLOAD"
        
        # Only 5 essential fields for node creation
        essential_fields = [
            "cclom:title",
            "cclom:general_description", 
            "cclom:general_keyword",
            "ccm:wwwurl",
            "cclom:general_language"
        ]
        
        clean_metadata = {}
        for field in essential_fields:
            value = metadata.get(field)
            if value is not None and value != "" and value != []:
                # Normalize to array
                if isinstance(value, list):
                    clean_metadata[field] = value
                else:
                    clean_metadata[field] = [value]
        
        response = await client.post(
            create_url,
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json=clean_metadata
        )
        
        if response.status_code not in (200, 201):
            error_text = response.text
            return {
                "success": False,
                "error": f"Create node failed: {response.status_code} - {error_text}"
            }
        
        data = response.json()
        return {
            "success": True,
            "nodeId": data["node"]["ref"]["id"]
        }
    
    async def _set_metadata(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        metadata: dict
    ) -> dict:
        """Set full metadata on node."""
        metadata_url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?versionComment=METADATA_UPDATE"
        
        # Supported fields whitelist
        supported_fields = [
            "cclom:title",
            "cclom:general_description",
            "cclom:general_keyword",
            "ccm:wwwurl",
            "cclom:general_language",
            "ccm:taxonid",
            "ccm:educationalcontext",
            "ccm:educationalintendedenduserrole",
            "ccm:commonlicense_key",
            "ccm:commonlicense_cc_version",
            "ccm:oeh_publisher_combined",
            "cm:author",
            "oeh:eventType",
        ]
        
        # Filter and normalize
        normalized = {}
        for key, value in metadata.items():
            if key.startswith("virtual:") or key.startswith("schema:"):
                continue
            if key not in supported_fields:
                continue
            if value is None or value == "" or value == []:
                continue
            
            # Normalize to arrays and flatten complex objects
            if isinstance(value, list):
                flattened = []
                for item in value:
                    if item is None or item == "":
                        continue
                    flattened_item = self._flatten_value(item)
                    if flattened_item is not None:
                        flattened.append(flattened_item)
                normalized[key] = flattened
            elif isinstance(value, dict):
                flattened = self._flatten_value(value)
                normalized[key] = [flattened] if flattened else []
            else:
                normalized[key] = [value]
        
        # Handle license transformation
        self._transform_license(normalized, metadata)
        
        response = await client.post(
            metadata_url,
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json=normalized
        )
        
        if response.status_code not in (200, 201):
            return {
                "success": False,
                "error": f"Set metadata failed: {response.status_code}"
            }
        
        return {"success": True}
    
    def _flatten_value(self, item: Any) -> Any:
        """Flatten a complex object to a simple value for repository API."""
        if item is None:
            return None
        
        # Already a simple type
        if isinstance(item, (str, int, float, bool)):
            return item
        
        # Dictionary - extract the most relevant value
        if isinstance(item, dict):
            # Priority order for value extraction
            if "uri" in item:
                return item["uri"]
            if "name" in item:
                return item["name"]
            if "label" in item:
                return item["label"]
            if "@value" in item:
                return item["@value"]
            if "value" in item:
                return item["value"]
            # For complex objects like address, serialize to JSON
            import json
            return json.dumps(item, ensure_ascii=False)
        
        return str(item)
    
    def _transform_license(self, normalized: dict, original: dict):
        """Transform license URLs to key + version format."""
        license_url = original.get("ccm:custom_license")
        
        if license_url:
            if isinstance(license_url, list):
                license_url = license_url[0] if license_url else None
            if isinstance(license_url, dict):
                license_url = license_url.get("uri") or license_url.get("label")
            
            if license_url and isinstance(license_url, str):
                license_key = license_url.split("/")[-1]
                
                if license_key.endswith("_40"):
                    normalized["ccm:commonlicense_key"] = [license_key[:-3]]
                    normalized["ccm:commonlicense_cc_version"] = ["4.0"]
                elif license_key == "OTHER":
                    normalized["ccm:commonlicense_key"] = ["CUSTOM"]
                else:
                    normalized["ccm:commonlicense_key"] = [license_key]
        
        # Default version if license set but no version
        if "ccm:commonlicense_key" in normalized and "ccm:commonlicense_cc_version" not in normalized:
            normalized["ccm:commonlicense_cc_version"] = ["4.0"]
    
    async def _set_collections(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        collection_ids: list[str]
    ) -> dict:
        """Add node to collections."""
        results = []
        
        for collection_id in collection_ids:
            try:
                url = f"{base_url}/rest/collection/v1/collections/-home-/{collection_id}/references/{node_id}"
                response = await client.put(
                    url,
                    headers={
                        "Authorization": self._auth_header,
                        "Accept": "application/json"
                    }
                )
                results.append({
                    "collectionId": collection_id,
                    "success": response.status_code in (200, 201)
                })
            except Exception as e:
                results.append({
                    "collectionId": collection_id,
                    "success": False,
                    "error": str(e)
                })
        
        return {"results": results}
    
    async def _start_workflow(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str
    ) -> dict:
        """Start review workflow."""
        workflow_url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/workflow"
        
        response = await client.put(
            workflow_url,
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json={
                "receiver": [{"authorityName": "GROUP_ORG_WLO-Uploadmanager"}],
                "comment": "Upload via Metadata Agent API",
                "status": "200_tocheck",
                "logLevel": "info"
            }
        )
        
        if response.status_code not in (200, 201):
            print(f"⚠️ Start workflow failed: {response.status_code}")
            return {"success": False}
        
        return {"success": True}


# Singleton instance
_repository_service: Optional[RepositoryService] = None


def get_repository_service() -> Optional[RepositoryService]:
    """Get repository service singleton (requires credentials in environment)."""
    global _repository_service
    
    if _repository_service is None:
        from ..config import get_settings
        settings = get_settings()
        
        username = settings.wlo_guest_username
        password = settings.wlo_guest_password
        
        if username and password:
            _repository_service = RepositoryService(username, password)
        else:
            return None
    
    return _repository_service
