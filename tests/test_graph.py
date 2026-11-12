import unittest

from models.schemas import Chunk, Entity, ExtractionResult, LoadedDocument, Relation
from services.graph_service import (
    AWAIT_INDEXES_QUERY,
    CHUNK_SEARCH_QUERY,
    ENTITY_CHUNK_QUERY,
    ENTITY_SEARCH_QUERY,
    FACT_QUERY,
    GRAPH_FACT_SEARCH_QUERY,
    SCHEMA_STATEMENTS,
    GraphService,
    build_ingestion_payload,
    search_terms,
)


class GraphServiceTests(unittest.TestCase):
    def setUp(self):
        self.document = LoadedDocument(
            id="doc-id",
            source_id="doc-id",
            title="notes.txt",
            text="Acme is based in Riverside.",
            original_text="Acme is based in Riverside.",
            media_type="text/plain",
            page_count=0,
        )
        self.chunk = Chunk(
            id="doc-id:0",
            source_id="doc-id",
            index=0,
            text=self.document.text,
            original_text=self.document.text,
        )

    def test_ingestion_payload_links_entities_and_relation_to_chunk(self):
        extraction = ExtractionResult(
            entities=[
                Entity(name="Acme", type="Organization"),
                Entity(name="Riverside", type="Location"),
            ],
            relationships=[
                Relation(
                    source="Acme",
                    target="Riverside",
                    relationship="based in",
                )
            ],
        )

        payload = build_ingestion_payload(
            self.document,
            [self.chunk],
            [extraction],
        )

        self.assertEqual(len(payload["mentions"]), 2)
        self.assertEqual(len(payload["facts"]), 1)
        fact = payload["facts"][0]
        self.assertEqual(fact["chunk_id"], self.chunk.id)
        self.assertEqual(fact["relationship"], "based in")
        self.assertEqual(payload["source_id"], self.document.source_id)
        self.assertEqual(payload["original_text"], self.document.original_text)
        self.assertEqual(payload["chunks"][0]["original_text"], self.chunk.original_text)
        self.assertEqual(fact["evidence"], self.chunk.original_text)
        self.assertEqual(fact["filename"], self.document.title)

    def test_entity_identity_is_stable_across_name_case_changes(self):
        first = build_ingestion_payload(
            self.document,
            [self.chunk],
            [
                ExtractionResult(
                    entities=[Entity(name="Acme", type="Organization")],
                    relationships=[],
                )
            ],
        )
        second = build_ingestion_payload(
            self.document,
            [self.chunk],
            [
                ExtractionResult(
                    entities=[Entity(name=" ACME ", type="Company")],
                    relationships=[],
                )
            ],
        )

        self.assertEqual(first["mentions"][0]["id"], second["mentions"][0]["id"])

    def test_source_record_identity_is_scoped_to_workspace_generation(self):
        first = GraphService.source_record_id("workspace", "generation-a", "source")
        repeated = GraphService.source_record_id("workspace", "generation-a", "source")
        replaced = GraphService.source_record_id("workspace", "generation-b", "source")

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, replaced)

        payload = build_ingestion_payload(
            self.document,
            [self.chunk],
            [ExtractionResult(entities=[], relationships=[])],
            workspace_id="workspace",
            generation_id="generation-a",
        )
        self.assertEqual(payload["workspace_id"], "workspace")
        self.assertEqual(payload["generation_id"], "generation-a")
        self.assertEqual(
            payload["document_id"],
            GraphService.source_record_id(
                "workspace",
                "generation-a",
                self.document.source_id,
            ),
        )

    def test_facts_use_only_fixed_relationship_types_and_are_provenanced(self):
        self.assertIn("MERGE (c)-[:SUPPORTS]->(f)", FACT_QUERY)
        self.assertIn("MERGE (f)-[:SUBJECT]->(subject)", FACT_QUERY)
        self.assertIn("MERGE (f)-[:OBJECT]->(object)", FACT_QUERY)
        self.assertIn("f.relationship = item.relationship", FACT_QUERY)
        self.assertNotIn("$relationship", FACT_QUERY)
        self.assertNotIn("RELATED_TO", FACT_QUERY)

        self.assertTrue(any("CREATE CONSTRAINT" in query for query in SCHEMA_STATEMENTS))
        self.assertTrue(any("CREATE INDEX" in query for query in SCHEMA_STATEMENTS))
        self.assertTrue(
            any("FOR (f:Fact) REQUIRE f.id IS UNIQUE" in query for query in SCHEMA_STATEMENTS)
        )
        self.assertTrue(
            any("CREATE FULLTEXT INDEX chunk_text_fulltext_index" in query for query in SCHEMA_STATEMENTS)
        )
        self.assertTrue(
            any("source_workspace_generation_index" in query for query in SCHEMA_STATEMENTS)
        )

    def test_schema_setup_executes_constraints_and_indexes(self):
        class FakeResult:
            def consume(self):
                return None

        class FakeDriver:
            def __init__(self):
                self.statements = []

            def session(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return None

            def run(self, statement):
                self.statements.append(statement)
                return FakeResult()

        class FakeConnection:
            def __init__(self, driver):
                self.driver = driver

        driver = FakeDriver()
        GraphService(FakeConnection(driver)).ensure_schema()

        self.assertEqual(
            driver.statements,
            [*SCHEMA_STATEMENTS, AWAIT_INDEXES_QUERY],
        )

    def test_search_uses_fulltext_entities_traversal_and_original_chunks(self):
        def chunk_row(chunk_id, text, relationships, entities):
            return {
                "chunk_id": chunk_id,
                "source": "notes.txt",
                "source_id": "source-id",
                "chunk_index": int(chunk_id[-1]),
                "page_number": None,
                "section": None,
                "text": text.lower(),
                "original_text": text,
                "entities": entities,
                "relationships": relationships,
                "score": 0.5,
            }

        graph_fact = {
            "source": "Acme",
            "predicate": "LOCATED_IN",
            "target": "Riverside",
        }

        class FakeSession:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return None

            def run(self, statement, **parameters):
                self.calls.append((statement, parameters))
                if statement == ENTITY_SEARCH_QUERY:
                    return [{"id": "entity-id", "score": 0.8}]
                if statement == CHUNK_SEARCH_QUERY:
                    return [
                        chunk_row("chunk-0", "Direct original passage.", [], [])
                    ]
                if statement == ENTITY_CHUNK_QUERY:
                    return [
                        {
                            **chunk_row(
                                "chunk-1",
                                "Mentioned original passage.",
                                [graph_fact],
                                ["Acme"],
                            ),
                            "matched_id": "entity-id",
                        }
                    ]
                if statement == GRAPH_FACT_SEARCH_QUERY:
                    return [
                        {
                            **chunk_row(
                                "chunk-2",
                                "Related original passage.",
                                [graph_fact],
                                ["Acme", "Riverside"],
                            ),
                            "matched_id": "entity-id",
                        }
                    ]
                raise AssertionError(f"Unexpected query: {statement}")

        class FakeDriver:
            def __init__(self):
                self.fake_session = FakeSession()

            def session(self):
                return self.fake_session

        class FakeConnection:
            def __init__(self):
                self.driver = FakeDriver()

        connection = FakeConnection()
        results = GraphService(connection).search(
            'Acme") OR *:*',
            generation_id="generation-a",
        )

        self.assertEqual(
            [item["chunk_id"] for item in results],
            ["chunk-2", "chunk-1", "chunk-0"],
        )
        self.assertEqual(results[0]["text"], "Related original passage.")
        self.assertEqual(results[0]["relationships"], [graph_fact])
        calls = connection.driver.fake_session.calls
        self.assertEqual(
            [query for query, _ in calls],
            [
                ENTITY_SEARCH_QUERY,
                CHUNK_SEARCH_QUERY,
                ENTITY_CHUNK_QUERY,
                GRAPH_FACT_SEARCH_QUERY,
            ],
        )
        self.assertEqual(calls[0][1]["search_query"], '"acme" OR "or"')
        for _, parameters in calls:
            self.assertEqual(parameters["workspace_id"], "LOCAL_GRAPHRAG")
            self.assertEqual(parameters["generation_id"], "generation-a")
        self.assertIn("$workspace_id", ENTITY_SEARCH_QUERY)
        self.assertIn("$generation_id", CHUNK_SEARCH_QUERY)

    def test_ingestion_requires_an_extraction_for_each_chunk(self):
        with self.assertRaises(ValueError):
            build_ingestion_payload(self.document, [self.chunk], [])

    def test_search_terms_remove_common_words_and_duplicates(self):
        self.assertEqual(search_terms("What does Acme do with Acme?"), ["acme"])

    def test_source_deletion_is_generation_scoped_and_only_prunes_its_entities(self):
        class FakeResult:
            def single(self):
                return {"deleted": 1, "entity_ids": ["entity-id"]}

            def consume(self):
                return None

        class FakeSession:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return None

            def run(self, statement, **parameters):
                self.calls.append((statement, parameters))
                return FakeResult()

        class FakeConnection:
            def __init__(self):
                self.fake_session = FakeSession()

            def session(self):
                return self.fake_session

        connection = FakeConnection()
        deleted = GraphService(connection, workspace_id="workspace").delete_source(
            "source-record-id",
            "generation-id",
        )

        self.assertTrue(deleted)
        deletion_query, deletion_params = connection.fake_session.calls[0]
        self.assertIn("$workspace_id", deletion_query)
        self.assertEqual(deletion_params["workspace_id"], "workspace")
        self.assertEqual(deletion_params["generation_id"], "generation-id")
        prune_query, prune_params = connection.fake_session.calls[1]
        self.assertIn("$entity_ids", prune_query)
        self.assertEqual(prune_params["entity_ids"], ["entity-id"])


if __name__ == "__main__":
    unittest.main()
