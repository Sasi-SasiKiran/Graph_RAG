import unittest
from unittest.mock import patch

from database.neo4j_connection import Neo4jConnection


class Neo4jConnectionTests(unittest.TestCase):
    @patch("database.neo4j_connection.GraphDatabase.driver")
    def test_blank_password_uses_local_no_auth_mode(self, create_driver):
        Neo4jConnection("bolt://127.0.0.1:7687", "neo4j", "")

        create_driver.assert_called_once_with(
            "bolt://127.0.0.1:7687",
            auth=None,
        )

    @patch("database.neo4j_connection.GraphDatabase.driver")
    def test_configured_password_is_passed_to_the_driver(self, create_driver):
        Neo4jConnection("bolt://127.0.0.1:7687", "neo4j", "configured-value")

        create_driver.assert_called_once_with(
            "bolt://127.0.0.1:7687",
            auth=("neo4j", "configured-value"),
        )


if __name__ == "__main__":
    unittest.main()
