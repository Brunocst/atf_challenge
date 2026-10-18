"""Policy retrieval: chunk, embed, cosine, threshold.

No vector database. At nine short documents a store adds a dependency and moves the
retrieval decision somewhere it cannot be read line by line. See README for the
production swap (Agent Search data store + VertexAiSearchTool) and why it is not
usable in this build.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from app import config


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    text: str
    effective_date: str | None
    status: str
    region: str
    supersedes: str | None = None

    def embed_text(self) -> str:
        """What actually gets embedded. The doc_id is included so a query naming a
        policy ('the price match rule') has something to latch onto; the section
        heading is included because it carries topic signal the body may not."""
        return f"{self.title} - {self.section}\n{self.text}"

    def as_dict(self, score: float | None = None) -> dict:
        out = {
            "doc_id": self.doc_id,
            "title": self.title,
            "section": self.section,
            "effective_date": self.effective_date,
            "text": self.text,
        }
        if score is not None:
            out["similarity"] = round(float(score), 4)
        return out


FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_front_matter(raw: str) -> tuple[dict, str]:
    """Minimal `key: value` front-matter parser. Deliberately not PyYAML: six keys,
    documented format, one less dependency."""
    match = FRONT_MATTER.match(raw)
    if not match:
        return {}, raw
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, raw[match.end():]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@lru_cache(maxsize=1)
def load_chunks() -> tuple[Chunk, ...]:
    """Read every doc in docs/ and split it at '## ' headings."""
    chunks: list[Chunk] = []
    for path in sorted(config.DOCS_DIR.glob("*.md")):
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
        doc_id = meta.get("doc_id", path.stem)
        title = meta.get("title", path.stem)
        # Anything before the first '## ' is the document preamble; keep it as a
        # chunk of its own so the top-of-document summary is retrievable.
        sections: list[tuple[str, str]] = []
        current_heading = title
        current_lines: list[str] = []
        for line in body.splitlines():
            if line.startswith("## "):
                if current_lines and "".join(current_lines).strip():
                    sections.append((current_heading, "\n".join(current_lines).strip()))
                current_heading = line[3:].strip()
                current_lines = []
            else:
                current_lines.append(line)
        if current_lines and "".join(current_lines).strip():
            sections.append((current_heading, "\n".join(current_lines).strip()))

        for heading, text in sections:
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}#{_slug(heading)}",
                    doc_id=doc_id,
                    title=title,
                    section=heading,
                    text=text,
                    effective_date=meta.get("effective_date"),
                    status=meta.get("status", "current"),
                    region=meta.get("region", "US"),
                    supersedes=meta.get("supersedes"),
                )
            )
    return tuple(chunks)


@lru_cache(maxsize=1)
def _client():
    import os

    from google import genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No GEMINI_API_KEY set. Copy .env.example to .env and add a key from "
            "https://aistudio.google.com/apikey"
        )
    return genai.Client(api_key=api_key)


def _embed(texts: list[str], task_type: str) -> np.ndarray:
    """Embed via the Gemini Developer API, caching doc vectors on disk.

    The cache is keyed by (model, content hash) so editing one policy doc only
    re-embeds that doc, and an eval run is not billed for re-embedding the corpus.
    """
    from google.genai import types

    vectors: list[list[float]] = []
    for text in texts:
        resp = _client().models.embed_content(
            model=config.EMBED_MODEL,
            contents=[text],
            config=types.EmbedContentConfig(task_type=task_type),
        )
        vectors.append(list(resp.embeddings[0].values))
    return np.asarray(vectors, dtype=np.float32)


def _corpus_matrix(chunks: tuple[Chunk, ...]) -> np.ndarray:
    texts = [c.embed_text() for c in chunks]
    digest = hashlib.sha256(
        (config.EMBED_MODEL + "\x00" + "\x00".join(texts)).encode("utf-8")
    ).hexdigest()
    if config.EMBEDDINGS_CACHE.exists():
        cached = json.loads(config.EMBEDDINGS_CACHE.read_text())
        if cached.get("digest") == digest:
            return np.asarray(cached["vectors"], dtype=np.float32)
    matrix = _embed(texts, task_type="RETRIEVAL_DOCUMENT")
    config.EMBEDDINGS_CACHE.write_text(
        json.dumps({"digest": digest, "model": config.EMBED_MODEL,
                    "vectors": matrix.tolist()})
    )
    return matrix


def _cosine_scores(query_vector: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q = query_vector / (np.linalg.norm(query_vector) + 1e-10)
    m = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10)
    return m @ q


def retrieve(query: str, k: int = config.TOP_K) -> list[tuple[Chunk, float]]:
    """Top-k current policy chunks for a query, highest similarity first.

    Superseded documents are filtered out here rather than left for the model to
    notice. That is a deliberate trade: a deterministic metadata filter is testable,
    whereas hoping an LLM spots `status: superseded` and prefers the newer date is
    not. The golden set covers the case either way.
    """
    chunks = tuple(c for c in load_chunks() if c.status != "superseded")
    if not chunks:
        return []
    matrix = _corpus_matrix(chunks)
    scores = _cosine_scores(_embed([query], task_type="RETRIEVAL_QUERY")[0], matrix)
    order = np.argsort(-scores)[:k]
    return [(chunks[i], float(scores[i])) for i in order]


# ---------------------------------------------------------------------------
# ADK tool
# ---------------------------------------------------------------------------


def search_policies(query: str) -> dict:
    """Search NovaMart's policy documents for text relevant to a question.

    Call this before answering anything about policy, and call it with the
    employee's own wording. Returns the most relevant sections with their doc_id,
    effective date and similarity score. Similarity below the configured threshold
    means nothing in the corpus covers the question - in that case do not answer
    from general knowledge, escalate instead.

    Args:
        query: The employee's question, or the policy topic to look up.

    Returns:
        A dict with 'status' ("ok" or "no_match"), 'matches' (list of policy
        sections with doc_id, effective_date and text), and 'threshold'.
    """
    if not query or not query.strip():
        return {"status": "no_match", "reason": "empty query", "matches": []}

    hits = retrieve(query)
    if not hits or hits[0][1] < config.MIN_SIMILARITY:
        return {
            "status": "no_match",
            "reason": "No policy section passed the relevance threshold.",
            "threshold": config.MIN_SIMILARITY,
            "best_similarity": round(hits[0][1], 4) if hits else None,
            "matches": [],
        }
    return {
        "status": "ok",
        "threshold": config.MIN_SIMILARITY,
        "matches": [chunk.as_dict(score) for chunk, score in hits],
    }
