"""
Step 5 of the worker: split page text into overlapping chunks, each prefixed
with a header so retrieval AND citation know where it came from.

Sizes are in words, a close-enough proxy for tokens (about 0.75 words per token).
~450 words is roughly 600 tokens, inside the 500-800 token range we chose.
"""

from dataclasses import dataclass

CHUNK_WORDS = 450
OVERLAP_WORDS = 60


@dataclass
class Chunk:
    index: int
    page: int | None
    content: str


def chunk_pages(pages, doc_number: str, revision: str, title: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    step = CHUNK_WORDS - OVERLAP_WORDS
    for page, text in pages:
        words = text.split()
        if not words:
            continue
        for start in range(0, len(words), step):
            window = words[start:start + CHUNK_WORDS]
            page_label = f"p.{page}" if page else "no page"
            header = f"[{doc_number} Rev {revision} | {title} | {page_label}]"
            chunks.append(Chunk(len(chunks), page, header + "\n" + " ".join(window)))
            if start + CHUNK_WORDS >= len(words):
                break                                   # last window reached the end
    return chunks
