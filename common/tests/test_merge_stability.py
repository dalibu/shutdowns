#!/usr/bin/env python3
"""
Test script to verify merge_consecutive_slots stability.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from common.formatting import merge_consecutive_slots


def test_merge_stability():
    """Test that merge produces consistent results."""
    
    # Test data
    schedule = {
        "10.12.25": [
            {"shutdown": "05:00–06:00", "status": "відключення"},
            {"shutdown": "06:00–07:00", "status": "відключення"},
            {"shutdown": "07:00–08:00", "status": "відключення"},
            {"shutdown": "08:00–09:00", "status": "відключення"},
            {"shutdown": "09:00–10:00", "status": "відключення"},
            {"shutdown": "10:00–11:00", "status": "відключення"},
            {"shutdown": "11:00–12:00", "status": "відключення"},
            {"shutdown": "15:30–16:30", "status": "відключення"},
            {"shutdown": "16:30–17:30", "status": "відключення"},
            {"shutdown": "17:30–18:30", "status": "відключення"},
            {"shutdown": "18:30–19:30", "status": "відключення"},
            {"shutdown": "19:30–20:30", "status": "відключення"},
            {"shutdown": "20:30–21:30", "status": "відключення"},
            {"shutdown": "21:30–22:00", "status": "відключення"},
        ]
    }
    
    print("=" * 80)
    print("MERGE STABILITY TEST")
    print("=" * 80)
    
    print(f"\nInput: {len(schedule['10.12.25'])} slots")
    
    # Merge multiple times
    results = []
    for i in range(5):
        merged = merge_consecutive_slots(schedule)
        results.append(merged)
        print(f"\nRun {i+1}: {len(merged['10.12.25'])} merged slots")
        for slot in merged['10.12.25']:
            print(f"  - {slot}")
    
    # Check consistency
    print("\n" + "=" * 80)
    print("CONSISTENCY CHECK:")
    all_same = all(r == results[0] for r in results[1:])
    if all_same:
        print("✓ All runs produced identical results")
    else:
        print("✗ Results differ across runs!")
        for i, result in enumerate(results):
            print(f"\nRun {i+1}:")
            print(result)
    print("=" * 80)


if __name__ == "__main__":
    test_merge_stability()
