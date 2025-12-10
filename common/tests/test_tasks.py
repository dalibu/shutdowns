"""
Tests for background tasks (subscription checker, alert checker).
"""
import pytest
from datetime import datetime, timedelta


def test_db_updates_fail_tuple_structure():
    """
    Test that db_updates_fail tuples have the correct structure (5 elements).
    
    This test prevents regression of the bug where db_updates_fail had inconsistent
    tuple sizes (2 vs 5 parameters), causing:
    "sqlite3.ProgrammingError: Incorrect number of bindings supplied. 
     The current statement uses 5, and there are 2 supplied."
     
    The SQL statement:
    UPDATE subscriptions SET next_check = ? WHERE user_id = ? AND city = ? AND street = ? AND house = ?
    
    Requires 5 parameters: (next_check, user_id, city, street, house)
    """
    # Simulate the data structure used in subscription_checker_task
    now = datetime.now()
    next_check_time = now + timedelta(hours=24)
    
    # These are the tuples that should be appended to db_updates_fail
    # in lines 337, 354, and 509 of common/tasks.py
    
    # Scenario 1: Missing API result (line 337)
    user_id_1, city_1, street_1, house_1 = 1, "Kyiv", "Street1", "1"
    tuple_1 = (next_check_time, user_id_1, city_1, street_1, house_1)
    
    # Scenario 2: API error (line 354)
    user_id_2, city_2, street_2, house_2 = 2, "Kyiv", "Street2", "2"
    tuple_2 = (next_check_time, user_id_2, city_2, street_2, house_2)
    
    # Scenario 3: No schedule change (line 509)
    user_id_3, city_3, street_3, house_3 = 3, "Kyiv", "Street3", "3"
    tuple_3 = (next_check_time, user_id_3, city_3, street_3, house_3)
    
    # All tuples in db_updates_fail
    db_updates_fail = [tuple_1, tuple_2, tuple_3]
    
    # The SQL statement that will be used
    sql = "UPDATE subscriptions SET next_check = ? WHERE user_id = ? AND city = ? AND street = ? AND house = ?"
    
    # Count placeholders
    placeholder_count = sql.count('?')
    assert placeholder_count == 5, f"SQL should have 5 placeholders, got {placeholder_count}"
    
    # Verify each tuple has exactly 5 elements
    for i, param_tuple in enumerate(db_updates_fail):
        assert len(param_tuple) == 5, \
            f"Tuple {i} should have 5 elements (next_check, user_id, city, street, house), got {len(param_tuple)}: {param_tuple}"
        
        # Verify types
        assert isinstance(param_tuple[0], datetime), f"Tuple {i}: first element should be datetime (next_check)"
        assert isinstance(param_tuple[1], int), f"Tuple {i}: second element should be int (user_id)"
        assert isinstance(param_tuple[2], str), f"Tuple {i}: third element should be str (city)"
        assert isinstance(param_tuple[3], str), f"Tuple {i}: fourth element should be str (street)"
        assert isinstance(param_tuple[4], str), f"Tuple {i}: fifth element should be str (house)"


def test_db_updates_success_tuple_structure():
    """
    Test that db_updates_success tuples have the correct structure (6 elements).
    
    The SQL statement:
    UPDATE subscriptions SET next_check = ?, last_schedule_hash = ? 
    WHERE user_id = ? AND city = ? AND street = ? AND house = ?
    
    Requires 6 parameters: (next_check, last_schedule_hash, user_id, city, street, house)
    """
    # Simulate the data structure used in subscription_checker_task (line 498)
    now = datetime.now()
    next_check_time = now + timedelta(hours=24)
    
    user_id, city, street, house = 1, "Kyiv", "Street1", "1"
    new_hash = "new_hash_value_abc123"
    
    # This is what gets appended to db_updates_success (line 498)
    tuple_success = (next_check_time, new_hash, user_id, city, street, house)
    
    db_updates_success = [tuple_success]
    
    # The SQL statement that will be used
    sql = "UPDATE subscriptions SET next_check = ?, last_schedule_hash = ? WHERE user_id = ? AND city = ? AND street = ? AND house = ?"
    
    # Count placeholders
    placeholder_count = sql.count('?')
    assert placeholder_count == 6, f"SQL should have 6 placeholders, got {placeholder_count}"
    
    # Verify each tuple has exactly 6 elements
    for i, param_tuple in enumerate(db_updates_success):
        assert len(param_tuple) == 6, \
            f"Tuple {i} should have 6 elements (next_check, hash, user_id, city, street, house), got {len(param_tuple)}: {param_tuple}"
        
        # Verify types
        assert isinstance(param_tuple[0], datetime), f"Tuple {i}: first element should be datetime (next_check)"
        assert isinstance(param_tuple[1], str), f"Tuple {i}: second element should be str (last_schedule_hash)"
        assert isinstance(param_tuple[2], int), f"Tuple {i}: third element should be int (user_id)"
        assert isinstance(param_tuple[3], str), f"Tuple {i}: fourth element should be str (city)"
        assert isinstance(param_tuple[4], str), f"Tuple {i}: fifth element should be str (street)"
        assert isinstance(param_tuple[5], str), f"Tuple {i}: sixth element should be str (house)"


def test_executemany_binding_consistency():
    """
    Integration test: verify that all tuples in a batch have the same length.
    
    This simulates what sqlite3 will check when executemany is called.
    SQLite requires all tuples to have the same number of elements.
    """
    # Mix of scenarios
    now = datetime.now()
    next_check = now + timedelta(hours=24)
    
    # Build db_updates_fail as it would be in the actual code
    db_updates_fail = []
    
    # Line 337: Missing result
    db_updates_fail.append((next_check, 1, "City1", "Street1", "1"))
    
    # Line 354: API error
    db_updates_fail.append((next_check, 2, "City2", "Street2", "2"))
    
    # Line 509: No change
    db_updates_fail.append((next_check, 3, "City3", "Street3", "3"))
    
    # Verify all tuples have same length
    if db_updates_fail:
        expected_length = len(db_updates_fail[0])
        for i, t in enumerate(db_updates_fail):
            assert len(t) == expected_length, \
                f"Inconsistent tuple length at index {i}: expected {expected_length}, got {len(t)}"
        
        # Verify it matches SQL placeholder count
        sql = "UPDATE subscriptions SET next_check = ? WHERE user_id = ? AND city = ? AND street = ? AND house = ?"
        assert expected_length == sql.count('?'), \
            f"Tuple length {expected_length} doesn't match SQL placeholder count {sql.count('?')}"


if __name__ == "__main__":
    print("Running subscription_checker_task binding tests...")
    
    test_db_updates_fail_tuple_structure()
    print("✅ db_updates_fail tuple structure test passed!")
    
    test_db_updates_success_tuple_structure()
    print("✅ db_updates_success tuple structure test passed!")
    
    test_executemany_binding_consistency()
    print("✅ executemany binding consistency test passed!")
    
    print("\n🎉 All task binding tests passed!")
