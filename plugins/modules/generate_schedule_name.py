#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2026
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
module: generate_schedule_name
short_description: Generate schedule name based on date range
version_added: "1.0.0"
description:
    - Generates a formatted schedule name based on start and end dates
    - Returns different formats depending on the date range
    - Empty dates return "always"
    - Same start/end date returns "{day}{month}{year}" (e.g., "22Feb2026")
    - Whole month coverage returns "{month}{year}" (e.g., "Mar2026")
    - Date range returns "{day}{month}-{day}{month}{year}" (e.g., "1Feb-31Mar2026")
options:
    start_date:
        description:
            - Start date in YYYY-MM-DD format
        required: true
        type: str
    end_date:
        description:
            - End date in YYYY-MM-DD format
        required: true
        type: str
author:
    - Your Name
'''

EXAMPLES = r'''
# Generate schedule name for same day
- name: Generate schedule for single day
  generate_schedule_name:
    start_date: "2026-02-22"
    end_date: "2026-02-22"
  register: result
# Returns: { "schedule_name": "22Feb2026" }

# Generate schedule name for whole month
- name: Generate schedule for whole month
  generate_schedule_name:
    start_date: "2026-03-01"
    end_date: "2026-03-31"
  register: result
# Returns: { "schedule_name": "Mar2026" }

# Generate schedule name for date range
- name: Generate schedule for date range
  generate_schedule_name:
    start_date: "2026-02-15"
    end_date: "2026-03-20"
  register: result
# Returns: { "schedule_name": "15Feb-20Mar2026" }

# Generate schedule name for multi-month range
- name: Generate schedule for multi-month range
  generate_schedule_name:
    start_date: "2026-02-01"
    end_date: "2026-03-31"
  register: result
# Returns: { "schedule_name": "1Feb-31Mar2026" }

# Generate schedule name for empty dates (always)
- name: Generate schedule for always
  generate_schedule_name:
    start_date: ""
    end_date: ""
  register: result
# Returns: { "schedule_name": "always" }
'''

RETURN = r'''
schedule_name:
    description: The generated schedule name
    type: str
    returned: always
    sample: "1Feb-31Mar2026"
'''

from ansible.module_utils.basic import AnsibleModule
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


def run_module():
    """Main module execution."""
    module_args = dict(
        start_date=dict(type='str', required=True),
        end_date=dict(type='str', required=True),
    )

    result = dict(
        changed=False,
        schedule_name='',
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    start_date = module.params['start_date']
    end_date = module.params['end_date']

    try:
        schedule_name = generate_schedule_name(start_date, end_date)
        result['schedule_name'] = schedule_name
        module.exit_json(**result)
    except ValueError as e:
        module.fail_json(msg=str(e), **result)
    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}", **result)


def main():
    run_module()


if __name__ == '__main__':
    main()
