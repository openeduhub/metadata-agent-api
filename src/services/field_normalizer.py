"""
Field Normalizer Service for metadata API.
Normalizes user input and extracted values based on field schema.
Ported from metadata-agent-canvas-optimized FieldNormalizerService.
"""
import re
from typing import Any, Optional
from datetime import date

from ..utils.text_utils import levenshtein_distance


class FieldNormalizer:
    """Normalizes field values based on schema definitions."""
    
    # German number words
    GERMAN_NUMBERS = {
        'null': 0, 'eins': 1, 'zwei': 2, 'drei': 3, 'vier': 4,
        'fünf': 5, 'sechs': 6, 'sieben': 7, 'acht': 8, 'neun': 9,
        'zehn': 10, 'elf': 11, 'zwölf': 12, 'dreizehn': 13, 'vierzehn': 14,
        'fünfzehn': 15, 'sechzehn': 16, 'siebzehn': 17, 'achtzehn': 18, 'neunzehn': 19,
        'zwanzig': 20, 'dreißig': 30, 'vierzig': 40, 'fünfzig': 50,
        'sechzig': 60, 'siebzig': 70, 'achtzig': 80, 'neunzig': 90,
        'hundert': 100, 'tausend': 1000
    }
    
    # Day of week mapping
    DAY_MAPPING = {
        "monday": "MO", "montag": "MO", "mo": "MO",
        "tuesday": "TU", "dienstag": "TU", "di": "TU",
        "wednesday": "WE", "mittwoch": "WE", "mi": "WE",
        "thursday": "TH", "donnerstag": "TH", "do": "TH",
        "friday": "FR", "freitag": "FR", "fr": "FR",
        "saturday": "SA", "samstag": "SA", "sa": "SA",
        "sunday": "SU", "sonntag": "SU", "so": "SU",
    }
    
    def normalize_field_value(
        self,
        value: Any,
        field_schema: dict,
        normalize_vocabularies: bool = True
    ) -> Any:
        """
        Normalize a single field value based on its schema.
        
        Args:
            value: The value to normalize
            field_schema: The field's schema definition
            normalize_vocabularies: Whether to normalize vocabulary values
            
        Returns:
            Normalized value
        """
        if value is None or value == '':
            return value
            
        datatype = field_schema.get('datatype', 'string')
        vocabulary = field_schema.get('vocabulary')
        is_multiple = field_schema.get('multiple', False)
        
        # Handle arrays
        if is_multiple and isinstance(value, list):
            return [
                self._normalize_single_value(v, datatype, vocabulary, normalize_vocabularies)
                for v in value
                if v is not None and v != ''
            ]
        
        return self._normalize_single_value(value, datatype, vocabulary, normalize_vocabularies)
    
    def _normalize_single_value(
        self,
        value: Any,
        datatype: str,
        vocabulary: Optional[dict],
        normalize_vocabularies: bool
    ) -> Any:
        """Normalize a single (non-array) value."""
        
        # Vocabulary normalization
        if vocabulary and normalize_vocabularies:
            normalized = self._normalize_vocabulary(value, vocabulary)
            if normalized is not None:
                return normalized
        
        # Type-specific normalization
        if datatype == 'boolean':
            return self._normalize_boolean(value)
        elif datatype in ('number', 'integer'):
            return self._normalize_number(value, datatype == 'integer')
        elif datatype == 'date':
            return self._normalize_date(value)
        elif datatype == 'datetime':
            return self._normalize_datetime(value)
        elif datatype == 'time':
            return self._normalize_time(value)
        elif datatype in ('uri', 'url'):
            return self._normalize_url(value)
        elif datatype == 'duration':
            return self._normalize_duration(value)
        
        # Default: return as-is (strings)
        return value
    
    def _normalize_boolean(self, value: Any) -> Optional[bool]:
        """Normalize boolean values."""
        if isinstance(value, bool):
            return value
        if not isinstance(value, str):
            return None
            
        s = value.lower().strip()
        if s in ('true', 'ja', 'yes', 'wahr', '1'):
            return True
        if s in ('false', 'nein', 'no', 'falsch', '0'):
            return False
        return None
    
    def _normalize_number(self, value: Any, as_integer: bool = False) -> Optional[float | int]:
        """Normalize number values."""
        if isinstance(value, (int, float)):
            return int(value) if as_integer else value
        if not isinstance(value, str):
            return None
            
        s = value.lower().strip().replace(',', '.')
        
        # Try German number words
        if s in self.GERMAN_NUMBERS:
            return self.GERMAN_NUMBERS[s]
        
        try:
            num = float(s)
            return int(num) if as_integer else num
        except ValueError:
            return None
    
    def _normalize_date(self, value: Any) -> Optional[str]:
        """Normalize date to ISO format (YYYY-MM-DD)."""
        if not isinstance(value, str):
            return None
            
        s = value.strip()
        
        # Already ISO format
        if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
            return s
        
        # German format DD.MM.YYYY
        formats = [
            (r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$', True),   # DD.MM.YYYY
            (r'^(\d{1,2})/(\d{1,2})/(\d{4})$', True),     # DD/MM/YYYY
            (r'^(\d{1,2})-(\d{1,2})-(\d{4})$', True),     # DD-MM-YYYY
        ]
        
        for pattern, day_first in formats:
            match = re.match(pattern, s)
            if match:
                day = int(match.group(1))
                month = int(match.group(2))
                year = int(match.group(3))
                
                if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                    try:
                        d = date(year, month, day)
                        return d.isoformat()
                    except ValueError:
                        pass
        
        # Try parsing ISO with time
        match = re.match(r'^(\d{4}-\d{2}-\d{2})T', s)
        if match:
            return match.group(1)
        
        return s  # Return original value instead of None to prevent data loss
    
    def _normalize_datetime(self, value: Any) -> Optional[str]:
        """Normalize datetime to ISO format."""
        if not isinstance(value, str):
            return None
            
        s = value.strip()
        
        # Already ISO format with time
        if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?', s):
            # Remove seconds if present
            match = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})', s)
            return match.group(1) if match else s
        
        # Date only - add midnight
        date_normalized = self._normalize_date(s)
        if date_normalized and date_normalized != s:
            return f"{date_normalized}T00:00"
        
        return s  # Return original value instead of None to prevent data loss
    
    def _normalize_time(self, value: Any) -> Optional[str]:
        """Normalize time to HH:MM format."""
        if not isinstance(value, str):
            return None
            
        s = value.strip()
        
        match = re.match(r'^(\d{1,2}):(\d{2})(?::\d{2})?$', s)
        if match:
            hours = int(match.group(1))
            minutes = int(match.group(2))
            if 0 <= hours <= 23 and 0 <= minutes <= 59:
                return f"{hours:02d}:{minutes:02d}"
        
        return s  # Return original value instead of None to prevent data loss
    
    def _normalize_url(self, value: Any) -> Optional[str]:
        """Normalize URL - add https:// if missing."""
        if not isinstance(value, str):
            return None
            
        s = value.strip()
        
        if re.match(r'^https?://', s, re.IGNORECASE):
            return s
        
        # Looks like a domain
        if re.match(r'^[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,}', s):
            return f'https://{s}'
        
        return s
    
    def _normalize_duration(self, value: Any) -> Optional[str]:
        """Normalize duration to ISO 8601 format."""
        if not isinstance(value, str):
            return None
            
        s = value.strip().upper()
        
        # Already ISO format
        if re.match(r'^P(\d+[YMWD])*T?(\d+[HMS])*$', s) and s != 'P' and s != 'PT':
            return s
        
        s_lower = value.lower().strip()
        
        # Parse common formats
        patterns = [
            (r'^(\d+)\s*(stunden?|hours?|h)$', lambda m: f"PT{m.group(1)}H"),
            (r'^(\d+)\s*(minuten?|minutes?|min|m)$', lambda m: f"PT{m.group(1)}M"),
            (r'^(\d+)\s*(tage?|days?|d)$', lambda m: f"P{m.group(1)}D"),
            (r'^(\d+)\s*(wochen?|weeks?|w)$', lambda m: f"P{m.group(1)}W"),
        ]
        
        for pattern, formatter in patterns:
            match = re.match(pattern, s_lower)
            if match:
                return formatter(match)
        
        return None
    
    def _normalize_vocabulary(self, value: Any, vocabulary: dict) -> Optional[Any]:
        """Normalize value against vocabulary concepts."""
        if not value:
            return None
            
        concepts = vocabulary.get('concepts', [])
        vocab_type = vocabulary.get('type', 'open')
        
        if not concepts:
            return value
        
        # Check if concepts have URIs
        has_uris = any(c.get('uri') for c in concepts)
        
        # Handle array values
        if isinstance(value, list):
            normalized = [self._match_concept(v, concepts, has_uris, vocab_type) for v in value]
            if vocab_type == 'closed':
                return [n for n in normalized if n is not None]
            return normalized
        
        return self._match_concept(value, concepts, has_uris, vocab_type)
    
    def _match_concept(
        self,
        value: str,
        concepts: list,
        has_uris: bool,
        vocab_type: str
    ) -> Optional[str]:
        """Match a single value against vocabulary concepts."""
        if not isinstance(value, str):
            return value
            
        value_lower = value.lower().strip()
        
        # Exact URI match
        if has_uris:
            for concept in concepts:
                if concept.get('uri') == value:
                    return value
        
        # Exact label match
        for concept in concepts:
            label = concept.get('label', '')
            if isinstance(label, dict):
                label = label.get('de', label.get('en', ''))
            
            if label.lower() == value_lower:
                return concept.get('uri') if has_uris and concept.get('uri') else label
            
            # Check altLabels
            alt_labels = concept.get('altLabels', [])
            for alt in alt_labels:
                if alt.lower() == value_lower:
                    return concept.get('uri') if has_uris and concept.get('uri') else label
        
        # Fuzzy match for closed vocabularies
        if vocab_type == 'closed':
            best_match = self._find_fuzzy_match(value_lower, concepts)
            if best_match:
                return best_match.get('uri') if has_uris and best_match.get('uri') else best_match.get('label')
            return None
        
        return value
    
    def _find_fuzzy_match(self, value: str, concepts: list) -> Optional[dict]:
        """Find closest matching concept using Levenshtein distance."""
        best_match = None
        best_distance = float('inf')
        
        for concept in concepts:
            label = concept.get('label', '')
            if isinstance(label, dict):
                label = label.get('de', label.get('en', ''))
            
            distance = self._levenshtein_distance(value, label.lower())
            if distance < best_distance:
                best_distance = distance
                best_match = concept
        
        # Only accept if distance is within 30% of string length or max 3
        max_distance = min(3, len(value) * 0.3)
        if best_match and best_distance <= max_distance:
            return best_match
        
        return None
    
    def _levenshtein_distance(self, a: str, b: str) -> int:
        """Calculate Levenshtein edit distance."""
        return levenshtein_distance(a, b)
    
    def normalize_day_of_week(self, value: str) -> str:
        """Convert day name to short code (MO, TU, etc.)."""
        if not value:
            return value
        
        lower = value.lower().strip()
        return self.DAY_MAPPING.get(lower, value.upper() if len(value) == 2 else value)


# Singleton instance
_normalizer: Optional[FieldNormalizer] = None


def get_field_normalizer() -> FieldNormalizer:
    """Get singleton instance of field normalizer."""
    global _normalizer
    if _normalizer is None:
        _normalizer = FieldNormalizer()
    return _normalizer
