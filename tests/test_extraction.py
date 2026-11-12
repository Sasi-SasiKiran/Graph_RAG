import unittest

from pydantic import ValidationError

from models.schemas import ExtractionResult
from services.entity_extractor import EntityExtractor, validate_extraction
from services.ollama_service import OllamaServiceError


class FakeOllama:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, prompt, system_prompt, format_schema=None):
        self.calls.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "format_schema": format_schema,
            }
        )
        return self.responses.pop(0)


class ExtractionTests(unittest.TestCase):
    def test_normalizes_duplicate_entities_and_relationships(self):
        result = validate_extraction(
            """
            {
              "entities": [
                {"name": "Acme", "type": "Organization"},
                {"name": " acme ", "type": "Company"},
                {"name": "Riverside", "type": "Location"}
              ],
              "relationships": [
                {"source": "Acme", "target": "Riverside", "relationship": "LOCATED_IN"},
                {"source": " acme ", "target": "Riverside", "relationship": "located_in"}
              ]
            }
            """
        )

        self.assertEqual(len(result.entities), 2)
        self.assertEqual(len(result.relationships), 1)
        self.assertEqual(result.entities[0].kind, "Organization")
        self.assertEqual(result.relationships[0].predicate, "LOCATED_IN")

    def test_rejects_relationships_with_unknown_endpoints(self):
        with self.assertRaises(ValidationError):
            validate_extraction(
                """
                {
                  "entities": [{"name": "Acme", "type": "Organization"}],
                  "relationships": [
                    {"source": "Acme", "target": "Unknown", "relationship": "KNOWS"}
                  ]
                }
                """
            )

    def test_rejects_extra_fields_and_blank_entity_types(self):
        with self.assertRaises(ValidationError):
            validate_extraction(
                '{"entities":[{"name":"Acme","type":" ","extra":"value"}],'
                '"relationships":[]}'
            )

    def test_requires_both_entity_and_relationship_arrays(self):
        with self.assertRaises(ValidationError):
            validate_extraction('{"entities":[]}')

    def test_requires_the_schema_field_names_not_legacy_aliases(self):
        with self.assertRaises(ValidationError):
            validate_extraction(
                '{"entities":[{"name":"Acme","kind":"Organization"}],'
                '"relationships":[]}'
            )

    def test_schema_uses_the_expected_json_property_names(self):
        schema = ExtractionResult.model_json_schema()

        self.assertIn("type", schema["$defs"]["Entity"]["properties"])
        self.assertIn(
            "relationship",
            schema["$defs"]["Relation"]["properties"],
        )
        self.assertIn("entities", schema["required"])
        self.assertIn("relationships", schema["required"])
        self.assertIn("type", schema["$defs"]["Entity"]["required"])

    def test_retries_malformed_json_then_accepts_valid_json(self):
        valid = (
            '{"entities":[{"name":"Acme","type":"Organization"}],'
            '"relationships":[]}'
        )
        ollama = FakeOllama(["not JSON", valid])

        result = EntityExtractor(ollama).extract("Acme is an organization.")

        self.assertEqual(result.entities[0].name, "Acme")
        self.assertEqual(len(ollama.calls), 2)
        self.assertIsNotNone(ollama.calls[0]["format_schema"])
        self.assertIn("previous response was invalid", ollama.calls[1]["prompt"])

    def test_retries_relationships_with_unknown_endpoints(self):
        invalid = (
            '{"entities":[{"name":"Acme","type":"Organization"}],'
            '"relationships":[{"source":"Acme","target":"Unknown",'
            '"relationship":"KNOWS"}]}'
        )
        valid = (
            '{"entities":[{"name":"Acme","type":"Organization"},'
            '{"name":"Riverside","type":"Location"}],'
            '"relationships":[{"source":"Acme","target":"Riverside",'
            '"relationship":"LOCATED_IN"}]}'
        )
        ollama = FakeOllama([invalid, valid])

        result = EntityExtractor(ollama).extract("Acme is in Riverside.")

        self.assertEqual(len(result.relationships), 1)
        self.assertIn("relationship endpoint", ollama.calls[1]["prompt"])

    def test_retries_empty_response_and_fails_after_bounded_attempts(self):
        ollama = FakeOllama(["", "", ""])

        with self.assertRaisesRegex(OllamaServiceError, "after 3 attempts"):
            EntityExtractor(ollama).extract("No entities here.")

        self.assertEqual(len(ollama.calls), 3)

    def test_rejects_invalid_attempt_count(self):
        with self.assertRaises(ValueError):
            EntityExtractor(FakeOllama([]), max_attempts=4)


if __name__ == "__main__":
    unittest.main()
