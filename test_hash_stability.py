#!/usr/bin/env python3
"""
Test script to verify hash stability for schedule data.
This helps debug false positive change notifications.
"""

import sys
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from common.bot_base import get_schedule_hash_compact, normalize_schedule_for_hash


def test_hash_stability():
    """Test that identical schedules produce identical hashes."""
    
    # Sample schedule data (same as what DTEK parser returns)
    schedule1 = {
        "city": "м. Дніпро",
        "street": "вул. Короленко",
        "house_num": "24",
        "group": "3.1",
        "schedule": {
            "10.12.25": [
                {"shutdown": "05:00–12:00", "status": "відключення"},
                {"shutdown": "15:30–22:00", "status": "відключення"}
            ]
        }
    }
    
    # Same schedule, different field order
    schedule2 = {
        "group": "3.1",
        "schedule": {
            "10.12.25": [
                {"status": "відключення", "shutdown": "05:00–12:00"},
                {"status": "відключення", "shutdown": "15:30–22:00"}
            ]
        },
        "city": "м. Дніпро",
        "street": "вул. Короленко",
        "house_num": "24"
    }
    
    # Same schedule, different slot order
    schedule3 = {
        "city": "м. Дніпро",
        "street": "вул. Короленко",
        "house_num": "24",
        "group": "3.1",
        "schedule": {
            "10.12.25": [
                {"shutdown": "15:30–22:00", "status": "відключення"},
                {"shutdown": "05:00–12:00", "status": "відключення"}
            ]
        }
    }
    
    # Different schedule
    schedule4 = {
        "city": "м. Дніпро",
        "street": "вул. Короленко",
        "house_num": "24",
        "group": "3.1",
        "schedule": {
            "10.12.25": [
                {"shutdown": "05:00–12:00", "status": "відключення"},
                {"shutdown": "15:30–23:00", "status": "відключення"}  # Different end time
            ]
        }
    }
    
    # Calculate hashes
    hash1 = get_schedule_hash_compact(schedule1)
    hash2 = get_schedule_hash_compact(schedule2)
    hash3 = get_schedule_hash_compact(schedule3)
    hash4 = get_schedule_hash_compact(schedule4)
    
    # Get normalized data for inspection
    norm1 = normalize_schedule_for_hash(schedule1)
    norm2 = normalize_schedule_for_hash(schedule2)
    norm3 = normalize_schedule_for_hash(schedule3)
    norm4 = normalize_schedule_for_hash(schedule4)
    
    print("=" * 80)
    print("HASH STABILITY TEST")
    print("=" * 80)
    
    print("\n1. IDENTICAL SCHEDULES (different field order):")
    print(f"   Hash 1: {hash1}")
    print(f"   Hash 2: {hash2}")
    print(f"   Match:  {hash1 == hash2} {'✓' if hash1 == hash2 else '✗'}")
    print(f"\n   Normalized 1: {json.dumps(norm1, ensure_ascii=False, sort_keys=True)}")
    print(f"   Normalized 2: {json.dumps(norm2, ensure_ascii=False, sort_keys=True)}")
    
    print("\n2. SAME SCHEDULE (different slot order):")
    print(f"   Hash 1: {hash1}")
    print(f"   Hash 3: {hash3}")
    print(f"   Match:  {hash1 == hash3} {'✓' if hash1 == hash3 else '✗'}")
    print(f"\n   Normalized 1: {json.dumps(norm1, ensure_ascii=False, sort_keys=True)}")
    print(f"   Normalized 3: {json.dumps(norm3, ensure_ascii=False, sort_keys=True)}")
    
    print("\n3. DIFFERENT SCHEDULES:")
    print(f"   Hash 1: {hash1}")
    print(f"   Hash 4: {hash4}")
    print(f"   Match:  {hash1 == hash4} {'✗' if hash1 != hash4 else '✓ (UNEXPECTED!)'}")
    print(f"\n   Normalized 1: {json.dumps(norm1, ensure_ascii=False, sort_keys=True)}")
    print(f"   Normalized 4: {json.dumps(norm4, ensure_ascii=False, sort_keys=True)}")
    
    print("\n" + "=" * 80)
    print("SUMMARY:")
    all_pass = (hash1 == hash2) and (hash1 == hash3) and (hash1 != hash4)
    if all_pass:
        print("✓ All tests passed - hash function is stable")
    else:
        print("✗ Some tests failed - hash function may be unstable")
        if hash1 != hash2:
            print("  - FAIL: Different field order produces different hash")
        if hash1 != hash3:
            print("  - FAIL: Different slot order produces different hash")
        if hash1 == hash4:
            print("  - FAIL: Different schedules produce same hash")
    print("=" * 80)


if __name__ == "__main__":
    test_hash_stability()
