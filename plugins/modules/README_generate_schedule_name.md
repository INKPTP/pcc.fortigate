# Generate Schedule Name Module

An Ansible module that generates formatted schedule names based on date ranges.

## Overview

This module takes a start date and end date, and returns a formatted schedule name according to specific rules:

- **Empty dates** → `"always"`
- **Same start/end date** → `"{day}{month}{year}"` (e.g., `"22Feb2026"`)
- **Whole month coverage** → `"{month}{year}"` (e.g., `"Mar2026"`)
- **Date range** → `"{day}{month}-{day}{month}{year}"` (e.g., `"1Feb-31Mar2026"`)

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| start_date | string | yes | Start date in YYYY-MM-DD format |
| end_date | string | yes | End date in YYYY-MM-DD format |

## Return Values

| Key | Type | Description |
|-----|------|-------------|
| schedule_name | string | The generated schedule name |

## Examples

### Single Day Schedule
```yaml
- name: Generate schedule for single day
  pcc.fortigate.generate_schedule_name:
    start_date: "2026-02-22"
    end_date: "2026-02-22"
  register: result
# Returns: { "schedule_name": "22Feb2026" }
```

### Whole Month Schedule
```yaml
- name: Generate schedule for whole month
  pcc.fortigate.generate_schedule_name:
    start_date: "2026-03-01"
    end_date: "2026-03-31"
  register: result
# Returns: { "schedule_name": "Mar2026" }
```

### Date Range Schedule
```yaml
- name: Generate schedule for date range
  pcc.fortigate.generate_schedule_name:
    start_date: "2026-02-15"
    end_date: "2026-03-20"
  register: result
# Returns: { "schedule_name": "15Feb-20Mar2026" }
```

### Multi-Month Range
```yaml
- name: Generate schedule for multi-month range
  pcc.fortigate.generate_schedule_name:
    start_date: "2026-02-01"
    end_date: "2026-03-31"
  register: result
# Returns: { "schedule_name": "1Feb-31Mar2026" }
```

### Always Schedule (Empty Dates)
```yaml
- name: Generate schedule for always
  pcc.fortigate.generate_schedule_name:
    start_date: ""
    end_date: ""
  register: result
# Returns: { "schedule_name": "always" }
```

## Test Results

All test cases passed:

| Test Case | Input | Expected Output | Result |
|-----------|-------|-----------------|--------|
| Same start and end date | 2026-02-22 to 2026-02-22 | 22Feb2026 | ✓ PASS |
| Whole month (March) | 2026-03-01 to 2026-03-31 | Mar2026 | ✓ PASS |
| Multi-month range | 2026-02-01 to 2026-03-31 | 1Feb-31Mar2026 | ✓ PASS |
| Date range within months | 2026-02-15 to 2026-03-20 | 15Feb-20Mar2026 | ✓ PASS |
| Empty dates | (empty) to (empty) | always | ✓ PASS |
| Whole month (January) | 2026-01-01 to 2026-01-31 | Jan2026 | ✓ PASS |
| Whole month (February non-leap) | 2026-02-01 to 2026-02-28 | Feb2026 | ✓ PASS |
| Whole month (February leap year) | 2024-02-01 to 2024-02-29 | Feb2024 | ✓ PASS |
| Whole month (December) | 2026-12-01 to 2026-12-31 | Dec2026 | ✓ PASS |
| Same month range | 2026-01-15 to 2026-01-20 | 15Jan-20Jan2026 | ✓ PASS |
| Single day (first of month) | 2026-01-01 to 2026-01-01 | 1Jan2026 | ✓ PASS |
| Single day (last of month) | 2026-12-31 to 2026-12-31 | 31Dec2026 | ✓ PASS |

## Features

- ✅ Handles leap years correctly
- ✅ Validates date formats
- ✅ Detects whole month coverage automatically
- ✅ Supports all date range scenarios
- ✅ Properly formats day numbers (no leading zeros)
- ✅ Uses 3-letter month abbreviations

## Running Tests

To test the module functionality:

```bash
cd plugins/modules
python test_generate_schedule_name.py
```

To test with Ansible:

```bash
ansible-playbook test_generate_schedule_name.yml
```

## Notes

- Date format must be YYYY-MM-DD
- Module automatically detects leap years for February calculations
- Empty date strings are treated as "always" schedule
