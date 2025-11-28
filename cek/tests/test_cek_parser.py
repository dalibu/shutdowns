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
        with patch('cek.parser.cek_parser.run_parser_service_botasaurus') as mock_botasaurus:
            mock_botasaurus.return_value = {
                "data": {
                    "city": "TestValue",
                    "group": "1.1"
                }
            }
            
            result = await run_parser_service("City", "Street", "1")
            
            assert "data" in result
            assert result["data"]["group"] == "1.1"
            mock_botasaurus.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_parser_service_with_cached_group(self):
        """Test parser with cached group skipping step 1"""
        with patch('cek.parser.cek_parser.run_parser_service_botasaurus') as mock_botasaurus:
            mock_botasaurus.return_value = {
                "data": {
                    "city": "TestValue",
                    "group": "1.1"
                }
            }
            
            result = await run_parser_service("City", "Street", "1", cached_group="1.1")
            
            assert result["data"]["group"] == "1.1"
            mock_botasaurus.assert_called_once()
            # Verify cached_group was passed to botasaurus
            args, kwargs = mock_botasaurus.call_args
            assert args[0]['cached_group'] == "1.1"

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
