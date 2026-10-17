"""Mistral OCR fallback for scanned PDFs (no text layer)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from content_core.common import ExtractionOutput

from open_notebook.graphs import source as source_graph
from open_notebook.utils import mistral_ocr

PDF_BYTES = b"%PDF-1.3\n%scan"


def _client_factory(handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real(transport=httpx.MockTransport(handler), **kwargs)

    return factory


async def _ocr(tmp_path, handler, *, key="sk-test", name="scan.pdf", data=PDF_BYTES):
    path = tmp_path / name
    path.write_bytes(data)
    with (
        patch.object(mistral_ocr, "get_api_key", new=AsyncMock(return_value=key)),
        patch.object(mistral_ocr.httpx, "AsyncClient", _client_factory(handler)),
    ):
        return await mistral_ocr.ocr_file(str(path))


@pytest.mark.asyncio
async def test_pdf_pages_are_joined_as_markdown(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read().decode()
        return httpx.Response(
            200, json={"pages": [{"markdown": "# Seite 1"}, {"markdown": "Seite 2"}]}
        )

    text = await _ocr(tmp_path, handler)

    assert text == "# Seite 1\n\nSeite 2"
    assert seen["auth"] == "Bearer sk-test"
    assert '"document_url"' in seen["body"]
    assert "data:application/pdf;base64," in seen["body"]
    assert "mistral-ocr-latest" in seen["body"]


@pytest.mark.asyncio
async def test_image_is_sent_as_image_url(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"pages": [{"markdown": "Foto"}]})

    text = await _ocr(tmp_path, handler, name="foto.png", data=b"\x89PNG\r\n")

    assert text == "Foto"
    assert '"image_url"' in seen["body"]
    assert "data:image/png;base64," in seen["body"]


@pytest.mark.asyncio
async def test_no_mistral_credential_skips_the_call(tmp_path):
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("no request expected")

    assert await _ocr(tmp_path, handler, key=None) is None


@pytest.mark.asyncio
async def test_unsupported_type_skips_the_call(tmp_path):
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("no request expected")

    assert await _ocr(tmp_path, handler, name="x.bin", data=b"abc") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"message": "Unauthorized sk-test"}),
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"pages": []}),
        httpx.Response(200, json={"pages": [{"markdown": "  "}]}),
    ],
)
async def test_failures_return_none(tmp_path, response):
    assert await _ocr(tmp_path, lambda request: response) is None


@pytest.mark.asyncio
async def test_network_error_returns_none(tmp_path):
    def handler(request):
        raise httpx.ConnectError("down")

    assert await _ocr(tmp_path, handler) is None


async def _run_graph(extract, ocr):
    settings = SimpleNamespace(
        youtube_preferred_languages=None,
        default_content_processing_engine_url=None,
        default_content_processing_engine_doc=None,
        docling_ocr=None,
        docling_formulas=None,
        docling_vision=None,
    )
    with (
        patch.object(
            source_graph.ContentSettings,
            "get_instance",
            new=AsyncMock(return_value=settings),
        ),
        patch.object(
            source_graph.ModelManager,
            "get_defaults",
            new=AsyncMock(
                return_value=SimpleNamespace(default_speech_to_text_model=None)
            ),
        ),
        patch.object(source_graph, "extract_content", new=extract),
        patch.object(source_graph, "ocr_file", new=ocr),
    ):
        return await source_graph.content_process(
            {"content_state": {"file_path": "/app/data/uploads/scan.pdf"}}  # type: ignore[typeddict-item]
        )


@pytest.mark.asyncio
async def test_empty_extraction_falls_back_to_ocr():
    empty = ExtractionOutput(content="", title="scan")
    ocr = AsyncMock(return_value="erkannter Text")

    result = await _run_graph(AsyncMock(return_value=empty), ocr)

    assert result["extraction"].content == "erkannter Text"
    assert result["extraction"].title == "scan"
    ocr.assert_awaited_once_with("/app/data/uploads/scan.pdf")


@pytest.mark.asyncio
async def test_text_pdf_never_calls_ocr():
    full = ExtractionOutput(content="Text vorhanden", title="t")
    ocr = AsyncMock(return_value="unused")

    result = await _run_graph(AsyncMock(return_value=full), ocr)

    assert result["extraction"].content == "Text vorhanden"
    ocr.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_after_failed_ocr_keeps_the_original_error():
    empty = ExtractionOutput(content="", title="")

    with pytest.raises(ValueError, match="Could not extract any text content"):
        await _run_graph(AsyncMock(return_value=empty), AsyncMock(return_value=None))
