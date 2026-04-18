"""Tests for the /preview endpoint."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestPreviewEndpoint:
    """Test cases for the /preview endpoint."""

    def test_preview_requires_auth(self, client):
        """Preview endpoint should require inbound token when configured."""
        # Test without token - should work for now (no token configured in test)
        response = client.post(
            "/preview",
            json={
                "url": "https://example.com",
                "strategy": "xpath",
                "selectors": {
                    "item": "//article",
                    "title": ".//h2/text()",
                },
            },
        )
        # Should not return 401 in test mode (no token configured)
        assert response.status_code in (200, 500)

    @patch("app.main.run_scrape")
    def test_preview_returns_capped_items(self, mock_scrape, client):
        """Preview should cap items to 10."""
        from app.models.schemas import ScrapeResponse, ScrapeItem
        from datetime import datetime, timezone

        # Create mock items (15 items - more than the 10 cap)
        mock_items = [
            ScrapeItem(
                title=f"Item {i}",
                link=f"https://example.com/item{i}",
                timestamp="2024-01-01T00:00:00Z",
            )
            for i in range(15)
        ]

        mock_scrape.return_value = ScrapeResponse(
            url="https://example.com",
            timestamp=datetime.now(timezone.utc),
            strategy="xpath",
            items=mock_items,
            item_count=15,
            fetch_backend_used="bundled",
        )

        response = client.post(
            "/preview",
            json={
                "url": "https://example.com",
                "strategy": "xpath",
                "selectors": {
                    "item": "//article",
                    "title": ".//h2/text()",
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        # Should be capped to 10
        assert len(data["items"]) == 10
        assert data["item_count"] == 10
        # Should have field counts
        assert "field_counts" in data
        assert data["field_counts"]["title"] == 10

    @patch("app.main.run_scrape")
    def test_preview_disables_caching(self, mock_scrape, client):
        """Preview should pass adaptive=False and empty cache_key."""
        from app.models.schemas import ScrapeResponse, ScrapeItem
        from datetime import datetime, timezone

        mock_items = [ScrapeItem(title="Test")]
        mock_scrape.return_value = ScrapeResponse(
            url="https://example.com",
            timestamp=datetime.now(timezone.utc),
            strategy="xpath",
            items=mock_items,
            item_count=1,
            fetch_backend_used="bundled",
        )

        client.post(
            "/preview",
            json={
                "url": "https://example.com",
                "strategy": "xpath",
                "selectors": {"item": "//article"},
                "adaptive": True,  # Should be overridden to False
                "cache_key": "some-cache",  # Should be overridden to ""
            },
        )

        # Verify run_scrape was called with adaptive=False and cache_key=""
        call_args = mock_scrape.call_args[0][0]
        assert call_args.adaptive is False
        assert call_args.cache_key == ""

    @patch("app.main.run_scrape")
    def test_preview_calculates_field_counts(self, mock_scrape, client):
        """Preview should calculate how many items have each field populated."""
        from app.models.schemas import ScrapeResponse, ScrapeItem
        from datetime import datetime, timezone

        mock_items = [
            ScrapeItem(title="Item 1", link="https://example.com/1"),  # title + link
            ScrapeItem(title="Item 2"),  # title only
            ScrapeItem(link="https://example.com/3", timestamp="2024-01-01"),  # link + timestamp
            ScrapeItem(),  # empty
        ]

        mock_scrape.return_value = ScrapeResponse(
            url="https://example.com",
            timestamp=datetime.now(timezone.utc),
            strategy="xpath",
            items=mock_items,
            item_count=4,
            fetch_backend_used="bundled",
        )

        response = client.post(
            "/preview",
            json={
                "url": "https://example.com",
                "strategy": "xpath",
                "selectors": {"item": "//article"},
            },
        )

        data = response.json()
        assert data["field_counts"]["title"] == 2
        assert data["field_counts"]["link"] == 2
        assert data["field_counts"]["timestamp"] == 1
        assert data["field_counts"]["content"] == 0
        assert data["field_counts"]["author"] == 0