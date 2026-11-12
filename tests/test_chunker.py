import unittest

from models.schemas import DocumentSegment
from services.text_chunker import chunk_segments, chunk_text


class TextChunkerTests(unittest.TestCase):
    def test_chunks_respect_size_and_retain_overlapping_context(self):
        text = " ".join(f"word{index}" for index in range(80))
        chunks = chunk_text(text, chunk_size=80, overlap=15)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 80 for chunk in chunks))
        self.assertTrue(any(
            set(chunks[index].split()) & set(chunks[index + 1].split())
            for index in range(len(chunks) - 1)
        ))

    def test_blank_text_returns_no_chunks(self):
        self.assertEqual(chunk_text(" \n\t "), [])

    def test_overlap_must_be_smaller_than_chunk_size(self):
        with self.assertRaises(ValueError):
            chunk_text("some text", chunk_size=10, overlap=10)

    def test_chunks_prefer_sentence_boundaries(self):
        text = " ".join(
            f"Sentence {index} contains a complete fact." for index in range(8)
        )
        chunks = chunk_text(text, chunk_size=75, overlap=12)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.endswith(".") for chunk in chunks))

    def test_segment_chunking_preserves_source_page_section_and_original_text(self):
        segment = DocumentSegment(
            text="First sentence is here.\n\nSecond sentence is here.",
            page_number=3,
            section="Summary",
        )

        chunks = chunk_segments([segment], "source-id", chunk_size=30, overlap=8)

        self.assertGreater(len(chunks), 1)
        self.assertEqual(
            [chunk.id for chunk in chunks],
            [f"source-id:{index}" for index in range(len(chunks))],
        )
        self.assertTrue(all(chunk.source_id == "source-id" for chunk in chunks))
        self.assertTrue(all(chunk.page_number == 3 for chunk in chunks))
        self.assertTrue(all(chunk.section == "Summary" for chunk in chunks))
        self.assertTrue(all(chunk.original_text.strip() for chunk in chunks))
        self.assertTrue(
            all(" ".join(chunk.original_text.split()) == chunk.text for chunk in chunks)
        )

    def test_chunk_ids_can_be_scoped_to_a_workspace_generation(self):
        chunks = chunk_segments(
            [DocumentSegment(text="A sentence with useful content.")],
            "content-source-id",
            chunk_size=200,
            overlap=20,
            chunk_id_prefix="generation-source-id",
        )

        self.assertEqual(chunks[0].id, "generation-source-id:0")
        self.assertEqual(chunks[0].source_id, "content-source-id")


if __name__ == "__main__":
    unittest.main()
