"""
Tests for DTEK Bot implementation (Functional)
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from dtek.bot.bot import (
    get_shutdowns_data,
    send_schedule_response
)

@pytest.mark.unit
class TestDtekBotFunctions:
    """Tests for DTEK bot functions"""

    @pytest.mark.asyncio
    async def test_get_shutdowns_data_success(self):
        """Test fetching shutdowns data successfully"""
        mock_response = {
            "data": {
                "city": "Дніпро",
                "schedule": {}
            }
        }
        
        # Mock local factory
        with patch('dtek.bot.bot.get_data_source') as mock_get_source:
            mock_source = AsyncMock()
            mock_source.get_schedule.return_value = mock_response["data"]
            mock_get_source.return_value = mock_source
            
            result = await get_shutdowns_data("Дніпро", "Сонячна", "6")
            
            assert result == mock_response["data"]
            mock_get_source.assert_called_once()
            mock_source.get_schedule.assert_called_with("Дніпро", "Сонячна", "6")

    @pytest.mark.asyncio
    async def test_get_shutdowns_data_error(self):
        """Test fetching shutdowns data with error"""
        with patch('dtek.bot.bot.get_data_source') as mock_get_source:
            mock_source = AsyncMock()
            mock_source.get_schedule.side_effect = Exception("API Error")
            mock_get_source.return_value = mock_source
            
            with pytest.raises(ValueError):
                await get_shutdowns_data("Дніпро", "Сонячна", "6")

    @pytest.mark.asyncio
    async def test_send_schedule_response_48h(self):
        """Test sending 48h schedule response (shutdowns tomorrow)"""
        message = AsyncMock()
        api_data = {
            "city": "Дніпро",
            "street": "Сонячна",
            "house_num": "6",
            "group": "1",
            "schedule": {
                "12.11.24": [{"shutdown": "10:00–12:00"}],
                "13.11.24": [{"shutdown": "10:00–12:00"}] # Shutdown tomorrow
            }
        }
        
        with patch('dtek.bot.bot.generate_48h_schedule_image') as mock_48h, \
             patch('dtek.bot.bot.generate_24h_schedule_image') as mock_24h:
            mock_48h.return_value = b"fake_48h_image"
            mock_24h.return_value = b"fake_24h_image"
            
            await send_schedule_response(message, api_data, is_subscribed=False)
            
            # Should use 48h image
            mock_48h.assert_called_once()
            mock_24h.assert_not_called()
            
            # Verify caption
            args, _ = message.answer.call_args_list[1] # 0 is header, 1 is caption (if image sent)
            assert "48 годин" in args[0]
            message.answer_photo.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_schedule_response_24h(self):
        """Test sending 24h schedule response (NO shutdowns tomorrow)"""
        message = AsyncMock()
        api_data = {
            "city": "Дніпро",
            "street": "Сонячна",
            "house_num": "6",
            "group": "1",
            "schedule": {
                "12.11.24": [{"shutdown": "10:00–12:00"}],
                "13.11.24": [] # No shutdowns tomorrow
            }
        }
        
        with patch('dtek.bot.bot.generate_48h_schedule_image') as mock_48h, \
             patch('dtek.bot.bot.generate_24h_schedule_image') as mock_24h:
            mock_48h.return_value = b"fake_48h_image"
            mock_24h.return_value = b"fake_24h_image"
            
            await send_schedule_response(message, api_data, is_subscribed=False)
            
            # Should use 24h image
            mock_48h.assert_not_called()
            mock_24h.assert_called_once()
            
            # Verify caption
            args, _ = message.answer.call_args_list[1]
            assert "сьогодні" in args[0]
            message.answer_photo.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
