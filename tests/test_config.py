import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from config import Settings, load_settings


class SettingsTests(unittest.TestCase):
    def test_loads_requested_local_environment_variable_names(self):
        environment = {
            "NEO4J_URI": "bolt://127.0.0.1:7687",
            "NEO4J_USERNAME": "local-user",
            "NEO4J_PASSWORD": "local-secret",
            "NEO4J_DATABASE": "neo4j",
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
            "OLLAMA_MODEL": "qwen3:4b",
            "WORKSPACE": "test-workspace",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = load_settings()

        self.assertEqual(settings.neo4j_user, "local-user")
        self.assertEqual(settings.neo4j_database, "neo4j")
        self.assertEqual(settings.workspace, "test-workspace")
        self.assertEqual(settings.ollama_model, "qwen3:4b")
        self.assertEqual(settings.neo4j_password.get_secret_value(), "local-secret")

    def test_accepts_blank_password_to_report_authentication_status_at_runtime(self):
        settings = Settings(neo4j_password="")

        self.assertEqual(settings.neo4j_password.get_secret_value(), "")

    def test_rejects_non_local_service_addresses(self):
        with self.assertRaises(ValidationError):
            Settings(
                neo4j_password="test",
                ollama_base_url="http://example.com:11434",
            )


if __name__ == "__main__":
    unittest.main()
