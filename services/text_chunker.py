import re

from models.schemas import Chunk, DocumentSegment

SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(text):
        if character.isspace():
            if normalized and normalized[-1] != " ":
                normalized.append(" ")
                offsets.append(index)
        else:
            normalized.append(character)
            offsets.append(index)
    if normalized and normalized[-1] == " ":
        normalized.pop()
        offsets.pop()
    return "".join(normalized), offsets


def _sentence_boundaries(text: str, start: int, end: int) -> list[int]:
    return [
        match.end()
        for match in SENTENCE_BOUNDARY.finditer(text, start, end)
        if start < match.end() <= end
    ]


def _choose_end(text: str, start: int, hard_end: int, chunk_size: int) -> int:
    if hard_end == len(text):
        return hard_end

    minimum = start + max(1, chunk_size // 2)
    sentence_ends = [
        boundary
        for boundary in _sentence_boundaries(text, start, hard_end)
        if minimum <= boundary <= hard_end
    ]
    if sentence_ends:
        return sentence_ends[-1]

    word_boundary = text.rfind(" ", minimum, hard_end)
    return word_boundary if word_boundary > start else hard_end


def _choose_next_start(text: str, start: int, end: int, overlap: int) -> int:
    target = max(start + 1, end - overlap)
    sentence_starts = [
        boundary
        for boundary in _sentence_boundaries(text, start, end)
        if start < boundary <= end
    ]
    if sentence_starts:
        return min(sentence_starts, key=lambda boundary: abs(boundary - target))

    word_boundary = text.rfind(" ", start, target)
    if word_boundary > start:
        return word_boundary + 1
    return target


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 150) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("Chunk size must be greater than zero.")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk overlap must be non-negative and smaller than chunk size.")

    normalized, _ = _normalize_with_offsets(text)
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = _choose_end(normalized, start, min(start + chunk_size, len(normalized)), chunk_size)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = _choose_next_start(normalized, start, end, overlap)

    return chunks


def chunk_segments(
    segments: list[DocumentSegment],
    source_id: str,
    chunk_size: int = 1200,
    overlap: int = 150,
    chunk_id_prefix: str | None = None,
) -> list[Chunk]:
    if chunk_size <= 0:
        raise ValueError("Chunk size must be greater than zero.")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk overlap must be non-negative and smaller than chunk size.")

    chunks: list[Chunk] = []
    for segment in segments:
        normalized, offsets = _normalize_with_offsets(segment.text)
        if not normalized:
            continue
        start = 0
        while start < len(normalized):
            end = _choose_end(
                normalized,
                start,
                min(start + chunk_size, len(normalized)),
                chunk_size,
            )
            text = normalized[start:end].strip()
            if text:
                raw_start = offsets[start]
                raw_end = offsets[end - 1] + 1
                chunks.append(
                    Chunk(
                        id=f"{chunk_id_prefix or source_id}:{len(chunks)}",
                        source_id=source_id,
                        index=len(chunks),
                        text=text,
                        original_text=segment.text[raw_start:raw_end],
                        page_number=segment.page_number,
                        section=segment.section,
                    )
                )
            if end >= len(normalized):
                break
            start = _choose_next_start(normalized, start, end, overlap)

    return chunks
