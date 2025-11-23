"""
Tests for DTEK Parser (Functional)
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from dtek.parser.dtek_parser import run_parser_service

@pytest.mark.unit
class TestDtekParser:
    """Tests for DTEK parser functions"""

    @pytest.mark.asyncio
    async def test_run_parser_service_success(self):
        """Test successful parser run"""
        # Mock playwright
        with patch('dtek.parser.dtek_parser.async_playwright') as mock_playwright:
            mock_context = AsyncMock()
            mock_browser = AsyncMock()
            
            # Create page as MagicMock to allow sync methods like locator
            mock_page = MagicMock()
            
            # Setup async methods on page
            mock_page.goto = AsyncMock()
            mock_page.click = AsyncMock()
            mock_page.fill = AsyncMock()
            mock_page.type = AsyncMock()
            mock_page.wait_for_selector = AsyncMock()
            mock_page.wait_for_timeout = AsyncMock()
            mock_page.close = AsyncMock()
            
            mock_playwright.return_value.__aenter__.return_value = mock_context
            mock_context.chromium.launch.return_value = mock_browser
            mock_browser.new_page.return_value = mock_page
            
            # Mock locator (synchronous)
            mock_locator = MagicMock()
            mock_page.locator.return_value = mock_locator
            
            # Mock locator methods
            mock_locator.first = MagicMock()
            mock_locator.first.click = AsyncMock()
            
            mock_locator.inner_text = AsyncMock(return_value="Черга 1")
            mock_locator.input_value = AsyncMock(return_value="TestValue")
            mock_locator.count = AsyncMock(return_value=0)
            
            # Handle chained calls like locator().first.click()
            mock_locator.first.click = AsyncMock()
            
            # Handle locator().nth(i).locator(...)
            mock_locator.nth.return_value.locator.return_value = mock_locator

            result = await run_parser_service("City", "Street", "1")
            
            assert "data" in result
            assert result["data"]["city"] == "TestValue"
            assert result["data"]["group"] == "1"

    @pytest.mark.asyncio
    async def test_run_parser_service_error(self):
        """Test parser error handling"""
        with patch('dtek.parser.dtek_parser.async_playwright') as mock_playwright:
            mock_playwright.side_effect = Exception("Browser Error")
            
            with pytest.raises(Exception):
                await run_parser_service("City", "Street", "1")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])