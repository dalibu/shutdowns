"""
Tests for /check command with groups.

Regression test: Missing import of detect_check_input_type caused /check [group] to fail.
"""

import pytest


@pytest.mark.asyncio
async def test_check_command_import_verification():
    """
    Verify that detect_check_input_type is properly imported in handlers.
    
    BUG: /check 5.2 failed with "Адреса має бути введена у форматі" error
    CAUSE: detect_check_input_type was not imported globally in handlers.py
    FIX: Added to imports (line 44)
    
    This test would have FAILED before the fix, catching the import bug.
    Without this import, /check [group] would fail with NameError.
    """
    from common import handlers
    
    # Verify function is available in handlers module
    assert hasattr(handlers, 'detect_check_input_type'), \
        "detect_check_input_type must be imported in handlers module for /check to work with groups"
    
    # Verify it works correctly
    from common.handlers import detect_check_input_type
    
    # Test DTEK/CEK group patterns
    test_cases = [
        ("3.1", "group", "3.1"),
        ("5.2", "group", "5.2"),
        ("1.1", "group", "1.1"),
        ("6.2", "group", "6.2"),
        ("3,1", "group", "3.1"),  # Normalized
        ("5 2", "group", "5.2"),  # Normalized
        ("м. Kyiv, Main St, 1", "address", "м. Kyiv, Main St, 1"),
        ("Дніпро, Янгеля, 33", "address", "Дніпро, Янгеля, 33"),
    ]
    
    for input_text, expected_type, expected_value in test_cases:
        input_type, value = detect_check_input_type(input_text)
        assert input_type == expected_type, \
            f"Input '{input_text}' should be detected as '{expected_type}', got '{input_type}'"
        if expected_type == "group":
            assert value == expected_value, \
                f"Group '{input_text}' should normalize to '{expected_value}', got '{value}'"


@pytest.mark.asyncio
async def test_handle_check_command_exists():
    """
    Verify that handle_check_command is exported and can be called.
    """
    from common.handlers import handle_check_command
    
    # Should be callable
    assert callable(handle_check_command)
    
    # Check signature has required params
    import inspect
    sig = inspect.signature(handle_check_command)
    param_names = list(sig.parameters.keys())
    
    assert 'message' in param_names
    assert 'state' in param_names
    assert 'ctx' in param_names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
