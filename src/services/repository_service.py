"""Repository service for uploading metadata to WLO edu-sharing repository."""
import base64
import json
import logging
from typing import Any, Optional
import httpx

logger = logging.getLogger(__name__)

from ..utils.schema_loader import get_repo_fields


def _get_repository_configs() -> dict:
    """Build repository configs using settings for inbox IDs."""
    from ..config import get_settings
    settings = get_settings()
    return {
        "staging": {
            "base_url": "https://repository.staging.openeduhub.net/edu-sharing",
            "inbox_id": settings.wlo_inbox_id_staging,
        },
        "prod": {
            "base_url": "https://redaktion.openeduhub.net/edu-sharing",
            "inbox_id": settings.wlo_inbox_id_prod,
        },
        # Alias for backwards compatibility
        "production": {
            "base_url": "https://redaktion.openeduhub.net/edu-sharing",
            "inbox_id": settings.wlo_inbox_id_prod,
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
        context: str = "default",
        version: str = "latest",
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
        config = _get_repository_configs().get(repository)
        if not config:
            return {
                "success": False,
                "error": f"Unknown repository: {repository}. Use 'staging' or 'production'."
            }
        
        base_url = config["base_url"]
        inbox_id = config["inbox_id"]
        
        # Extract metadata fields (remove processing info, etc.)
        clean_metadata = self._extract_metadata_fields(metadata)
        
        # Determine which schema was used from metadataset field
        schema_file = metadata.get("metadataset") or None
        
        # Load repo-eligible fields from schemas
        repo_field_ids = get_repo_fields(context, version, schema_file)
        print(f"📋 Repo fields from schema: {len(repo_field_ids)} fields")
        if schema_file:
            print(f"   Schemas: core.json + {schema_file}")
        
        try:
            # Longer timeout for sequential edu-sharing calls (especially on Vercel)
            timeout = httpx.Timeout(45.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
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
                
                # 2b. Add required aspects for special fields
                await self._ensure_aspects(client, base_url, node_id, clean_metadata)
                
                # 3. Set full metadata (only repo_field=true fields from schemas)
                metadata_result = await self._set_metadata(
                    client, base_url, node_id, clean_metadata, repo_field_ids
                )
                
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
                
                result = {
                    "success": True,
                    "repository": repository,
                    "node": {
                        "nodeId": node_id,
                        "title": title,
                        "description": description[:200] + "..." if description and len(description) > 200 else description,
                        "wwwurl": wwwurl,
                        "repositoryUrl": f"{base_url}/components/render/{node_id}"
                    },
                    "fields_written": metadata_result.get("fields_written", 0),
                    "fields_skipped": metadata_result.get("fields_skipped", 0),
                }
                
                # Add field errors if any
                field_errors = metadata_result.get("field_errors", [])
                if field_errors:
                    result["field_errors"] = field_errors
                    result["error"] = f"{len(field_errors)} Feld(er) konnten nicht geschrieben werden"
                
                # If ALL fields failed, mark as unsuccessful
                if metadata_result.get("fields_written", 0) == 0 and not metadata_result.get("success", True):
                    result["success"] = False
                    result["step"] = "setMetadata"
                
                return result
                
        except httpx.TimeoutException as e:
            print(f"❌ Repository upload timed out: {e}")
            return {
                "success": False,
                "error": f"Timeout bei der Verbindung zum Repository: {e}"
            }
        except httpx.ConnectError as e:
            print(f"❌ Repository connection failed: {e}")
            return {
                "success": False,
                "error": f"Verbindung zum Repository fehlgeschlagen: {e}"
            }
        except Exception as e:
            print(f"❌ Repository upload failed: {type(e).__name__}: {e}")
            return {
                "success": False,
                "error": f"{type(e).__name__}: {e}"
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
        
        clean_metadata = {"ccm:linktype": ["USER_GENERATED"]}
        for field in essential_fields:
            value = metadata.get(field)
            if value is not None and value != "" and value != []:
                # Normalize to array
                if isinstance(value, list):
                    clean_metadata[field] = value
                else:
                    clean_metadata[field] = [value]
        
        print(f"📡 Creating node at: {create_url[:80]}...")
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
            error_text = response.text[:500]
            print(f"❌ Create node failed: {response.status_code} - {error_text}")
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
        metadata: dict,
        repo_field_ids: set[str] | None = None
    ) -> dict:
        """
        Set full metadata on node using dynamically loaded repo fields from schemas.
        
        Strategy:
        1. Filter metadata to only include fields with repo_field=true in schemas
        2. Try bulk update with all fields
        3. If bulk fails, retry field-by-field to identify problematic fields
        4. Report per-field errors
        """
        metadata_url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?versionComment=METADATA_UPDATE&obeyMds=false"
        
        # Normalize metadata values for repository API
        normalized = self._normalize_for_repo(metadata, repo_field_ids)
        
        # Handle license transformation
        self._transform_license(normalized, metadata)
        
        # Extract geo coordinates from schema:location → cm:latitude / cm:longitude
        self._extract_geo_coordinates(normalized, metadata)
        
        # Transform cm:author → ccm:lifecyclecontributer_author (VCARD format)
        self._transform_author_to_vcard(normalized)
        
        if not normalized:
            return {
                "success": True,
                "fields_written": 0,
                "fields_skipped": 0,
                "field_errors": []
            }
        
        fields_to_write = set(normalized.keys())
        print(f"📝 Writing {len(fields_to_write)} fields to node {node_id}")
        
        # --- Strategy 1: Bulk update (all fields at once) ---
        response = await client.post(
            metadata_url,
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json=normalized
        )
        
        if response.status_code in (200, 201):
            print(f"✅ Bulk metadata update succeeded: {len(normalized)} fields")
            return {
                "success": True,
                "fields_written": len(normalized),
                "fields_skipped": 0,
                "field_errors": []
            }
        
        # --- Strategy 2: Bulk failed → field-by-field fallback ---
        bulk_error = response.text[:500]
        print(f"⚠️ Bulk metadata update failed ({response.status_code}): {bulk_error}")
        print(f"🔄 Retrying field-by-field to identify problematic fields...")
        
        fields_written = 0
        fields_skipped = 0
        field_errors = []
        
        for field_id, field_value in normalized.items():
            try:
                single_response = await client.post(
                    metadata_url,
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                        "Accept": "application/json"
                    },
                    json={field_id: field_value}
                )
                
                if single_response.status_code in (200, 201):
                    fields_written += 1
                else:
                    fields_skipped += 1
                    error_text = single_response.text[:200]
                    field_errors.append({
                        "field_id": field_id,
                        "error": f"HTTP {single_response.status_code}: {error_text}",
                        "status_code": single_response.status_code
                    })
                    print(f"   ❌ {field_id}: {single_response.status_code}")
            except Exception as e:
                fields_skipped += 1
                field_errors.append({
                    "field_id": field_id,
                    "error": str(e),
                    "status_code": None
                })
                print(f"   ❌ {field_id}: {e}")
        
        print(f"📊 Field-by-field result: {fields_written} written, {fields_skipped} failed")
        
        return {
            "success": fields_written > 0,
            "fields_written": fields_written,
            "fields_skipped": fields_skipped,
            "field_errors": field_errors
        }
    
    def _normalize_for_repo(
        self, metadata: dict, repo_field_ids: set[str] | None = None
    ) -> dict:
        """
        Filter and normalize metadata for repository API.
        
        Only includes fields that:
        - Have repo_field=true in schema (if repo_field_ids provided)
        - Don't start with 'virtual:' or 'schema:' (internal prefixes)
        - Have non-empty values
        """
        normalized = {}
        
        # If no repo fields could be loaded from schemas, refuse to write blindly
        if not repo_field_ids:
            print("⚠️ No repo_field_ids loaded from schemas — skipping metadata write")
            return normalized
        
        for key, value in metadata.items():
            # Skip internal/virtual fields
            if key.startswith("virtual:") or key.startswith("schema:"):
                continue
            
            # Only include fields with repo_field=true in schema
            if key not in repo_field_ids:
                continue
            
            # Skip empty values
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
                if flattened:
                    normalized[key] = flattened
            elif isinstance(value, dict):
                flattened = self._flatten_value(value)
                if flattened is not None:
                    normalized[key] = [flattened]
            else:
                normalized[key] = [value]
        
        return normalized
    
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
            return json.dumps(item, ensure_ascii=False)
        
        return str(item)
    
    # Valid edu-sharing license keys (used for validation)
    VALID_LICENSE_KEYS = {
        "NONE", "CC_0", "CC0", "CC_BY", "CC BY", "CC_BY_SA", "CC BY-SA",
        "CC_BY_ND", "CC BY-ND", "CC_BY_NC", "CC BY-NC",
        "CC_BY_NC_SA", "CC BY-NC-SA", "CC_BY_NC_ND", "CC BY-NC-ND",
        "PDM", "CUSTOM", "SCHULFUNK", "UNTERRICHTS_UND_LEHRMEDIEN",
        "COPYRIGHT_FREE", "COPYRIGHT_LICENSE",
    }

    def _transform_license(self, normalized: dict, original: dict):
        """Transform license URLs to key + version format.
        
        Only transforms ccm:custom_license if it looks like a vocabulary URI
        (contains '/'). Plain text values are kept as custom license text.
        Validates ccm:commonlicense_key against known edu-sharing keys.
        """
        license_val = original.get("ccm:custom_license")
        
        if license_val:
            if isinstance(license_val, list):
                license_val = license_val[0] if license_val else None
            if isinstance(license_val, dict):
                license_val = license_val.get("uri") or license_val.get("label")
            
            if license_val and isinstance(license_val, str):
                # Only transform if it looks like a URI (contains '/')
                if "/" in license_val:
                    license_key = license_val.split("/")[-1]
                    
                    if license_key.endswith("_40"):
                        normalized["ccm:commonlicense_key"] = [license_key[:-3]]
                        normalized["ccm:commonlicense_cc_version"] = ["4.0"]
                    elif license_key == "OTHER":
                        normalized["ccm:commonlicense_key"] = ["CUSTOM"]
                    elif license_key in self.VALID_LICENSE_KEYS:
                        normalized["ccm:commonlicense_key"] = [license_key]
                    
                    # Remove the URI from ccm:custom_license (it was transformed)
                    normalized.pop("ccm:custom_license", None)
                else:
                    # Plain text → keep as custom license, set key to CUSTOM
                    if "ccm:commonlicense_key" not in normalized:
                        normalized["ccm:commonlicense_key"] = ["CUSTOM"]
        
        # Validate ccm:commonlicense_key against known keys
        if "ccm:commonlicense_key" in normalized:
            key_list = normalized["ccm:commonlicense_key"]
            if isinstance(key_list, list) and key_list:
                key = str(key_list[0]).strip()
                if key not in self.VALID_LICENSE_KEYS:
                    logger.warning(f"Invalid license key removed: {key[:80]}")
                    del normalized["ccm:commonlicense_key"]
                    normalized.pop("ccm:commonlicense_cc_version", None)
        
        # Default CC version only for CC-type licenses
        if "ccm:commonlicense_key" in normalized and "ccm:commonlicense_cc_version" not in normalized:
            key = normalized["ccm:commonlicense_key"][0] if normalized["ccm:commonlicense_key"] else ""
            if str(key).startswith("CC"):
                normalized["ccm:commonlicense_cc_version"] = ["4.0"]
    
    def _transform_author_to_vcard(self, normalized: dict):
        """
        Transform cm:author plain names to VCARD format for ccm:lifecyclecontributer_author.
        
        The WLO repo stores authors as VCARD strings in ccm:lifecyclecontributer_author,
        not as plain strings in cm:author.
        
        Example: "Philipp Lang" → "BEGIN:VCARD\nFN:Philipp Lang\nN:Lang;Philipp\nVERSION:3.0\nEND:VCARD"
        """
        authors = normalized.pop("cm:author", None)
        if not authors:
            return
        
        vcards = []
        for author in authors:
            author = str(author).strip()
            if not author:
                continue
            
            parts = author.rsplit(" ", 1)
            if len(parts) == 2:
                first, last = parts[0], parts[1]
                vcard = f"BEGIN:VCARD\nFN:{author}\nN:{last};{first}\nVERSION:3.0\nEND:VCARD"
            else:
                # Single name or organization
                vcard = f"BEGIN:VCARD\nFN:{author}\nN:{author}\nVERSION:3.0\nEND:VCARD"
            
            vcards.append(vcard)
        
        if vcards:
            normalized["ccm:lifecyclecontributer_author"] = vcards
            print(f"👤 Author VCARD: {len(vcards)} entries → ccm:lifecyclecontributer_author")
    
    def _extract_geo_coordinates(self, normalized: dict, original: dict):
        """
        Extract geo coordinates and map to cm:latitude / cm:longitude.
        
        Sources (in priority order):
        1. schema:location[].geo.latitude/longitude  (event, course, education_*, organization)
        2. schema:geo.latitude/longitude              (organization top-level fallback)
        """
        # Source 1: schema:location[].geo
        locations = original.get("schema:location")
        if locations:
            if not isinstance(locations, list):
                locations = [locations]
            
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                geo = loc.get("geo")
                if not isinstance(geo, dict):
                    continue
                
                lat = geo.get("latitude")
                lon = geo.get("longitude")
                
                if lat is not None and lon is not None:
                    normalized["cm:latitude"] = [str(lat)]
                    normalized["cm:longitude"] = [str(lon)]
                    print(f"📍 Geo (location): {lat}, {lon} → cm:latitude, cm:longitude")
                    return
        
        # Source 2: schema:geo (organization.json top-level)
        geo = original.get("schema:geo")
        if isinstance(geo, dict):
            lat = geo.get("latitude")
            lon = geo.get("longitude")
            if lat is not None and lon is not None:
                normalized["cm:latitude"] = [str(lat)]
                normalized["cm:longitude"] = [str(lon)]
                print(f"📍 Geo (top-level): {lat}, {lon} → cm:latitude, cm:longitude")
                return
    
    async def _ensure_aspects(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        metadata: dict
    ):
        """
        Add required aspects to node based on metadata content.
        
        Aspects are Alfresco extension packages that enable specific property groups.
        Without the correct aspect, the repo silently drops writes to those properties.
        """
        extra_aspects = []
        
        # cm:geographic → needed for cm:latitude, cm:longitude
        has_geo = False
        locations = metadata.get("schema:location")
        if locations:
            if isinstance(locations, list):
                for loc in locations:
                    if isinstance(loc, dict) and isinstance(loc.get("geo"), dict):
                        has_geo = True
                        break
            elif isinstance(locations, dict) and isinstance(locations.get("geo"), dict):
                has_geo = True
        # Also check schema:geo (organization.json top-level)
        if not has_geo and isinstance(metadata.get("schema:geo"), dict):
            geo = metadata["schema:geo"]
            if geo.get("latitude") is not None and geo.get("longitude") is not None:
                has_geo = True
        if has_geo:
            extra_aspects.append("cm:geographic")
        
        # cm:author → needed for ccm:lifecyclecontributer_author
        if metadata.get("cm:author"):
            extra_aspects.append("cm:author")
        
        if not extra_aspects:
            return
        
        # Read current aspects, merge, PUT back
        try:
            aspects_url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/aspects"
            r = await client.get(
                f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?propertyFilter=-all-",
                headers={"Authorization": self._auth_header, "Accept": "application/json"}
            )
            current_aspects = []
            if r.status_code == 200:
                current_aspects = r.json().get("node", {}).get("aspects", [])
            
            new_aspects = [a for a in extra_aspects if a not in current_aspects]
            if new_aspects:
                full_list = current_aspects + new_aspects
                r = await client.put(
                    aspects_url,
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                        "Accept": "application/json"
                    },
                    json=full_list
                )
                if r.status_code == 200:
                    print(f"🔧 Aspects added: {new_aspects}")
                else:
                    print(f"⚠️ Failed to add aspects {new_aspects}: {r.status_code}")
        except Exception as e:
            print(f"⚠️ Aspect update error: {e}")
    
    async def verify_node(
        self,
        node_id: str,
        repository: str = "staging",
        expected_metadata: dict[str, Any] | None = None,
        context: str = "default",
        version: str = "latest",
    ) -> dict[str, Any]:
        """
        Read metadata from repository and optionally compare against expected values.
        
        Args:
            node_id: The node ID to verify
            repository: 'staging' or 'prod'
            expected_metadata: If provided, compute field-level diff
            context: Schema context for repo_field filtering
            version: Schema version for repo_field filtering
            
        Returns:
            Dict with actual_metadata, optional diff and summary
        """
        config = _get_repository_configs().get(repository)
        if not config:
            return {"success": False, "error": f"Unknown repository: {repository}"}
        
        base_url = config["base_url"]
        
        try:
            timeout = httpx.Timeout(30.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                # Fetch node metadata
                url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?propertyFilter=-all-"
                response = await client.get(
                    url,
                    headers={
                        "Authorization": self._auth_header,
                        "Accept": "application/json"
                    }
                )
                
                if response.status_code != 200:
                    return {
                        "success": False,
                        "error": f"Failed to fetch node: HTTP {response.status_code} — {response.text[:300]}"
                    }
                
                data = response.json()
                properties = data.get("node", {}).get("properties", {})
                
                # Convert to flat metadata (same logic as input_source_service)
                actual = self._properties_to_flat(properties)
                
                result = {
                    "success": True,
                    "node_id": node_id,
                    "repository": repository,
                    "actual_metadata": actual,
                }
                
                # If expected metadata provided, compute diff
                if expected_metadata:
                    diff, summary = self._compute_diff(
                        expected_metadata, actual, context, version
                    )
                    result["diff"] = diff
                    result["summary"] = summary
                
                return result
                
        except httpx.TimeoutException as e:
            return {"success": False, "error": f"Timeout: {e}"}
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}"}
    
    def _properties_to_flat(self, properties: dict) -> dict:
        """Convert repository array-style properties to flat metadata."""
        flat = {}
        for key, value in properties.items():
            # Skip internal/system properties
            if key.startswith("sys:") or key.startswith("virtual:"):
                continue
            # Skip DISPLAYNAME variants
            if key.endswith("_DISPLAYNAME"):
                continue
            # Skip VCARD sub-fields (keep only the main VCARD field)
            if "VCARD_" in key:
                continue
            # Skip cm: system fields (keep only metadata-relevant ones)
            cm_keep = {"cm:author", "cm:latitude", "cm:longitude"}
            if key.startswith("cm:") and key not in cm_keep:
                continue
            
            if isinstance(value, list):
                if len(value) == 1:
                    flat[key] = value[0]
                elif len(value) > 1:
                    flat[key] = value
                # Skip empty lists
            elif value is not None:
                flat[key] = value
        
        return flat
    
    def _compute_diff(
        self,
        expected: dict,
        actual: dict,
        context: str,
        version: str,
    ) -> tuple[list[dict], dict]:
        """
        Compute field-level SOLL/IST diff.
        
        Returns:
            Tuple of (diff_list, summary_counts)
        """
        # Clean expected metadata (remove processing/header keys)
        excluded = {
            "contextName", "schemaVersion", "metadataset",
            "language", "exportedAt", "processing", "_origins",
            "repository", "check_duplicates", "start_workflow",
        }
        clean_expected = {k: v for k, v in expected.items() if k not in excluded}
        
        # Load repo fields to know which fields were eligible for writing
        schema_file = expected.get("metadataset")
        repo_field_ids = get_repo_fields(context, version, schema_file)
        
        diff = []
        summary = {"match": 0, "mismatch": 0, "missing_in_repo": 0, "extra_in_repo": 0, "not_written": 0}
        
        # Check all expected fields
        seen_keys = set()
        for field_id, expected_val in clean_expected.items():
            seen_keys.add(field_id)
            
            # Skip empty expected values
            if expected_val is None or expected_val == "" or expected_val == []:
                continue
            
            # Fields that were never eligible for repo write
            if field_id.startswith("virtual:") or field_id.startswith("schema:"):
                # These are internal fields that get transformed (e.g. schema:location → cm:latitude)
                diff.append({
                    "field_id": field_id,
                    "status": "not_written",
                    "expected": expected_val,
                    "actual": None,
                })
                summary["not_written"] += 1
                continue
            
            if repo_field_ids and field_id not in repo_field_ids:
                diff.append({
                    "field_id": field_id,
                    "status": "not_written",
                    "expected": expected_val,
                    "actual": None,
                })
                summary["not_written"] += 1
                continue
            
            actual_val = actual.get(field_id)
            
            if actual_val is None:
                # Special case: cm:author is transformed to ccm:lifecyclecontributer_author
                if field_id == "cm:author":
                    author_fn = actual.get("ccm:lifecyclecontributer_authorFN")
                    if author_fn:
                        diff.append({
                            "field_id": field_id,
                            "status": "match",
                            "expected": expected_val,
                            "actual": f"(transformed → ccm:lifecyclecontributer_authorFN: {author_fn})",
                        })
                        summary["match"] += 1
                        continue
                
                diff.append({
                    "field_id": field_id,
                    "status": "missing_in_repo",
                    "expected": expected_val,
                    "actual": None,
                })
                summary["missing_in_repo"] += 1
            elif self._values_match(expected_val, actual_val):
                diff.append({
                    "field_id": field_id,
                    "status": "match",
                    "expected": expected_val,
                    "actual": actual_val,
                })
                summary["match"] += 1
            else:
                diff.append({
                    "field_id": field_id,
                    "status": "mismatch",
                    "expected": expected_val,
                    "actual": actual_val,
                })
                summary["mismatch"] += 1
        
        # Check for extra fields in repo that weren't in expected
        for field_id, actual_val in actual.items():
            if field_id in seen_keys:
                continue
            if actual_val is None or actual_val == "" or actual_val == []:
                continue
            diff.append({
                "field_id": field_id,
                "status": "extra_in_repo",
                "expected": None,
                "actual": actual_val,
            })
            summary["extra_in_repo"] += 1
        
        # Sort: problems first, then matches
        status_order = {"missing_in_repo": 0, "mismatch": 1, "not_written": 2, "extra_in_repo": 3, "match": 4}
        diff.sort(key=lambda d: status_order.get(d["status"], 5))
        
        return diff, summary
    
    def _values_match(self, expected: Any, actual: Any) -> bool:
        """Compare expected and actual values, handling type differences."""
        # Normalize both to comparable form
        exp_norm = self._normalize_compare(expected)
        act_norm = self._normalize_compare(actual)
        return exp_norm == act_norm
    
    def _normalize_compare(self, value: Any) -> Any:
        """Normalize a value for comparison."""
        if isinstance(value, list):
            if len(value) == 1:
                return self._normalize_compare(value[0])
            return sorted(str(v).strip().lower() for v in value)
        if isinstance(value, dict):
            # Extract URI or label for comparison
            if "uri" in value:
                return str(value["uri"]).strip().lower()
            if "label" in value:
                return str(value["label"]).strip().lower()
            return json.dumps(value, sort_keys=True, ensure_ascii=False).lower()
        return str(value).strip().lower()

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
