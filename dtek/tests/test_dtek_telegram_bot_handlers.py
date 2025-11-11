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
    
    def test_pluralize_special_cases(self):
        """Особые случаи плюрализации"""
        from dtek_telegram_bot import _pluralize_hours
        
        # Числа на -11, -12, -13, -14
        assert _pluralize_hours(11.0) == "годин"
        assert _pluralize_hours(111.0) == "годин"
        assert _pluralize_hours(211.0) == "годин"
        
        # Большие числа
        assert _pluralize_hours(21.0) == "годину"
        assert _pluralize_hours(101.0) == "годину"
        
        # Дробные
        assert _pluralize_hours(0.1) == "години"
        assert _pluralize_hours(100.5) == "години"


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
    """Тесты валидации данных"""
    
    def test_schedule_hash_consistency(self):
        """Хеш расписания должен быть консистентным"""
        from dtek_telegram_bot import _get_schedule_hash
        
        schedule1 = {
            "schedule": {
                "12.11.24": [
                    {"time": "10-11", "disconection": "full"}
                ]
            }
        }
        
        schedule2 = {
            "schedule": {
                "12.11.24": [
                    {"time": "10-11", "disconection": "full"}
                ]
            }
        }
        
        hash1 = _get_schedule_hash(schedule1)
        hash2 = _get_schedule_hash(schedule2)
        
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hash length
    
    def test_empty_schedule_handling(self):
        """Обработка пустого расписания"""
        from dtek_telegram_bot import (
            _get_schedule_hash,
            format_shutdown_message,
            _process_single_day_schedule
        )
        
        # Пустой словарь
        assert _get_schedule_hash({"schedule": {}}) == "NO_SCHEDULE_FOUND"
        
        # Пустой список слотов
        result = _process_single_day_schedule("12.11.24", [])
        assert "не заплановані" in result.lower()
        
        # Форматирование без расписания
        data = {
            "city": "Test",
            "street": "Test",
            "house_num": "1",
            "group": "1.1",
            "schedule": {}
        }
        result = format_shutdown_message(data)
        assert "не вдалося" in result.lower() or "не знайдено" in result.lower()


@pytest.mark.parametrize("hours,expected_word", [
    (0.5, "години"),
    (1.0, "годину"),
    (2.0, "години"),
    (3.0, "години"),
    (4.0, "години"),
    (5.0, "годин"),
    (11.0, "годин"),
    (21.0, "годину"),
    (22.0, "години"),
    (100.0, "годин"),
])
def test_pluralize_hours_parametrized(hours, expected_word):
    """Параметризованный тест плюрализации"""
    from dtek_telegram_bot import _pluralize_hours
    assert _pluralize_hours(hours) == expected_word


@pytest.mark.parametrize("minutes,expected", [
    (0, "00:00"),
    (30, "00:30"),
    (60, "01:00"),
    (90, "01:30"),
    (1440, "24:00"),
])
def test_format_minutes_parametrized(minutes, expected):
    """Параметризованный тест форматирования минут"""
    from dtek_telegram_bot import format_minutes_to_hh_m
    assert format_minutes_to_hh_m(minutes) == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])