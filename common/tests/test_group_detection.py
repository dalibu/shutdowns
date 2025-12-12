"""
Tests for group detection functionality in common.bot_base module
"""
import pytest
from common.bot_base import detect_check_input_type


@pytest.mark.unit
class TestDetectCheckInputType:
    """Tests for detect_check_input_type function - DTEK group vs address detection"""
    
    # ============================================================
    # VALID DTEK GROUPS (1-6).(1-2)
    # ============================================================
    
    def test_valid_group_with_dot(self):
        """Valid group with dot separator"""
        input_type, value = detect_check_input_type("3.1")
        assert input_type == "group"
        assert value == "3.1"
    
    def test_valid_group_with_comma(self):
        """Valid group with comma separator (normalized to dot)"""
        input_type, value = detect_check_input_type("3,1")
        assert input_type == "group"
        assert value == "3.1"
    
    def test_valid_group_with_space(self):
        """Valid group with space separator (normalized to dot)"""
        input_type, value = detect_check_input_type("3 1")
        assert input_type == "group"
        assert value == "3.1"
    
    def test_min_group(self):
        """Minimum valid group (1.1)"""
        input_type, value = detect_check_input_type("1.1")
        assert input_type == "group"
        assert value == "1.1"
    
    def test_max_group(self):
        """Maximum valid group (6.2)"""
        input_type, value = detect_check_input_type("6.2")
        assert input_type == "group"
        assert value == "6.2"
    
    @pytest.mark.parametrize("group_input,expected_normalized", [
        ("1.1", "1.1"),
        ("1.2", "1.2"),
        ("2.1", "2.1"),
        ("2.2", "2.2"),
        ("3.1", "3.1"),
        ("3.2", "3.2"),
        ("4.1", "4.1"),
        ("4.2", "4.2"),
        ("5.1", "5.1"),
        ("5.2", "5.2"),
        ("6.1", "6.1"),
        ("6.2", "6.2"),
    ])
    def test_all_valid_dtek_groups(self, group_input, expected_normalized):
        """Test all 12 valid DTEK groups"""
        input_type, value = detect_check_input_type(group_input)
        assert input_type == "group"
        assert value == expected_normalized
    
    def test_group_with_surrounding_spaces(self):
        """Group with spaces before and after (trimmed)"""
        input_type, value = detect_check_input_type("  3.1  ")
        assert input_type == "group"
        assert value == "3.1"
    
    def test_group_with_spaces_around_dot(self):
        """Group with spaces around separator"""
        input_type, value = detect_check_input_type("3 . 1")
        assert input_type == "group"
        assert value == "3.1"
    
    def test_group_with_spaces_around_comma(self):
        """Group with spaces around comma"""
        input_type, value = detect_check_input_type("3 , 1")
        assert input_type == "group"
        assert value == "3.1"
    
    def test_group_with_multiple_spaces(self):
        """Group with multiple spaces as separator"""
        input_type, value = detect_check_input_type("4  2")
        assert input_type == "group"
        assert value == "4.2"
    
    # ============================================================
    # INVALID GROUPS (should be detected as address)
    # ============================================================
    
    def test_first_digit_too_large(self):
        """Invalid: first digit > 6"""
        input_type, value = detect_check_input_type("7.1")
        assert input_type == "address"
        assert value == "7.1"
    
    def test_first_digit_zero(self):
        """Invalid: first digit < 1"""
        input_type, value = detect_check_input_type("0.1")
        assert input_type == "address"
        assert value == "0.1"
    
    def test_second_digit_too_large(self):
        """Invalid: second digit > 2"""
        input_type, value = detect_check_input_type("3.3")
        assert input_type == "address"
        assert value == "3.3"
    
    def test_second_digit_zero(self):
        """Invalid: second digit < 1"""
        input_type, value = detect_check_input_type("3.0")
        assert input_type == "address"
        assert value == "3.0"
    
    def test_too_many_digits_first_part(self):
        """Invalid: too many digits in first part"""
        input_type, value = detect_check_input_type("12.5")
        assert input_type == "address"
        assert value == "12.5"
    
    def test_single_number_no_second_part(self):
        """Invalid: single number (no second part)"""
        input_type, value = detect_check_input_type("3")
        assert input_type == "address"
        assert value == "3"
    
    def test_too_many_digits_both_parts(self):
        """Invalid: both parts have too many digits"""
        input_type, value = detect_check_input_type("123.456")
        assert input_type == "address"
        assert value == "123.456"
    
    def test_too_many_parts(self):
        """Invalid: too many parts"""
        input_type, value = detect_check_input_type("3.1.2")
        assert input_type == "address"
        assert value == "3.1.2"
    
    def test_letters_instead_of_numbers(self):
        """Invalid: letters instead of numbers"""
        input_type, value = detect_check_input_type("abc.def")
        assert input_type == "address"
        assert value == "abc.def"
    
    # ============================================================
    # ADDRESS PATTERNS
    # ============================================================
    
    def test_full_ukrainian_address(self):
        """Full Ukrainian address with city, street, house"""
        address = "м. Дніпро, вул. Сонячна набережна, 6"
        input_type, value = detect_check_input_type(address)
        assert input_type == "address"
        assert value == address
    
    def test_village_address(self):
        """Village address"""
        address = "сел. Село, вул. Вулиця, 10"
        input_type, value = detect_check_input_type(address)
        assert input_type == "address"
        assert value == address
    
    def test_latin_address(self):
        """Address with Latin characters"""
        address = "Kyiv, Street, 5"
        input_type, value = detect_check_input_type(address)
        assert input_type == "address"
        assert value == address
    
    # ============================================================
    # EDGE CASES
    # ============================================================
    
    def test_empty_string(self):
        """Empty string should return unknown"""
        input_type, value = detect_check_input_type("")
        assert input_type == "unknown"
        assert value == ""
    
    def test_only_spaces(self):
        """String with only spaces should return unknown"""
        input_type, value = detect_check_input_type("   ")
        assert input_type == "unknown"
        assert value == ""
    
    # ============================================================
    # BOUNDARY CASES
    # ============================================================
    
    @pytest.mark.parametrize("invalid_group", [
        "0.1",  # first digit too small
        "7.1",  # first digit too large
        "1.0",  # second digit too small
        "1.3",  # second digit too large
        "10.1", # first part two digits
        "1.10", # second part two digits
    ])
    def test_boundary_invalid_groups(self, invalid_group):
        """Test boundary cases that should NOT be valid groups"""
        input_type, value = detect_check_input_type(invalid_group)
        assert input_type == "address", f"{invalid_group} should be address, not group"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
