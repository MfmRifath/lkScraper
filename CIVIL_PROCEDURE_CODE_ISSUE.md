# Civil Procedure Code Structure Issue

## Problem

The Civil Procedure Code (`legislation_C_89`) has sections out of order and incorrectly assigned to parts:

### Current (Incorrect) Structure:
- Part 1 (MAIN): Sections 1, 373, 4, 5, 11... (all mixed up)
- Part 2 (MAIN): Sections 6-10
- Part 3 (MAIN): Sections 374-391
- ...etc

### Expected (Correct) Structure:
- PART I: OF ACTIONS IN GENERAL (sections 1-372)
- PART II: OF SUMMARY PROCEDURE (sections 373-391)
- PART III: INCIDENTAL PROCEEDINGS (sections 392-404)
- PART IV: ACTIONS IN PARTICULAR CASES (sections 405-455)
- PART V: PROVISIONAL REMEDIES (sections 456-649)
- PART VI: OF SPECIAL PROCEEDINGS (sections 650-675)
- PART VII: OF THE AIDING AND CONTROLLING OF EXECUTORS (sections 676-752)
- PART VIII: OF APPEALS (sections 753-777)
- PART IX: OF SUMMARY PROCEDURE IN RESPECT OF CONTEMPTS (sections 792-801)
- PART X: MISCELLANEOUS (sections 802-840)

## Root Cause

The Civil Procedure Code HTML structure is unusual:

1. **PART headers are in a hidden input field**: The PART I through PART X headers exist in `<input name="selectedhtml" type="hidden">`, not in the visible DOM
2. **Only PART I has a proper font tag**: The visible DOM only has `<font class="sectionpart">PART I</font>`
3. **Section tables don't reference their PART**: The section tables in the visible DOM don't have clear markers indicating which PART they belong to

## What the Scraper Does

1. **PART Detection**: Looks for `<font class="sectionpart">` tags → Only finds PART I
2. **Textual Extraction**: Analyzes the hidden input field → Correctly finds all 10 PARTS with section ranges
3. **Section Scraping**: Processes section tables from visible DOM → Gets all 609 sections
4. **Section Assignment**: Tries to assign sections to parts based on textual containers → **FAILS** because:
   - The visible section tables don't have PART markers
   - The routing logic can't match sections to the correct PART
   - Sections get assigned to wrong parts or all lumped into MAIN PART

## Fixes Applied

### Fix 1: Enhanced PART Detection (Lines 2501-2549)
```python
# Now searches both:
# 1. Visible DOM for <font class="sectionpart">
# 2. Hidden input field for PART headers
# 3. Merges and deduplicates results
```

### Fix 2: Section-to-PART Routing
The textual extraction correctly identifies:
```
Textual containers found: 11
  - MAIN PART: None (sections 4-800)
  - PART I: OF ACTIONS IN GENERAL (sections 6-8)
  - PART II: None (sections 373-391)
  - PART III: None (sections 392-404)
  ...
```

**However**, the section ranges are incorrect (e.g., PART I shows "sections 6-8" when it should be "sections 1-372").

## Additional Fix Needed

The `_fix_misplaced_sections` function needs to use the textual container information more aggressively to reassign sections:

1. Parse the hidden input to extract the exact PART boundaries
2. For each section, determine which PART it belongs to based on:
   - Section number
   - Position in the hidden input text
   - PART boundaries
3. Reassign sections to correct parts

## Temporary Workaround

For now, users can:
1. Use the textual container section ranges as a guide
2. Manually verify section assignments in the JSON output
3. Note that the scraper DOES capture all 609 sections correctly, they're just in the wrong organizational structure

## Files Modified

- `scrape_full_legislations.py` (lines 2501-2549): Enhanced PART detection to search hidden input field

## Testing

```bash
python3 scrape_full_legislations.py legislation_C_89
```

Check output:
- "Found X PART headers" should show 10 (currently shows 1)
- "Textual containers found" should show 11 (already correct)
- Section assignment should match textual container ranges (currently incorrect)
