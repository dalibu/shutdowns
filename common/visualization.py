"""
Common visualization functions for schedule diagrams.
Handles 48-hour and 24-hour circular clock-face schedule images.
"""

import os
import io
import math
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

def generate_48h_schedule_image(days_slots: Dict[str, List[Dict[str, Any]]], font_path: str, current_time: Optional[datetime] = None) -> Optional[bytes]:
    """
    Генерирует 48-часовое изображение графика (clock-face) для DTEK.
    - 48 секторов (по 1 часу).
    - Верхняя половина = День 1, Нижняя половина = День 2.
    - Цвета: Желтый (свет есть), Черный (света нет).
    - Метки: начало/конец отключений + 00:00 и 24:00.
    """
    if not days_slots:
        return None

    try:
        # 1. Сортировка дат
        try:
            sorted_dates = sorted(days_slots.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
        except ValueError:
            sorted_dates = sorted(days_slots.keys())
        
        # 2. Проверяем наличие отключений
        has_any_shutdowns = False
        for slots in days_slots.values():
            if slots:
                has_any_shutdowns = True
                break
        
        if not has_any_shutdowns:
            return None

        # 3. Настройка рисования
        SCALE = 2  # 2x scale = 600x600
        base_size = 300
        size = base_size * SCALE
        padding = 30 * SCALE
        center = (size // 2, size // 2)
        radius = (size // 2) - padding
        bbox = [padding, padding, size - padding, size - padding]
        image = Image.new('RGB', (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(image)

        # 4. Шрифт
        # Slightly smaller than direct scaling (9 * 3 = 27) for a finer look
        font_size = 18
        font = None
        try:
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            logger.warning(f"Font not found at '{font_path}', using default")
            font = ImageFont.load_default()

        # 5. Рисуем фон (свет есть везде) - Желтый круг
        draw.ellipse(bbox, fill="#FFD700", outline=None)

        # 6. Вычисляем угол поворота диаграммы (если указано текущее время)
        rotation_offset = 0.0
        if current_time:
            try:
                current_date_str = current_time.strftime('%d.%m.%y')
                # Определяем, какой это день (1-й или 2-й)
                if len(sorted_dates) >= 1 and current_date_str == sorted_dates[0]:
                    # День 1
                    current_minutes = current_time.hour * 60 + current_time.minute
                elif len(sorted_dates) >= 2 and current_date_str == sorted_dates[1]:
                    # День 2
                    current_minutes = (24 * 60) + (current_time.hour * 60 + current_time.minute)
                else:
                    current_minutes = 0
                
                # Поворачиваем диаграмму так, чтобы текущее время было наверху
                # Вращение ПРОТИВ часовой стрелки (отрицательный угол в системе координат)
                rotation_offset = -(current_minutes / (48.0 * 60.0)) * 360.0
            except Exception as e:
                logger.warning(f"Failed to calculate rotation offset: {e}")
                rotation_offset = 0.0

        # 7. Рисуем отключения (черные сектора) с точностью до минут
        degrees_per_minute = 360.0 / (48.0 * 60.0)  # 0.125 градуса на минуту

        for day_idx, date in enumerate(sorted_dates[:2]):
            slots = days_slots[date]
            day_offset_minutes = day_idx * 24 * 60
            
            for slot in slots:
                try:
                    time_str = slot.get('shutdown', '00:00–00:00')
                    time_parts = time_str.split('–')
                    if len(time_parts) != 2:
                        continue
                        
                    start_h, start_m = map(int, time_parts[0].split(':'))
                    end_h, end_m = map(int, time_parts[1].split(':'))
                    
                    # Минуты от начала первой даты (0..2880)
                    start_abs = day_offset_minutes + (start_h * 60 + start_m)
                    end_abs = day_offset_minutes + (end_h * 60 + end_m)
                    
                    # Обработка перехода через полночь
                    if end_h < start_h:
                        end_abs += 24 * 60
                    
                    # Рисуем сектор с учетом поворота диаграммы
                    # 00:00 День 1 = 270 градусов (Верх) + rotation_offset
                    start_angle = 270 + (start_abs * degrees_per_minute) + rotation_offset
                    end_angle = 270 + (end_abs * degrees_per_minute) + rotation_offset
                    
                    draw.pieslice(bbox, start_angle, end_angle, fill="#000000", outline=None)

                except Exception as e:
                    logger.warning(f"Error processing DTEK slot '{slot}': {e}")
                    continue

        # 8. Белые разделительные линии (каждый час) с учетом поворота
        degrees_per_hour = 360.0 / 48.0
        for hour in range(48):
            angle_deg = 270 + (hour * degrees_per_hour) + rotation_offset
            angle_rad = math.radians(angle_deg)
            x_pos = center[0] + radius * math.cos(angle_rad)
            y_pos = center[1] + radius * math.sin(angle_rad)
            # Thicker lines for better visibility
            draw.line([center, (x_pos, y_pos)], fill="#FFFFFF", width=4)

        # 7. Белый центральный круг
        inner_radius = int(radius * 0.50)
        inner_bbox = [
            center[0] - inner_radius,
            center[1] - inner_radius,
            center[0] + inner_radius,
            center[1] + inner_radius
        ]
        draw.ellipse(inner_bbox, fill='#FFFFFF', outline=None)
        
        # --- Разделительная линия (0-24) - Вертикальная с учетом поворота ---
        # Линия разделяет День 1 и День 2 (на 24 часа = 180 градусов от начала)
        divider_angle = 270 + 180 + rotation_offset  # 90 градусов (горизонтальная линия влево)
        divider_rad = math.radians(divider_angle)
        divider_x1 = center[0] + radius * math.cos(divider_rad)
        divider_y1 = center[1] + radius * math.sin(divider_rad)
        divider_x2 = center[0] - radius * math.cos(divider_rad)
        divider_y2 = center[1] - radius * math.sin(divider_rad)
        draw.line(
            [(divider_x1, divider_y1), (divider_x2, divider_y2)],
            fill="#000000", width=2
        )

        # 8. Даты
        # Используем тот же размер шрифта, что и для часов на циферблате
        date_font_size = 18
        date_font = None
        try:
            date_font = ImageFont.truetype(font_path, date_font_size)
        except IOError:
            date_font = ImageFont.load_default()

        try:
            # День 1 (Сегодня) - размещаем в центре над разделительной линией
            if len(sorted_dates) >= 1:
                date1 = sorted_dates[0]
                
                temp_img = Image.new('RGBA', (200 * SCALE, 50 * SCALE), (255, 255, 255, 0))
                temp_draw = ImageDraw.Draw(temp_img)
                temp_draw.text((100 * SCALE, 25 * SCALE), date1, fill='#000000', font=date_font, anchor="mm")
                
                bbox_date = temp_img.getbbox()
                if bbox_date:
                    cropped_date = temp_img.crop(bbox_date)
                    
                    # Размещаем в центре круга, НАД разделительной линией (перпендикулярно к ней)
                    # Разделительная линия: 270 + 180 + rotation_offset
                    divider_angle = 270 + 180 + rotation_offset
                    
                    # Поворачиваем текст вдоль разделительной линии
                    text_rotation_angle = -divider_angle
                    # Проверяем, не перевернут ли текст вверх ногами
                    normalized_angle = divider_angle % 360
                    if 90 < normalized_angle < 270:
                        # Добавляем 180 градусов, чтобы текст был читаемым
                        text_rotation_angle += 180
                    rotated_date = cropped_date.rotate(text_rotation_angle, expand=True)
                    
                    # Определяем два перпендикулярных направления
                    perp1_angle = divider_angle + 90
                    perp2_angle = divider_angle - 90
                    
                    # Небольшое расстояние от центра перпендикулярно линии
                    offset_distance = 12 * SCALE
                    x1 = center[0] + offset_distance * math.cos(math.radians(perp1_angle))
                    y1 = center[1] + offset_distance * math.sin(math.radians(perp1_angle))
                    x2 = center[0] + offset_distance * math.cos(math.radians(perp2_angle))
                    y2 = center[1] + offset_distance * math.sin(math.radians(perp2_angle))
                    
                    # День 1 (сегодня) должен быть СВЕРХУ - выбираем направление с меньшей Y
                    # Если Y одинаковые (вертикальная линия), используем X
                    if abs(y1 - y2) > 1:
                        # Используем Y-координату
                        if y1 < y2:
                            perpendicular_angle = perp1_angle
                        else:
                            perpendicular_angle = perp2_angle
                    else:
                        # Y одинаковые, используем X (правая сторона для дня 1 - меньшая дата)
                        if x1 > x2:
                            perpendicular_angle = perp1_angle
                        else:
                            perpendicular_angle = perp2_angle
                    
                    perpendicular_rad = math.radians(perpendicular_angle)
                    date1_x = center[0] + offset_distance * math.cos(perpendicular_rad)
                    date1_y = center[1] + offset_distance * math.sin(perpendicular_rad)
                    
                    paste_x = int(date1_x - rotated_date.width // 2)
                    paste_y = int(date1_y - rotated_date.height // 2)
                    image.paste(rotated_date, (paste_x, paste_y), rotated_date)
            
            # День 2 (Завтра) - размещаем в центре под разделительной линией
            if len(sorted_dates) >= 2:
                date2 = sorted_dates[1]
                
                temp_img = Image.new('RGBA', (200 * SCALE, 50 * SCALE), (255, 255, 255, 0))
                temp_draw = ImageDraw.Draw(temp_img)
                temp_draw.text((100 * SCALE, 25 * SCALE), date2, fill='#000000', font=date_font, anchor="mm")
                
                bbox_date = temp_img.getbbox()
                if bbox_date:
                    cropped_date = temp_img.crop(bbox_date)
                    
                    # Размещаем в центре круга, ПОД разделительной линией (перпендикулярно к ней)
                    divider_angle = 270 + 180 + rotation_offset
                    
                    # Поворачиваем текст вдоль разделительной линии
                    text_rotation_angle = -divider_angle
                    # Проверяем, не перевернут ли текст вверх ногами
                    normalized_angle = divider_angle % 360
                    if 90 < normalized_angle < 270:
                        # Добавляем 180 градусов, чтобы текст был читаемым
                        text_rotation_angle += 180
                    rotated_date = cropped_date.rotate(text_rotation_angle, expand=True)
                    
                    # Определяем два перпендикулярных направления
                    perp1_angle = divider_angle + 90
                    perp2_angle = divider_angle - 90
                    
                    # Вычисляем Y-координаты для обоих направлений
                    offset_distance = 12 * SCALE
                    x1 = center[0] + offset_distance * math.cos(math.radians(perp1_angle))
                    y1 = center[1] + offset_distance * math.sin(math.radians(perp1_angle))
                    x2 = center[0] + offset_distance * math.cos(math.radians(perp2_angle))
                    y2 = center[1] + offset_distance * math.sin(math.radians(perp2_angle))
                    
                    # День 2 (завтра) должен быть СНИЗУ - выбираем направление с большей Y
                    # Если Y одинаковые (вертикальная линия), используем X
                    if abs(y1 - y2) > 1:
                        # Используем Y-координату
                        if y1 > y2:
                            perpendicular_angle = perp1_angle
                        else:
                            perpendicular_angle = perp2_angle
                    else:
                        # Y одинаковые, используем X (левая сторона для дня 2 - большая дата)
                        if x1 < x2:
                            perpendicular_angle = perp1_angle
                        else:
                            perpendicular_angle = perp2_angle
                    
                    perpendicular_rad = math.radians(perpendicular_angle)
                    date2_x = center[0] + offset_distance * math.cos(perpendicular_rad)
                    date2_y = center[1] + offset_distance * math.sin(perpendicular_rad)
                    
                    paste_x = int(date2_x - rotated_date.width // 2)
                    paste_y = int(date2_y - rotated_date.height // 2)
                    image.paste(rotated_date, (paste_x, paste_y), rotated_date)

        except Exception as e:
            logger.warning(f"Failed to add dates: {e}")

        # 9. Метки часов (все часы для обоих дней)
        label_radius = radius + (padding * 0.35)
        
        # Рисуем метки для всех часов (0-23 для каждого дня)
        # Всего 48 меток
        for hour in range(48):
            angle_deg = 270 + (hour * degrees_per_hour) + rotation_offset
            angle_rad = math.radians(angle_deg)
            
            x_pos = center[0] + label_radius * math.cos(angle_rad)
            y_pos = center[1] + label_radius * math.sin(angle_rad)
            
            # Метка вида "00", "01", ..., "23" (повторяется для второго дня)
            hour_label = hour % 24
            label = f"{hour_label:02d}"
            
            try:
                draw.text((x_pos, y_pos), label, fill="black", font=font, anchor="mm")
            except Exception:
                # Fallback для старых версий Pillow
                bbox_text = draw.textbbox((0, 0), label, font=font)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
                draw.text((x_pos - text_width / 2, y_pos - text_height / 2), label, fill="black", font=font)

        # 10. Маркер текущего времени (треугольник всегда наверху)
        if current_time:
            try:
                # Треугольник всегда указывает наверх (270 градусов)
                marker_angle = 270
                angle_rad = math.radians(marker_angle)
                
                if True:  # Всегда рисуем, если есть current_time
                    # Рисуем маркер внутри белого центрального круга (черный треугольник)
                    # Используем ранее рассчитанный inner_radius (радиус белого круга)
                    
                    # Отступ (gap) между краем белого круга и вершиной треугольника в пикселях
                    gap = 2 * SCALE
                    triangle_height = 8 * SCALE  # Высота треугольника (длина от вершины к основанию)
                    triangle_base_width = 6 * SCALE  # Ширина основания треугольника
                    
                    # Вершина треугольника (apex) указывает на текущий сектор
                    # Располагаем вершину близко к краю белого круга
                    apex_distance = inner_radius - gap
                    apex_x = center[0] + apex_distance * math.cos(angle_rad)
                    apex_y = center[1] + apex_distance * math.sin(angle_rad)
                    
                    # Два других угла треугольника (основание)
                    # Основание находится ближе к центру
                    base_distance = apex_distance - triangle_height
                    base_center_x = center[0] + base_distance * math.cos(angle_rad)
                    base_center_y = center[1] + base_distance * math.sin(angle_rad)
                    
                    # Перпендикулярный угол для разнесения углов основания
                    perp_angle = angle_rad + math.radians(90)
                    base_x1 = base_center_x + (triangle_base_width / 2) * math.cos(perp_angle)
                    base_y1 = base_center_y + (triangle_base_width / 2) * math.sin(perp_angle)
                    base_x2 = base_center_x - (triangle_base_width / 2) * math.cos(perp_angle)
                    base_y2 = base_center_y - (triangle_base_width / 2) * math.sin(perp_angle)
                    
                    # Рисуем треугольник
                    draw.polygon([
                        (apex_x, apex_y),
                        (base_x1, base_y1),
                        (base_x2, base_y2)
                    ], fill="black")
            except Exception as e:
                logger.warning(f"Failed to draw current time marker: {e}")

        # 10. Сохранение
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        buf.seek(0)
        return buf.getvalue()

    except Exception as e:
        logger.error(f"Failed to generate 48h schedule image: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Failed to generate 48h schedule image: {e}", exc_info=True)
        return None

def generate_24h_schedule_image(day_slots: Dict[str, List[Dict[str, Any]]], font_path: str, current_time: Optional[datetime] = None) -> Optional[bytes]:
    """
    Генерирует 24-часовое изображение графика для ЦЕК.
    - 24 равных сектора (по 1 часу каждый) с белыми разделительными линиями.
    - Метки часов в середине каждого сектора (например, "20-21", "08-09").
    """
    if not day_slots:
        return None

    try:
        # 1. Получаем данные для сегодня
        try:
            sorted_dates = sorted(day_slots.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
        except ValueError:
            sorted_dates = sorted(day_slots.keys())
        
        if not sorted_dates:
            return None
            
        today_date = sorted_dates[0]
        today_slots = day_slots[today_date]
        
        if not today_slots:
            return None
        
        # 2. Настройка рисования
        SCALE = 2  # 2x scale = 600x600
        base_size = 300
        size = base_size * SCALE
        padding = 30 * SCALE
        center = (size // 2, size // 2)
        radius = (size // 2) - padding
        bbox = [padding, padding, size - padding, size - padding]
        image = Image.new('RGB', (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(image)

        # 3. Загрузка шрифта
        font_size = 18
        font = None
        try:
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            logger.warning(f"Font not found at '{font_path}', using default")
            font = ImageFont.load_default()

        # 4. Вычисляем угол поворота диаграммы (если указано текущее время)
        rotation_offset = 0.0
        if current_time:
            try:
                current_date_str = current_time.strftime('%d.%m.%y')
                if current_date_str == today_date:
                    current_minutes = current_time.hour * 60 + current_time.minute
                    # Поворачиваем диаграмму так, чтобы текущее время было наверху
                    # Вращение ПРОТИВ часовой стрелки (отрицательный угол в системе координат)
                    rotation_offset = -(current_minutes / (24.0 * 60.0)) * 360.0
            except Exception as e:
                logger.warning(f"Failed to calculate rotation offset: {e}")
                rotation_offset = 0.0

        # 5. Рисуем фон (свет есть везде) - Желтый круг
        draw.ellipse(bbox, fill="#FFD700", outline=None)

        # 6. Рисуем отключения (черные сектора) с точностью до минут
        for slot in today_slots:
            try:
                time_str = slot.get('shutdown', '00:00–00:00')
                time_parts = time_str.split('–')
                if len(time_parts) != 2:
                    continue
                    
                start_h, start_m = map(int, time_parts[0].split(':'))
                end_h, end_m = map(int, time_parts[1].split(':'))
                
                # Переводим время в минуты от начала дня
                start_minutes = start_h * 60 + start_m
                end_minutes = end_h * 60 + end_m
                
                # Обработка перехода через полночь (для одной диаграммы обрезаем по 24:00)
                if end_minutes < start_minutes:
                    end_minutes = 24 * 60  # Рисуем до конца дня
                
                # Переводим минуты в углы с учетом поворота
                # 00:00 = 270 градусов (Верх) + rotation_offset
                # 1 минута = 0.25 градуса
                start_angle = 270 + (start_minutes * 0.25) + rotation_offset
                end_angle = 270 + (end_minutes * 0.25) + rotation_offset
                
                draw.pieslice(bbox, start_angle, end_angle, fill="#000000", outline=None)
                        
            except Exception as e:
                logger.warning(f"Error processing shutdown slot '{slot}': {e}")
                continue

        # 7. Рисуем белые разделительные линии (каждый час) с учетом поворота
        degrees_per_hour = 360.0 / 24.0
        for hour in range(24):
            angle_deg = 270 + (hour * degrees_per_hour) + rotation_offset
            angle_rad = math.radians(angle_deg)
            x_pos = center[0] + radius * math.cos(angle_rad)
            y_pos = center[1] + radius * math.sin(angle_rad)
            # Thicker lines for better visibility
            draw.line([center, (x_pos, y_pos)], fill="#FFFFFF", width=4)

        # 7. Белый центральный круг
        inner_radius = int(radius * 0.50)
        inner_bbox = [
            center[0] - inner_radius,
            center[1] - inner_radius,
            center[0] + inner_radius,
            center[1] + inner_radius
        ]
        draw.ellipse(inner_bbox, fill='#FFFFFF', outline=None)
        
        # 8. Дата в центре
        try:
            temp_img = Image.new('RGBA', (100 * SCALE, 100 * SCALE), (255, 255, 255, 0))
            temp_draw = ImageDraw.Draw(temp_img)
            temp_draw.text((50 * SCALE, 50 * SCALE), today_date, fill='#000000', font=font, anchor="mm")
            
            bbox_date = temp_img.getbbox()
            if bbox_date:
                cropped_date = temp_img.crop(bbox_date)
                paste_x = int(center[0] - cropped_date.width // 2)
                paste_y = int(center[1] - cropped_date.height // 2)
                image.paste(cropped_date, (paste_x, paste_y), cropped_date)
        except Exception as e:
            logger.warning(f"Failed to add date: {e}")

        # 9. Метки часов напротив разделительных линий с учетом поворота
        label_radius = radius + (padding * 0.35)

        for hour in range(0, 24):  # Все 24 hours
            # Угол разделительной линии с учетом поворота
            angle_deg = 270 + (hour * degrees_per_hour) + rotation_offset
            angle_rad = math.radians(angle_deg)
            
            x_pos = center[0] + label_radius * math.cos(angle_rad)
            y_pos = center[1] + label_radius * math.sin(angle_rad)
            
            # Метка вида "00", "01", ...
            label = f"{hour:02d}"
            
            try:
                draw.text((x_pos, y_pos), label, fill="black", font=font, anchor="mm")
            except Exception:
                # Fallback для старых версий Pillow
                bbox_text = draw.textbbox((0, 0), label, font=font)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
                draw.text((x_pos - text_width / 2, y_pos - text_height / 2), label, fill="black", font=font)

        # 10. Маркер текущего времени (треугольник всегда наверху)
        if current_time:
            try:
                current_date_str = current_time.strftime('%d.%m.%y')
                if current_date_str == today_date:
                    # Треугольник всегда указывает наверх (270 градусов)
                    marker_angle = 270
                    
                    angle_rad = math.radians(marker_angle)
                    # Рисуем маркер внутри белого центрального круга (черный треугольник)
                    # Используем ранее рассчитанный inner_radius (радиус белого круга)
                    
                    # Отступ (gap) между краем белого круга и вершиной треугольника в пикселях
                    gap = 2 * SCALE
                    triangle_height = 8 * SCALE  # Высота треугольника (длина от вершины к основанию)
                    triangle_base_width = 6 * SCALE  # Ширина основания треугольника
                    
                    # Вершина треугольника (apex) указывает на текущий сектор
                    # Располагаем вершину близко к краю белого круга
                    apex_distance = inner_radius - gap
                    apex_x = center[0] + apex_distance * math.cos(angle_rad)
                    apex_y = center[1] + apex_distance * math.sin(angle_rad)
                    
                    # Два других угла треугольника (основание)
                    # Основание находится ближе к центру
                    base_distance = apex_distance - triangle_height
                    base_center_x = center[0] + base_distance * math.cos(angle_rad)
                    base_center_y = center[1] + base_distance * math.sin(angle_rad)
                    
                    # Перпендикулярный угол для разнесения углов основания
                    perp_angle = angle_rad + math.radians(90)
                    base_x1 = base_center_x + (triangle_base_width / 2) * math.cos(perp_angle)
                    base_y1 = base_center_y + (triangle_base_width / 2) * math.sin(perp_angle)
                    base_x2 = base_center_x - (triangle_base_width / 2) * math.cos(perp_angle)
                    base_y2 = base_center_y - (triangle_base_width / 2) * math.sin(perp_angle)
                    
                    # Рисуем треугольник
                    draw.polygon([
                        (apex_x, apex_y),
                        (base_x1, base_y1),
                        (base_x2, base_y2)
                    ], fill="black")
            except Exception as e:
                logger.warning(f"Failed to draw current time marker: {e}")

        # 10. Сохранение
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        logger.error(f"Failed to generate 24h CEK diagram: {e}", exc_info=True)
        return None
