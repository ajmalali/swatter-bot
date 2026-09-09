"""Text to vectors. fastembed locally by default, or any OpenAI-compatible embeddings endpoint."""

from __future__ import annotations

from typing import Protocol

import numpy as np

from swatter.config import Settings


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) float32 array of unit-normalised vectors."""
        ...


class FastEmbedEmbedder:
    def __init__(self, model: str) -> None:
        from fastembed import TextEmbedding  # heavy import, keep it lazy

        self._model = TextEmbedding(model_name=model)

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.asarray(list(self._model.embed(texts)), dtype=np.float32)
        return _normalise(vectors)


class EndpointEmbedder:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            base_url=base_url, api_key=api_key or "none", default_headers=extra_headers or None
        )
        self._model = model

    def embed(self, texts: list[str]) -> np.ndarray:
        response = self._client.embeddings.create(model=self._model, input=texts)
        vectors = np.asarray([d.embedding for d in response.data], dtype=np.float32)
        return _normalise(vectors)


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "endpoint":
        assert settings.embedding_base_url  # validated in Settings
        return EndpointEmbedder(
            settings.embedding_base_url,
            settings.embedding_api_key,
            settings.embedding_model,
            settings.embedding_extra_headers,
        )
    return FastEmbedEmbedder(settings.embedding_model)
