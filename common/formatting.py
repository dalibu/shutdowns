"""
Common formatting functions for schedule display.
Handles text formatting and status messages.
"""

import logging
import pytz
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from .bot_base import (
    parse_time_range,
    format_minutes_to_hh_mm,
    get_shutdown_duration_str_by_hours
)

logger = logging.getLogger(__name__)

def process_single_day_schedule_compact(date: str, slots: List[Dict[str, Any]], provider: str = "ДТЕК") -> str:
    """
    Генерирует компактное текстовое представление расписания для одного дня.
    Возвращает строку в формате:
    "🔴 14.11.2025: 10,5 год. відключень 00:00 - 02:00 (2 год.)..."
    Для ЦЕК использует 🟡/⚫, для ДТЕК - 🟡/⚫ (теперь одинаковые)
    """
    outage_slots = slots

    # Выбираем емодзі (теперь одинаковые для всех)
    emoji_no_shutdown = "🟡"
    emoji_shutdown = "⚫"
    
    # Сценарий: Нет отключений -> ничего не показываем (боты сами решают, что отправлять)
    if not outage_slots:
        return ""

    groups = []
    current_group = None
    total_duration_minutes = 0.0  # Суммируем в минутах для точности

    for slot in outage_slots:
        try:
            # --- ИЗМЕНЕНИЕ: Читаем ключ 'shutdown' вместо 'time' ---
            time_str = slot.get('shutdown', '00:00–00:00')
            slot_start_min, slot_end_min = parse_time_range(time_str)
            if slot_start_min == 0 and slot_end_min == 0:
                 continue  # Ошибка парсинга, пропускаем
            # Учитываем длительность слота для подсчёта итога
            slot_duration_min = slot_end_min - slot_start_min

            total_duration_minutes += slot_duration_min

            # Логика объединения слотов
            if current_group is None:
                current_group = {
                    "start_min": slot_start_min,
                    "end_min": slot_end_min,
                    "duration_minutes": slot_duration_min 
                }
            elif slot_start_min <= current_group["end_min"]:  # Проверяем пересечение или стыковку
                # Объединяем: расширяем конец и суммируем длительность
                current_group["end_min"] = max(current_group["end_min"], slot_end_min)
                current_group["duration_minutes"] += slot_duration_min
            else:
                # Слот не пересекается, сохраняем текущую группу и начинаем новую
                groups.append(current_group)
                current_group = {
                    "start_min": slot_start_min,
                    "end_min": slot_end_min,
                    "duration_minutes": slot_duration_min
                }
        except Exception as e:
            logger.error(f"Error processing slot {slot}: {e}")
            continue

    if current_group:
        groups.append(current_group)

    if not groups:
         return f"❌ {date}: Помилка парсингу слотів"
    
    # Формируем выходную строку
    total_duration_hours = total_duration_minutes / 60.0
    total_duration_str = get_shutdown_duration_str_by_hours(total_duration_hours)
    output_parts = [f"{emoji_shutdown} {date}: {total_duration_str} відключень\n"]
    
    for group in groups:
        start_time_final = format_minutes_to_hh_mm(group["start_min"])
        end_time_final = format_minutes_to_hh_mm(group["end_min"])
        group_duration_hours = group["duration_minutes"] / 60.0
        duration_str = get_shutdown_duration_str_by_hours(group_duration_hours)
        
        # Формат: " 00:00 - 02:00 (2 год.)"
        output_parts.append(f" {start_time_final} - {end_time_final} ({duration_str})\n")

    return "".join(output_parts)

def get_current_status_message(schedule: dict) -> Optional[str]:
    """
    Определяет текущий статус (свет есть/нет) и время следующего изменения.
    Возвращает отформатированное сообщение или None, если данных недостаточно.
    """
    if not schedule:
        return None

    try:
        # 1. Получаем текущее время в Киеве
        kiev_tz = pytz.timezone('Europe/Kiev')
        now = datetime.now(kiev_tz)

        current_date_str = now.strftime('%d.%m.%y')
        
        # 2. Собираем все слоты отключений в один список с datetime
        #    Учитываем сегодня и завтра, чтобы найти ближайшее событие
        all_outage_intervals = []

        # Сортируем даты
        try:
            sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
        except ValueError:
            sorted_dates = sorted(schedule.keys())

        for date_str in sorted_dates:
            # Пропускаем прошедшие дни (если вдруг они есть в json), но оставляем сегодня
            try:
                date_obj = datetime.strptime(date_str, '%d.%m.%y').date()
                if date_obj < now.date():
                    continue
            except ValueError:
                continue

            slots = schedule.get(date_str, [])
            for slot in slots:
                time_str = slot.get('shutdown', '00:00–00:00')
                start_min, end_min = parse_time_range(time_str)
                
                # Преобразуем в datetime
                # start_min - минуты от начала дня date_obj
                start_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=start_min)
                end_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=end_min)
                
                all_outage_intervals.append((start_dt, end_dt))

        # Сортируем интервалы по времени начала
        all_outage_intervals.sort(key=lambda x: x[0])

        # 3. Объединяем пересекающиеся или стыкующиеся интервалы
        merged_intervals = []
        if all_outage_intervals:
            current_start, current_end = all_outage_intervals[0]
            for next_start, next_end in all_outage_intervals[1:]:
                if next_start <= current_end:
                    current_end = max(current_end, next_end)
                else:
                    merged_intervals.append((current_start, current_end))
                    current_start, current_end = next_start, next_end
            merged_intervals.append((current_start, current_end))

        # 4. Определяем текущий статус
        is_light_off = False
        current_outage_end = None
        next_outage_start = None

        for start_dt, end_dt in merged_intervals:
            if start_dt <= now < end_dt:
                is_light_off = True
                current_outage_end = end_dt
                break
            elif start_dt > now:
                next_outage_start = start_dt
                break

        if is_light_off:
            # Ищем следующее включение (это current_outage_end)
            # Формируем сообщение
            time_str = current_outage_end.strftime('%H:%M')
            return f"⚫ Зараз діє відключення до {time_str}"
        else:
            # Свет есть. Ищем ближайшее отключение.
            if next_outage_start:
                time_str = next_outage_start.strftime('%H:%M')
                return f"🟡 Наступне відключення у {time_str}"
            else:
                # Якщо відключень немає - не показуємо статусне повідомлення
                return None

    except Exception as e:
        logger.error(f"Error calculating current status: {e}")
        return None
