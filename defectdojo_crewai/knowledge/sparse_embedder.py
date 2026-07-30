"""Sparse embedding client for TEI's BGE-M3 /embed_sparse endpoint.

Only used when HYBRID_SEARCH_ENABLED=True and EMBEDDING_PROVIDER=tei.
The TEI sparse endpoint lives at the server root (not under /v1).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger(__name__)

# TEI's sparse endpoint returns at most this many tokens per input;
# reasonable default from the BGE-M3 vocabulary.
DEFAULT_SPARSE_MAX_TOKENS = 256

_SPARSE_PATH = "/embed_sparse"


class SparseEmbedder:
    """Call TEI /embed_sparse and return Qdrant-compatible sparse vectors."""

    def __init__(
        self,
        *,
        tei_base_url: str,
        api_key: str = "",
        timeout_seconds: float = 30,
        max_tokens: int = DEFAULT_SPARSE_MAX_TOKENS,
    ) -> None:
        # Derive the server root from the possibly-/v1-prefixed base URL.
        parsed = urlparse(tei_base_url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        self._endpoint = urljoin(root, _SPARSE_PATH)
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._session = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=[429, 502, 503, 504],
            allowed_methods=["POST"],
        )
        self._session.mount("http://", HTTPAdapter(max_retries=retry))
        self._session.mount("https://", HTTPAdapter(max_retries=retry))

    def embed(self, texts: list[str]) -> list[dict[str, Any]]:
        """Return a list of Qdrant-compatible sparse dicts for *texts*.

        Each dict has ``indices`` (list[int]) and ``values`` (list[float]).
        """
        if not texts:
            return []

        results: list[dict[str, Any]] = []
        batch_size = max(1, min(32, len(texts)))
        for offset in range(0, len(texts), batch_size):
            batch = texts[offset : offset + batch_size]
            results.extend(self._embed_batch(batch))
        return results

    # ------------------------------------------------------------------
    def _embed_batch(self, batch: list[str]) -> list[dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload: dict[str, Any] = {
            "inputs": batch,
            "truncate": True,
        }
        if self._max_tokens:
            payload["max_tokens"] = self._max_tokens

        try:
            response = self._session.post(
                self._endpoint,
                json=payload,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            raw: list[dict[str, Any]] = response.json()
        except Exception:
            LOGGER.exception(
                "Sparse embedding request failed for %d texts to %s",
                len(batch),
                self._endpoint,
            )
            raise

        if len(raw) != len(batch):
            raise ValueError(
                f"TEI /embed_sparse returned {len(raw)} vectors for "
                f"{len(batch)} inputs."
            )

        sparse_vectors: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError(
                    f"Unexpected sparse vector format: {type(item).__name__}"
                )
            sparse_vectors.append(
                {
                    "indices": list(item.get("indices") or []),
                    "values": list(item.get("values") or []),
                }
            )
        return sparse_vectors
