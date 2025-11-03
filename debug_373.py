from scrape_full_legislations import MainHTMLProcessor
import json

processor = MainHTMLProcessor('data/html/legislation_test', 'data/legislations/legislation_test')
processor.debug_mode = True

html_path = 'data/html/legislation_test/legislation_C_89/legislation_C_89.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

print("=" * 60)
print("DEBUGGING SECTION 373 MISPLACEMENT")
print("=" * 60)

json_data = processor.construct_json_data(html_content, 'legislation_C_89')

print("\n\n" + "=" * 60)
print("FINAL RESULTS")
print("=" * 60)

# Find section 373
found_373 = False
for part in json_data.get('parts', []):
    for group in part.get('section_groups', []):
        for section in group.get('sections', []):
            if section.get('number') == '373':
                print(f"\nSection 373 found in:")
                print(f"  Part: {part.get('number')}")
                print(f"  Chapter: {group.get('number')}")
                found_373 = True

if not found_373:
    print("\n Section 373 NOT FOUND")

# Check PART II content
print("\n\nPART II content:")
for part in json_data.get('parts', []):
    if part.get('number') == 'PART II':
        for group in part.get('section_groups', []):
            sections = [s.get('number') for s in group.get('sections', [])]
            print(f"  Chapter {group.get('number')}: {len(sections)} sections")
            if sections:
                print(f"    Range: {sections[0]} to {sections[-1]}")
