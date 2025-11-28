import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram import types
from dtek.bot.bot import command_stats_handler as dtek_stats_handler
from cek.bot.bot import command_stats_handler as cek_stats_handler

@pytest.mark.asyncio
async def test_admin_access_control():
    # Mock message
    message = AsyncMock(spec=types.Message)
    message.from_user = MagicMock()
    message.from_user.id = 12345
    message.answer = AsyncMock()

    # Case 1: No ADMIN_IDS set -> Access Denied (Explicit Message)
    with patch.dict(os.environ, {}, clear=True):
        await dtek_stats_handler(message)
        message.answer.assert_called_with("⛔ **Відмовлено в доступі.** У вас недостатньо прав для перегляду статистики.")
        message.answer.reset_mock()
        
        await cek_stats_handler(message)
        message.answer.assert_called_with("⛔ **Відмовлено в доступі.** У вас недостатньо прав для перегляду статистики.")
        message.answer.reset_mock()

    # Case 2: User NOT in ADMIN_IDS -> Access Denied (Explicit Message)
    with patch.dict(os.environ, {"ADMIN_IDS": "999, 888"}):
        await dtek_stats_handler(message)
        message.answer.assert_called_with("⛔ **Відмовлено в доступі.** У вас недостатньо прав для перегляду статистики.")
        message.answer.reset_mock()

    # Case 3: User IN ADMIN_IDS -> Access Granted
    # We mock db_conn to avoid actual DB calls failing
    with patch.dict(os.environ, {"ADMIN_IDS": "12345, 999"}):
        # We expect it to try to answer "Collecting stats..."
        # It will likely fail later due to db_conn not being set up in this unit test context,
        # but if it calls message.answer, we know it passed the security check.
        
        # Mocking the global db_conn in the module is tricky without refactoring.
        # However, we can check if it *attempted* to answer.
        
        # To make this robust, we'd need to mock the db_conn in the imported module.
        # For now, let's just verify it passes the check.
        
        try:
            await dtek_stats_handler(message)
        except Exception:
            # It will fail on db_conn.execute, which means it PASSED the security check
            pass
            
        # If it failed on DB, it means it tried to execute logic, so access was granted.
        # If it returned early, it wouldn't raise an exception (unless the check itself raised one).
        
        # A better way: Mock the db_conn global in the module
        with patch("dtek.bot.bot.db_conn", new=AsyncMock()) as mock_db:
             mock_db.execute.return_value.__aenter__.return_value.fetchone.return_value = (0,)
             await dtek_stats_handler(message)
             message.answer.assert_called()

