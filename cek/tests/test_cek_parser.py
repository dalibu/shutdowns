"""
Tests for CEK Parser (Functional)
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from cek.parser.cek_parser import run_parser_service

@pytest.mark.unit
class TestCekParser:
    """Tests for CEK parser functions"""

    @pytest.mark.asyncio
    async def test_run_parser_service_success(self):
        """Test successful parser run"""
        with patch('cek.parser.cek_parser.async_playwright') as mock_playwright:
            mock_context = AsyncMock()
            mock_browser = AsyncMock()
            
            # Create page as MagicMock to allow sync methods like locator
            mock_page = MagicMock()
            mock_page.goto = AsyncMock()
            mock_page.click = AsyncMock()
            mock_page.fill = AsyncMock()
            mock_page.type = AsyncMock()
            mock_page.wait_for_selector = AsyncMock()
            mock_page.wait_for_timeout = AsyncMock()
            mock_page.close = AsyncMock()
            mock_page.select_option = AsyncMock()
            
            mock_playwright.return_value.__aenter__.return_value = mock_context
            mock_context.chromium.launch.return_value = mock_browser
            mock_browser.new_page.return_value = mock_page
            
            # Mock locator (synchronous)
            mock_locator = MagicMock()
            mock_page.locator.return_value = mock_locator
            
            # Mock locator methods
            mock_locator.first = MagicMock()
            mock_locator.first.click = AsyncMock()
            
            mock_locator.inner_text = AsyncMock(return_value="Черга 1.1")
            mock_locator.input_value = AsyncMock(return_value="TestValue")
            mock_locator.all = AsyncMock(return_value=[]) # No rows for simplicity
            
            # Handle chained calls
            mock_locator.first.click = AsyncMock()
            
            result = await run_parser_service("City", "Street", "1")
            
            assert "data" in result
            assert result["data"]["group"] == "1.1"

    @pytest.mark.asyncio
    async def test_run_parser_service_with_cached_group(self):
        """Test parser with cached group skipping step 1"""
        with patch('cek.parser.cek_parser.async_playwright') as mock_playwright:
            mock_context = AsyncMock()
            mock_browser = AsyncMock()
            mock_page = MagicMock()
            mock_page.goto = AsyncMock()
            mock_page.wait_for_timeout = AsyncMock()
            mock_page.select_option = AsyncMock()
            mock_page.fill = AsyncMock()
            mock_page.click = AsyncMock()
            mock_page.close = AsyncMock()
            
            mock_playwright.return_value.__aenter__.return_value = mock_context
            mock_context.chromium.launch.return_value = mock_browser
            mock_browser.new_page.return_value = mock_page
            
            mock_locator = MagicMock()
            mock_page.locator.return_value = mock_locator
            mock_locator.all = AsyncMock(return_value=[])
            
            result = await run_parser_service("City", "Street", "1", cached_group="1.1")
            
            assert result["data"]["group"] == "1.1"
            # Should NOT have called goto for group lookup URL (simplified check)
            # Actually it calls goto for schedule URL always.
            # But it shouldn't call goto for GROUP_LOOKUP_URL if cached_group is present.
            # We can check call args of goto.
            
            # GROUP_LOOKUP_URL is https://cek.dp.ua/index.php/cpojivaham/pobutovi-spozhyvachi/viznachennya-chergy.html
            # SCHEDULE_URL is https://cek.dp.ua/index.php/cpojivaham/vidkliuchennia/2-uncategorised/921-grafik-pogodinikh-vidklyuchen.html
            
            # Check that GROUP_LOOKUP_URL was NOT visited
            for call in mock_page.goto.call_args_list:
                args, _ = call
                assert "viznachennya-chergy.html" not in args[0]

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
