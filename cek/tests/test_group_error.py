# -*- coding: utf-8 -*-
"""Tests for handling group determination errors in CEK and DTEK bots.
Ensures that when the parser cannot determine a group for an address,
the bot raises a user‑friendly ValueError with the correct message.
"""

import pytest
from unittest.mock import patch

# Import the async function to test
from cek.bot.bot import get_shutdowns_data as cek_get_shutdowns_data
from dtek.bot.bot import get_shutdowns_data as dtek_get_shutdowns_data

# Sample address that triggers the error in the parser
CITY = "м. Павлоград"
STREET = "вул. Нова"
HOUSE = "7"

@pytest.mark.asyncio
async def test_cek_group_determination_error():
    """CEK bot should raise a ValueError with a clear message when group cannot be determined."""
    from unittest.mock import AsyncMock
    # Mock local factory
    with patch("cek.bot.bot.get_data_source") as mock_get_source:
        mock_source = AsyncMock()
        mock_source.get_schedule.side_effect = Exception(f"Could not determine group for address {CITY}, {STREET}, {HOUSE}")
        mock_get_source.return_value = mock_source
        
        with pytest.raises(ValueError) as excinfo:
            await cek_get_shutdowns_data(CITY, STREET, HOUSE)
        # The error message should be user‑friendly and not contain the raw parser traceback
        assert "Не вдалося знайти групу для адреси" in str(excinfo.value)
        assert CITY in str(excinfo.value) and STREET in str(excinfo.value) and HOUSE in str(excinfo.value)

@pytest.mark.asyncio
async def test_dtek_group_determination_error():
    """DTEK bot should raise a ValueError with a clear message when group cannot be determined."""
    from unittest.mock import AsyncMock
    with patch("dtek.bot.bot.get_data_source") as mock_get_source:
        mock_source = AsyncMock()
        mock_source.get_schedule.side_effect = Exception(f"Could not determine group for address {CITY}, {STREET}, {HOUSE}")
        mock_get_source.return_value = mock_source
        
        with pytest.raises(ValueError) as excinfo:
            await dtek_get_shutdowns_data(CITY, STREET, HOUSE)
        assert "Не вдалося знайти групу для адреси" in str(excinfo.value)
        assert CITY in str(excinfo.value) and STREET in str(excinfo.value) and HOUSE in str(excinfo.value)
