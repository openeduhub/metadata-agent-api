"""LLM service for parallel metadata extraction."""
import asyncio
import json
import time
import re
import httpx
from typing import Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..config import get_settings
from ..utils.text_utils import levenshtein_distance


class LLMService:
    """Service for LLM-based metadata extraction with parallel processing."""
    
    def __init__(self, llm_provider: Optional[str] = None, llm_model: Optional[str] = None):
        """
        Initialize LLM service.
        
        Args:
            llm_provider: Override default provider ('openai', 'b-api-openai', 'b-api-academiccloud')
            llm_model: Override default model for the provider
        """
        settings = get_settings()
        self.settings = settings
        
        # Get config with optional overrides
        self.llm_config = settings.get_llm_config(
            provider_override=llm_provider,
            model_override=llm_model
        )
        
        # Provider info
        self.provider = self.llm_config["provider"]
        self.api_key = self.llm_config["api_key"]
        self.api_base = self.llm_config["api_base"]
        self.model = self.llm_config["model"]
        self.temperature = self.llm_config["temperature"]
        self.requires_custom_header = self.llm_config["requires_custom_header"]
        self.max_tokens = settings.llm_max_tokens
        self.max_retries = settings.llm_max_retries
        
        # HTTP client for B-API (requires custom headers)
        self.http_client = httpx.AsyncClient(timeout=60.0)
        
        print(f"🤖 LLM Service initialized: {self.provider}")
        print(f"   Model: {self.model}")
        print(f"   API Base: {self.api_base}")
    
    async def close(self):
        """Close the HTTP client."""
        if self.http_client:
            await self.http_client.aclose()
    
    async def _call_llm(
        self,
        messages: list[dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Call LLM API with proper provider configuration.
        Supports OpenAI and B-API (with X-API-KEY header).
        """
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens
        
        request_body = {
            "model": self.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": tokens,
        }
        
        # Build headers based on provider
        headers = {"Content-Type": "application/json"}
        if self.requires_custom_header:
            # B-API uses X-API-KEY header
            headers["X-API-KEY"] = self.api_key
        else:
            # OpenAI uses Bearer token
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        # Make request
        api_url = f"{self.api_base}/chat/completions"
        
        for attempt in range(self.max_retries):
            try:
                response = await self.http_client.post(
                    api_url,
                    headers=headers,
                    json=request_body,
                )
                
                if response.status_code == 200:
                    return response.json()
                
                # Retry on transient errors
                if response.status_code in [429, 500, 502, 503, 504]:
                    wait_time = (attempt + 1) * self.settings.llm_retry_delay
                    print(f"⚠️ LLM API returned {response.status_code}, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                
                # Non-retryable error
                error_text = response.text
                raise Exception(f"LLM API error {response.status_code}: {error_text}")
                
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep((attempt + 1) * self.settings.llm_retry_delay)
                    continue
                raise Exception(f"LLM API connection failed after retries: {e}")
        
        raise Exception("LLM API failed after all retries")
    
    async def extract_field(
        self,
        field: dict[str, Any],
        text: str,
        existing_value: Any = None,
        language: str = "de",
        retry_count: int = 0,
    ) -> tuple[str, Any, Optional[str]]:
        """
        Extract a single field value from text with retry logic.
        
        Returns: (field_id, extracted_value, error_message)
        """
        field_id = field.get("id", "unknown")
        max_retries = self.settings.llm_max_retries
        
        try:
            # Build prompt with retry hints if this is a retry
            prompt = self._build_extraction_prompt(
                field, text, existing_value, language, retry_count
            )
            
            response = await self._call_llm(
                messages=[
                    {"role": "system", "content": self._get_system_prompt(language)},
                    {"role": "user", "content": prompt},
                ],
            )
            
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # Parse JSON response
            result = self._parse_json_response(content)
            extracted_value = result.get("value") if result else None
            
            # Normalize the extracted value
            if extracted_value is not None:
                normalized = self._normalize_value(extracted_value, field)
                
                # Check if normalization failed for vocabulary fields
                system = field.get("system", {})
                vocabulary = system.get("vocabulary", {})
                if vocabulary and vocabulary.get("type") == "closed":
                    # Validate vocabulary result
                    valid = self._is_valid_vocabulary_value(normalized, vocabulary)
                    if not valid and retry_count < max_retries:
                        # Retry with hint about invalid value
                        return await self.extract_field(
                            field, text, existing_value, language, retry_count + 1
                        )
                
                extracted_value = normalized
            
            # If no value extracted and field seems important, try LLM normalization
            if extracted_value is None and retry_count < max_retries:
                system = field.get("system", {})
                if system.get("required", False):
                    # Retry for required fields
                    return await self.extract_field(
                        field, text, existing_value, language, retry_count + 1
                    )
            
            return (field_id, extracted_value, None)
            
        except Exception as e:
            if retry_count < max_retries:
                # Retry on error
                await asyncio.sleep(self.settings.llm_retry_delay)
                return await self.extract_field(
                    field, text, existing_value, language, retry_count + 1
                )
            return (field_id, None, str(e))
    
    def _is_valid_vocabulary_value(self, value: Any, vocabulary: dict) -> bool:
        """Check if value is valid for closed vocabulary."""
        if value is None:
            return False
        
        concepts = vocabulary.get("concepts", [])
        valid_uris = {c.get("uri") for c in concepts}
        
        if isinstance(value, list):
            return all(v in valid_uris for v in value if v is not None)
        return value in valid_uris
    
    async def normalize_with_llm(
        self,
        value: Any,
        field: dict[str, Any],
        language: str = "de",
    ) -> Any:
        """Use LLM to normalize a value when local normalization fails."""
        field_id = field.get("id", "")
        system = field.get("system", {})
        datatype = system.get("datatype", "string")
        
        prompt = self._build_normalization_prompt(value, field, language)
        
        try:
            response = await self._call_llm(
                messages=[
                    {"role": "system", "content": self._get_normalization_system_prompt(language)},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=200,
            )
            
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            result = self._parse_normalization_response(content, datatype)
            
            return result if result is not None else value
            
        except Exception:
            return value
    
    def _get_normalization_system_prompt(self, language: str) -> str:
        """Get system prompt for normalization."""
        if language == "de":
            return "Du bist ein Daten-Normalisierungs-Assistent. Gib NUR den normalisierten Wert zurück, ohne Erklärung."
        return "You are a data normalization assistant. Return ONLY the normalized value without any explanation."
    
    def _build_normalization_prompt(
        self,
        value: Any,
        field: dict[str, Any],
        language: str,
    ) -> str:
        """Build prompt for LLM-based normalization."""
        system = field.get("system", {})
        datatype = system.get("datatype", "string")
        label = self._get_localized(field.get("label", {}), language)
        
        parts = []
        
        if language == "de":
            parts.append(f"Normalisiere folgenden Wert für das Feld '{label}':")
            parts.append(f"Eingabe: {value}")
            parts.append(f"Datentyp: {datatype}")
            
            if datatype == "date":
                parts.append("\nGib das Datum im Format YYYY-MM-DD zurück.")
                parts.append("Beispiele: '15. September 2026' → 2026-09-15")
            elif datatype == "datetime":
                parts.append("\nGib Datum und Zeit im Format YYYY-MM-DDTHH:MM:SS zurück.")
            elif datatype in ("number", "integer"):
                parts.append("\nGib nur die Zahl zurück (keine Einheiten).")
                parts.append("Wandle Zahlwörter um: 'dreihundertfünfzig' → 350")
            elif datatype == "boolean":
                parts.append("\nGib nur true oder false zurück.")
            
            vocabulary = system.get("vocabulary", {})
            if vocabulary and vocabulary.get("concepts"):
                parts.append("\nVerfügbare Werte (verwende exakt diese):")
                for c in vocabulary.get("concepts", [])[:15]:
                    label_text = self._get_localized(c.get("label", {}), language)
                    parts.append(f"  - {label_text}: {c.get('uri', '')}")
        else:
            parts.append(f"Normalize the following value for field '{label}':")
            parts.append(f"Input: {value}")
            parts.append(f"Datatype: {datatype}")
            
            if datatype == "date":
                parts.append("\nReturn the date in YYYY-MM-DD format.")
            elif datatype in ("number", "integer"):
                parts.append("\nReturn only the number (no units).")
            elif datatype == "boolean":
                parts.append("\nReturn only true or false.")
        
        return "\n".join(parts)
    
    def _parse_normalization_response(self, content: str, datatype: str) -> Any:
        """Parse LLM normalization response."""
        content = content.strip()
        
        # Remove quotes if present
        if content.startswith('"') and content.endswith('"'):
            content = content[1:-1]
        if content.startswith("'") and content.endswith("'"):
            content = content[1:-1]
        
        # Handle null
        if content.lower() in ('null', 'none', 'n/a', ''):
            return None
        
        # Parse based on datatype
        if datatype == "boolean":
            if content.lower() in ('true', 'ja', 'yes', '1'):
                return True
            if content.lower() in ('false', 'nein', 'no', '0'):
                return False
            return None
        
        if datatype in ("number", "integer"):
            try:
                num = float(content.replace(',', '.'))
                return int(num) if datatype == "integer" else num
            except ValueError:
                return None
        
        # For strings, dates, etc. - return cleaned content
        return content
    
    async def extract_fields_parallel(
        self,
        fields: list[dict[str, Any]],
        text: str,
        existing_metadata: Optional[dict[str, Any]] = None,
        language: str = "de",
        max_workers: int = 10,
    ) -> dict[str, Any]:
        """
        Extract multiple fields in parallel with worker limit.
        
        Returns: dict with field_id -> value mappings
        """
        semaphore = asyncio.Semaphore(max_workers)
        results = {}
        errors = []
        
        async def extract_with_semaphore(field: dict[str, Any]) -> None:
            async with semaphore:
                field_id = field.get("id", "unknown")
                existing_value = existing_metadata.get(field_id) if existing_metadata else None
                
                field_id, value, error = await self.extract_field(
                    field, text, existing_value, language
                )
                
                if error:
                    errors.append(f"{field_id}: {error}")
                elif value is not None:
                    results[field_id] = value
        
        # Create tasks for all fields
        tasks = [extract_with_semaphore(field) for field in fields]
        
        # Run all tasks concurrently
        await asyncio.gather(*tasks)
        
        return {"values": results, "errors": errors}
    
    def _get_system_prompt(self, language: str) -> str:
        """Get the system prompt for extraction."""
        if language == "de":
            return """Du bist ein Experte für Metadaten-Extraktion im Bildungsbereich.
Deine Aufgabe ist es, strukturierte Metadaten aus Texten zu extrahieren und zu generieren.

WICHTIGE REGELN:
1. Für TITEL und BESCHREIBUNG: Generiere passende Texte basierend auf dem Inhalt, auch wenn kein expliziter Titel vorhanden ist
2. Für andere Felder: Extrahiere nur Informationen, die im Text enthalten sind
3. Erfinde KEINE faktischen Informationen (Daten, Orte, Namen) - aber generiere passende Titel/Beschreibungen
4. Bei Unsicherheit für nicht-generierbare Felder: Wert auf null setzen
5. Antworte IMMER im JSON-Format mit dem Schlüssel "value"
6. Bei Vokabular-Feldern: Verwende EXAKT die vorgegebenen URIs
7. Bei Mehrfachwerten: Gib ein Array zurück
8. Bei Datumsangaben: ISO 8601 Format (YYYY-MM-DD)"""
        else:
            return """You are an expert in metadata extraction for educational content.
Your task is to extract and generate structured metadata from texts.

IMPORTANT RULES:
1. For TITLE and DESCRIPTION: Generate suitable texts based on the content, even if no explicit title is present
2. For other fields: Extract only information present in the text
3. Do NOT invent factual information (dates, places, names) - but generate suitable titles/descriptions
4. If uncertain for non-generatable fields: set value to null
5. ALWAYS respond in JSON format with the key "value"
6. For vocabulary fields: Use EXACTLY the provided URIs
7. For multiple values: Return an array
8. For dates: Use ISO 8601 format (YYYY-MM-DD)"""
    
    def _build_extraction_prompt(
        self,
        field: dict[str, Any],
        text: str,
        existing_value: Any = None,
        language: str = "de",
        retry_count: int = 0,
    ) -> str:
        """Build the extraction prompt for a field with retry hints."""
        field_id = field.get("id", "")
        label = self._get_localized(field.get("label", {}), language)
        description = self._get_localized(field.get("description", {}), language)
        prompt_hint = self._get_localized(field.get("prompt", {}), language)
        
        system = field.get("system", {})
        datatype = system.get("datatype", "string")
        multiple = system.get("multiple", False)
        required = system.get("required", False)
        
        # Build prompt parts
        parts = []
        
        # Add retry hints if this is a retry attempt
        if retry_count > 0:
            if language == "de":
                parts.append(f"⚠️ VERSUCH {retry_count + 1} - Bitte genauer prüfen!")
                if retry_count == 1:
                    parts.append("Der vorherige Versuch lieferte kein gültiges Ergebnis.")
                    parts.append("Analysiere den Text genauer und suche nach impliziten Informationen.")
                elif retry_count >= 2:
                    parts.append("LETZTER VERSUCH - Suche auch nach indirekten Hinweisen.")
                    parts.append("Falls keine Information findbar: null zurückgeben.")
                parts.append("")
            else:
                parts.append(f"⚠️ ATTEMPT {retry_count + 1} - Please check more carefully!")
                if retry_count == 1:
                    parts.append("The previous attempt did not yield a valid result.")
                    parts.append("Analyze the text more carefully and look for implicit information.")
                elif retry_count >= 2:
                    parts.append("LAST ATTEMPT - Also look for indirect hints.")
                    parts.append("If no information can be found: return null.")
                parts.append("")
        
        # Check if this is a generative field (title/description)
        is_generative_field = field_id in ["cclom:title", "cclom:general_description"]
        
        if is_generative_field:
            if language == "de":
                parts.append(f"🎯 GENERIERE das Feld '{label}' (ID: {field_id}) basierend auf folgendem Text:")
                parts.append("HINWEIS: Erstelle einen passenden, aussagekräftigen Wert basierend auf dem Textinhalt!")
            else:
                parts.append(f"🎯 GENERATE the field '{label}' (ID: {field_id}) based on the following text:")
                parts.append("NOTE: Create a suitable, meaningful value based on the text content!")
        else:
            if language == "de":
                parts.append(f"Extrahiere das Feld '{label}' (ID: {field_id}) aus folgendem Text:")
            else:
                parts.append(f"Extract the field '{label}' (ID: {field_id}) from the following text:")
        
        parts.append(f"\n--- TEXT ---\n{text}\n--- ENDE ---\n")
        
        if description:
            parts.append(f"Beschreibung: {description}" if language == "de" else f"Description: {description}")
        
        if prompt_hint:
            parts.append(f"Hinweis: {prompt_hint}" if language == "de" else f"Hint: {prompt_hint}")
        
        parts.append(f"Datentyp: {datatype}" if language == "de" else f"Datatype: {datatype}")
        
        if multiple:
            parts.append("Mehrfachwerte erlaubt (Array)" if language == "de" else "Multiple values allowed (Array)")
        
        # Add object structure info if present (items, variants, fields)
        items = system.get("items", {})
        if items and items.get("datatype") == "object":
            structure_info = self._build_structure_info(items, language)
            if structure_info:
                parts.append(structure_info)
        
        # Add vocabulary info if present
        vocabulary = system.get("vocabulary", {})
        if vocabulary:
            concepts = vocabulary.get("concepts", [])
            if concepts:
                vocab_type = vocabulary.get("type", "open")
                if language == "de":
                    parts.append(f"\nVokabular ({vocab_type}):")
                    parts.append("⚠️ WICHTIG: Verwende NUR die exakten URIs aus dieser Liste!")
                    if retry_count > 0:
                        parts.append("❌ Erfinde KEINE eigenen URIs oder Labels!")
                else:
                    parts.append(f"\nVocabulary ({vocab_type}):")
                    parts.append("⚠️ IMPORTANT: Use ONLY the exact URIs from this list!")
                    if retry_count > 0:
                        parts.append("❌ Do NOT invent your own URIs or labels!")
                
                for concept in concepts[:20]:  # Limit to 20 concepts
                    uri = concept.get("uri", "")
                    concept_label = self._get_localized(concept.get("label", {}), language)
                    parts.append(f"  - {concept_label}: {uri}")
                
                if len(concepts) > 20:
                    parts.append(f"  ... und {len(concepts) - 20} weitere" if language == "de" 
                                else f"  ... and {len(concepts) - 20} more")
        
        # Add examples if present
        examples = field.get("examples", {})
        example_list = examples.get(language, examples.get("de", []))
        if example_list:
            parts.append("\nBeispiele:" if language == "de" else "\nExamples:")
            for ex in example_list[:3]:
                parts.append(f"  - {ex}")
        
        # Add existing value context
        if existing_value is not None:
            if language == "de":
                parts.append(f"\n📋 AKTUELLER WERT: {json.dumps(existing_value, ensure_ascii=False)}")
                parts.append("AKTUALISIERUNGS-REGELN:")
                parts.append("1. Wenn der Text NEUE oder GEÄNDERTE Informationen für dieses Feld enthält → Aktualisiere den Wert")
                parts.append("2. Wenn der Text KEINE Informationen zu diesem Feld enthält → Behalte den aktuellen Wert")
                parts.append("3. Prüfe genau auf: Datumsänderungen, Ortsänderungen, Namensänderungen etc.")
            else:
                parts.append(f"\n📋 CURRENT VALUE: {json.dumps(existing_value, ensure_ascii=False)}")
                parts.append("UPDATE RULES:")
                parts.append("1. If the text contains NEW or CHANGED information for this field → Update the value")
                parts.append("2. If the text contains NO information about this field → Keep the current value")
                parts.append("3. Check carefully for: date changes, location changes, name changes etc.")
        
        # Final instruction
        if language == "de":
            parts.append('\nAntworte mit JSON: {"value": <extrahierter_wert>}')
            if not required:
                parts.append('Wenn keine Information gefunden: {"value": null}')
        else:
            parts.append('\nRespond with JSON: {"value": <extracted_value>}')
            if not required:
                parts.append('If no information found: {"value": null}')
        
        return "\n".join(parts)
    
    def _get_localized(self, obj: dict[str, str], language: str) -> str:
        """Get localized string with fallback."""
        if isinstance(obj, str):
            return obj
        return obj.get(language, obj.get("de", obj.get("en", "")))
    
    def _build_structure_info(self, items: dict[str, Any], language: str) -> str:
        """
        Build structure information for complex object fields.
        
        Generates prompt text describing the expected object structure
        including nested fields from ALL variants with their descriptions and prompts.
        """
        parts = []
        
        if language == "de":
            parts.append("\n📋 OBJEKTSTRUKTUR - Gib ein Array von Objekten mit folgender Struktur zurück:")
        else:
            parts.append("\n📋 OBJECT STRUCTURE - Return an array of objects with the following structure:")
        
        # Get discriminator field name (e.g., "@type")
        discriminator = items.get("discriminator", "")
        
        # Get variants (different object types)
        variants = items.get("variants", [])
        
        for vi, variant in enumerate(variants):
            variant_type = variant.get("@type", "Object")
            variant_label = self._get_localized(variant.get("label", {}), language)
            variant_desc = self._get_localized(variant.get("description", {}), language)
            
            # Skip empty variants (like PostalAddress with no fields)
            variant_fields = variant.get("fields", [])
            if not variant_fields and variant_type != "Default":
                continue
            
            if len(variants) > 1 and variant_type != "Default":
                if language == "de":
                    parts.append(f"\n--- Variante {vi + 1}: {variant_label} ({discriminator}: {variant_type}) ---")
                else:
                    parts.append(f"\n--- Variant {vi + 1}: {variant_label} ({discriminator}: {variant_type}) ---")
                if variant_desc:
                    parts.append(f"  {variant_desc}")
            elif variant_type != "Default":
                if language == "de":
                    parts.append(f"\nTyp: {variant_label} ({discriminator}: {variant_type})")
                else:
                    parts.append(f"\nType: {variant_label} ({discriminator}: {variant_type})")
            
            if language == "de":
                parts.append("Felder:")
            else:
                parts.append("Fields:")
            
            # Add fields from variant with descriptions and prompts
            for field in variant_fields:
                self._append_field_info(parts, field, language, indent=1)
        
        # Build a dynamic example from first variant
        if variants:
            example = self._build_dynamic_example(variants, discriminator)
            if example:
                if language == "de":
                    parts.append("\nBeispiel-Ausgabe:")
                else:
                    parts.append("\nExample output:")
                parts.append(f"```json\n{json.dumps({'value': [example]}, indent=2, ensure_ascii=False)}\n```")
        
        return "\n".join(parts)
    
    def _append_field_info(
        self,
        parts: list[str],
        field: dict[str, Any],
        language: str,
        indent: int = 1,
    ) -> None:
        """Append field info including description and prompt to parts list."""
        prefix = "  " * indent
        field_id = field.get("id", "")
        field_label = self._get_localized(field.get("label", {}), language)
        field_desc = self._get_localized(field.get("description", {}), language)
        field_prompt = self._get_localized(field.get("prompt", {}), language)
        field_system = field.get("system", {})
        field_datatype = field_system.get("datatype", "string")
        
        # Check for nested fields (e.g., address with streetAddress, postalCode)
        nested_fields = field.get("fields", [])
        if nested_fields:
            desc_suffix = f" — {field_desc}" if field_desc else ""
            parts.append(f"{prefix}- {field_id}: ({field_label}, Objekt){desc_suffix}")
            if field_prompt:
                parts.append(f"{prefix}  💡 {field_prompt}")
            for nested in nested_fields:
                self._append_field_info(parts, nested, language, indent=indent + 1)
        else:
            desc_suffix = f" — {field_desc}" if field_desc else ""
            parts.append(f"{prefix}- {field_id}: {field_label} ({field_datatype}){desc_suffix}")
            if field_prompt:
                parts.append(f"{prefix}  💡 {field_prompt}")
    
    def _build_dynamic_example(
        self,
        variants: list[dict[str, Any]],
        discriminator: str,
    ) -> Optional[dict[str, Any]]:
        """Build a dynamic example object from the first non-empty variant."""
        for variant in variants:
            variant_type = variant.get("@type", "")
            variant_fields = variant.get("fields", [])
            if not variant_fields:
                continue
            
            example = {}
            if variant_type and variant_type != "Default" and discriminator:
                example[discriminator] = variant_type
            
            for field in variant_fields:
                field_id = field.get("id", "")
                nested = field.get("fields", [])
                if nested:
                    sub_example = {}
                    for nf in nested:
                        sub_example[nf.get("id", "")] = "..."
                    example[field_id] = sub_example
                else:
                    example[field_id] = "..."
            
            return example
        
        return None
    
    def _parse_json_response(self, content: str) -> Optional[dict[str, Any]]:
        """Parse JSON from LLM response, handling various formats."""
        try:
            json_str = content.strip()
            
            # Remove markdown code blocks if present
            code_block_match = re.search(r'```json\s*([\s\S]*?)\s*```', json_str)
            if code_block_match:
                json_str = code_block_match.group(1).strip()
            
            # Try to parse directly
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass
            
            # Try to extract JSON patterns
            array_match = re.search(r'\[[\s\S]*\]', json_str)
            object_match = re.search(r'\{[\s\S]*\}', json_str)
            
            if object_match:
                return json.loads(object_match.group(0))
            if array_match:
                return {"value": json.loads(array_match.group(0))}
            
            # Try to extract string value
            string_match = re.search(r'"([^"]+)"', json_str)
            if string_match:
                return {"value": string_match.group(1)}
            
            # Check for null
            if json_str.lower() == 'null':
                return {"value": None}
            
            # Check for number
            try:
                return {"value": float(json_str) if '.' in json_str else int(json_str)}
            except ValueError:
                pass
            
            return None
            
        except Exception as e:
            print(f"❌ JSON parse error: {e}")
            return None
    
    def _normalize_value(self, value: Any, field: dict[str, Any]) -> Any:
        """
        Normalize extracted value based on field schema.
        Handles: booleans, numbers, dates, datetimes, times, URLs, 
        geo coordinates, vocabulary matching.
        """
        if value is None:
            return None
        
        system = field.get("system", {})
        datatype = system.get("datatype", "string")
        vocabulary = system.get("vocabulary", {})
        field_id = field.get("id", "")
        
        # Handle arrays
        if isinstance(value, list):
            return [self._normalize_single_value(v, datatype, vocabulary, field_id) for v in value]
        
        return self._normalize_single_value(value, datatype, vocabulary, field_id)
    
    def _normalize_single_value(
        self, 
        value: Any, 
        datatype: str, 
        vocabulary: dict,
        field_id: str = ""
    ) -> Any:
        """Normalize a single value."""
        if value is None:
            return None
        
        # Boolean normalization
        if datatype == "boolean":
            return self._normalize_boolean(value)
        
        # Number normalization
        if datatype in ("number", "integer"):
            return self._normalize_number(value, datatype)
        
        # Date normalization
        if datatype == "date":
            return self._normalize_date(value)
        
        # Datetime normalization
        if datatype == "datetime":
            return self._normalize_datetime(value)
        
        # Time normalization
        if datatype == "time":
            return self._normalize_time(value)
        
        # URL normalization
        if datatype in ("uri", "url"):
            return self._normalize_url(value)
        
        # Geo coordinate validation
        if "latitude" in field_id.lower() or "longitude" in field_id.lower():
            return self._normalize_geo_coordinate(value, field_id)
        
        # Vocabulary validation
        if vocabulary and vocabulary.get("concepts"):
            return self._validate_vocabulary(value, vocabulary)
        
        return value
    
    def _normalize_boolean(self, value: Any) -> Optional[bool]:
        """Normalize boolean values."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            val = value.lower().strip()
            if val in ("ja", "yes", "wahr", "true", "1"):
                return True
            if val in ("nein", "no", "falsch", "false", "0"):
                return False
        if isinstance(value, (int, float)):
            return value != 0
        return None
    
    def _normalize_number(self, value: Any, datatype: str) -> Optional[float]:
        """Normalize number values including complex German number words."""
        if isinstance(value, (int, float)):
            return int(value) if datatype == "integer" else value
        
        if isinstance(value, str):
            val = value.lower().strip()
            
            # Try to parse complex German number words
            parsed = self._parse_german_number(val)
            if parsed is not None:
                return int(parsed) if datatype == "integer" else parsed
            
            # Try direct numeric parsing
            try:
                num = float(val.replace(',', '.').replace(' ', ''))
                return int(num) if datatype == "integer" else num
            except ValueError:
                pass
        
        return None
    
    def _parse_german_number(self, text: str) -> Optional[int]:
        """Parse complex German number words like 'dreihundertvierundsiebzig'."""
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
        
        text = text.strip().lower()
        
        # Direct match for simple numbers
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
        
        result = 0
        remaining = text
        
        # Handle thousands (e.g., "zweitausend", "eintausend")
        if 'tausend' in remaining:
            parts = remaining.split('tausend', 1)
            prefix = parts[0].strip()
            if prefix == '' or prefix == 'ein':
                result += 1000
            elif prefix in ones:
                result += ones[prefix] * 1000
            elif prefix in teens:
                result += teens[prefix] * 1000
            elif prefix in tens:
                result += tens[prefix] * 1000
            else:
                # Try compound (e.g., "einundzwanzigtausend")
                compound = self._parse_compound_under_100(prefix)
                if compound is not None:
                    result += compound * 1000
            remaining = parts[1].strip() if len(parts) > 1 else ''
        
        # Handle hundreds (e.g., "dreihundert")
        if 'hundert' in remaining:
            parts = remaining.split('hundert', 1)
            prefix = parts[0].strip()
            if prefix == '' or prefix == 'ein':
                result += 100
            elif prefix in ones:
                result += ones[prefix] * 100
            remaining = parts[1].strip() if len(parts) > 1 else ''
        
        # Handle remaining (under 100)
        if remaining:
            under_100 = self._parse_compound_under_100(remaining)
            if under_100 is not None:
                result += under_100
        
        return result if result > 0 or text == 'null' else None
    
    def _parse_compound_under_100(self, text: str) -> Optional[int]:
        """Parse compound numbers under 100 like 'vierundsiebzig'."""
        ones = {
            'ein': 1, 'eins': 1, 'zwei': 2, 'drei': 3, 'vier': 4,
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
        
        text = text.strip()
        
        # Direct matches
        if text in ones:
            return ones[text]
        if text in teens:
            return teens[text]
        if text in tens:
            return tens[text]
        
        # Compound format: "einundXX" or "XundXX"
        if 'und' in text:
            parts = text.split('und', 1)
            ones_part = parts[0].strip()
            tens_part = parts[1].strip()
            
            if ones_part in ones and tens_part in tens:
                return ones[ones_part] + tens[tens_part]
        
        return None
    
    def _normalize_date(self, value: Any) -> Optional[str]:
        """Normalize date to ISO format (YYYY-MM-DD) with extended German support."""
        if not isinstance(value, str):
            return None
        
        val = value.strip()
        
        # Already ISO format (date only)
        if re.match(r'^\d{4}-\d{2}-\d{2}$', val):
            return val
        
        # ISO datetime - extract date part
        match = re.match(r'^(\d{4}-\d{2}-\d{2})T', val)
        if match:
            return match.group(1)
        
        # DD.MM.YYYY or D.M.YYYY (German)
        match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$', val)
        if match:
            return self._validate_and_format_date(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            )
        
        # DD.MM.YY (2-digit year)
        match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{2})$', val)
        if match:
            year = int(match.group(3))
            year = 2000 + year if year < 50 else 1900 + year
            return self._validate_and_format_date(
                int(match.group(1)), int(match.group(2)), year
            )
        
        # DD/MM/YYYY
        match = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', val)
        if match:
            return self._validate_and_format_date(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            )
        
        # DD-MM-YYYY
        match = re.match(r'^(\d{1,2})-(\d{1,2})-(\d{4})$', val)
        if match:
            return self._validate_and_format_date(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            )
        
        # German month names: "15. September 2026" or "15 September 2026"
        german_months = {
            'januar': 1, 'jan': 1, 'februar': 2, 'feb': 2, 'märz': 3, 'mär': 3, 'mar': 3,
            'april': 4, 'apr': 4, 'mai': 5, 'juni': 6, 'jun': 6,
            'juli': 7, 'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'sept': 9,
            'oktober': 10, 'okt': 10, 'november': 11, 'nov': 11, 'dezember': 12, 'dez': 12
        }
        
        # Pattern: "15. September 2026" or "15 Sep 2026"
        match = re.match(r'^(\d{1,2})\.?\s*([a-zäöü]+)\s+(\d{4})$', val, re.IGNORECASE)
        if match:
            day = int(match.group(1))
            month_name = match.group(2).lower()
            year = int(match.group(3))
            if month_name in german_months:
                return self._validate_and_format_date(day, german_months[month_name], year)
        
        # English pattern: "September 15, 2026"
        match = re.match(r'^([a-z]+)\s+(\d{1,2}),?\s*(\d{4})$', val, re.IGNORECASE)
        if match:
            month_name = match.group(1).lower()
            day = int(match.group(2))
            year = int(match.group(3))
            if month_name in german_months:
                return self._validate_and_format_date(day, german_months[month_name], year)
        
        return val  # Return as-is if parsing fails
    
    def _validate_and_format_date(self, day: int, month: int, year: int) -> Optional[str]:
        """Validate date components and format as ISO."""
        if not (1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100):
            return None
        
        # Additional validation - check actual day count for month
        days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        # Leap year
        if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
            days_in_month[1] = 29
        
        if day > days_in_month[month - 1]:
            return None
        
        return f"{year}-{month:02d}-{day:02d}"
    
    def _normalize_datetime(self, value: Any) -> Optional[str]:
        """Normalize datetime to ISO 8601 format (YYYY-MM-DDTHH:MM:SS)."""
        if not isinstance(value, str):
            return None
        
        val = value.strip()
        
        # Already ISO datetime format
        if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?', val):
            # Ensure seconds are present
            if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$', val):
                return val + ":00"
            return val
        
        # Date only - add midnight
        if re.match(r'^\d{4}-\d{2}-\d{2}$', val):
            return val + "T00:00:00"
        
        # German format: "15.03.2025 14:30" or "15.03.2025 14:30:00"
        match = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$', val)
        if match:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            hour, minute = int(match.group(4)), int(match.group(5))
            second = int(match.group(6)) if match.group(6) else 0
            
            if 1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{year}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"
        
        return val
    
    def _normalize_time(self, value: Any) -> Optional[str]:
        """Normalize time to HH:MM:SS format."""
        if not isinstance(value, str):
            return None
        
        val = value.strip()
        
        # Already correct format HH:MM:SS
        if re.match(r'^\d{2}:\d{2}:\d{2}$', val):
            return val
        
        # HH:MM format - add seconds
        match = re.match(r'^(\d{1,2}):(\d{2})$', val)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}:00"
        
        # H:MM format
        match = re.match(r'^(\d{1}):(\d{2})$', val)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            if 0 <= hour <= 9 and 0 <= minute <= 59:
                return f"0{hour}:{minute:02d}:00"
        
        # German: "14 Uhr 30" or "14:30 Uhr"
        match = re.match(r'^(\d{1,2})\s*[Uu]hr\s*(\d{2})?$', val)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)) if match.group(2) else 0
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}:00"
        
        return val
    
    def _normalize_geo_coordinate(self, value: Any, field_id: str) -> Optional[float]:
        """Normalize and validate geo coordinates (latitude/longitude)."""
        # Already a number
        if isinstance(value, (int, float)):
            return self._validate_geo_range(float(value), field_id)
        
        if isinstance(value, str):
            val = value.strip()
            
            # Handle German decimal separator
            val = val.replace(',', '.')
            
            # Remove degree symbols
            val = re.sub(r'[°º]', '', val)
            
            try:
                num = float(val)
                return self._validate_geo_range(num, field_id)
            except ValueError:
                pass
        
        return None
    
    def _validate_geo_range(self, value: float, field_id: str) -> Optional[float]:
        """Validate geo coordinate range and round to 7 decimal places."""
        is_latitude = 'latitude' in field_id.lower() or 'lat' in field_id.lower()
        is_longitude = 'longitude' in field_id.lower() or 'lon' in field_id.lower() or 'lng' in field_id.lower()
        
        if is_latitude:
            if value < -90 or value > 90:
                return None
        elif is_longitude:
            if value < -180 or value > 180:
                return None
        
        # Round to 7 decimal places (~1cm precision)
        return round(value, 7)
    
    def _normalize_url(self, value: Any) -> Optional[str]:
        """Normalize URL, adding protocol if missing."""
        if not isinstance(value, str):
            return None
        
        val = value.strip()
        
        # Already has protocol
        if re.match(r'^https?://', val, re.IGNORECASE):
            return val
        
        # Add https:// if looks like URL
        if re.match(r'^[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,}', val):
            return f"https://{val}"
        
        return val
    
    def _validate_vocabulary(self, value: Any, vocabulary: dict) -> Any:
        """Validate value against vocabulary, return URI or original value."""
        concepts = vocabulary.get("concepts", [])
        vocab_type = vocabulary.get("type", "open")
        is_closed = vocab_type in ("closed", "skos")
        
        if not concepts:
            return value
        
        value_lower = str(value).lower().strip()
        
        # Check if already a valid URI
        uri_match = next((c for c in concepts if c.get("uri") == value), None)
        if uri_match:
            return value
        
        # Find by label (exact match)
        for concept in concepts:
            label = self._get_localized(concept.get("label", {}), "de")
            if label.lower() == value_lower:
                return concept.get("uri", label)
            
            # Check alt labels
            alt_labels = concept.get("altLabels", [])
            for alt in alt_labels:
                if alt.lower() == value_lower:
                    return concept.get("uri", label)
        
        # For closed vocabulary, try fuzzy matching
        if is_closed:
            best_match = self._fuzzy_match_vocabulary(value_lower, concepts)
            if best_match:
                return best_match
            return None  # Invalid value for closed vocabulary
        
        return value  # Open vocabulary: keep original
    
    def _fuzzy_match_vocabulary(self, value: str, concepts: list) -> Optional[str]:
        """Fuzzy match value against vocabulary concepts."""
        best_match = None
        best_distance = float('inf')
        
        for concept in concepts:
            label = self._get_localized(concept.get("label", {}), "de").lower()
            distance = self._levenshtein_distance(value, label)
            
            if distance < best_distance:
                best_distance = distance
                best_match = concept
        
        # Accept if distance is small (max 30% of string length)
        max_distance = min(3, max(1, int(len(value) * 0.3)))
        if best_match and best_distance <= max_distance:
            return best_match.get("uri", self._get_localized(best_match.get("label", {}), "de"))
        
        return None
    
    def _levenshtein_distance(self, a: str, b: str) -> int:
        """Calculate Levenshtein distance between two strings."""
        return levenshtein_distance(a, b)
    
    async def detect_content_type(
        self,
        text: str,
        content_types: list[dict[str, Any]],
        language: str = "de",
    ) -> str:
        """Detect the content type from text."""
        if not content_types:
            return "learning_material.json"
        
        # Truncate text for content type detection — only first ~5000 chars needed
        detection_text = text[:5000] if len(text) > 5000 else text
        
        # Build options list
        options = []
        for ct in content_types:
            label = self._get_localized(ct.get("label", {}), language)
            schema_file = ct.get("schema_file", "")
            options.append(f"- {label}: {schema_file}")
        
        if language == "de":
            prompt = f"""Analysiere folgenden Text und bestimme den passenden Inhaltstyp:

--- TEXT ---
{detection_text}
--- ENDE ---

Verfügbare Inhaltstypen:
{chr(10).join(options)}

Antworte mit JSON: {{"content_type": "<schema_file>"}}"""
        else:
            prompt = f"""Analyze the following text and determine the appropriate content type:

--- TEXT ---
{detection_text}
--- END ---

Available content types:
{chr(10).join(options)}

Respond with JSON: {{"content_type": "<schema_file>"}}"""
        
        try:
            response = await self._call_llm(
                messages=[
                    {"role": "system", "content": self._get_system_prompt(language)},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=100,
            )
            
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            result = self._parse_json_response(content)
            return result.get("content_type", "learning_material.json") if result else "learning_material.json"
            
        except Exception:
            return "learning_material.json"


# Singleton instance (default config)
_llm_service: Optional[LLMService] = None


def get_llm_service(
    llm_provider: Optional[str] = None, 
    llm_model: Optional[str] = None
) -> LLMService:
    """
    Get LLM service instance.
    
    If provider or model overrides are specified, creates a new instance.
    Otherwise returns the cached default instance.
    """
    global _llm_service
    
    # If overrides specified, create new instance with those settings
    if llm_provider is not None or llm_model is not None:
        return LLMService(llm_provider=llm_provider, llm_model=llm_model)
    
    # Return cached default instance
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
