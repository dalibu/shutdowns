"""
Helper functions for group subscription commands.
"""

import logging
from datetime import datetime, timedelta
import pytz
from .bot_base import DEFAULT_INTERVAL_HOURS, get_hours_str
from .formatting import format_group_name


async def handle_group_subscription(
    message,
    group_name: str,
    interval_hours: float,
    ctx
):
    """
    Handle direct group subscription (e.g., /subscribe 3.1).
    
    Args:
        message: Telegram message object
        group_name: Group name (e.g., "3.1")
        interval_hours: Check interval in hours
        ctx: BotContext with provider config
    """
    user_id = message.from_user.id
    db_conn = ctx.db_conn
    provider_code = ctx.provider_code
    logger = ctx.logger or logging.getLogger(__name__)
    
    logger.info(f"Group subscription request: user={user_id}, group={group_name}, interval={interval_hours}h")
    
    # Check if already subscribed to this group
    try:
        cursor = await db_conn.execute("""
            SELECT interval_hours, notification_lead_time
            FROM group_subscriptions
            WHERE user_id = ? AND provider = ? AND group_name = ?
        """, (user_id, provider_code, group_name))
        
        existing = await cursor.fetchone()
        
        if existing:
            existing_interval, existing_lead_time = existing
            if existing_interval == interval_hours:
                # Already subscribed with same interval
                hours_str = f'{interval_hours:g}'.replace('.', ',')
                interval_display = f"{hours_str} {get_hours_str(interval_hours)}"
                
                msg = f"✅ Ви вже підписані на чергу `{format_group_name(group_name)}`\n"
                msg += f"⏰ Інтервал перевірки: {interval_display}\n"
                if existing_lead_time > 0:
                    msg += f"🔔 Сповіщення: за {existing_lead_time} хв до події"
                
                await message.answer(msg, parse_mode="Markdown")
                return
            else:
                # Update interval
                kiev_tz = pytz.timezone('Europe/Kiev')
                now = datetime.now(kiev_tz)
                next_check = now + timedelta(hours=interval_hours)
                
                await db_conn.execute("""
                    UPDATE group_subscriptions
                    SET interval_hours = ?, next_check = ?
                    WHERE user_id = ? AND provider = ? AND group_name = ?
                """, (interval_hours, next_check, user_id, provider_code, group_name))
                await db_conn.commit()
                
                hours_str = f'{interval_hours:g}'.replace('.', ',')
                interval_display = f"{hours_str} {get_hours_str(interval_hours)}"
                
                await message.answer(
                    f"✅ Інтервал перевірки оновлено для черги `{format_group_name(group_name)}`\n"
                    f"⏰ Новий інтервал: {interval_display}",
                    parse_mode="Markdown"
                )
                logger.info(f"Updated group subscription interval: {existing_interval}h → {interval_hours}h")
                return
                
    except Exception as e:
        logger.error(f"Failed to check existing group subscription: {e}")
        await message.answer("❌ **Помилка БД** при перевірці підписки.")
        return
    
    # Check if user has address subscriptions in this group
    addr_count = 0
    addr_list = []
    try:
        cursor = await db_conn.execute("""
            SELECT a.city, a.street, a.house
            FROM subscriptions s
            JOIN addresses a ON a.id = s.address_id
            WHERE s.user_id = ? AND a.provider = ? AND a.group_name = ?
        """, (user_id, provider_code, group_name))
        
        addr_rows = await cursor.fetchall()
        addr_count = len(addr_rows)
        addr_list = [f"{row[0]}, {row[1]}, {row[2]}" for row in addr_rows]
        
    except Exception as e:
        logger.error(f"Failed to check address subscriptions: {e}")
        # Continue anyway
    
    # Create group subscription
    try:
        kiev_tz = pytz.timezone('Europe/Kiev')
        now = datetime.now(kiev_tz)
        next_check = now + timedelta(hours=interval_hours)
        
        await db_conn.execute("""
            INSERT INTO group_subscriptions 
            (user_id, provider, group_name, interval_hours, next_check, last_schedule_hash, notification_lead_time)
            VALUES (?, ?, ?, ?, ?, 'NO_SCHEDULE_FOUND_AT_SUBSCRIPTION', 15)
        """, (user_id, provider_code, group_name, interval_hours, next_check))
        
        await db_conn.commit()
        
    except Exception as e:
        logger.error(f"Failed to create group subscription: {e}")
        await message.answer("❌ **Помилка БД** при створенні підписки.")
        return
    
    # Build success message
    hours_str = f'{interval_hours:g}'.replace('.', ',')
    interval_display = f"{hours_str} {get_hours_str(interval_hours)}"
    
    msg_parts = [
        "✅ **Підписка створена!**",
        f"👥 Черга: `{format_group_name(group_name)}`",
        f"⏰ Інтервал перевірки: {interval_display}",
        f"🔔 Сповіщення: увімкнено (за 15 хв до події)"
    ]
    
    if addr_count > 0:
        msg_parts.append(f"\n📍 У вас також є {addr_count} {'адреса' if addr_count == 1 else ('адреси' if addr_count < 5 else 'адрес')} в цій черзі:")
        for addr in addr_list[:3]:  # Show max 3
            msg_parts.append(f"  • {addr}")
        if addr_count > 3:
            msg_parts.append(f"  • ...і ще {addr_count - 3}")
        msg_parts.append("\n💡 Ви отримаєте **одне** повідомлення зі списком всіх адрес.")
    else:
        msg_parts.append("\n💡 Ви отримаєте повідомлення про зміни графіка для цієї черги.")
    
    await message.answer("\n".join(msg_parts), parse_mode="Markdown")
    logger.info(f"Group subscription created: group={group_name}, interval={interval_hours}h, addr_count={addr_count}")
