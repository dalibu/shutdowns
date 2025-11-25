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
        with patch('dtek.parser.dtek_parser.run_parser_service_botasaurus') as mock_botasaurus:
            mock_botasaurus.return_value = {
                "data": {
                    "city": "TestValue",
                    "group": "1"
                }
            }
            
            result = await run_parser_service("City", "Street", "1")
            
            assert "data" in result
            assert result["data"]["city"] == "TestValue"
            assert result["data"]["group"] == "1"
            mock_botasaurus.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_parser_service_error(self):
        """Test parser error handling"""
        with patch('dtek.parser.dtek_parser.run_parser_service_botasaurus') as mock_botasaurus:
            mock_botasaurus.side_effect = Exception("Browser Error")
            
            with pytest.raises(Exception):
                await run_parser_service("City", "Street", "1")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])