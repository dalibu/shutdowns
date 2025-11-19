"""
Дополнительные тесты для Telegram handlers (команд бота)
Эти тесты можно добавить в основной файл или использовать отдельно
"""
import pytest
import sys
import os
from unittest.mock import Mock, AsyncMock, patch, MagicMock

# Добавляем путь к модулю
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import dtek_telegram_bot
    from dtek_telegram_bot import HUMAN_USERS
except ImportError:
    pytest.skip("Cannot import dtek_telegram_bot", allow_module_level=True)


@pytest.mark.unit
class TestCaptchaHandler:
    """Тесты для обработки CAPTCHA"""
    
    @pytest.mark.asyncio
    async def test_handle_captcha_check_new_user(self, mock_telegram_message, mock_fsm_state):
        """Новый пользователь должен пройти CAPTCHA"""
        user_id = 999999  # Новый пользователь
        mock_telegram_message.from_user.id = user_id
        
        # Убедимся что пользователя нет в кеше
        HUMAN_USERS.pop(user_id, None)
        
        result = await dtek_telegram_bot._handle_captcha_check(
            mock_telegram_message,
            mock_fsm_state
        )
        
        # Должен вернуть False (не прошел еще)
        assert result is False
        
        # Должно быть отправлено сообщение с вопросом
        mock_telegram_message.answer.assert_called_once()
        call_args = mock_telegram_message.answer.call_args[0][0]
        assert "?" in call_args  # В сообщении есть вопрос
        
        # FSM state должен быть установлен
        mock_fsm_state.set_state.assert_called_once()
        mock_fsm_state.update_data.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_handle_captcha_check_existing_user(self, mock_telegram_message, mock_fsm_state):
        """Существующий пользователь должен пройти без CAPTCHA"""
        user_id = 123456
        mock_telegram_message.from_user.id = user_id
        
        # Добавляем пользователя в кеш
        HUMAN_USERS[user_id] = True
        
        result = await dtek_telegram_bot._handle_captcha_check(
            mock_telegram_message,
            mock_fsm_state
        )
        
        # Должен вернуть True (уже прошел)
        assert result is True
        
        # Сообщения не должно быть
        mock_telegram_message.answer.assert_not_called()
        
        # Очищаем после теста
        HUMAN_USERS.pop(user_id, None)


@pytest.mark.unit
class TestGlobalCacheBehavior:
    """Тесты поведения глобальных кешей"""
    
    def test_human_users_persistence(self):
        """HUMAN_USERS сохраняет данные между вызовами"""
        test_user_id = 88888
        
        # Добавляем пользователя
        HUMAN_USERS[test_user_id] = True
        assert test_user_id in HUMAN_USERS
        
        # Проверяем что он остался
        assert HUMAN_USERS.get(test_user_id) is True
        
        # Очищаем
        HUMAN_USERS.pop(test_user_id, None)
        assert test_user_id not in HUMAN_USERS
    
    def test_address_cache_structure(self):
        """ADDRESS_CACHE имеет правильную структуру"""
        from dtek_telegram_bot import ADDRESS_CACHE
        from datetime import datetime
        
        test_key = ("TestCity", "TestStreet", "1")
        test_data = {
            'last_schedule_hash': 'test_hash_12345',
            'last_checked': datetime.now()
        }
        
        ADDRESS_CACHE[test_key] = test_data
        
        # Проверяем структуру
        assert test_key in ADDRESS_CACHE
        assert 'last_schedule_hash' in ADDRESS_CACHE[test_key]
        assert 'last_checked' in ADDRESS_CACHE[test_key]
        assert isinstance(ADDRESS_CACHE[test_key]['last_checked'], datetime)
        
        # Очищаем
        ADDRESS_CACHE.pop(test_key, None)


@pytest.mark.unit
class TestHelperFunctions:
    """Тесты вспомогательных функций"""
    
    def test_format_minutes_edge_cases(self):
        """Граничные случаи форматирования минут"""
        from dtek_telegram_bot import format_minutes_to_hh_m
        
        # Очень большие значения
        assert format_minutes_to_hh_m(1000) == "16:40"
        assert format_minutes_to_hh_m(9999) == "166:39"
        
        # Ровные часы
        assert format_minutes_to_hh_m(180) == "03:00"
        assert format_minutes_to_hh_m(300) == "05:00"
    
    def test_get_hours_str(self):
        """Тест функции _get_hours_str (упрощенной версии)"""
        from dtek_telegram_bot import _get_hours_str
        
        # Функция всегда возвращает "год."
        assert _get_hours_str(0.5) == "год."
        assert _get_hours_str(1.0) == "год."
        assert _get_hours_str(5.0) == "год."
        assert _get_hours_str(100.0) == "год."
    
    def test_shutdown_duration_str(self):
        """Тест форматирования длительности отключений"""
        from dtek_telegram_bot import _get_shutdown_duration_str_by_hours
        
        # Целые часы
        assert _get_shutdown_duration_str_by_hours(1.0) == "1 год."
        assert _get_shutdown_duration_str_by_hours(2.0) == "2 год."
        
        # Дробные часы
        result = _get_shutdown_duration_str_by_hours(0.5)
        assert "0,5" in result and "год." in result
        
        result = _get_shutdown_duration_str_by_hours(2.5)
        assert "2,5" in result and "год." in result
        
        # Ноль и отрицательные
        assert _get_shutdown_duration_str_by_hours(0) == "0 год."
        assert _get_shutdown_duration_str_by_hours(-1) == "0 год."


@pytest.mark.integration
class TestFullWorkflow:
    """Интеграционные тесты полного workflow"""
    
    @pytest.mark.asyncio
    async def test_check_command_full_flow(self, mock_telegram_message, mock_fsm_state):
        """Полный флоу команды /check"""
        # Этот тест требует больше настройки и моков
        # Оставлен как шаблон для расширения
        pass
    
    @pytest.mark.asyncio
    async def test_subscription_lifecycle(self):
        """Жизненный цикл подписки: создание -> проверка -> удаление"""
        # Этот тест требует реальной БД или полного мока
        # Оставлен как шаблон для расширения
        pass


@pytest.mark.unit
class TestDataValidation:
    """Тесты валидации данных и хеширования"""
    
    def test_schedule_hash_consistency(self):
        """Хеш расписания должен быть консистентным"""
        from dtek_telegram_bot import _get_schedule_hash_compact
        
        # Новый формат с shutdown
        schedule1 = {
            "schedule": {
                "12.11.24": [
                    {"shutdown": "10:00–11:00"}
                ]
            }
        }
        
        schedule2 = {
            "schedule": {
                "12.11.24": [
                    {"shutdown": "10:00–11:00"}
                ]
            }
        }
        
        hash1 = _get_schedule_hash_compact(schedule1)
        hash2 = _get_schedule_hash_compact(schedule2)
        
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hash length
    
    def test_schedule_hash_different_for_different_data(self):
        """Разные расписания дают разные хеши"""
        from dtek_telegram_bot import _get_schedule_hash_compact
        
        schedule1 = {
            "schedule": {
                "12.11.24": [
                    {"shutdown": "10:00–11:00"}
                ]
            }
        }
        
        schedule2 = {
            "schedule": {
                "12.11.24": [
                    {"shutdown": "14:00–15:00"}  # Другое время
                ]
            }
        }
        
        hash1 = _get_schedule_hash_compact(schedule1)
        hash2 = _get_schedule_hash_compact(schedule2)
        
        assert hash1 != hash2
    
    def test_schedule_hash_different_slots_same_duration(self):
        """Разные слоты с одинаковой суммарной длительностью должны давать разные хеши."""
        from dtek_telegram_bot import _get_schedule_hash_compact
        
        # 3 часа отключений, двумя слотами
        schedule_A = {
            "schedule": {
                "13.11.24": [
                    {"shutdown": "08:00–09:00"}, # 1 hour
                    {"shutdown": "14:00–16:00"}  # 2 hours
                ]
            }
        }
        
        # 3 часа отключений, другими двумя слотами
        schedule_B = {
            "schedule": {
                "13.11.24": [
                    {"shutdown": "00:00–02:00"}, # 2 hours
                    {"shutdown": "20:00–21:00"}  # 1 hour
                ]
            }
        }
        
        hash_A = _get_schedule_hash_compact(schedule_A)
        hash_B = _get_schedule_hash_compact(schedule_B)
        
        # С новым хешированием, основанным на слотах, хеши должны быть разными
        assert hash_A != hash_B

    def test_schedule_hash_stability_on_slot_order_change(self):
        """
        Проверка устойчивости хеша: разный порядок слотов в исходных данных 
        должен давать ОДИНАКОВЫЙ хеш после нормализации.
        """
        from dtek_telegram_bot import _get_schedule_hash_compact
        
        # Расписание А: слоты расположены в хронологическом порядке
        schedule_A = {
            "schedule": {
                "15.11.24": [
                    {"shutdown": "10:00–12:00"}, # 1-й слот
                    {"shutdown": "14:00–16:00"}  # 2-й слот
                ]
            }
        }
        
        # Расписание Б: слоты расположены в обратном порядке (имитация нестабильности API)
        schedule_B = {
            "schedule": {
                "15.11.24": [
                    {"shutdown": "14:00–16:00"}, # 2-й слот
                    {"shutdown": "10:00–12:00"}  # 1-й слот
                ]
            }
        }
        
        hash_A = _get_schedule_hash_compact(schedule_A)
        hash_B = _get_schedule_hash_compact(schedule_B)
        
        # Благодаря нормализации (сортировке), хеши должны быть одинаковыми!
        assert hash_A == hash_B
        assert hash_A != "NO_SCHEDULE_FOUND"

    def test_schedule_hash_ignores_extraneous_fields(self):
        """
        Проверка: хеш должен игнорировать любые дополнительные поля, 
        кроме поля 'shutdown', чтобы избежать ложных срабатываний.
        """
        from dtek_telegram_bot import _get_schedule_hash_compact
        
        schedule_clean = {
            "schedule": {
                "16.11.24": [
                    {"shutdown": "10:00–11:00"}
                ]
            }
        }
        
        schedule_dirty = {
            "schedule": {
                "16.11.24": [
                    {
                        "shutdown": "10:00–11:00",
                        "extra_info": "Some random text that changed", # Дополнительное поле
                        "group_id": 123456
                    }
                ]
            }
        }
        
        hash_clean = _get_schedule_hash_compact(schedule_clean)
        hash_dirty = _get_schedule_hash_compact(schedule_dirty)
        
        # Хеши должны быть одинаковыми, так как дополнительная информация игнорируется
        assert hash_clean == hash_dirty

    def test_empty_schedule_handling(self):
        """Обработка пустого расписания"""
        from dtek_telegram_bot import (
            _get_schedule_hash_compact,
            _process_single_day_schedule_compact
        )
        
        # Пустой словарь
        assert _get_schedule_hash_compact({"schedule": {}}) == "NO_SCHEDULE_FOUND"
        
        # Пустой список слотов
        result = _process_single_day_schedule_compact("12.11.24", [])
        assert "не заплановані" in result.lower()
    
    def test_parse_time_range(self):
        """Тест парсинга временного диапазона"""
        from dtek_telegram_bot import parse_time_range
        
        # Нормальные случаи
        start, end = parse_time_range("10:00–11:00")
        assert start == 600  # 10 * 60
        assert end == 660    # 11 * 60
        
        # Переход через полночь
        start, end = parse_time_range("23:00–01:00")
        assert start == 1380  # 23 * 60
        assert end == 1500    # 01 * 60 + 1440 (24 часа)
        
        # Ошибочный формат возвращает (0, 0)
        start, end = parse_time_range("invalid")
        assert start == 0
        assert end == 0


@pytest.mark.parametrize("minutes,expected", [
    (0, "00:00"),
    (30, "00:30"),
    (60, "01:00"),
    (90, "01:30"),
    (1440, "24:00"),
    (720, "12:00"),
    (1380, "23:00"),
])
def test_format_minutes_parametrized(minutes, expected):
    """Параметризованный тест форматирования минут"""
    from dtek_telegram_bot import format_minutes_to_hh_m
    assert format_minutes_to_hh_m(minutes) == expected


@pytest.mark.parametrize("hours,expected_contains", [
    (0, "0 год."),
    (0.5, "0,5 год."),
    (1.0, "1 год."),
    (2.5, "2,5 год."),
    (10, "10 год."),
])
def test_shutdown_duration_str_parametrized(hours, expected_contains):
    """Параметризованный тест форматирования длительности"""
    from dtek_telegram_bot import _get_shutdown_duration_str_by_hours
    result = _get_shutdown_duration_str_by_hours(hours)
    assert expected_contains in result


@pytest.mark.unit
class TestProcessSingleDayScheduleCompact:
    """Дополнительные тесты для _process_single_day_schedule_compact"""
    
    def test_multiple_gaps(self):
        """Несколько разрывов в расписании"""
        from dtek_telegram_bot import _process_single_day_schedule_compact
        
        slots = [
            {"shutdown": "08:00–09:00"},
            {"shutdown": "11:00–12:00"},  # Разрыв 09:00-11:00
            {"shutdown": "14:00–15:00"},  # Разрыв 12:00-14:00
        ]
        result = _process_single_day_schedule_compact("15.11.24", slots)
        
        # Должно быть 3 отдельных слота
        assert "08:00 - 09:00" in result
        assert "11:00 - 12:00" in result
        assert "14:00 - 15:00" in result
    
    def test_very_long_outage(self):
        """Очень длинное отключение"""
        from dtek_telegram_bot import _process_single_day_schedule_compact
        
        slots = [
            {"shutdown": "00:00–08:00"},
            {"shutdown": "08:00–16:00"},
            {"shutdown": "16:00–24:00"},
        ]
        result = _process_single_day_schedule_compact("15.11.24", slots)
        
        # Все должно объединиться в один 24-часовой слот
        assert "00:00 - 24:00" in result
        assert "24 год." in result



@pytest.mark.unit
class TestAlertCommand:
    """Тесты для команды /alert"""

    @pytest.mark.asyncio
    async def test_alert_command_success(self, mock_telegram_message):
        """Успешная установка алерта"""
        from dtek_telegram_bot import cmd_alert
        
        mock_telegram_message.text = "/alert 15"
        mock_telegram_message.from_user.id = 12345
        
        # Mock DB
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone.return_value = (1,) # Subscription exists
        mock_conn.execute.return_value = mock_cursor
        
        with patch('dtek_telegram_bot.db_conn', mock_conn):
            await cmd_alert(mock_telegram_message)
            
        # Verify DB update
        mock_conn.execute.assert_any_call(
            "UPDATE subscriptions SET notification_lead_time = ? WHERE user_id = ?",
            (15, 12345)
        )
        mock_conn.commit.assert_called_once()
        
        # Verify success message
        mock_telegram_message.answer.assert_called_with(
            "🔔 Сповіщення встановлено! Ви отримаєте повідомлення за **15 хв.** до зміни статусу світла."
        )

    @pytest.mark.asyncio
    async def test_alert_command_disable(self, mock_telegram_message):
        """Отключение алерта (0 минут)"""
        from dtek_telegram_bot import cmd_alert
        
        mock_telegram_message.text = "/alert 0"
        mock_telegram_message.from_user.id = 12345
        
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.execute.return_value = mock_cursor
        
        with patch('dtek_telegram_bot.db_conn', mock_conn):
            await cmd_alert(mock_telegram_message)
            
        mock_conn.execute.assert_any_call(
            "UPDATE subscriptions SET notification_lead_time = ? WHERE user_id = ?",
            (0, 12345)
        )
        
        mock_telegram_message.answer.assert_called_with(
            "🔕 Сповіщення про наближення подій вимкнено."
        )

    @pytest.mark.asyncio
    async def test_alert_command_invalid_args(self, mock_telegram_message):
        """Неверные аргументы"""
        from dtek_telegram_bot import cmd_alert
        
        # Case 1: No args
        mock_telegram_message.text = "/alert"
        await cmd_alert(mock_telegram_message)
        mock_telegram_message.answer.assert_called()
        assert "Використання" in mock_telegram_message.answer.call_args[0][0]
        
        # Case 2: Non-integer
        mock_telegram_message.text = "/alert abc"
        await cmd_alert(mock_telegram_message)
        assert "вкажіть число" in mock_telegram_message.answer.call_args[0][0]
        
        # Case 3: Out of range
        mock_telegram_message.text = "/alert 200"
        await cmd_alert(mock_telegram_message)
        assert "від 0 до 120" in mock_telegram_message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_alert_command_no_subscription(self, mock_telegram_message):
        """Нет подписки"""
        from dtek_telegram_bot import cmd_alert
        
        mock_telegram_message.text = "/alert 15"
        mock_telegram_message.from_user.id = 12345
        
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone.return_value = None # No subscription
        mock_conn.execute.return_value = mock_cursor
        
        with patch('dtek_telegram_bot.db_conn', mock_conn):
            await cmd_alert(mock_telegram_message)
            
        mock_telegram_message.answer.assert_called_with(
            "❌ Ви ще не підписані на оновлення. Спочатку використайте `/subscribe`."
        )



@pytest.mark.unit
class TestSubscribeCommand:
    """Тесты для команды /subscribe (с интеграцией алертов)"""

    @pytest.mark.asyncio
    async def test_subscribe_enables_alert_default(self, mock_telegram_message, mock_fsm_state):
        """Подписка включает алерт по умолчанию (15 мин), если он был 0"""
        from dtek_telegram_bot import command_subscribe_handler, HUMAN_USERS
        
        user_id = 12345
        mock_telegram_message.from_user.id = user_id
        mock_telegram_message.text = "/subscribe"
        
        # Setup
        HUMAN_USERS[user_id] = True
        
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        
        # 1. Check last_check -> found
        # 2. Check existing sub -> None (new sub) OR existing but we need to check alert logic
        # Let's simulate new subscription
        
        # Mock responses for sequential execute calls
        # Call 1: SELECT ... from user_last_check
        # Call 2: SELECT ... from subscriptions (check existing)
        # Call 3: SELECT notification_lead_time ... (check alert)
        # Call 4: INSERT OR REPLACE ...
        
        # We need to be careful with mocking multiple execute calls
        # Let's use side_effect for execute to return different cursors or the same cursor with different results
        
        cursor_last_check = AsyncMock()
        cursor_last_check.fetchone.return_value = ("Dnipro", "Street", "1", "hash")
        
        cursor_check_sub = AsyncMock()
        cursor_check_sub.fetchone.return_value = None # No existing sub
        
        cursor_check_alert = AsyncMock()
        cursor_check_alert.fetchone.return_value = (0,) # Current alert is 0 (or None/default)
        
        mock_conn.execute.side_effect = [
            cursor_last_check,
            cursor_check_sub,
            cursor_check_alert,
            AsyncMock() # Insert
        ]
        
        with patch('dtek_telegram_bot.db_conn', mock_conn):
            await command_subscribe_handler(mock_telegram_message, mock_fsm_state)
            
        # Verify INSERT called with notification_lead_time = 15
        # The last call to execute should be the INSERT
        insert_call = mock_conn.execute.call_args
        sql = insert_call[0][0]
        params = insert_call[0][1]
        
        assert "INSERT OR REPLACE INTO subscriptions" in sql
        assert "notification_lead_time" in sql
        # Params: user_id, city, street, house, interval, next_check, hash, lead_time
        assert params[-1] == 15 # Last param is new_lead_time
        
        # Verify message mentions alert
        mock_telegram_message.answer.assert_called()
        args = mock_telegram_message.answer.call_args[0][0]
        assert "15 хв" in args

    @pytest.mark.asyncio
    async def test_subscribe_preserves_custom_alert(self, mock_telegram_message, mock_fsm_state):
        """Подписка сохраняет пользовательский алерт (например, 30 мин)"""
        from dtek_telegram_bot import command_subscribe_handler, HUMAN_USERS
        
        user_id = 12345
        mock_telegram_message.from_user.id = user_id
        mock_telegram_message.text = "/subscribe"
        HUMAN_USERS[user_id] = True
        
        mock_conn = AsyncMock()
        
        cursor_last_check = AsyncMock()
        cursor_last_check.fetchone.return_value = ("Dnipro", "Street", "1", "hash")
        
        cursor_check_sub = AsyncMock()
        cursor_check_sub.fetchone.return_value = None
        
        cursor_check_alert = AsyncMock()
        cursor_check_alert.fetchone.return_value = (30,) # User set 30 min previously
        
        mock_conn.execute.side_effect = [
            cursor_last_check,
            cursor_check_sub,
            cursor_check_alert,
            AsyncMock()
        ]
        
        with patch('dtek_telegram_bot.db_conn', mock_conn):
            await command_subscribe_handler(mock_telegram_message, mock_fsm_state)
            
        insert_call = mock_conn.execute.call_args
        params = insert_call[0][1]
        
        assert params[-1] == 30 # Should stay 30
        
        # Message should mention 30 min
        args = mock_telegram_message.answer.call_args[0][0]
        assert "30 хв" in args