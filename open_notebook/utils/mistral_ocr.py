"""
Mistral OCR fallback for scanned documents.

content-core extracts the text layer of a PDF; a scan or phone photo exported as
PDF has none, so extraction returns empty content. When a Mistral credential is
configured, ``ocr_file`` sends the file to Mistral's OCR endpoint and returns
the recognised text as Markdown. It never raises: any failure returns ``None``
so the caller keeps its normal "no text content" error.
"""

import base64
import mimetypes
import os
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from open_notebook.ai.key_provider import get_api_key

OCR_URL = "https://api.mistral.ai/v1/ocr"
OCR_MODEL = "mistral-ocr-latest"
# Mistral caps documents at 50 MB / 1000 pages; base64 adds a third on top.
MAX_FILE_BYTES = 50 * 1024 * 1024
TIMEOUT_SECONDS = 300

_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/avif"}


def _document_payload(file_path: str, data: bytes) -> Optional[dict]:
    mime = mimetypes.guess_type(file_path)[0] or ""
    if data[:5] == b"%PDF-":
        mime = "application/pdf"
    encoded = base64.b64encode(data).decode("ascii")
    if mime == "application/pdf":
        return {
            "type": "document_url",
            "document_url": f"data:{mime};base64,{encoded}",
        }
    if mime in _IMAGE_TYPES:
        return {"type": "image_url", "image_url": f"data:{mime};base64,{encoded}"}
    return None


def _pages_to_text(body: dict) -> str:
    pages = body.get("pages") or []
    return "\n\n".join(
        (page.get("markdown") or "").strip() for page in pages if isinstance(page, dict)
    ).strip()


async def ocr_file(file_path: str) -> Optional[str]:
    """OCR ``file_path`` (PDF or image) with Mistral; ``None`` if unavailable or failed."""
    api_key = await get_api_key("mistral")
    if not api_key:
        logger.info("Mistral OCR skipped: no Mistral credential configured")
        return None

    try:
        size = os.path.getsize(file_path)
        if size > MAX_FILE_BYTES:
            logger.warning(f"Mistral OCR skipped: {file_path} is {size} bytes (>50 MB)")
            return None
        data = Path(file_path).read_bytes()
    except OSError as e:
        logger.warning(f"Mistral OCR skipped: cannot read {file_path}: {e}")
        return None

    document = _document_payload(file_path, data)
    if document is None:
        logger.info(f"Mistral OCR skipped: unsupported file type for {file_path}")
        return None

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                OCR_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": OCR_MODEL, "document": document},
            )
        response.raise_for_status()
        text = _pages_to_text(response.json())
    except httpx.HTTPStatusError as e:
        # Log status only: the response body can echo request details.
        logger.warning(f"Mistral OCR failed: HTTP {e.response.status_code}")
        return None
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"Mistral OCR failed ({type(e).__name__}): {e}")
        return None

    if not text:
        logger.info("Mistral OCR returned no text")
        return None
    logger.info(f"Mistral OCR recognised {len(text)} characters in {file_path}")
    return text
