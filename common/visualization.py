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

def generate_48h_schedule_image(days_slots: Dict[str, List[Dict[str, Any]]], font_path: str) -> Optional[bytes]:
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
        size = 300
        padding = 30
        center = (size // 2, size // 2)
        radius = (size // 2) - padding
        bbox = [padding, padding, size - padding, size - padding]
        image = Image.new('RGB', (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(image)

        # 4. Шрифт
        font_size = 9
        font = None
        try:
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            logger.warning(f"Font not found at '{font_path}', using default")
            font = ImageFont.load_default()

        # 5. Рисуем фон (свет есть везде) - Желтый круг
        draw.ellipse(bbox, fill="#FFD700", outline=None)

        # 6. Рисуем отключения (черные сектора) с точностью до минут
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
                    
                    # Рисуем сектор
                    # 00:00 День 1 = 270 градусов (Верх)
                    start_angle = 270 + (start_abs * degrees_per_minute)
                    end_angle = 270 + (end_abs * degrees_per_minute)
                    
                    draw.pieslice(bbox, start_angle, end_angle, fill="#000000", outline=None)

                except Exception as e:
                    logger.warning(f"Error processing DTEK slot '{slot}': {e}")
                    continue

        # 6. Белые разделительные линии (каждый час)
        degrees_per_hour = 360.0 / 48.0
        for hour in range(48):
            angle_deg = 270 + (hour * degrees_per_hour)
            angle_rad = math.radians(angle_deg)
            x_pos = center[0] + radius * math.cos(angle_rad)
            y_pos = center[1] + radius * math.sin(angle_rad)
            draw.line([center, (x_pos, y_pos)], fill="#FFFFFF", width=2)

        # 7. Белый центральный круг
        inner_radius = int(radius * 0.50)
        inner_bbox = [
            center[0] - inner_radius,
            center[1] - inner_radius,
            center[0] + inner_radius,
            center[1] + inner_radius
        ]
        draw.ellipse(inner_bbox, fill='#FFFFFF', outline=None)
        
        # --- Разделительная линия (0-24) - Вертикальная ---
        draw.line(
            [(center[0], padding), (center[0], size - padding)],
            fill="#000000", width=1
        )

        # 8. Даты
        date_font_size = 11
        date_font = None
        try:
            date_font = ImageFont.truetype(font_path, date_font_size)
        except IOError:
            date_font = ImageFont.load_default()

        try:
            # День 1 (Правая половина)
            if len(sorted_dates) >= 1:
                date1 = sorted_dates[0]
                
                temp_img = Image.new('RGBA', (100, 50), (255, 255, 255, 0))
                temp_draw = ImageDraw.Draw(temp_img)
                temp_draw.text((50, 25), date1, fill='#000000', font=date_font, anchor="mm")
                
                bbox_date = temp_img.getbbox()
                if bbox_date:
                    cropped_date = temp_img.crop(bbox_date)
                    # Поворачиваем на -90 (по часовой) для чтения сверху-вниз
                    rotated_date = cropped_date.rotate(-90, expand=True)
                    
                    paste_x = int(center[0] + 10)
                    paste_y = int(center[1] - rotated_date.height // 2)
                    image.paste(rotated_date, (paste_x, paste_y), rotated_date)
            
            # День 2 (Левая половина)
            if len(sorted_dates) >= 2:
                date2 = sorted_dates[1]
                
                temp_img = Image.new('RGBA', (100, 50), (255, 255, 255, 0))
                temp_draw = ImageDraw.Draw(temp_img)
                temp_draw.text((50, 25), date2, fill='#000000', font=date_font, anchor="mm")
                
                bbox_date = temp_img.getbbox()
                if bbox_date:
                    cropped_date = temp_img.crop(bbox_date)
                    # Поворачиваем на 90 (против часовой) для чтения снизу-вверх
                    rotated_date = cropped_date.rotate(90, expand=True)
                    
                    paste_x = int(center[0] - rotated_date.width - 10)
                    paste_y = int(center[1] - rotated_date.height // 2)
                    image.paste(rotated_date, (paste_x, paste_y), rotated_date)

        except Exception as e:
            logger.warning(f"Failed to add dates: {e}")

        # 9. Метки (начало/конец отключений + 00:00 и 24:00)
        label_radius = radius + (padding * 0.6)
        label_points = set()
        
        # Добавляем фиксированные метки
        label_points.add(0.0)
        label_points.add(24.0)
        
        for day_idx, date in enumerate(sorted_dates[:2]):
            slots = days_slots[date]
            day_offset = day_idx * 24
            
            for slot in slots:
                try:
                    time_str = slot.get('shutdown', '00:00–00:00')
                    time_parts = time_str.split('–')
                    if len(time_parts) != 2:
                        continue
                        
                    start_h, start_m = map(int, time_parts[0].split(':'))
                    end_h, end_m = map(int, time_parts[1].split(':'))
                    
                    start_point = day_offset + start_h + (start_m / 60.0)
                    end_point = day_offset + end_h + (end_m / 60.0)
                    
                    if end_point < start_point:
                        end_point += 24
                        
                    label_points.add(start_point)
                    label_points.add(end_point)
                    
                except Exception:
                    continue
        
        # Рисуем метки
        for point in label_points:
            angle_deg = 270 + (point * degrees_per_hour)
            angle_rad = math.radians(angle_deg)
            
            x_pos = center[0] + label_radius * math.cos(angle_rad)
            y_pos = center[1] + label_radius * math.sin(angle_rad)
            
            # Текст метки: "HH" если минут 0, иначе "HH:MM"
            if abs(point - 24.0) < 0.001:
                label = "24"
            else:
                hours = int(point) % 24
                minutes = int(round((point - int(point)) * 60))
                if minutes == 0:
                    label = f"{hours:02d}"
                else:
                    label = f"{hours:02d}:{minutes:02d}"
            
            try:
                draw.text((x_pos, y_pos), label, fill="black", font=font, anchor="mm")
            except Exception:
                bbox_text = draw.textbbox((0, 0), label, font=font)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
                draw.text((x_pos - text_width / 2, y_pos - text_height / 2), label, fill="black", font=font)

        # 10. Сохранение
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        buf.seek(0)
        return buf.getvalue()

    except Exception as e:
        logger.error(f"Failed to generate 48h schedule image: {e}", exc_info=True)
        return None

def generate_24h_schedule_image(day_slots: Dict[str, List[Dict[str, Any]]], font_path: str) -> Optional[bytes]:
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
        size = 300
        padding = 30
        center = (size // 2, size // 2)
        radius = (size // 2) - padding
        bbox = [padding, padding, size - padding, size - padding]
        image = Image.new('RGB', (size, size), (255, 255, 255))
        draw = ImageDraw.Draw(image)

        # 3. Загрузка шрифта
        font_size = 9
        font = None
        try:
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            logger.warning(f"Font not found at '{font_path}', using default")
            font = ImageFont.load_default()

        # 4. Рисуем фон (свет есть везде) - Желтый круг
        draw.ellipse(bbox, fill="#FFD700", outline=None)

        # 5. Рисуем отключения (черные сектора) с точностью до минут
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
                
                # Переводим минуты в углы
                # 00:00 = 270 градусов (Верх)
                # 1 минута = 0.25 градуса (360 / 1440)
                start_angle = 270 + (start_minutes * 0.25)
                end_angle = 270 + (end_minutes * 0.25)
                
                draw.pieslice(bbox, start_angle, end_angle, fill="#000000", outline=None)
                        
            except Exception as e:
                logger.warning(f"Error processing shutdown slot '{slot}': {e}")
                continue

        # 6. Рисуем белые разделительные линии (каждый час)
        degrees_per_hour = 360.0 / 24.0
        for hour in range(24):
            angle_deg = 270 + (hour * degrees_per_hour)
            angle_rad = math.radians(angle_deg)
            x_pos = center[0] + radius * math.cos(angle_rad)
            y_pos = center[1] + radius * math.sin(angle_rad)
            draw.line([center, (x_pos, y_pos)], fill="#FFFFFF", width=2)

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
            temp_img = Image.new('RGBA', (100, 100), (255, 255, 255, 0))
            temp_draw = ImageDraw.Draw(temp_img)
            temp_draw.text((50, 50), today_date, fill='#000000', font=font, anchor="mm")
            
            bbox_date = temp_img.getbbox()
            if bbox_date:
                cropped_date = temp_img.crop(bbox_date)
                paste_x = int(center[0] - cropped_date.width // 2)
                paste_y = int(center[1] - cropped_date.height // 2)
                image.paste(cropped_date, (paste_x, paste_y), cropped_date)
        except Exception as e:
            logger.warning(f"Failed to add date: {e}")

        # 9. Метки часов напротив разделительных линий
        label_radius = radius + (padding * 0.5)
        
        for hour in range(0, 24):  # Все 24 часа
            # Угол разделительной линии
            angle_deg = 270 + (hour * degrees_per_hour)
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

        # 10. Сохранение
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        buf.seek(0)
        return buf.getvalue()

    except Exception as e:
        logger.error(f"Failed to generate 24h CEK diagram: {e}", exc_info=True)
        return None
