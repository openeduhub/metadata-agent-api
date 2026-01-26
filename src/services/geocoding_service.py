"""Geocoding service using Photon API (Komoot) to convert addresses to coordinates."""
import asyncio
import re
from typing import Any, Optional
import httpx


class GeocodingService:
    """
    Geocoding service using Photon API from Komoot.
    Converts addresses to geo-coordinates (latitude/longitude).
    """
    
    # Photon API endpoint (free, no API key required)
    PHOTON_API_URL = "https://photon.komoot.io/api"
    
    # Rate limiting: 1 request per second
    RATE_LIMIT_MS = 1000
    
    def __init__(self):
        self._last_request_time = 0
    
    async def geocode_address(self, address: str, language: str = "de") -> Optional[dict]:
        """
        Geocode an address string using Photon API.
        
        Args:
            address: Address string (e.g., "Erfurter Straße 1, 99423 Weimar")
            language: Language for results (de, en, fr, it)
            
        Returns:
            Dict with latitude, longitude, and enriched address data, or None
        """
        if not address or not address.strip():
            return None
        
        try:
            # Rate limiting
            await self._wait_for_rate_limit()
            
            # Build API URL
            params = {
                "q": address,
                "lang": language,
                "limit": 1
            }
            
            headers = {
                "User-Agent": "MetadataAgentAPI/1.0 (geocoding service)"
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.PHOTON_API_URL, params=params, headers=headers)
                
                if response.status_code != 200:
                    print(f"❌ Photon API error: {response.status_code}")
                    return None
                
                data = response.json()
            
            # Check if we got results
            if not data.get("features") or len(data["features"]) == 0:
                print(f"⚠️ No geocoding results for: {address}")
                return None
            
            feature = data["features"][0]
            coords = feature.get("geometry", {}).get("coordinates", [])
            props = feature.get("properties", {})
            
            if len(coords) < 2:
                print("❌ Invalid coordinates in Photon response")
                return None
            
            # Photon returns [longitude, latitude]
            result = {
                "latitude": round(coords[1], 7),
                "longitude": round(coords[0], 7),
                "enriched_address": {
                    "street": props.get("street"),
                    "housenumber": props.get("housenumber"),
                    "postal_code": props.get("postcode"),
                    "city": props.get("city"),
                    "state": props.get("state"),
                    "country": props.get("country"),
                    "country_code": props.get("countrycode"),
                    "district": props.get("district"),
                },
                "osm_data": {
                    "osm_type": props.get("osm_type"),
                    "osm_id": props.get("osm_id"),
                    "osm_key": props.get("osm_key"),
                    "osm_value": props.get("osm_value"),
                    "type": props.get("type"),
                }
            }
            
            print(f"✅ Geocoded '{address}' → {result['latitude']}, {result['longitude']}")
            return result
            
        except Exception as e:
            print(f"❌ Geocoding error: {e}")
            return None
    
    async def geocode_location_string(self, location: str, language: str = "de") -> Optional[dict]:
        """
        Geocode a simple location string (city name, address, etc.)
        
        Args:
            location: Location string
            language: Language for results
            
        Returns:
            Dict with coordinates or None
        """
        return await self.geocode_address(location, language)
    
    async def geocode_locations(
        self, 
        locations: list[str], 
        language: str = "de"
    ) -> list[Optional[dict]]:
        """
        Geocode multiple locations sequentially (respecting rate limit).
        
        Args:
            locations: List of location strings
            language: Language for results
            
        Returns:
            List of geocoding results (None for failed ones)
        """
        results = []
        for location in locations:
            result = await self.geocode_address(location, language)
            results.append(result)
        return results
    
    async def enrich_metadata_with_geocoding(
        self, 
        metadata: dict[str, Any],
        language: str = "de"
    ) -> dict[str, Any]:
        """
        Enrich metadata with geocoding results.
        
        Converts schema:location entries to structured objects with geo coordinates.
        Each location gets its own geo object with latitude/longitude.
        
        Input formats supported:
        - String array: ["Erfurter Str. 1, 99423 Weimar"]
        - Object array: [{"name": "...", "address": {...}}]
        
        Output format (per location):
        {
            "name": "...",
            "address": {"streetAddress": "...", "postalCode": "...", ...},
            "geo": {"latitude": 50.97, "longitude": 11.33}
        }
        
        Args:
            metadata: Generated metadata dict
            language: Language for geocoding
            
        Returns:
            Metadata with enriched location objects including geo coordinates
        """
        location_field = metadata.get("schema:location")
        
        if not location_field:
            print("ℹ️ No schema:location field found, skipping geocoding")
            return metadata
        
        print(f"🌍 Found schema:location with {len(location_field) if isinstance(location_field, list) else 1} location(s)")
        
        # Convert to list if single value
        if isinstance(location_field, str):
            location_field = [location_field]
        
        if not isinstance(location_field, list):
            return metadata
        
        # Process each location
        enriched_locations = []
        for i, location in enumerate(location_field):
            print(f"📍 Processing location {i + 1}/{len(location_field)}: {location}")
            enriched_location = await self._enrich_single_location(location, language)
            enriched_locations.append(enriched_location)
        
        # Update metadata with enriched locations
        metadata["schema:location"] = enriched_locations
        
        return metadata
    
    async def _enrich_single_location(
        self, 
        location: Any, 
        language: str
    ) -> dict[str, Any]:
        """
        Enrich a single location with geocoding.
        
        Args:
            location: String or dict location
            language: Geocoding language
            
        Returns:
            Structured location object with geo coordinates
        """
        # If already a structured object
        if isinstance(location, dict):
            # Check if already has geo coordinates
            if location.get("geo") and location["geo"].get("latitude"):
                print(f"  ✅ Already has coordinates: {location['geo']}")
                return location
            
            # Build address string for geocoding
            address_obj = location.get("address", {})
            address_str = self._build_address_string(address_obj) if address_obj else ""
            
            # Fallback: If address is empty/incomplete, try using the name field
            if not address_str and location.get("name"):
                name = location.get("name", "")
                print(f"  ℹ️ Address empty, using name for geocoding: {name}")
                address_str = name
            
            if address_str:
                geo_result = await self.geocode_address(address_str, language)
                if geo_result:
                    location["geo"] = {
                        "latitude": geo_result["latitude"],
                        "longitude": geo_result["longitude"]
                    }
                    # Enrich address if empty or incomplete
                    if not address_obj or not self._build_address_string(address_obj):
                        location["address"] = self._build_address_object(geo_result)
                        print(f"  ✅ Enriched address from geocoding result")
            
            return location
        
        # If string, convert to structured object
        if isinstance(location, str):
            address_str = location.strip()
            
            # Try to parse name from address (format: "Name, Address")
            name = None
            if "," in address_str:
                parts = address_str.split(",", 1)
                # If first part doesn't look like a street address, use as name
                first_part = parts[0].strip()
                if not any(char.isdigit() for char in first_part) and len(first_part) < 50:
                    name = first_part
                    address_str = parts[1].strip() if len(parts) > 1 else address_str
            
            # Geocode the address
            geo_result = await self.geocode_address(location, language)
            
            if geo_result:
                enriched = geo_result.get("enriched_address", {})
                
                result = {
                    "address": {
                        "streetAddress": enriched.get("street") or "",
                        "postalCode": enriched.get("postal_code") or "",
                        "addressLocality": enriched.get("city") or "",
                        "addressRegion": enriched.get("state") or "",
                        "addressCountry": enriched.get("country_code", "").upper() or enriched.get("country") or ""
                    },
                    "geo": {
                        "latitude": geo_result["latitude"],
                        "longitude": geo_result["longitude"]
                    }
                }
                
                # Add name if extracted or use locality
                if name:
                    result["name"] = name
                
                # Add housenumber to street if available
                if enriched.get("housenumber") and enriched.get("street"):
                    result["address"]["streetAddress"] = f"{enriched['street']} {enriched['housenumber']}"
                
                print(f"  ✅ Geocoded to: {result['geo']['latitude']}, {result['geo']['longitude']}")
                return result
            else:
                # Return basic structure even without geocoding
                return {
                    "name": location,
                    "address": {},
                    "geo": {}
                }
        
        # Unknown format, return as-is wrapped
        return {"name": str(location), "address": {}, "geo": {}}
    
    def _build_address_object(self, geo_result: dict) -> dict:
        """Build structured address object from geocoding result."""
        enriched = geo_result.get("enriched_address", {})
        
        street = enriched.get("street") or ""
        if enriched.get("housenumber"):
            street = f"{street} {enriched['housenumber']}".strip()
        
        return {
            "streetAddress": street,
            "postalCode": enriched.get("postal_code") or "",
            "addressLocality": enriched.get("city") or "",
            "addressRegion": enriched.get("state") or "",
            "addressCountry": enriched.get("country_code", "").upper() or ""
        }
    
    def _build_address_string(self, address_obj: dict) -> str:
        """Build address string from structured address object."""
        parts = []
        
        # Try different address field names
        street = address_obj.get("streetAddress") or address_obj.get("street") or ""
        if street:
            parts.append(street)
        
        postal_code = address_obj.get("postalCode") or address_obj.get("postal_code") or ""
        city = address_obj.get("addressLocality") or address_obj.get("city") or ""
        
        if postal_code and city:
            parts.append(f"{postal_code} {city}")
        elif city:
            parts.append(city)
        
        country = address_obj.get("addressCountry") or address_obj.get("country") or ""
        if country:
            parts.append(country)
        
        return ", ".join(parts)
    
    async def _wait_for_rate_limit(self) -> None:
        """Wait for rate limit (1 request per second)."""
        import time
        now = time.time() * 1000  # Convert to milliseconds
        time_since_last = now - self._last_request_time
        
        if time_since_last < self.RATE_LIMIT_MS:
            wait_time = (self.RATE_LIMIT_MS - time_since_last) / 1000
            await asyncio.sleep(wait_time)
        
        self._last_request_time = time.time() * 1000


# Singleton instance
_geocoding_service: Optional[GeocodingService] = None


def get_geocoding_service() -> GeocodingService:
    """Get or create geocoding service singleton."""
    global _geocoding_service
    if _geocoding_service is None:
        _geocoding_service = GeocodingService()
    return _geocoding_service
