"""Small SiliconFlow-compatible embeddings client."""

from __future__ import annotations

import numpy as np
import requests


class EmbeddingServiceError(RuntimeError):
    """Raised when an embedding request cannot produce a trustworthy batch."""


class EmbeddingClient:
    def __init__(
        self,
        api_url,
        api_key,
        model,
        use_system_proxy=False,
        timeout=60,
        session=None,
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.trust_env = use_system_proxy

    def embed(self, texts):
        if not texts:
            raise EmbeddingServiceError("Embedding input cannot be empty.")
        try:
            response = self.session.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": list(texts),
                    "encoding_format": "float",
                    "truncate": "right",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise EmbeddingServiceError(f"Embedding request failed: {exc}") from exc

        if response.status_code != 200:
            detail = response.text[:300].strip()
            raise EmbeddingServiceError(
                f"Embedding API returned HTTP {response.status_code}: {detail}"
            )

        try:
            items = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = np.asarray([item["embedding"] for item in items], dtype=np.float32)
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingServiceError("Embedding API returned an invalid payload.") from exc

        if len(vectors) != len(texts):
            raise EmbeddingServiceError(
                f"Embedding API returned {len(vectors)} vectors for {len(texts)} inputs."
            )
        if vectors.ndim != 2 or vectors.shape[1] == 0:
            raise EmbeddingServiceError("Embedding API returned an invalid vector shape.")

        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if np.any(norms == 0):
            raise EmbeddingServiceError("Embedding API returned a zero-length vector.")
        return vectors / norms
