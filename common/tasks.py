"""
Common background tasks for power shutdown bots.
Contains subscription checker and alert processing logic.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List, Callable, Awaitable, Optional
import aiosqlite
from aiogram import Bot
from aiogram.types import BufferedInputFile
import pytz

from .bot_base import (
    BotContext,
    ADDRESS_CACHE,
    SCHEDULE_DATA_CACHE,
    DEFAULT_INTERVAL_HOURS,
    CHECKER_LOOP_INTERVAL_SECONDS,
    get_schedule_hash_compact,
    get_hours_str,
    format_user_info,
    parse_time_range,
    get_group_cache,
    update_group_cache,
    get_cached_group_for_address,
    get_group_for_address,
    update_address_group_mapping,
    find_addresses_by_group,
    get_address_id,  # New normalized function
    update_address_group,  # New normalized function
    get_address_by_id,  # New normalized function
)
from .formatting import (
    process_single_day_schedule_compact,
    get_current_status_message,
    format_group_name,
)
from .log_context import set_user_context, clear_user_context


async def _process_alert_for_user(
    bot: Bot,
    user_id: int,
    city: str,
    street: str,
    house: str,
    lead_time: int,
    last_alert_event_start_str: str,
    now: datetime,
    logger: logging.Logger,
    user_info: str = None
) -> Optional[str]:
    """
    Checks if an alert should be sent to the user.
    
    Returns the event datetime string if alert was sent, None otherwise.
    """
    # Set user context for logging
    set_user_context(user_id)
    
    try:
        if user_info is None:
            user_info = str(user_id)
            
        address_key = (city, street, house)
        data = SCHEDULE_DATA_CACHE.get(address_key)
        
        if not data:
            logger.debug(f"Alert check skipped: no schedule data in cache yet for {address_key} (subscription not checked yet or bot restarted)")
            return None
        
        schedule = data.get("schedule", {})
        if not schedule:
            logger.debug(f"Alert check: no schedule data")
            return None
        
        kiev_tz = pytz.timezone('Europe/Kiev')
        
        # Collect all events (start and end of shutdowns)
        events = []
        
        try:
            sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
        except ValueError:
            sorted_dates = sorted(schedule.keys())
        
        for date_str in sorted_dates:
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
                
                start_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=start_min)
                end_dt = kiev_tz.localize(datetime.combine(date_obj, datetime.min.time())) + timedelta(minutes=end_min)
                
                events.append((start_dt, 'off_start'))
                events.append((end_dt, 'on_start'))
        
        events.sort(key=lambda x: x[0])
        
        logger.debug(f"Alert check: found {len(events)} events total")
        
        # Find the next future event
        target_event = None
        for event_dt, event_type in events:
            if event_dt > now:
                target_event = (event_dt, event_type)
                break
        
        if not target_event:
            logger.debug(f"Alert check: no future events found")
            return None
            
        event_dt, event_type = target_event
        time_to_event = (event_dt - now).total_seconds() / 60.0  # minutes
        
        msg_type = "відключення" if event_type == 'off_start' else "включення"
        logger.debug(f"Alert check: next event is {msg_type} at {event_dt.strftime('%H:%M')} (in {time_to_event:.1f} min), lead_time={lead_time} min")
        
        # Check if it's time to send alert
        if 0 < time_to_event <= lead_time:
            event_dt_str = event_dt.isoformat()
            
            if last_alert_event_start_str != event_dt_str:
                # Format address for display
                address_display = f"`{city}, {street}, {house}`"
                
                # Send alert!
                time_str = event_dt.strftime('%H:%M')
                minutes_left = int(time_to_event)
                
                msg = f"⚠️ **Увага!** Через {minutes_left} хв. у {time_str} очікується **{msg_type}** світла.\n📍 Адреса: {address_display}"
                
                logger.info(f"Sending alert: {msg_type} at {time_str} in {minutes_left} min for {address_display}")
                
                try:
                    await bot.send_message(user_id, msg, parse_mode="Markdown")
                    logger.info(f"Alert sent successfully, event_dt={event_dt_str}")
                    return event_dt_str  # Return event time for DB update
                except Exception as e:
                    logger.error(f"Failed to send alert: {e}")
                    return None
            else:
                logger.debug(f"Alert check: alert already sent for this event (last_alert={last_alert_event_start_str})")
        else:
            if time_to_event <= 0:
                logger.debug(f"Alert check: event already passed")
            else:
                logger.debug(f"Alert check: event too far ({time_to_event:.1f} min > {lead_time} min)")
        
        return None
    finally:
        # Always clear user context
        clear_user_context()


async def alert_checker_task(
    bot: Bot,
    db_conn_getter: Callable[[], aiosqlite.Connection],
    logger: logging.Logger
):
    """
    Background task for checking and sending alerts.
    
    Args:
        bot: Aiogram Bot instance
        db_conn_getter: Callable that returns current db connection
        logger: Logger instance for this provider
    """
    logger.info("Alert checker started.")
    while True:
        await asyncio.sleep(60)
        db_conn = db_conn_getter()
        if db_conn is None:
            continue

        kiev_tz = pytz.timezone('Europe/Kiev')
        now = datetime.now(kiev_tz)

        try:
            cursor = await db_conn.execute("""
                SELECT s.user_id, a.city, a.street, a.house, s.notification_lead_time, s.last_alert_event_start
                FROM subscriptions s
                JOIN addresses a ON a.id = s.address_id
                WHERE s.notification_lead_time > 0
            """)
            rows = await cursor.fetchall()
            
            if rows:
                logger.debug(f"Alert check cycle at {now.strftime('%H:%M:%S')}: checking {len(rows)} user(s) with notifications enabled")
            
            for row in rows:
                user_id, city, street, house, lead_time, last_alert_event_start_str = row
                
                # Get user info for logging
                try:
                    user = await bot.get_chat(user_id)
                    user_info = format_user_info(user)
                except:
                    user_info = str(user_id)
                
                logger.debug(f"Processing alerts, lead_time={lead_time} min")
                
                new_last_alert = await _process_alert_for_user(
                    bot, user_id, city, street, house, lead_time, last_alert_event_start_str, now, logger, user_info
                )
                
                if new_last_alert:
                    # Set context for the DB update log
                    set_user_context(user_id)
                    logger.info(f"Updating last_alert_event_start to {new_last_alert}")
                    clear_user_context()
                    
                    await db_conn.execute(
                        "UPDATE subscriptions SET last_alert_event_start = ? WHERE user_id = ?",
                        (new_last_alert, user_id)
                    )
                    await db_conn.commit()

        except Exception as e:
            logger.error(f"Error in alert_checker_task loop: {e}", exc_info=True)



async def subscription_checker_task(
    bot: Bot,
    ctx: BotContext,
    db_conn_getter: Callable[[], aiosqlite.Connection],
    get_shutdowns_data: Callable[..., Awaitable[dict]],
    generate_24h_image: Callable,
    generate_48h_image: Callable,
    get_cached_group: Optional[Callable[..., Awaitable[Optional[str]]]] = None
):
    """
    Background task: periodically checks schedule for all subscribed users.
    
    Args:
        bot: Aiogram Bot instance
        ctx: BotContext with provider configuration
        db_conn_getter: Callable that returns current db connection
        get_shutdowns_data: Async function to fetch shutdown data
        generate_24h_image: Function to generate 24h schedule image
        generate_48h_image: Function to generate 48h schedule image
        get_cached_group: Optional function to get cached group for address (CEK optimization)
    """
    logger = ctx.logger
    provider = ctx.provider_name
    font_path = ctx.font_path
    
    logger.info("Subscription checker started.")
    while True:
        await asyncio.sleep(CHECKER_LOOP_INTERVAL_SECONDS)
        db_conn = db_conn_getter()
        if db_conn is None:
            logger.error("DB connection is not available. Skipping check cycle.")
            continue

        import pytz
        kiev_tz = pytz.timezone('Europe/Kiev')
        now = datetime.now(kiev_tz)
        users_to_check = []
        try:
            cursor = await db_conn.execute("""
                SELECT s.user_id, a.city, a.street, a.house, s.interval_hours, s.last_schedule_hash
                FROM subscriptions s
                JOIN addresses a ON a.id = s.address_id
                WHERE s.next_check <= ?
            """, (now,))
            rows = await cursor.fetchall()
            if not rows:
                logger.debug("Subscription check skipped: no users require check.")
                continue

            for row in rows:
                users_to_check.append({
                    'user_id': row[0],
                    'city': row[1],
                    'street': row[2],
                    'house': row[3],
                    'interval_hours': row[4],
                    'last_schedule_hash': row[5]
                })
        except Exception as e:
            logger.error(f"Failed to fetch subscriptions from DB: {e}", exc_info=True)
            continue

        logger.debug(f"Starting subscription check for {len(users_to_check)} users at {now.strftime('%H:%M:%S')}.")

        addresses_to_check_map: Dict[Tuple[str, str, str], List[int]] = {}
        for sub_data in users_to_check:
            address_key = (sub_data['city'], sub_data['street'], sub_data['house'])
            if address_key not in addresses_to_check_map:
                addresses_to_check_map[address_key] = []
            addresses_to_check_map[address_key].append(sub_data['user_id'])

        logger.info(f"Checking {len(addresses_to_check_map)} unique addresses now for {len(users_to_check)} users.")

        api_results: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

        for address_key in addresses_to_check_map.keys():
            city, street, house = address_key
            address_str = f"`{city}, {street}, {house}`"
            
            # Get first user_id for this address (for logging context)
            user_ids_for_address = addresses_to_check_map[address_key]
            first_user_id = user_ids_for_address[0] if user_ids_for_address else None
            
            try:
                # Set user context for parser logs (use first user from list)
                if first_user_id:
                    set_user_context(first_user_id)
                
                # === GROUP CACHE OPTIMIZATION (with normalized addresses) ===
                # Step 1: Get address_id and cached group
                address_id, cached_group = await get_address_id(
                    db_conn, city, street, house
                )
                
                data = None
                current_hash = None
                used_cache = False
                
                if address_id and cached_group:
                    logger.debug(f"Address {address_str} [ID:{address_id}] belongs to group {cached_group}")
                    
                    # Step 2: Try to get schedule from group cache
                    group_cache = await get_group_cache(
                        db_conn, cached_group, ctx.provider_code
                    )
                    
                    if group_cache:
                        # Cache hit! Use cached data
                        logger.info(f"✓ Cache HIT for {address_str}, group {cached_group} (age: fresh)")
                        data = group_cache['data']
                        current_hash = group_cache['hash']
                        used_cache = True
                    else:
                        # Cache miss or stale - need to fetch from provider
                        logger.info(f"✗ Cache MISS for {address_str}, group {cached_group} (stale or not found)")
                
                # Step 3: Fetch from provider if needed
                if data is None:
                    logger.debug(f"Calling parser for address {address_str}")
                    
                    # Use cached_group if available (CEK optimization for parser)
                    if cached_group and get_cached_group:
                        data = await get_shutdowns_data(city, street, house, cached_group)
                    else:
                        data = await get_shutdowns_data(city, street, house)
                    
                    current_hash = get_schedule_hash_compact(data)
                    
                    # Step 4: Update group cache with fresh data
                    if data.get('group'):
                        group_from_parser = data['group']
                        await update_group_cache(
                            db_conn, group_from_parser, ctx.provider_code,
                            current_hash, data
                        )
                        logger.debug(f"Updated group cache for {group_from_parser}")
                
                # Step 5: Update address group in normalized table
                if address_id and data and data.get('group'):
                    await update_address_group(db_conn, address_id, data['group'])
                
                
                # Log parser results for debugging
                schedule = data.get("schedule", {}) if data else {}
                if logger.level <= logging.DEBUG:
                    import json
                    cache_status = "CACHE" if used_cache else "PARSER"
                    logger.debug(f"{cache_status} returned for {address_str}: hash={current_hash[:16] if current_hash else 'None'}, schedule={json.dumps(schedule, ensure_ascii=False)}")
                
                # Update in-memory caches
                ADDRESS_CACHE[address_key] = {
                    'last_schedule_hash': current_hash,
                    'last_checked': now
                }
                SCHEDULE_DATA_CACHE[address_key] = data
                
                api_results[address_key] = data
                
            except Exception as e:
                logger.error(f"Error checking address {address_str}: {e}")
                api_results[address_key] = {"error": str(e)}
            finally:
                # Always clear context after processing address
                clear_user_context()


        db_updates_success = []
        db_updates_fail = []

        for sub_data in users_to_check:
            user_id = sub_data['user_id']
            city = sub_data['city']
            street = sub_data['street']
            house = sub_data['house']
            address_key = (city, street, house)
            address_str = f"`{city}, {street}, {house}`"
            interval_hours = sub_data.get('interval_hours', DEFAULT_INTERVAL_HOURS)
            interval_delta = timedelta(hours=interval_hours)
            next_check_time = now + interval_delta
            data_or_error = api_results.get(address_key)
            
            # Set user context for all logs related to this user's notification
            set_user_context(user_id)
            
            try:
                # Get address_id for this subscription
                address_id, _ = await get_address_id(db_conn, city, street, house)
                if not address_id:
                    logger.error(f"Failed to get address_id for {address_str}")
                    continue

                if data_or_error is None:
                    logger.error(f"Address {address_key} was checked, but result is missing.")
                    db_updates_fail.append((next_check_time, user_id, address_id))
                    continue

                if "error" in data_or_error:
                    error_message = data_or_error['error']
                    final_message = f"❌ **Помилка перевірки** для {address_str}: {error_message}\n*Перевірка буде повторена через {f'{interval_hours:g}'.replace('.', ',')} {get_hours_str(interval_hours)}.*"
                    try:
                        await bot.send_message(chat_id=user_id, text=final_message, parse_mode="Markdown")
                    except Exception as e:
                        logger.error(f"Failed to send error message: {e}")

                    db_updates_fail.append((next_check_time, user_id, address_id))
                    continue

                data = data_or_error
                last_hash = sub_data.get('last_schedule_hash')
                new_hash = ADDRESS_CACHE[address_key]['last_schedule_hash']

                # Check if there are real changes in schedule
                schedule = data.get("schedule", {})
                has_actual_schedule = any(slots for slots in schedule.values() if slots)
                
                # Log hash comparison for debugging
                if last_hash and new_hash != last_hash:
                    logger.info(f"Hash changed for {address_str}: {last_hash[:16] if last_hash and len(last_hash) >= 16 else last_hash} → {new_hash[:16]}")
                    # Log normalized schedule for deep debugging (only when hash changes)
                    if logger.level <= logging.DEBUG:
                        from .bot_base import normalize_schedule_for_hash
                        import json
                        normalized = normalize_schedule_for_hash(data)
                        logger.debug(f"Normalized schedule: {json.dumps(normalized, ensure_ascii=False, sort_keys=True)}")
                
                # Send notification only if:
                # 1. Hash changed AND
                # 2. There is actual schedule OR it's first check (last_hash in special values)
                should_notify = (
                    new_hash != last_hash and 
                    (has_actual_schedule or last_hash in (None, "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION"))
                )
                
                if should_notify:
                    group = format_group_name(data.get("group"))
                    
                    interval_str = f"{f'{interval_hours:g}'.replace('.', ',')} год"
                    update_header = "🔔 **ОНОВЛЕННЯ ГРАФІКУ!**" if last_hash not in (None, "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION") else "🔔 **Графік перевірено**"
                    
                    try:
                        sorted_dates = sorted(schedule.keys(), key=lambda d: datetime.strptime(d, '%d.%m.%y'))
                    except ValueError:
                        sorted_dates = sorted(schedule.keys())

                    # Generate diagram (24h or 48h)
                    has_shutdowns_tomorrow = False
                    if len(sorted_dates) >= 2:
                        tomorrow_date = sorted_dates[1]
                        if schedule.get(tomorrow_date):
                            has_shutdowns_tomorrow = True
                    
                    image_data = None
                    diagram_caption = ""
                    filename = ""

                    kiev_tz = pytz.timezone('Europe/Kiev')
                    current_time = datetime.now(kiev_tz)

                    if has_shutdowns_tomorrow:
                        # 48 hours
                        days_slots_48h = {}
                        for date in sorted_dates[:2]:
                            days_slots_48h[date] = schedule.get(date, [])
                        
                        if any(slots for slots in days_slots_48h.values()):
                            image_data = generate_48h_image(days_slots_48h, font_path, current_time=current_time)
                        diagram_caption = "🕙 **Загальний графік на 48 годин**"
                        filename = "schedule_48h_update.png"
                    else:
                        # 24 hours
                        if sorted_dates:
                            today_date = sorted_dates[0]
                            today_slots = {today_date: schedule.get(today_date, [])}
                            if schedule.get(today_date):
                                image_data = generate_24h_image(today_slots, font_path, current_time=current_time)
                                diagram_caption = "🕙 **Графік на сьогодні**"
                                filename = "schedule_24h_update.png"

                    # Build message parts
                    message_parts = []
                    message_parts.append(f"{update_header}\nдля {address_str} (інтервал {interval_str})")
                    message_parts.append(f"📍 Адреса: `{city}, {street}, {house}`\n👥 Черга: `{group}`")
                    
                    if diagram_caption:
                        message_parts.append(diagram_caption)
                    
                    # Text data by days
                    for date in sorted_dates:
                        slots = schedule[date]
                        day_text = process_single_day_schedule_compact(date, slots, provider)
                        if day_text and day_text.strip():
                            message_parts.append(day_text.strip())

                    # Status message
                    status_msg = get_current_status_message(schedule)
                    if status_msg:
                        message_parts.append(status_msg)
                    
                    # Combine all parts
                    full_message = "\n\n".join(message_parts)
                    
                    # Send message with photo and caption
                    try:
                        if image_data:
                            # Telegram allows up to 1024 characters in caption
                            if len(full_message) <= 1024:
                                image_file = BufferedInputFile(image_data, filename=filename)
                                await bot.send_photo(
                                    chat_id=user_id,
                                    photo=image_file,
                                    caption=full_message,
                                    parse_mode="Markdown"
                                )
                            else:
                                # Send photo with short caption and text separately
                                short_caption = "\n\n".join(message_parts[:3])  # Header + address + diagram
                                remaining_text = "\n\n".join(message_parts[3:])  # Rest
                                
                                image_file = BufferedInputFile(image_data, filename=filename)
                                await bot.send_photo(
                                    chat_id=user_id,
                                    photo=image_file,
                                    caption=short_caption,
                                    parse_mode="Markdown"
                                )
                                await bot.send_message(
                                    chat_id=user_id,
                                    text=remaining_text,
                                    parse_mode="Markdown",
                                    disable_notification=True
                                )
                        else:
                            # No diagram - just send text
                            await bot.send_message(
                                chat_id=user_id,
                                text=full_message,
                                parse_mode="Markdown"
                            )
                    except Exception as e:
                        logger.error(f"Failed to send update notification: {e}")
                    
                    db_updates_success.append((next_check_time, new_hash, user_id, address_id))
                    logger.info(f"Notification sent. Hash updated to {new_hash[:8]}.")
                else:
                    logger.debug(f"Check for {address_str}. No change detected (hash: {new_hash[:16] if new_hash else 'None'}, last: {last_hash[:16] if last_hash and len(last_hash) >= 16 else last_hash}).")
                    db_updates_fail.append((next_check_time, user_id, address_id))
            
            finally:
                # Always clear context after processing user
                clear_user_context()


        try:
            if db_updates_success:
                await db_conn.executemany("""
                    UPDATE subscriptions 
                    SET next_check = ?, last_schedule_hash = ? 
                    WHERE user_id = ? AND address_id = ?
                """, db_updates_success)
            if db_updates_fail:
                await db_conn.executemany("""
                    UPDATE subscriptions 
                    SET next_check = ? 
                    WHERE user_id = ? AND address_id = ?
                """, db_updates_fail)
            await db_conn.commit()
            logger.debug(f"DB updated for {len(db_updates_success)} success and {len(db_updates_fail)} other checks.")
        except Exception as e:
             logger.error(f"Failed to batch update subscriptions in DB: {e}", exc_info=True)
