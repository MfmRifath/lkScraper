import json
import sys
import os

def capitalize_title(title):
    """
    Capitalize the first letter of each word in the legislation title (Title Case).
    """
    if title is None or title == "":
        return title
    return title.title()

def process_legislation_file(file_path):
    """
    Process a legislation JSON file and capitalize all legislation titles.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        count = 0

        # Handle different JSON structures
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and 'title' in item:
                    original = item['title']
                    item['title'] = capitalize_title(item['title'])
                    count += 1
        elif isinstance(data, dict):
            if 'title' in data:
                original = data['title']
                data['title'] = capitalize_title(data['title'])
                count += 1

            # Handle nested structures (e.g., legislations array)
            for value in data.values():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and 'title' in item:
                            original = item['title']
                            item['title'] = capitalize_title(item['title'])
                            count += 1

        # Write back to file
        if count > 0:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        return count

    except FileNotFoundError:
        print(f"✗ Error: File not found - {file_path}")
        return 0
    except json.JSONDecodeError:
        print(f"✗ Error: Invalid JSON in file - {file_path}")
        return 0
    except Exception as e:
        print(f"✗ Error processing {file_path}: {str(e)}")
        return 0

def main():
    """
    Main function to capitalize titles in legislation files.
    """
    if len(sys.argv) < 2:
        print("Usage: python caps.py <file_or_directory_path>")
        print("Example: python caps.py data/legislations/legislation_C")
        print("Example: python caps.py data/legislations/legislation_C/legislation_C_1.json")
        return

    path = sys.argv[1]

    # Check if path is a directory
    if os.path.isdir(path):
        print(f"Processing directory: {path}\n")

        # Get all JSON files in the directory
        json_files = [f for f in os.listdir(path) if f.endswith('.json')]

        if not json_files:
            print(f"No JSON files found in {path}")
            return

        total_titles = 0
        total_files = 0

        for json_file in sorted(json_files):
            file_path = os.path.join(path, json_file)
            count = process_legislation_file(file_path)
            if count > 0:
                print(f"✓ {json_file}: {count} title(s) updated")
                total_titles += count
                total_files += 1

        print(f"\n{'='*50}")
        print(f"Processed {total_files} file(s)")
        print(f"Updated {total_titles} legislation title(s) in total")
        print(f"{'='*50}")

    elif os.path.isfile(path):
        print(f"Processing file: {path}\n")
        count = process_legislation_file(path)
        if count > 0:
            print(f"\n✓ Successfully updated {count} legislation title(s)")

    else:
        # Try adding .json extension
        if not path.endswith('.json'):
            json_path = path + '.json'
            if os.path.isfile(json_path):
                print(f"Processing file: {json_path}\n")
                count = process_legislation_file(json_path)
                if count > 0:
                    print(f"\n✓ Successfully updated {count} legislation title(s)")
            else:
                print(f"✗ Error: Path not found - {path}")
        else:
            print(f"✗ Error: Path not found - {path}")

if __name__ == "__main__":
    main()
