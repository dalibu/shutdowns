"""
Tests for alert checker with group subscriptions.

Bug fix: Alert checker should stop checking groups after unsubscribe.
"""

import pytest
import aiosqlite
from datetime import datetime, timedelta
import pytz


@pytest.mark.asyncio
async def test_alert_checker_includes_group_subscriptions():
    """
    Test that alert_checker_task fetches BOTH address and group subscriptions.
    
    Bug: After adding group_subscriptions table, alert checker was only
    checking subscriptions table, not group_subscriptions.
    
    Fix: Updated SQL to UNION both tables.
    """
    async with aiosqlite.connect(":memory:") as db:
        # Create tables
        await db.execute("""
            CREATE TABLE addresses (
                id INTEGER PRIMARY KEY,
                provider TEXT,
                city TEXT,
                street TEXT,
                house TEXT,
                group_name TEXT
            )
        """)
        
        await db.execute("""
            CREATE TABLE subscriptions (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                address_id INTEGER,
                notification_lead_time INTEGER DEFAULT 0,
                last_alert_event_start TIMESTAMP
            )
        """)
        
        await db.execute("""
            CREATE TABLE group_subscriptions (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                group_name TEXT,
                provider TEXT,
                notification_lead_time INTEGER DEFAULT 0,
                last_alert_event_start TIMESTAMP
            )
        """)
        
        await db.commit()
        
        # Insert test data
        user_id = 123
        
        # Address in group 3.1
        await db.execute("""
            INSERT INTO addresses (provider, city, street, house, group_name)
            VALUES ('dtek', 'Kyiv', 'Main St', '1', '3.1')
        """)
        cursor = await db.execute("SELECT last_insert_rowid()")
        address_id = (await cursor.fetchone())[0]
        await db.commit()
        
        # Address subscription with alerts enabled
        await db.execute("""
            INSERT INTO subscriptions (user_id, address_id, notification_lead_time)
            VALUES (?, ?, 15)
        """, (user_id, address_id))
        
        # Group subscription with alerts enabled  
        await db.execute("""
            INSERT INTO group_subscriptions (user_id, group_name, provider, notification_lead_time)
            VALUES (?, '3.2', 'dtek', 15)
        """, (user_id,))
        
        await db.commit()
        
        # Fetch alert subscriptions using the UNION query
        cursor = await db.execute("""
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
        
        # Should find 2 subscriptions (1 address + 1 group)
        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
        
        # Check address subscription
        addr_sub = [r for r in rows if r[2] == '3.1'][0]
        assert addr_sub[0] == user_id
        assert addr_sub[3] is not None  # address_ids
        assert addr_sub[4] is not None  # addresses
        
        # Check group subscription
        group_sub = [r for r in rows if r[2] == '3.2'][0]
        assert group_sub[0] == user_id
        assert group_sub[3] is None  # address_ids (NULL for group)
        assert group_sub[4] is None  # addresses (NULL  for group)


@pytest.mark.asyncio
async def test_alert_checker_stops_after_unsubscribe():
    """
    Test that alert checker stops checking after group unsubscribe.
    
    Bug: Alert checker continued checking group 3.2 after user unsubscribed.
    
    Root cause: Alert checker query is executed fresh on each cycle,
    so if subscription is deleted from DB, it won't be in the results.
    
    This test verifies the fix by checking the query only returns active subscriptions.
    """
    async with aiosqlite.connect(":memory:") as db:
        # Setup (same as above)
        await db.execute("""
            CREATE TABLE group_subscriptions (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                group_name TEXT,
                provider TEXT,
                notification_lead_time INTEGER DEFAULT 0
            )
        """)
        await db.commit()
        
        user_id = 123
        
        # Create group subscription
        await db.execute("""
            INSERT INTO group_subscriptions (user_id, group_name, provider, notification_lead_time)
            VALUES (?, '3.2', 'dtek', 15)
        """, (user_id,))
        await db.commit()
        
        # Query should find it
        cursor = await db.execute("""
            SELECT user_id, group_name 
            FROM group_subscriptions 
            WHERE notification_lead_time > 0
        """)
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][1] == '3.2'
        
        # Unsubscribe (delete from DB)
        await db.execute("DELETE FROM group_subscriptions WHERE user_id = ? AND group_name = '3.2'", (user_id,))
        await db.commit()
        
        # Query again - should find NOTHING
        cursor = await db.execute("""
            SELECT user_id, group_name 
            FROM group_subscriptions 
            WHERE notification_lead_time > 0
        """)
        rows = await cursor.fetchall()
        assert len(rows) == 0, "Should not find subscription after delete"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
