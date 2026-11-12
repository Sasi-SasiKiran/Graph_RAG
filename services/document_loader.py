import hashlib
import re
from pathlib import Path

import fitz
from bs4 import BeautifulSoup

from models.schemas import DocumentSegment, LoadedDocument

SUPPORTED_EXTENSIONS = {".pdf", ".html", ".htm", ".txt", ".md", ".markdown"}
HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def _clean_text(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _markdown_segments(text: str) -> list[DocumentSegment]:
    groups: list[tuple[str | None, list[str]]] = []
    current_section: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            groups.append((current_section, [body]))

    for line in text.splitlines():
        heading = HEADING_PATTERN.match(line)
        if heading:
            flush()
            current_section = heading.group(1).strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    flush()
    return [
        DocumentSegment(text=body[0], section=section)
        for section, body in groups
        if _clean_text(body[0])
    ]


def _html_segments(soup: BeautifulSoup) -> list[DocumentSegment]:
    body = soup.body or soup
    headings = {f"h{level}" for level in range(1, 7)}
    block_names = headings | {"p", "li", "pre", "blockquote", "td", "th"}
    section: str | None = None
    groups: list[tuple[str | None, list[str]]] = []
    for element in body.find_all(list(block_names)):
        if any(parent.name in block_names for parent in element.parents if parent is not body):
            continue
        text = element.get_text(" ", strip=True)
        if element.name in headings:
            if text:
                section = text
                groups.append((section, [text]))
        elif text:
            if groups and groups[-1][0] == section:
                groups[-1][1].append(text)
            else:
                groups.append((section, [text]))

    return [
        DocumentSegment(text="\n".join(texts), section=section_name)
        for section_name, texts in groups
        if _clean_text("\n".join(texts))
    ]


def load_document(filename: str, content: bytes) -> LoadedDocument:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported document type: {extension or 'no extension'}.")
    if not content:
        raise ValueError("The uploaded file is empty.")

    page_count = 0
    if extension == ".pdf":
        try:
            with fitz.open(stream=content, filetype="pdf") as pdf:
                page_count = pdf.page_count
                segments = [
                    DocumentSegment(text=page_text, page_number=page_number)
                    for page_number, page in enumerate(pdf, start=1)
                    if (page_text := page.get_text("text", sort=True).strip())
                ]
        except (fitz.FileDataError, RuntimeError) as exc:
            raise ValueError(f"Could not read PDF: {exc}") from exc
        if not segments:
            raise ValueError(
                "This PDF contains no extractable text. It may be scanned or "
                "image-only; OCR the PDF before uploading it."
            )
        original_text = "\n\n".join(segment.text for segment in segments)
        media_type = "application/pdf"
    elif extension in {".html", ".htm"}:
        try:
            original_text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("HTML files must use UTF-8 encoding.") from exc
        soup = BeautifulSoup(content, "html.parser")
        for element in soup(["script", "style", "noscript"]):
            element.decompose()
        segments = _html_segments(soup)
        if not segments:
            extracted = soup.get_text(separator="\n", strip=True)
            if extracted:
                segments = [DocumentSegment(text=extracted)]
        media_type = "text/html"
    else:
        try:
            original_text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Text files must use UTF-8 encoding.") from exc
        if extension in {".md", ".markdown"}:
            segments = _markdown_segments(original_text)
        else:
            segments = [DocumentSegment(text=original_text)]
        media_type = "text/markdown" if extension in {".md", ".markdown"} else "text/plain"

    segments = [
        segment
        for segment in segments
        if _clean_text(segment.text)
    ]
    text = "\n\n".join(_clean_text(segment.text) for segment in segments)
    if not text:
        raise ValueError("No readable text was found in the file.")

    source_id = hashlib.sha256(content).hexdigest()
    return LoadedDocument(
        id=source_id,
        source_id=source_id,
        title=Path(filename).name,
        text=text,
        original_text=original_text,
        media_type=media_type,
        page_count=page_count,
        segments=segments,
    )
