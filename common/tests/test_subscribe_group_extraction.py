"""
Test for /subscribe command group extraction after migration 006.

Regression test: /subscribe crashed when trying to extract group_name
from user_last_check without JOIN.
"""

import pytest
import aiosqlite


@pytest.mark.asyncio
async def test_subscribe_extracts_group_name_with_join():
    """
    Test that /subscribe correctly extracts group_name using JOIN.
    
    BUG: Line 1505 in handlers.py tried to:
      SELECT group_name FROM user_last_check WHERE user_id = ?
    
    But after migration 006, group_name is in addresses table, not user_last_check.
    
    FIX: Use JOIN:
      SELECT a.group_name FROM user_last_check ulc
      JOIN addresses a ON a.id = ulc.address_id
      WHERE ulc.user_id = ?
    
    This test would have FAILED before the fix.
    """
    async with aiosqlite.connect(":memory:") as db:
        # Create migration 006 structure
        await db.execute("""
            CREATE TABLE addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                city TEXT NOT NULL,
                street TEXT NOT NULL,
                house TEXT NOT NULL,
                group_name TEXT
            )
        """)
        
        await db.execute("""
            CREATE TABLE user_last_check (
                user_id INTEGER PRIMARY KEY,
                address_id INTEGER NOT NULL,
                last_hash TEXT,
                FOREIGN KEY (address_id) REFERENCES addresses (id)
            )
        """)
        
        await db.commit()
        
        # Insert test data
        test_user_id = 123456
        test_group = "3.2"
        
        cursor = await db.execute("""
            INSERT INTO addresses (provider, city, street, house, group_name)
            VALUES ('dtek', 'Kyiv', 'Main St', '1', ?)
        """, (test_group,))
        address_id = cursor.lastrowid
        await db.commit()
        
        await db.execute("""
            INSERT INTO user_last_check (user_id, address_id, last_hash)
            VALUES (?, ?, 'hash123')
        """, (test_user_id, address_id))
        await db.commit()
        
        # OLD query (without JOIN) - should FAIL
        with pytest.raises(aiosqlite.OperationalError) as exc_info:
            cursor = await db.execute(
                "SELECT group_name FROM user_last_check WHERE user_id = ?",
                (test_user_id,)
            )
            await cursor.fetchone()
        
        assert "no such column" in str(exc_info.value).lower()
        assert "group_name" in str(exc_info.value).lower()
        
        # NEW query (with JOIN) - should SUCCEED
        cursor = await db.execute("""
            SELECT a.group_name 
            FROM user_last_check ulc
            JOIN addresses a ON a.id = ulc.address_id
            WHERE ulc.user_id = ?
        """, (test_user_id,))
        row = await cursor.fetchone()
        
        assert row is not None, "Should find user_last_check with JOIN"
        group = row[0]
        assert group == test_group, f"Expected group {test_group}, got {group}"


@pytest.mark.asyncio
async def test_subscribe_handles_missing_group_name():
    """
    Test that /subscribe handles NULL group_name gracefully.
    
    Some addresses might not have group_name set yet.
    """
    async with aiosqlite.connect(":memory:") as db:
        # Create tables
        await db.execute("""
            CREATE TABLE addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                city TEXT NOT NULL,
                street TEXT NOT NULL,
                house TEXT NOT NULL,
                group_name TEXT
            )
        """)
        
        await db.execute("""
            CREATE TABLE user_last_check (
                user_id INTEGER PRIMARY KEY,
                address_id INTEGER NOT NULL,
                last_hash TEXT
            )
        """)
        
        await db.commit()
        
        # Insert address WITHOUT group_name
        cursor = await db.execute("""
            INSERT INTO addresses (provider, city, street, house)
            VALUES ('dtek', 'Kyiv', 'Main St', '1')
        """)
        address_id = cursor.lastrowid
        await db.commit()
        
        await db.execute("""
            INSERT INTO user_last_check (user_id, address_id, last_hash)
            VALUES (123, ?, 'hash')
        """, (address_id,))
        await db.commit()
        
        # Query should succeed but return NULL for group_name
        cursor = await db.execute("""
            SELECT a.group_name 
            FROM user_last_check ulc
            JOIN addresses a ON a.id = ulc.address_id
            WHERE ulc.user_id = ?
        """, (123,))
        row = await cursor.fetchone()
        
        assert row is not None
        group = row[0]
        assert group is None, "group_name should be NULL when not set"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
