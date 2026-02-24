#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Test script for generate_schedule_name module."""

import sys
from datetime import datetime
import calendar


def is_leap_year(year):
    """Check if a year is a leap year."""
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def get_days_in_month(year, month):
    """Get the number of days in a specific month."""
    return calendar.monthrange(year, month)[1]


def parse_date(date_string):
    """Parse date string in YYYY-MM-DD format."""
    try:
        return datetime.strptime(date_string, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"Invalid date format '{date_string}'. Expected YYYY-MM-DD format. Error: {e}")


def generate_schedule_name(start_date_str, end_date_str):
    """
    Generate schedule name based on date range.
    
    Args:
        start_date_str: Start date in YYYY-MM-DD format
        end_date_str: End date in YYYY-MM-DD format
    
    Returns:
        Formatted schedule name string
    
    Examples:
        2026-02-22 to 2026-02-22 → 22Feb2026
        2026-03-01 to 2026-03-31 → Mar2026
        2026-02-01 to 2026-03-31 → 1Feb-31Mar2026
        2026-02-15 to 2026-03-20 → 15Feb-20Mar2026
    """
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    
    # Handle empty dates
    if not start_date_str or not end_date_str:
        return "always"
    
    # Parse dates
    start_date = parse_date(start_date_str)
    end_date = parse_date(end_date_str)
    
    # Extract date components
    start_day = start_date.day
    start_month = start_date.month
    start_year = start_date.year
    start_month_name = months[start_month - 1]
    
    end_day = end_date.day
    end_month = end_date.month
    end_year = end_date.year
    end_month_name = months[end_month - 1]
    
    # Case 1: Same start and end date
    if start_date == end_date:
        return f"{start_day}{start_month_name}{start_year}"
    
    # Case 2: Check if it covers a whole month
    days_in_end_month = get_days_in_month(end_year, end_month)
    
    if (start_month == end_month and 
        start_year == end_year and 
        start_day == 1 and 
        end_day == days_in_end_month):
        return f"{end_month_name}{end_year}"
    
    # Case 3: Date range
    return f"{start_day}{start_month_name}-{end_day}{end_month_name}{end_year}"


def test_schedule_name_generation():
    """Test the schedule name generation function."""
    
    test_cases = [
        # (start_date, end_date, expected_result, description)
        ("2026-02-22", "2026-02-22", "22Feb2026", "Same start and end date"),
        ("2026-03-01", "2026-03-31", "Mar2026", "Whole month (March)"),
        ("2026-02-01", "2026-03-31", "1Feb-31Mar2026", "Multi-month range"),
        ("2026-02-15", "2026-03-20", "15Feb-20Mar2026", "Date range within months"),
        ("", "", "always", "Empty dates"),
        ("2026-01-01", "2026-01-31", "Jan2026", "Whole month (January)"),
        ("2026-02-01", "2026-02-28", "Feb2026", "Whole month (February non-leap)"),
        ("2024-02-01", "2024-02-29", "Feb2024", "Whole month (February leap year)"),
        ("2026-12-01", "2026-12-31", "Dec2026", "Whole month (December)"),
        ("2026-01-15", "2026-01-20", "15Jan-20Jan2026", "Same month range"),
        ("2026-01-01", "2026-01-01", "1Jan2026", "Single day (first of month)"),
        ("2026-12-31", "2026-12-31", "31Dec2026", "Single day (last of month)"),
    ]
    
    print("Testing schedule name generation...\n")
    print("=" * 80)
    
    passed = 0
    failed = 0
    
    for start_date, end_date, expected, description in test_cases:
        try:
            result = generate_schedule_name(start_date, end_date)
            status = "✓ PASS" if result == expected else "✗ FAIL"
            
            if result == expected:
                passed += 1
            else:
                failed += 1
            
            print(f"{status} | {description}")
            print(f"     Input:    {start_date} to {end_date}")
            print(f"     Expected: {expected}")
            print(f"     Got:      {result}")
            
            if result != expected:
                print(f"     ERROR: Mismatch!")
            
            print("-" * 80)
            
        except Exception as e:
            failed += 1
            print(f"✗ FAIL | {description}")
            print(f"     Input:    {start_date} to {end_date}")
            print(f"     Expected: {expected}")
            print(f"     ERROR:    {e}")
            print("-" * 80)
    
    print("=" * 80)
    print(f"\nTest Results: {passed} passed, {failed} failed out of {len(test_cases)} tests")
    
    if failed == 0:
        print("✓ All tests passed!")
        return 0
    else:
        print(f"✗ {failed} test(s) failed!")
        return 1


if __name__ == "__main__":
    exit_code = test_schedule_name_generation()
    sys.exit(exit_code)
