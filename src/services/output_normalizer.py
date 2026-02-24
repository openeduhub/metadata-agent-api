"""
Output normalizer for metadata API.
Transforms API output to match the structure expected by the Canvas web component.
"""
import re
from typing import Any, Optional


class OutputNormalizer:
    """Normalizes metadata output to match Canvas web component structure."""
    
    # Day of week mapping
    DAY_MAPPING = {
        "monday": "MO", "montag": "MO", "mo": "MO",
        "tuesday": "TU", "dienstag": "TU", "di": "TU",
        "wednesday": "WE", "mittwoch": "WE", "mi": "WE",
        "thursday": "TH", "donnerstag": "TH", "do": "TH",
        "friday": "FR", "freitag": "FR", "fr": "FR",
        "saturday": "SA", "samstag": "SA", "sa": "SA",
        "sunday": "SU", "sonntag": "SU", "so": "SU",
        # Schema.org format
        "schema:monday": "MO", "schema:tuesday": "TU", 
        "schema:wednesday": "WE", "schema:thursday": "TH",
        "schema:friday": "FR", "schema:saturday": "SA", 
        "schema:sunday": "SU",
    }
    
    def normalize_output(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize all metadata fields to match Canvas web component structure.
        """
        result = metadata.copy()
        
        # Person fields: convert strings to objects with name
        person_fields = [
            "schema:actor", "schema:performer", "schema:attendee",
            "schema:organizer", "schema:contributor", "schema:author"
        ]
        for field in person_fields:
            if field in result:
                result[field] = self._normalize_person_array(result[field])
        
        # Opening hours: normalize dayOfWeek to array with short codes
        if "schema:openingHoursSpecification" in result:
            result["schema:openingHoursSpecification"] = self._normalize_opening_hours(
                result["schema:openingHoursSpecification"]
            )
        
        # Event schedule: add missing fields
        if "schema:eventSchedule" in result:
            result["schema:eventSchedule"] = self._normalize_event_schedule(
                result["schema:eventSchedule"]
            )
        
        # Access service: convert strings to objects
        if "schema:accessService" in result:
            result["schema:accessService"] = self._normalize_access_service(
                result["schema:accessService"]
            )
        
        # About: ensure labels not URIs (keep as-is if already labels)
        if "schema:about" in result:
            result["schema:about"] = self._normalize_about(result["schema:about"])
        
        # Note: DateTime normalization is handled generically by FieldNormalizer
        # based on the field's datatype. No hardcoded field lists needed here.
        
        return result
    
    def _normalize_person_array(self, value: Any) -> list[dict]:
        """Convert person values to objects with name field."""
        if not value:
            return []
        
        if not isinstance(value, list):
            value = [value]
        
        result = []
        for item in value:
            if isinstance(item, str):
                result.append({"name": item})
            elif isinstance(item, dict):
                if "name" not in item and "@value" in item:
                    item["name"] = item.pop("@value")
                result.append(item)
        
        return result
    
    def _normalize_opening_hours(self, hours: Any) -> list[dict]:
        """Normalize opening hours specification."""
        if not hours or not isinstance(hours, list):
            return hours if hours else []
        
        result = []
        for item in hours:
            if not isinstance(item, dict):
                continue
            
            normalized = item.copy()
            
            # Convert dayOfWeek to array with short codes
            if "dayOfWeek" in normalized:
                dow = normalized["dayOfWeek"]
                if isinstance(dow, str):
                    # Single string - convert to array with short code
                    short_code = self._get_day_short_code(dow)
                    normalized["dayOfWeek"] = [short_code]
                elif isinstance(dow, list):
                    # Already array - ensure short codes
                    normalized["dayOfWeek"] = [
                        self._get_day_short_code(d) for d in dow
                    ]
            
            # Remove validThrough if same as validFrom
            if normalized.get("validThrough") == normalized.get("validFrom"):
                normalized.pop("validThrough", None)
            
            result.append(normalized)
        
        return result
    
    def _get_day_short_code(self, day: str) -> str:
        """Convert day name to short code (MO, TU, WE, etc.)."""
        if not day:
            return day
        
        day_lower = day.lower().strip()
        
        # Check direct mapping
        if day_lower in self.DAY_MAPPING:
            return self.DAY_MAPPING[day_lower]
        
        # Try to extract from various formats
        for key, code in self.DAY_MAPPING.items():
            if key in day_lower:
                return code
        
        # Already a short code?
        if day.upper() in ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]:
            return day.upper()
        
        return day
    
    def _normalize_event_schedule(self, schedule: Any) -> list[dict]:
        """Normalize event schedule with all required fields."""
        if not schedule or not isinstance(schedule, list):
            return schedule if schedule else []
        
        result = []
        for item in schedule:
            if not isinstance(item, dict):
                continue
            
            normalized = item.copy()
            
            # Parse startDate to extract components
            start_date = normalized.get("startDate", "")
            if start_date:
                # Extract date components
                date_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", start_date)
                time_match = re.search(r"T?(\d{2}):(\d{2})", start_date)
                
                if date_match:
                    year, month, day = date_match.groups()
                    
                    # Ensure date-only format for startDate/endDate
                    if "T" in str(start_date):
                        normalized["startDate"] = f"{year}-{month}-{day}"
                    
                    # Add byMonth if not present
                    if "byMonth" not in normalized:
                        normalized["byMonth"] = [int(month)]
                    
                    # Add byMonthDay if not present
                    if "byMonthDay" not in normalized:
                        normalized["byMonthDay"] = [int(day)]
                    
                    # Calculate day of week
                    if "byDay" not in normalized:
                        from datetime import date
                        try:
                            d = date(int(year), int(month), int(day))
                            days = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
                            normalized["byDay"] = [days[d.weekday()]]
                        except Exception:
                            pass
                
                if time_match:
                    hour = int(time_match.group(1))
                    if "byHour" not in normalized:
                        normalized["byHour"] = [hour]
            
            # Ensure endDate matches startDate format
            end_date = normalized.get("endDate", "")
            if end_date and "T" in str(end_date):
                date_match = re.match(r"(\d{4}-\d{2}-\d{2})", end_date)
                if date_match:
                    normalized["endDate"] = date_match.group(1)
            elif not end_date and normalized.get("startDate"):
                normalized["endDate"] = normalized["startDate"]
            
            result.append(normalized)
        
        return result
    
    def _normalize_access_service(self, services: Any) -> list[dict]:
        """Convert access service strings to objects."""
        if not services:
            return []
        
        if not isinstance(services, list):
            services = [services]
        
        result = []
        for item in services:
            if isinstance(item, str):
                # Convert string to object
                if "barrierefrei" in item.lower():
                    result.append({
                        "serviceType": "Barrierefreiheit",
                        "description": item
                    })
                elif "ermäßigung" in item.lower():
                    result.append({
                        "serviceType": "Ermäßigung",
                        "description": item
                    })
                else:
                    result.append({
                        "serviceType": "Hinweis",
                        "description": item
                    })
            elif isinstance(item, dict):
                result.append(item)
        
        return result
    
    def _normalize_about(self, about: Any) -> list[str]:
        """Normalize about field - convert URIs to labels if needed."""
        if not about:
            return []
        
        if not isinstance(about, list):
            about = [about]
        
        result = []
        for item in about:
            if isinstance(item, str):
                # If it's a DBpedia URI, extract the label
                if "dbpedia.org/resource/" in item:
                    label = item.split("/")[-1].replace("_", " ")
                    result.append(label)
                else:
                    result.append(item)
            else:
                result.append(str(item))
        
        return result
    


# Singleton instance
_normalizer: Optional[OutputNormalizer] = None


def get_output_normalizer() -> OutputNormalizer:
    """Get singleton instance of output normalizer."""
    global _normalizer
    if _normalizer is None:
        _normalizer = OutputNormalizer()
    return _normalizer
