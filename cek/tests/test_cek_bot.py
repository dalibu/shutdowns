"""
Tests for CEK Bot implementation (Functional)
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from cek.bot.bot import (
    get_shutdowns_data,
    send_schedule_response
)

@pytest.mark.unit
class TestCekBotFunctions:
    """Tests for CEK bot functions"""

    @pytest.mark.asyncio
    async def test_get_shutdowns_data_success(self):
        """Test fetching shutdowns data successfully"""
        mock_response = {
            "data": {
                "city": "Дніпро",
                "schedule": {}
            }
        }
        
        # Mock cek_parser
        with patch('cek.bot.bot.cek_parser', new=AsyncMock()) as mock_parser:
            mock_parser.return_value = mock_response
            
            result = await get_shutdowns_data("Дніпро", "Сонячна", "6")
            
            assert result == mock_response["data"]
            mock_parser.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_shutdowns_data_with_cache(self):
        """Test fetching shutdowns data with cached group"""
        mock_response = {
            "data": {
                "city": "Дніпро",
                "schedule": {}
            }
        }
        
        with patch('cek.bot.bot.cek_parser', new=AsyncMock()) as mock_parser:
            mock_parser.return_value = mock_response
            
            await get_shutdowns_data("Дніпро", "Сонячна", "6", cached_group="1.1")
            
            # Verify cached_group was passed to parser
            mock_parser.assert_called_with(
                city="Дніпро", street="Сонячна", house="6", 
                is_debug=False, cached_group="1.1"
            )

    @pytest.mark.asyncio
    async def test_get_shutdowns_data_error(self):
        """Test fetching shutdowns data with error"""
        with patch('cek.bot.bot.cek_parser', new=AsyncMock()) as mock_parser:
            mock_parser.side_effect = Exception("API Error")
            
            with pytest.raises(ValueError):
                await get_shutdowns_data("Дніпро", "Сонячна", "6")

    @pytest.mark.asyncio
    async def test_send_schedule_response(self):
        """Test sending schedule response"""
        message = AsyncMock()
        api_data = {
            "city": "Дніпро",
            "street": "Сонячна",
            "house_num": "6",
            "group": "1",
            "schedule": {
                "12.11.24": [{"shutdown": "10:00–12:00"}]
            }
        }
        
        with patch('cek.bot.bot.generate_24h_schedule_image') as mock_img:
            mock_img.return_value = b"fake_image_bytes"
            
            await send_schedule_response(message, api_data, is_subscribed=False)
            
            # Should send header, image, text, status, footer
            assert message.answer.call_count >= 4
            # Check if image was sent
            message.answer_photo.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
