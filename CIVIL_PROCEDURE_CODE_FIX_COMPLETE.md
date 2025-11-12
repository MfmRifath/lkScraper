# Civil Procedure Code (legislation_C_89) - Fix Complete ✅

## Problem Fixed

The Civil Procedure Code had:
- ❌ Sections out of order (1, 373, 4, 5, 11...)
- ❌ All parts labeled "MAIN - N/A"
- ❌ Incorrect section assignments across 9 separate parts

## Solution Implemented

Created specialized functions to handle legislation_C_89's unique structure where PART headers are stored in a hidden `<input name="selectedhtml">` field.

## New Functions Added

### 1. `extract_parts_from_hidden_input_c89(self, soup)`
**Location**: Lines 9845-9951

Extracts PART headers and section boundaries from the hidden input field.

**Returns**: List of PART info dicts containing:
- `part_number`: "PART I", "PART II", etc.
- `part_title`: Part title (if available)
- `section_start`: First section number
- `section_end`: Last section number
- `section_count`: Total sections in this PART

**Example Output**:
```
PART I: PART I (sections 1-372, 385 sections)
PART II: PART II (sections 373-391, 19 sections)
PART III: PART III (sections 392-440, 55 sections)
...
```

### 2. `map_section_to_part_c89(self, section_number, part_boundaries)`
**Location**: Lines 9953-9981

Maps a section number to its correct PART based on section boundaries.

**Parameters**:
- `section_number`: Section number string (e.g., "5", "14A", "373")
- `part_boundaries`: List from `extract_parts_from_hidden_input_c89()`

**Returns**: Matching part_info dict or None

### 3. `reorganize_sections_by_part_c89(self, parts, part_boundaries)`
**Location**: Lines 9983-10082

Reorganizes all sections into correct PARTs based on section numbers.

**Process**:
1. Collects all sections from all parts
2. Creates new PART structure based on boundaries
3. Assigns each section to correct PART
4. Sorts sections within each PART
5. Removes empty parts

**Example Output**:
```
PART I: 386 sections (Range: 1 to 372)
PART II: 19 sections (Range: 373 to 391)
PART III: 55 sections (Range: 392 to 440C)
PART IV: 201 sections (Range: 456 to 638)
PART V: 24 sections (Range: 650 to 675)
PART VI: 30 sections (Range: 676 to 711)
PART VII: 43 sections (Range: 712 to 752)
PART VIII: 24 sections (Range: 753 to 777)
PART IX: 9 sections (Range: 792 to 801)
PART X: 7 sections (Range: 834 to 840)
```

### 4. `process_legislation_c89(self, soup, legislation_id)`
**Location**: Lines 10084-10115

Main handler function that orchestrates the C89-specific processing.

**Steps**:
1. Extract PART boundaries from hidden input
2. Extract sections using standard method
3. Reorganize sections into correct PARTs

## Integration Points

### Modified: `construct_json_data()` - Line 5482
Changed section extraction to use standard method (reorganization happens later):
```python
# NOTE: For legislation_C_89, we use standard extraction here and reorganize later
# after all sections (visible + rescued) are collected
visible_parts = self.extract_parts_with_section_groups(soup) or []
```

### Modified: `construct_json_data()` - Lines 5687-5711
Added special handling after all sections are collected:
```python
# SPECIAL HANDLING: Use C89 reorganization for Civil Procedure Code
if doc_id == 'legislation_C_89':
    if self.debug_mode:
        print(f"  Using specialized C89 reorganization for {len(all_sections)} sections")

    # Get PART boundaries from hidden input
    part_boundaries = self.extract_parts_from_hidden_input_c89(soup)

    if part_boundaries:
        # Create temporary structure with all sections
        temp_parts = [{
            'part_number': 'TEMP',
            'part_title': None,
            'section_groups': [{
                'title': None,
                'sections': all_sections
            }]
        }]

        # Reorganize using C89 logic
        final_parts = self.reorganize_sections_by_part_c89(temp_parts, part_boundaries)
    else:
        # Fallback to standard routing
        final_parts = self.master_route_sections_to_structure(all_sections, textual_containers, full_text)
else:
    final_parts = self.master_route_sections_to_structure(all_sections, textual_containers, full_text)
```

## Results

### Before Fix:
```json
{
  "parts": [
    {
      "part_number": "MAIN",
      "part_title": "N/A",
      "sections": ["1", "373", "4", "5", "11", ...] // Out of order
    },
    {
      "part_number": "MAIN",
      "part_title": "N/A",
      "sections": ["6", "7", "8", "9", "10"]
    },
    ...
  ]
}
```

### After Fix:
```json
{
  "parts": [
    {
      "part_number": "PART I",
      "part_title": "PART I",
      "section_groups": [{
        "sections": ["1", "4", "5", "6", "7", "8", ...] // In order, 1-372
      }]
    },
    {
      "part_number": "PART II",
      "part_title": "PART II",
      "section_groups": [{
        "sections": ["373", "374", "375", ...] // In order, 373-391
      }]
    },
    ...
  ]
}
```

## Testing

```bash
# Run the scraper on Civil Procedure Code
python3 scrape_full_legislations.py legislation_C_89

# Check the output
python3 -c "
import json
with open('data/legislations/legislation_C/legislation_C_89.json', 'r') as f:
    data = json.load(f)
    print(f'Total parts: {len(data[\"parts\"])}')
    for part in data['parts']:
        sections = []
        for g in part['section_groups']:
            sections.extend([s['number'] for s in g['sections']])
        print(f\"{part['part_number']}: {len(sections)} sections ({sections[0]}-{sections[-1]})\")
"
```

**Expected Output**:
```
Total parts: 10
PART I: 386 sections (1-372)
PART II: 19 sections (373-391)
PART III: 55 sections (392-440C)
PART IV: 201 sections (456-638)
PART V: 24 sections (650-675)
PART VI: 30 sections (676-711)
PART VII: 43 sections (712-752)
PART VIII: 24 sections (753-777)
PART IX: 9 sections (792-801)
PART X: 7 sections (834-840)
```

## Key Features

1. **Automatic Detection**: Automatically detects legislation_C_89 and applies specialized handling
2. **Hidden Input Parsing**: Extracts PART structure from hidden input field
3. **Section Range Mapping**: Maps each section to correct PART based on numeric range
4. **Order Preservation**: Sections are sorted numerically within each PART
5. **All Sections Included**: Handles both visible (101) and rescued (697) sections = 798 total
6. **Fallback Support**: Falls back to standard processing if hidden input not found

## Files Modified

- **scrape_full_legislations.py**
  - Lines 9841-10115: New specialized C89 functions
  - Line 5482: Modified section extraction
  - Lines 5687-5711: Added C89 routing logic

## Documentation

- `CIVIL_PROCEDURE_CODE_ISSUE.md`: Original problem documentation
- `CIVIL_PROCEDURE_CODE_FIX_COMPLETE.md`: This file - solution documentation

## Status

✅ **COMPLETE AND TESTED**

All 798 sections of the Civil Procedure Code are now:
- ✅ Properly organized into 10 PARTs
- ✅ Sorted in correct numerical order within each PART
- ✅ Assigned to correct PARTs based on section numbers
- ✅ Saved to: `data/legislations/legislation_C/legislation_C_89.json`
