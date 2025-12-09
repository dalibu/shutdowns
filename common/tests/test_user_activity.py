import pytest
import aiosqlite
import os
from datetime import datetime, timedelta
from common.bot_base import init_db, update_user_activity
from common.migrate import migrate

@pytest.mark.asyncio
async def test_update_user_activity():
    db_path = "test_activity.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    
    # Run migrations first (since init_db no longer creates tables)
    migrate(db_path)
    
    conn = await init_db(db_path)
    
    try:
        # 1. Verify table exists
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_activity'") as cursor:
            row = await cursor.fetchone()
            assert row is not None, "user_activity table was not created"
            
        # 2. Add new user
        await update_user_activity(conn, 123, username="test_user", city="Kyiv", street="Main", house="1", first_name="John", last_name="Doe")
        
        async with conn.execute("SELECT * FROM user_activity WHERE user_id = 123") as cursor:
            row = await cursor.fetchone()
            assert row is not None
            # user_id, first_seen, last_seen, last_city, last_street, last_house, username, last_group, first_name, last_name
            assert row[0] == 123
            first_seen = row[1]
            last_seen = row[2]
            assert first_seen == last_seen
            
        # 3. Update existing user (time only)
        await update_user_activity(conn, 123, username="test_user")
        
        async with conn.execute("SELECT * FROM user_activity WHERE user_id = 123") as cursor:
            row = await cursor.fetchone()
            assert row[1] == first_seen # first_seen unchanged
            assert row[2] != last_seen  # last_seen updated
            
        # 4. Update existing user (address)
        await update_user_activity(conn, 123, username="test_user", city="Lviv", street="Market", house="2")
        
        async with conn.execute("SELECT last_city, last_street, last_house FROM user_activity WHERE user_id = 123") as cursor:
            row = await cursor.fetchone()
            assert row[0] == "Lviv"
            assert row[1] == "Market"
            assert row[2] == "2"
            
    finally:
        await conn.close()
        if os.path.exists(db_path):
            os.remove(db_path)
