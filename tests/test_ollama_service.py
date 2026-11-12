import unittest

from services.ollama_service import strip_thinking


class OllamaServiceTests(unittest.TestCase):
    def test_strips_qwen_thinking_before_final_answer(self):
        response = (
            "Let me analyze the evidence.\n</think>\n"
            "Acme is based in Riverside."
        )

        self.assertEqual(strip_thinking(response), "Acme is based in Riverside.")

    def test_preserves_responses_without_thinking_markers(self):
        self.assertEqual(strip_thinking("A concise answer."), "A concise answer.")


if __name__ == "__main__":
    unittest.main()
