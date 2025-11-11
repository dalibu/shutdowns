"""
Конфигурация pytest для тестирования Telegram бота
"""
import sys
import os
import pytest
from unittest.mock import Mock, AsyncMock  # <-- Добавлен AsyncMock

# Добавляем корневую директорию проекта в PYTHONPATH
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Настройки для asyncio тестов
@pytest.fixture(scope="session")
def event_loop_policy():
    """Устанавливаем политику event loop для всех async тестов"""
    import asyncio
    return asyncio.get_event_loop_policy()

# Фикстура для очистки глобальных кешей
@pytest.fixture(autouse=True)
def clean_global_caches():
    """Автоматически очищает глобальные кеши после каждого теста"""
    yield
    
    try:
        from dtek_telegram_bot import HUMAN_USERS, ADDRESS_CACHE
        HUMAN_USERS.clear()
        ADDRESS_CACHE.clear()
    except ImportError:
        pass

# Фикстура для мока базы данных
@pytest.fixture
async def mock_db_connection():
    """Mock для соединения с БД"""
    # from unittest.mock import AsyncMock  # <-- УБРАНО: уже импортирован выше
    import aiosqlite
    
    mock_conn = AsyncMock(spec=aiosqlite.Connection)
    mock_conn.execute = AsyncMock()
    # Убраны execute_fetchone и execute_fetchall, так как они были исправлены в dtek_telegram_bot.py
    # mock_conn.execute_fetchone = AsyncMock()
    # mock_conn.execute_fetchall = AsyncMock()
    mock_conn.commit = AsyncMock()
    mock_conn.close = AsyncMock()
    
    return mock_conn

# Фикстура для временной БД
@pytest.fixture
async def temp_db(tmp_path):
    """Создает временную БД для тестов"""
    from dtek_telegram_bot import init_db
    
    db_path = tmp_path / "test.db"
    conn = await init_db(str(db_path))
    
    yield conn
    
    await conn.close()

# Фикстура для мока Telegram сообщения
@pytest.fixture
def mock_telegram_message():
    """Mock для Telegram Message"""
    # from unittest.mock import Mock  # <-- УБРАНО: уже импортирован выше
    # from unittest.mock import AsyncMock # <-- УБРАНО: уже импортирован выше
    
    message = Mock()
    message.from_user.id = 123456789
    message.from_user.username = "test_user"
    message.text = "test message"
    message.answer = AsyncMock() # <-- Теперь AsyncMock определён
    
    return message

# Фикстура для мока FSM контекста
@pytest.fixture
def mock_fsm_state():
    """Mock для FSM State context"""
    # from unittest.mock import AsyncMock # <-- УБРАНО: уже импортирован выше
    
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.set_data = AsyncMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    state.get_state = AsyncMock(return_value=None)
    
    return state