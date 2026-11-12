import unittest

import fitz

from services.document_loader import load_document


class DocumentLoaderTests(unittest.TestCase):
    def test_html_extraction_skips_scripts_and_styles(self):
        document = load_document(
            "page.html",
            b"<html><body><h1>Local facts</h1><script>hidden()</script>"
            b"<style>.hidden{display:none}</style><p>Useful text</p>"
            b"<h2>More facts</h2><p>Additional evidence</p></body></html>",
        )

        self.assertIn("Local facts", document.text)
        self.assertIn("Useful text", document.text)
        self.assertNotIn("hidden()", document.text)
        self.assertEqual(document.title, "page.html")
        self.assertIn("Local facts", document.original_text)
        self.assertEqual(
            [segment.section for segment in document.segments],
            ["Local facts", "More facts"],
        )

    def test_pdf_extraction_reads_page_text(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "A local PDF fact.")
        page = pdf.new_page()
        page.insert_text((72, 72), "A second page fact.")
        content = pdf.tobytes()
        pdf.close()

        document = load_document("notes.pdf", content)

        self.assertEqual(document.page_count, 2)
        self.assertIn("A local PDF fact.", document.text)
        self.assertEqual(
            [segment.page_number for segment in document.segments],
            [1, 2],
        )
        self.assertIn("A local PDF fact.", document.original_text)

    def test_scanned_pdf_without_extractable_text_is_rejected(self):
        pdf = fitz.open()
        pdf.new_page()
        content = pdf.tobytes()
        pdf.close()

        with self.assertRaisesRegex(ValueError, "no extractable text"):
            load_document("scan.pdf", content)

    def test_markdown_sections_are_preserved(self):
        document = load_document(
            "notes.md",
            b"# Overview\nAcme is local.\n\n## Details\nIt opened in 2024.",
        )

        self.assertEqual(
            [segment.section for segment in document.segments],
            ["Overview", "Details"],
        )
        self.assertIn("# Overview", document.original_text)

    def test_unsupported_extension_is_rejected(self):
        with self.assertRaises(ValueError):
            load_document("document.docx", b"content")


if __name__ == "__main__":
    unittest.main()
