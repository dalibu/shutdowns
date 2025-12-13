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
    format_address_list,
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
    user_info: str = None,
    addresses: List[Dict[str, str]] = None,
    group_name: str = None
) -> Optional[str]:
    """
    Checks if an alert should be sent to the user.
    
    Now supports grouped alerts - displays all addresses in the group.
    
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
                # Format address(es) for display using helper
                if addresses and len(addresses) > 0:
                    group_display = format_group_name(group_name) if group_name else "невідомо"
                    address_info = format_address_list(addresses, group_display)
                else:
                    # Fallback for single address (backward compatibility)
                    address_info = f"📍 Адреса: `{city}, {street}, {house}`"
                
                # Send alert!
                time_str = event_dt.strftime('%H:%M')
                minutes_left = int(time_to_event)
                
                msg = f"⚠️ **Увага!** Через {minutes_left} хв. у {time_str} очікується **{msg_type}** світла.\n\n{address_info}"
                
                logger.info(f"Sending alert: {msg_type} at {time_str} in {minutes_left} min for group {group_name or 'unknown'}")
                
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
    
    Now groups alerts by (user_id, group_name) to avoid duplicate notifications
    for users with multiple addresses in the same group.
    
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
            # Fetch subscriptions grouped by (user_id, group_name)
            # Includes BOTH address subscriptions AND direct group subscriptions
            cursor = await db_conn.execute("""
                SELECT 
                    user_id,
                    group_key,
                    group_name,
                    address_ids,
                    addresses,
                    notification_lead_time,
                    last_alert_event_start
                FROM (
                    -- Address-based subscriptions
                    SELECT 
                        s.user_id,
                        COALESCE(a.group_name, 'unknown_' || a.id) as group_key,
                        a.group_name,
                        GROUP_CONCAT(a.id, '|') as address_ids,
                        GROUP_CONCAT(a.city || '::' || a.street || '::' || a.house, '|') as addresses,
                        MIN(s.notification_lead_time) as notification_lead_time,
                        MIN(s.last_alert_event_start) as last_alert_event_start
                    FROM subscriptions s
                    JOIN addresses a ON a.id = s.address_id
                    WHERE s.notification_lead_time > 0
                    GROUP BY s.user_id, group_key
                    
                    UNION ALL
                    
                    -- Direct group subscriptions
                    SELECT 
                        gs.user_id,
                        gs.group_name as group_key,
                        gs.group_name,
                        NULL as address_ids,
                        NULL as addresses,
                        gs.notification_lead_time,
                        gs.last_alert_event_start
                    FROM group_subscriptions gs
                    WHERE gs.notification_lead_time > 0
                )
            """)
            rows = await cursor.fetchall()
            
            if rows:
                logger.debug(f"Alert check cycle at {now.strftime('%H:%M:%S')}: checking {len(rows)} user-group combinations with notifications enabled")
            
            for row in rows:
                user_id = row[0]
                group_key = row[1]
                group_name = row[2]
                address_ids_str = row[3]
                addresses_str = row[4]
                lead_time = row[5]
                last_alert_event_start_str = row[6]
                
                # Parse address IDs
                address_ids = [int(aid) for aid in address_ids_str.split('|')] if address_ids_str else []
                
                # Parse addresses
                addresses = []
                if addresses_str:
                    for addr_str in addresses_str.split('|'):
                        parts = addr_str.split('::')
                        if len(parts) == 3:
                            addresses.append({
                                'city': parts[0],
                                'street': parts[1],
                                'house': parts[2]
                            })
                
                # Get sample address for alert checking
                if addresses:
                    # Address subscription - use first address
                    sample = addresses[0]
                    city = sample['city']
                    street = sample['street']
                    house = sample['house']
                else:
                    # Group subscription - fetch any address from this group
                    if not group_name:
                        continue
                    
                    try:
                        addr_cursor = await db_conn.execute(
                            "SELECT city, street, house FROM addresses WHERE group_name = ? LIMIT 1",
                            (group_name,)
                        )
                        addr_row = await addr_cursor.fetchone()
                        if not addr_row:
                            logger.warning(f"No addresses found for group {group_name}, skipping alert check")
                            continue
                        
                        city, street, house = addr_row
                    except Exception as e:
                        logger.error(f"Failed to fetch sample address for group {group_name}: {e}")
                        continue
                
                # Get user info for logging
                try:
                    user = await bot.get_chat(user_id)
                    user_info = format_user_info(user)
                except:
                    user_info = str(user_id)
                
                logger.debug(f"Processing alerts for group {group_key}, lead_time={lead_time} min")
                
                # Check alert using sample address (all addresses in group have same schedule)
                new_last_alert = await _process_alert_for_user(
                    bot, user_id, city, street, house, lead_time, last_alert_event_start_str, 
                    now, logger, user_info, addresses, group_name
                )
                
                if new_last_alert:
                    # Set context for the DB update log
                    set_user_context(user_id)
                    logger.info(f"Updating last_alert_event_start to {new_last_alert} for group {group_key}")
                    clear_user_context()
                    
                    # Update all subscriptions in this group
                    for address_id in address_ids:
                        await db_conn.execute(
                            "UPDATE subscriptions SET last_alert_event_start = ? WHERE user_id = ? AND address_id = ?",
                            (new_last_alert, user_id, address_id)
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
    Background task: periodically checks schedule for subscribed users.
    
    Now groups notifications by (user_id, group_name) to avoid duplicate messages
    for users with multiple addresses in the same group.
    
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
        
        # ═══════════════════════════════════════════════════════════════
        # STEP 1: Fetch subscriptions grouped by (user_id, group_name)
        # Now supports both address-based and direct group subscriptions
        # ═══════════════════════════════════════════════════════════════
        groups_to_check = []
        try:
            # 1a. Fetch address-based subscriptions grouped by user+group
            cursor = await db_conn.execute("""
                SELECT 
                    s.user_id,
                    COALESCE(a.group_name, 'unknown_' || a.id) as group_key,
                    a.group_name,
                    GROUP_CONCAT(a.id, '|') as address_ids,
                    GROUP_CONCAT(a.city || '::' || a.street || '::' || a.house, '|') as addresses,
                    MIN(s.interval_hours) as interval_hours,
                    MIN(s.last_schedule_hash) as last_schedule_hash
                FROM subscriptions s
                JOIN addresses a ON a.id = s.address_id
                WHERE s.next_check <= ?
                GROUP BY s.user_id, group_key
            """, (now,))
            addr_rows = await cursor.fetchall()
            
            # 1b. Fetch direct group subscriptions
            cursor = await db_conn.execute("""
                SELECT 
                    user_id,
                    group_name as group_key,
                    group_name,
                    NULL as address_ids,
                    NULL as addresses,
                    interval_hours,
                    last_schedule_hash
                FROM group_subscriptions
                WHERE next_check <= ? AND provider = ?
            """, (now, ctx.provider_code))
            group_rows = await cursor.fetchall()
            
            # Merge results: use dict to group by (user_id, group_key)
            merged = {}
            
            # Process address subscriptions
            for row in addr_rows:
                user_id = row[0]
                group_key = row[1]
                key = (user_id, group_key)
                
                if key not in merged:
                    merged[key] = {
                        'user_id': user_id,
                        'group_key': group_key,
                        'group_name': row[2],
                        'address_ids': [],
                        'addresses': [],
                        'interval_hours': row[5],
                        'last_schedule_hash': row[6],
                        'has_group_sub': False
                    }
                
                # Parse and add address IDs
                address_ids_str = row[3]
                if address_ids_str:
                    merged[key]['address_ids'].extend([int(aid) for aid in address_ids_str.split('|')])
                
                # Parse and add addresses
                addresses_str = row[4]
                if addresses_str:
                    for addr_str in addresses_str.split('|'):
                        parts = addr_str.split('::')
                        if len(parts) == 3:
                            merged[key]['addresses'].append({
                                'city': parts[0],
                                'street': parts[1],
                                'house': parts[2]
                            })
            
            # Process group subscriptions
            for row in group_rows:
                user_id = row[0]
                group_key = row[1]
                key = (user_id, group_key)
                
                if key not in merged:
                    # Pure group subscription (no addresses)
                    merged[key] = {
                        'user_id': user_id,
                        'group_key': group_key,
                        'group_name': row[2],
                        'address_ids': [],
                        'addresses': [],
                        'interval_hours': row[5],
                        'last_schedule_hash': row[6],
                        'has_group_sub': True
                    }
                else:
                    # User has both addresses AND group subscription for same group
                    # Just flag it, we'll still send one notification
                    merged[key]['has_group_sub'] = True
                    # Use MIN interval_hours and hash
                    if row[5] < merged[key]['interval_hours']:
                        merged[key]['interval_hours'] = row[5]
            
            # Convert to list
            for group_data in merged.values():
                # Use first address as sample if available
                sample_address = group_data['addresses'][0] if group_data['addresses'] else None
                group_data['sample_address'] = sample_address
                groups_to_check.append(group_data)
            
            if not groups_to_check:
                logger.debug("Subscription check skipped: no users require check.")
                continue
                
        except Exception as e:
            logger.error(f"Failed to fetch subscriptions from DB: {e}", exc_info=True)
            continue

        logger.debug(f"Starting subscription check for {len(groups_to_check)} user-group combinations at {now.strftime('%H:%M:%S')}.")

        # ═══════════════════════════════════════════════════════════════
        # STEP 2: Group by group_key for efficient API calls
        # ═══════════════════════════════════════════════════════════════
        groups_to_fetch_map = {}
        for group_data in groups_to_check:
            group_key = group_data['group_key']
            if group_key not in groups_to_fetch_map:
                groups_to_fetch_map[group_key] = {
                    'group_key': group_key,
                    'group_name': group_data['group_name'],
                    'sample_address': group_data['sample_address'],
                    'user_groups': []
                }
            groups_to_fetch_map[group_key]['user_groups'].append(group_data)

        logger.info(f"Checking {len(groups_to_fetch_map)} unique groups for {len(groups_to_check)} user-group combinations.")

        # ═══════════════════════════════════════════════════════════════
        # STEP 3: Fetch schedule for each unique group
        # ═══════════════════════════════════════════════════════════════
        api_results = {}

        for group_key, group_info in groups_to_fetch_map.items():
            group_name = group_info['group_name']
            sample_addr = group_info['sample_address']
            
            # If no sample address (group-only subscription), fetch any address from this group
            if not sample_addr and group_name:
                try:
                    cursor = await db_conn.execute("""
                        SELECT city, street, house
                        FROM addresses
                        WHERE provider = ? AND group_name = ?
                        LIMIT 1
                    """, (ctx.provider_code, group_name))
                    addr_row = await cursor.fetchone()
                    if addr_row:
                        sample_addr = {
                            'city': addr_row[0],
                            'street': addr_row[1],
                            'house': addr_row[2]
                        }
                        logger.debug(f"Found sample address for group {group_name}: {addr_row[0]}, {addr_row[1]}, {addr_row[2]}")
                    else:
                        logger.error(f"No addresses found in DB for group {group_name}")
                        api_results[group_key] = {"error": f"No addresses in database for group {group_name}"}
                        continue
                except Exception as e:
                    logger.error(f"Failed to fetch sample address for group {group_name}: {e}")
                    api_results[group_key] = {"error": str(e)}
                    continue
            
            if not sample_addr:
                logger.error(f"No sample address for group {group_key}, skipping")
                api_results[group_key] = {"error": "No sample address"}
                continue
            
            city = sample_addr['city']
            street = sample_addr['street']
            house = sample_addr['house']
            address_str = f"`{city}, {street}, {house}`"
            
            # Get first user_id for logging context
            first_user_group = group_info['user_groups'][0]
            first_user_id = first_user_group['user_id']
            
            try:
                # Set user context for parser logs
                set_user_context(first_user_id)
                
                # Try group cache first (if group is known)
                data = None
                current_hash = None
                used_cache = False
                
                if group_name and not group_key.startswith('unknown_'):
                    # Try group cache
                    group_cache = await get_group_cache(db_conn, group_name, ctx.provider_code)
                    
                    if group_cache:
                        # Cache hit!
                        logger.info(f"✓ Group cache HIT for {group_name} (sample: {address_str})")
                        data = group_cache['data']
                        current_hash = group_cache['hash']
                        used_cache = True
                
                # Fetch from provider if needed
                if data is None:
                    logger.debug(f"Calling parser for {address_str} (group: {group_name or 'unknown'})")
                    
                    # Use cached_group if available (CEK optimization)
                    if group_name and get_cached_group:
                        data = await get_shutdowns_data(city, street, house, group_name)
                    else:
                        data = await get_shutdowns_data(city, street, house)
                    
                    current_hash = get_schedule_hash_compact(data)
                    
                    # Update group cache with fresh data
                    if data.get('group'):
                        group_from_parser = data['group']
                        await update_group_cache(
                            db_conn, group_from_parser, ctx.provider_code,
                            current_hash, data
                        )
                        logger.debug(f"Updated group cache for {group_from_parser}")
                        
                        # Update address group in DB
                        address_id, _ = await get_address_id(db_conn, city, street, house)
                        if address_id:
                            await update_address_group(db_conn, address_id, group_from_parser)
                
                # Log results
                schedule = data.get("schedule", {}) if data else {}
                if logger.level <= logging.DEBUG:
                    import json
                    cache_status = "CACHE" if used_cache else "PARSER"
                    logger.debug(f"{cache_status} returned for group {group_key}: hash={current_hash[:16] if current_hash else 'None'}")
                
                # Update SCHEDULE_DATA_CACHE for alerts to work
                address_key = (city, street, house)
                ADDRESS_CACHE[address_key] = {
                    'last_schedule_hash': current_hash,
                    'last_checked': now
                }
                SCHEDULE_DATA_CACHE[address_key] = data
                
                api_results[group_key] = data
                
            except Exception as e:
                logger.error(f"Error checking group {group_key} (address {address_str}): {e}")
                api_results[group_key] = {"error": str(e)}
            finally:
                clear_user_context()

        # ═══════════════════════════════════════════════════════════════
        # STEP 4: Process results and send notifications
        # ═══════════════════════════════════════════════════════════════
        db_updates_success = []
        db_updates_fail = []

        for group_data in groups_to_check:
            user_id = group_data['user_id']
            group_key = group_data['group_key']
            group_name = group_data['group_name']
            address_ids = group_data['address_ids']
            addresses = group_data['addresses']
            interval_hours = group_data.get('interval_hours', DEFAULT_INTERVAL_HOURS)
            last_hash = group_data.get('last_schedule_hash')
            
            interval_delta = timedelta(hours=interval_hours)
            next_check_time = now + interval_delta
            
            data_or_error = api_results.get(group_key)
            
            # Set user context for all logs related to this user's notification
            set_user_context(user_id)
            
            try:
                if data_or_error is None:
                    logger.error(f"Group {group_key} was checked, but result is missing.")
                    for address_id in address_ids:
                        db_updates_fail.append((next_check_time, user_id, address_id))
                    continue

                if "error" in data_or_error:
                    error_message = data_or_error['error']
                    # For grouped errors, show group name if available
                    error_context = f"черги {format_group_name(group_name)}" if group_name else "групи адрес"
                    
                    # IMPORTANT: Do NOT send technical errors to users during automatic checks
                    # This prevents spamming users when there are temporary parser/server issues
                    # Errors are logged for debugging, but users won't be notified
                    logger.warning(
                        f"Subscription check failed for user {user_id}, {error_context}: {error_message}. "
                        f"Skipping notification. Next check in {interval_hours}h"
                    )
                    
                    # Note: We do NOT send this message to avoid user confusion:
                    # final_message = f"❌ **Помилка перевірки** для {error_context}: {error_message}\n*Перевірка буде повторена через {f'{interval_hours:g}'.replace('.', ',')} {get_hours_str(interval_hours)}.*"

                    for address_id in address_ids:
                        db_updates_fail.append((next_check_time, user_id, address_id))
                    continue

                data = data_or_error
                new_hash = get_schedule_hash_compact(data)

                # Check if there are real changes in schedule
                schedule = data.get("schedule", {})
                has_actual_schedule = any(slots for slots in schedule.values() if slots)
                
                # Log hash comparison
                if last_hash and new_hash != last_hash:
                    logger.info(f"Hash changed for group {group_key}: {last_hash[:16] if last_hash and len(last_hash) >= 16 else last_hash} → {new_hash[:16]}")
                
                # Send notification only if hash changed AND there is actual schedule
                should_notify = (
                    new_hash != last_hash and 
                    (has_actual_schedule or last_hash in (None, "NO_SCHEDULE_FOUND_AT_SUBSCRIPTION"))
                )
                
                if should_notify:
                    group_display = format_group_name(group_name) if group_name else "невідомо"
                    
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
                    message_parts.append(f"{update_header} (інтервал {interval_str})")
                    
                    # Use format_address_list helper for address display
                    address_info = format_address_list(addresses, group_display)
                    message_parts.append(address_info)
                    
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
                                short_caption = "\n\n".join(message_parts[:3])
                                remaining_text = "\n\n".join(message_parts[3:])
                                
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
                            await bot.send_message(
                                chat_id=user_id,
                                text=full_message,
                                parse_mode="Markdown"
                            )
                    except Exception as e:
                        logger.error(f"Failed to send update notification: {e}")
                    
                    # Update all address subscriptions in this group
                    for address_id in address_ids:
                        db_updates_success.append((next_check_time, new_hash, user_id, address_id))
                    
                    # Also update group subscription if exists
                    if group_data.get('has_group_sub') and group_name:
                        db_updates_success.append(('group', next_check_time, new_hash, user_id, group_name))
                    
                    logger.info(f"Notification sent for group {group_key}. Hash updated to {new_hash[:8]}.")
                else:
                    logger.debug(f"Check for group {group_key}. No change detected (hash: {new_hash[:16] if new_hash else 'None'}).")
                    for address_id in address_ids:
                        db_updates_fail.append((next_check_time, user_id, address_id))
                    
                    # Also update group subscription if exists
                    if group_data.get('has_group_sub') and group_name:
                        db_updates_fail.append(('group', next_check_time, user_id, group_name))
            
            finally:
                clear_user_context()

        # ═══════════════════════════════════════════════════════════════
        # STEP 5: Update database
        # ═══════════════════════════════════════════════════════════════
        try:
            # Separate address and group subscription updates
            addr_updates_success = [u for u in db_updates_success if u[0] != 'group']
            group_updates_success = [(u[1], u[2], u[3], u[4]) for u in db_updates_success if u[0] == 'group']
            
            addr_updates_fail = [u for u in db_updates_fail if u[0] != 'group']
            group_updates_fail = [(u[1], u[2], u[3]) for u in db_updates_fail if u[0] == 'group']
            
            # Update address subscriptions
            if addr_updates_success:
                await db_conn.executemany("""
                    UPDATE subscriptions 
                    SET next_check = ?, last_schedule_hash = ? 
                    WHERE user_id = ? AND address_id = ?
                """, addr_updates_success)
            if addr_updates_fail:
                await db_conn.executemany("""
                    UPDATE subscriptions 
                    SET next_check = ? 
                    WHERE user_id = ? AND address_id = ?
                """, addr_updates_fail)
            
            # Update group subscriptions
            if group_updates_success:
                await db_conn.executemany("""
                    UPDATE group_subscriptions 
                    SET next_check = ?, last_schedule_hash = ? 
                    WHERE user_id = ? AND group_name = ? AND provider = ?
                """, [(n, h, u, g, ctx.provider_code) for n, h, u, g in group_updates_success])
            if group_updates_fail:
                await db_conn.executemany("""
                    UPDATE group_subscriptions 
                    SET next_check = ? 
                    WHERE user_id = ? AND group_name = ? AND provider = ?
                """, [(n, u, g, ctx.provider_code) for n, u, g in group_updates_fail])
            
            await db_conn.commit()
            total_success = len(addr_updates_success) + len(group_updates_success)
            total_fail = len(addr_updates_fail) + len(group_updates_fail)
            logger.debug(f"DB updated for {total_success} success and {total_fail} other checks.")
        except Exception as e:
             logger.error(f"Failed to batch update subscriptions in DB: {e}", exc_info=True)
