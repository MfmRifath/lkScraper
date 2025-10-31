import json
from pathlib import Path
from scrape_full_legislations import MainHTMLProcessor

class ExtendedLegislationMerger:
    def __init__(self, html_folder, data_folder, input_directory=None, output_directory=None):
        self.html_folder = html_folder
        self.data_folder = data_folder
        self.processor = MainHTMLProcessor(html_folder, data_folder)
        self.input_directory = input_directory
        self.output_directory = output_directory
        
    def set_paths(self, input_directory, output_directory):
        """Set the input and output directories."""
        self.input_directory = input_directory
        self.output_directory = output_directory
    
    def process_legislation_folders(self):
        """
        Processes HTML files and merges MAIN PART section groups with the last part's section groups.
        Works with flat output directory structure where all JSON files are in one folder.
        """
        input_path = Path(self.input_directory)
        output_path = Path(self.output_directory)
        
        if not input_path.exists():
            print(f"Input directory {input_path} does not exist.")
            return
        
        if not output_path.exists():
            print(f"Output directory {output_path} does not exist.")
            return
        
        # Get all JSON files in the output directory
        all_json_files = list(output_path.glob("*.json"))
        json_files_dict = {file.stem: file for file in all_json_files}
        
        for legislation_folder in input_path.iterdir():
            if legislation_folder.is_dir():
                schedules_dir = legislation_folder / "parts"
                
                if schedules_dir.exists() and schedules_dir.is_dir():
                    # Look for corresponding JSON file in the flat output directory
                    folder_name = legislation_folder.name
                    
                    if folder_name not in json_files_dict:
                        print(f"No corresponding JSON file found for {folder_name}")
                        continue
                    
                    # Get the matching JSON file
                    main_json_file = json_files_dict[folder_name]
                    
                    # Load the existing JSON file
                    with open(main_json_file, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                    
                    # Process each HTML file and update the existing JSON
                    for html_file in schedules_dir.glob("*.html"):
                        with open(html_file, 'r', encoding='utf-8') as f:
                            html_content = f.read()
                        
                        # Get new data from the HTML file
                        # We're using the construct_json_data from HTMLProcessor
                        new_data = self.processor.construct_json_data(html_content, html_file.name)
                        
                        # Handle parts specifically checking for MAIN PART
                        if "parts" in new_data and "parts" in existing_data and existing_data["parts"]:
                            regular_parts = []
                            
                            for part in new_data["parts"]:
                                if part.get("number") == "MAIN PART" and "section_groups" in part:
                                    # Get the section groups from MAIN PART
                                    main_part_section_groups = part["section_groups"]
                                    
                                    # Get the last part in existing data
                                    last_part = existing_data["parts"][-1]
                                    
                                    # Ensure section_groups exists in the last part
                                    if "section_groups" not in last_part:
                                        last_part["section_groups"] = []
                                    
                                    # Directly append all section groups from MAIN PART to the last part
                                    last_part["section_groups"].extend(main_part_section_groups)
                                    print(f"Appended {len(main_part_section_groups)} section groups from MAIN PART to the last part")
                                else:
                                    # For non-MAIN PART parts, collect them to add later
                                    regular_parts.append(part)
                            
                            # Add all regular parts after handling MAIN PART
                            existing_data["parts"].extend(regular_parts)
                        
                        # Append new schedules to existing schedules
                        if "schedules" in new_data and "schedules" in existing_data:
                            existing_data["schedules"].extend(new_data["schedules"])
                        
                        print(f"Processed and appended data from: {html_file}")
                    
                    # Save the updated JSON back to the file
                    with open(main_json_file, "w", encoding="utf-8") as f:
                        json.dump(existing_data, f, indent=4)
                    
                    print(f"Updated JSON file: {main_json_file}")