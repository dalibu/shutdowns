"""
New version of subscription_checker_task with notification grouping by (user_id, group_name).

This is a complete replacement for the current subscription_checker_task in common/tasks.py.
After testing, this will replace the old version.

Key changes:
1. Groups subscriptions by (user_id, group_name) instead of individual addresses
2. Fetches schedule once per group instead of per address
3. Sends one notification per group with list of addresses
4. Updates all subscriptions in a group simultaneously
"""

async def subscription_checker_task_v2(
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
    from common.formatting import format_address_list
    
    logger = ctx.logger
    provider = ctx.provider_name
    font_path = ctx.font_path
    
    logger.info("Subscription checker started (v2 with grouping).")
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
        # ═══════════════════════════════════════════════════════════════
        groups_to_check = []
        try:
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
            rows = await cursor.fetchall()
            if not rows:
                logger.debug("Subscription check skipped: no users require check.")
                continue

            for row in rows:
                user_id = row[0]
                group_key = row[1]  # For grouping (includes unknown_ prefix if needed)
                group_name = row[2]  # Actual group_name (may be NULL)
                address_ids_str = row[3]
                addresses_str = row[4]
                interval_hours = row[5]
                last_schedule_hash = row[6]
                
                # Parse address IDs
                address_ids = [int(aid) for aid in address_ids_str.split('|')] if address_ids_str else []
                
                # Parse addresses (city::street::house format)
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
                
                # Use first address as sample for API calls
                sample_address = addresses[0] if addresses else None
                
                groups_to_check.append({
                    'user_id': user_id,
                    'group_key': group_key,  # For dict keys
                    'group_name': group_name,  # For display
                    'address_ids': address_ids,
                    'addresses': addresses,
                    'sample_address': sample_address,
                    'interval_hours': interval_hours,
                    'last_schedule_hash': last_schedule_hash
                })
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
                    final_message = f"❌ **Помилка перевірки** для {error_context}: {error_message}\n*Перевірка буде повторена через {f'{interval_hours:g}'.replace('.', ',')} {get_hours_str(interval_hours)}.*"
                    try:
                        await bot.send_message(chat_id=user_id, text=final_message, parse_mode="Markdown")
                    except Exception as e:
                        logger.error(f"Failed to send error message: {e}")

                    for address_id in address_ids:
                        db_updates_fail.append((next_check_time, user_id, address_id))
                    continue

                data = data_or_error
                new_hash = api_results[group_key].get('schedule_hash', get_schedule_hash_compact(data))

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
                    
                    # Update all subscriptions in this group
                    for address_id in address_ids:
                        db_updates_success.append((next_check_time, new_hash, user_id, address_id))
                    logger.info(f"Notification sent for group {group_key}. Hash updated to {new_hash[:8]}.")
                else:
                    logger.debug(f"Check for group {group_key}. No change detected (hash: {new_hash[:16] if new_hash else 'None'}).")
                    for address_id in address_ids:
                        db_updates_fail.append((next_check_time, user_id, address_id))
            
            finally:
                clear_user_context()

        # ═══════════════════════════════════════════════════════════════
        # STEP 5: Update database
        # ═══════════════════════════════════════════════════════════════
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
