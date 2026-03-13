"""Tests for deterministic shopping search checks."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from shopping_eval.checks import check_link_loads


@pytest.mark.asyncio
async def test_link_loads_success():
    """HEAD 200 → True."""
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("shopping_eval.checks.httpx.AsyncClient", return_value=mock_client):
        result = await check_link_loads("https://example.com/product")
    assert result is True


@pytest.mark.asyncio
async def test_link_loads_404():
    """HEAD 404 → False."""
    mock_resp = AsyncMock()
    mock_resp.status_code = 404
    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("shopping_eval.checks.httpx.AsyncClient", return_value=mock_client):
        result = await check_link_loads("https://example.com/gone")
    assert result is False


@pytest.mark.asyncio
async def test_link_loads_timeout():
    """Timeout → False."""
    import httpx as httpx_mod

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(side_effect=httpx_mod.TimeoutException("timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("shopping_eval.checks.httpx.AsyncClient", return_value=mock_client):
        result = await check_link_loads("https://example.com/slow")
    assert result is False


@pytest.mark.asyncio
async def test_link_loads_empty_url():
    """Empty URL → False."""
    result = await check_link_loads("")
    assert result is False
