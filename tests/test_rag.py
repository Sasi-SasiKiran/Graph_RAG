import unittest

from models.schemas import AnswerResult
from services.rag_service import RagService


class FakeGraph:
    def __init__(self, results):
        self.results = results

    def search(self, question):
        return self.results


class FakeOllama:
    def __init__(self):
        self.prompt = None

    def chat(self, prompt, system_prompt):
        self.prompt = prompt
        return "Acme is based in Riverside. [source: notes.txt, chunk 0]"


class RagServiceTests(unittest.TestCase):
    def test_answer_includes_graph_sources_and_relationships(self):
        graph = FakeGraph(
            [
                {
                    "source": "notes.txt",
                    "source_id": "notes-source-id",
                    "chunk_index": 0,
                    "page_number": 2,
                    "section": None,
                    "text": "Acme is based in Riverside.",
                    "entities": ["Acme", "Riverside"],
                    "relationships": [
                        {
                            "source": "Acme",
                            "predicate": "based in",
                            "target": "Riverside",
                        }
                    ],
                    "score": 3,
                }
            ]
        )
        ollama = FakeOllama()

        result = RagService(graph, ollama).ask("Where is Acme based?")

        self.assertIsInstance(result, AnswerResult)
        self.assertEqual(result.sources[0].title, "notes.txt")
        self.assertEqual(result.sources[0].source_id, "notes-source-id")
        self.assertEqual(result.sources[0].page_number, 2)
        self.assertEqual(result.relationships[0]["predicate"], "based in")
        self.assertIn("[source: notes.txt, chunk 0, page 2]", ollama.prompt)
        self.assertIn("Related graph facts retrieved from Neo4j", ollama.prompt)
        self.assertIn("Acme --based in--> Riverside", ollama.prompt)
        self.assertIn("Retrieved original document chunks", ollama.prompt)

    def test_no_evidence_does_not_call_ollama(self):
        ollama = FakeOllama()
        result = RagService(FakeGraph([]), ollama).ask("What is in this document?")

        self.assertEqual(result.sources, [])
        self.assertIsNone(ollama.prompt)

    def test_blank_question_is_rejected(self):
        with self.assertRaises(ValueError):
            RagService(FakeGraph([]), FakeOllama()).ask("   ")


if __name__ == "__main__":
    unittest.main()
