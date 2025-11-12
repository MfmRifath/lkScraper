import requests
from bs4 import BeautifulSoup
import json
import re
import os
import time
import random
from urllib.parse import urlparse
from pathlib import Path


class AmendmentProcessor:
    def __init__(self, base_input_dir, output_directory):
        self.base_input_dir = base_input_dir
        self.output_directory = output_directory
        
    def scrape_legislation(self, html):
        """
        Scrapes legislation content from a URL or HTML content and returns structured data.
        
        Args:
            url_or_html (str): URL of the legislation webpage or HTML content
            
        Returns:
            dict: Structured legislation data
        """
        # Get the HTML content
        soup = BeautifulSoup(html, 'html.parser')
        if not soup:
            return None
        
        # Extract basic legislation information
        title = self.extract_title(soup)
        description = self.extract_description(soup)
        preamble_list = self.extract_preamble(soup)
        enactment_date = self.extract_enactment_date(soup)
        
        # Check if there are any parts defined in the document
        has_parts = len(soup.find_all('font', class_='sectionpart')) > 0
        
        # Extract parts and their sections
        if has_parts:
            parts = self.extract_parts(soup)
        else:
            # If no parts, extract sections directly and put them in a "MAIN PART"
            parts = self.extract_main_part_only(soup)
        
        # Create the structured data
        legislation_data = {
            "title": title,
            "description": description,
            "preamble": preamble_list,
            "enactment_date": enactment_date,
            "parts": parts
        }
        
        return legislation_data
    
    def process_legislation_folders(self):
        """
        Processes HTML files from each legislation folder's amendment directory,
        combines all amendments into a single JSON object, and appends to the main legislation JSON file.
        """
        input_path = Path(self.base_input_dir)
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
                folder_name = legislation_folder.name
                amendment_dir = legislation_folder / "amendment"
                
                if not amendment_dir.exists() or not amendment_dir.is_dir():
                    print(f"No amendment directory found for {folder_name}")
                    continue
                    
                # Check if corresponding JSON file exists in output directory
                if folder_name not in json_files_dict:
                    print(f"No corresponding JSON file found for {folder_name}")
                    continue
                
                # Get the matching JSON file
                main_json_file = json_files_dict[folder_name]
                
                # Process all HTML files in the amendment directory
                amendment_files = list(amendment_dir.rglob("*.html"))
                if not amendment_files:
                    print(f"No amendment HTML files found for {folder_name}")
                    continue
                
                # Create a dictionary to store all amendments
                amendments_data = {}
                
                for html_file in amendment_files:
                    # Read the HTML content from the file
                    with open(html_file, 'r', encoding='utf-8') as f:
                        html_content = f.read()
                    
                    # Process the HTML content directly
                    amendment_data = self.scrape_legislation(html_content)
                    
                    if amendment_data:
                        # Add to amendments dictionary with filename as key
                        amendments_data[html_file.stem] = amendment_data
                        print(f"Processed amendment: {html_file.name}")
                    else:
                        print(f"Failed to process amendment: {html_file.name}")
                
                # Load the existing legislation JSON
                with open(main_json_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                
                # Add or update the amendments section
                existing_data["amendments"] = amendments_data
                
                # Save the updated JSON back to the file
                with open(main_json_file, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, indent=4)
                
                print(f"Updated {main_json_file} with {len(amendments_data)} amendments")


    def clean_text(self, text):
        """
        Clean text by removing unnecessary whitespace, fixing quotes, etc.
        """
        if not text:
            return ""
            
        # Remove unnecessary line breaks and extra spaces
        text = re.sub(r'\n+', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # Fix quotation marks
        text = re.sub(r'["\']{2,}', '"', text)  # Replace multiple quotes with a single one
        text = re.sub(r'\\+"', '"', text)  # Replace escaped quotes
        
        # Fix stray apostrophes and inconsistent quote types
        text = re.sub(r'\s*"\s*\'\s*', '"', text)
        text = re.sub(r'\s*\'\s*"\s*', '"', text)
        
        # Remove periods and spaces at the beginning of the text
        text = re.sub(r'^[.\s]+', '', text)
        
        # Clean up any ". some-text-after.andspace" patterns
        text = re.sub(r'\.\s+([^.]+)\.andspace', r'\1', text)
        
        # Fix inconsistent spacing around punctuation
        text = re.sub(r'\s*:\s*', ': ', text)
        text = re.sub(r'\s*;\s*', '; ', text)
        
        return text

    

    def extract_main_part_only(self, soup):
        """
        Extracts all sections when there are no explicit parts defined.
        
        Args:
            soup (BeautifulSoup): Parsed HTML
            
        Returns:
            list: List containing a single part dictionary with all sections
        """
        # Find all section tables
        section_tables = soup.find_all('table', cellspacing="4mm", width="70%")
        
        # Extract data for each section with continuation handling
        sections = self.extract_sections_with_continuations(section_tables)
        
        # Create a single "MAIN PART" containing all sections
        return [{
            "part_number": None,
            "part_title": "MAIN PART",
            "sections": sections
        }]

    def extract_sections_with_continuations(self, section_tables):
        """
        Extracts sections with proper handling of continuation sections.

        Args:
            section_tables (list): List of BeautifulSoup table elements

        Returns:
            list: List of section dictionaries with continuations included
        """
        sections = []
        current_section = None

        for idx, table in enumerate(section_tables):
            # Try to extract section number
            section_num = self.extract_section_number(table, idx)

            # If this table has a section number, it's a new section
            if section_num:
                # If we were tracking a previous section, finalize it
                if current_section:
                    self.finalize_section(current_section)
                    sections.append(current_section)

                # Start a new section
                current_section = self.extract_single_section(table, idx)
                current_section["continuation"] = []

            # If no section number, but we have a current section, treat as continuation
            elif current_section:
                continuation_content = self.extract_continuation_content(table)
                current_section["continuation"].append(continuation_content)

            # If no section number and no current section, something's wrong
            # We'll create a placeholder section
            else:
                placeholder_section = {
                    "section_number": f"unnumbered_{idx}",
                    "heading": self.extract_section_heading(table, idx) or "Unnumbered Section",
                    "content": self.extract_continuation_content(table),
                    "subsections": [],
                    "continuation": []
                }
                sections.append(placeholder_section)
                current_section = None

        # Don't forget to add the last section if there is one
        if current_section:
            self.finalize_section(current_section)
            sections.append(current_section)

        return sections

    def finalize_section(self, section):
        """
        Finalize a section by processing its continuations.
        For interpretation/definition sections, promote continuation definitions to subsections.

        Args:
            section (dict): Section dictionary to finalize
        """
        # Check if this is an interpretation section
        heading = section.get('heading') or ''
        heading = heading.lower() if heading else ''
        content = section.get('content') or ''
        is_interpretation = 'interpretation' in heading or 'definition' in heading

        # Also check content for interpretation indicators
        if not is_interpretation and content and isinstance(content, str):
            definition_indicators = [
                r'unless\s+the\s+context\s+otherwise\s+requires',
                r'following\s+definitions?\s+shall\s+apply',
            ]
            for pattern in definition_indicators:
                if re.search(pattern, content[:200], re.I):
                    is_interpretation = True
                    break

        # If this is an interpretation section and has continuations with definitions,
        # promote those definitions to be direct subsections
        if is_interpretation and section.get('continuation'):
            for cont in section['continuation']:
                if cont.get('subsections'):
                    # Add continuation subsections to main section subsections
                    section['subsections'].extend(cont['subsections'])
                    # Clear the continuation's subsections since we promoted them
                    cont['subsections'] = []

            # Remove empty continuations
            section['continuation'] = [
                cont for cont in section['continuation']
                if cont.get('content') or cont.get('subsections')
            ]

    def extract_continuation_content(self, table):
        """
        Extract content from a continuation table.

        Args:
            table (BeautifulSoup element): Table containing continuation content

        Returns:
            dict: Extracted continuation content
        """
        # Extract heading if present
        heading = self.extract_section_heading(table, 0)

        # Extract content
        content_element = table.find('font', class_='sectioncontent')
        content = ""
        subsections = []

        if content_element:
            full_content = content_element.get_text(separator=" ", strip=True)
            content = self.clean_text(full_content)

            # Extract any nested subsections from tables
            subsections = self.extract_nested_subsections(content_element)

            # If no table-based subsections found, check if this looks like a definition
            # Continuation tables often contain individual definitions
            if not subsections:
                # Check if this is a single definition entry (starts with a quoted term)
                # Updated to handle "with reference to" in addition to "in relation to"
                definition_match = re.match(r'^["\']([^"\']+)["\'][\s,]*((?:(?:in\s+relation\s+to|with\s+reference\s+to)[^;]*?[,;]?\s*)?(?:means|includes|shall\s+mean|shall\s+include|has\s+the\s+same\s+meaning))', content, re.I)
                if definition_match:
                    term = definition_match.group(1).strip()
                    definition_content = content

                    # Remove the term and quotes from the content
                    definition_content = re.sub(r'^["\']' + re.escape(term) + r'["\'][\s,]*', '', definition_content)
                    definition_content = self.clean_text(definition_content)

                    # This continuation is actually a definition, so return it as a subsection
                    subsections = [{
                        "identifier": f'"{term}"',
                        "content": definition_content,
                        "subsections": []
                    }]
                    # Clear the main content since it's now in subsections
                    content = ""

        return {
            "heading": heading,
            "content": content,
            "subsections": subsections
        }

    def extract_title(self, soup):
        """
        Extract the title from the HTML content.

        Args:
            soup (BeautifulSoup): Parsed HTML

        Returns:
            str: Extracted title
        """
        title_element = soup.find('font', class_='actname')
        title = self.clean_text(title_element.text) if title_element else "Unknown Title"

        # Remove section range from title (e.g., "(1 - 91)" or "(1-91)")
        # Pattern: space + (number + dash + number) at the end
        title = re.sub(r'\s*\(\s*\d+\s*[-–]\s*\d+\s*\)\s*$', '', title).strip()

        return title

    def extract_description(self, soup):
        """
        Extract the description from the HTML content.
        
        Args:
            soup (BeautifulSoup): Parsed HTML
            
        Returns:
            str: Extracted description
        """
        description_element = soup.find('td', class_='descriptionhead')
        return self.clean_text(description_element.text) if description_element else "No description available"

    def extract_preamble(self, soup):
        """
        Extract the preamble from the HTML content.
        
        Args:
            soup (BeautifulSoup): Parsed HTML
            
        Returns:
            list: List of preamble paragraphs
        """
        return [
            self.clean_text(p_tag.get_text(separator=" ")) 
            for p_tag in soup.find_all("p", class_="descriptioncontent")
        ] or []

    def extract_enactment_date(self, soup):
        """
        Extract the enactment date from the HTML content.
        
        Args:
            soup (BeautifulSoup): Parsed HTML
            
        Returns:
            str: Extracted enactment date or None
        """
        date_section = soup.find("sup", class_="datesup")
        if date_section:
            date_text = date_section.find_parent("b").get_text(strip=True, separator=" ")
            return self.clean_text(date_text)
        return None

    def extract_parts(self, soup):
        """
        Extract all parts and their sections from the HTML content.
        
        Args:
            soup (BeautifulSoup): Parsed HTML
            
        Returns:
            list: List of part dictionaries
        """
        parts = []
        
        # Get all section part elements
        part_elements = soup.find_all('font', class_='sectionpart')
        part_title_elements = soup.find_all('font', class_='sectionparttitle')
        
        # First, identify sections that don't belong to any part
        # These are typically at the beginning of the document
        pre_part_sections = []
        first_part_element = part_elements[0] if part_elements else None
        
        if first_part_element:
            # Find section tables that appear before the first part
            section_tables = soup.find_all('table', cellspacing="4mm", width="70%")
            pre_part_tables = []

            for table in section_tables:
                # If this table appears before the first part, it's a pre-part section
                if first_part_element.find_parent('tr').find_parent('table') and table.find_next('font', class_='sectionpart') == first_part_element:
                    pre_part_tables.append(table)
            
            # Extract sections with continuations from pre-part tables
            if pre_part_tables:
                pre_part_sections = self.extract_sections_with_continuations(pre_part_tables)
        
        # If we found any pre-part sections, add them as a special "General Provisions" part
        if pre_part_sections:
            parts.append({
                "part_number": None,
                "part_title": "MAIN PART",
                "sections": pre_part_sections
            })
        
        # Check if we have any part elements but no title elements
        # This can happen in some legislation formats
        if len(part_elements) > 0 and len(part_title_elements) == 0:
            # In this case, we'll treat each part_element as both a part marker and title
            for i, part_element in enumerate(part_elements):
                part_number = self.clean_text(part_element.text)
                part_title = self.clean_text(part_element.text)  # Using the same text as title
                
                # Get all section tables that follow this part element
                next_part = part_elements[i+1] if i+1 < len(part_elements) else None
                section_tables = self.extract_section_tables_between_parts(soup, part_element, next_part)
                
                # Extract sections with continuations from this part's tables
                sections = self.extract_sections_with_continuations(section_tables)
                
                parts.append({
                    "part_number": part_number,
                    "part_title": part_title,
                    "sections": sections
                })
        else:
            # Process each part with title
            for i, (part_element, title_element) in enumerate(zip(part_elements, part_title_elements)):
                part_number = self.clean_text(part_element.text)
                part_title = self.clean_text(title_element.text)
                
                # Get the next part element to know where this part ends
                next_part = part_elements[i+1] if i+1 < len(part_elements) else None
                
                # Get all section tables that belong to this part
                section_tables = self.extract_section_tables_between_parts(soup, title_element, next_part)
                
                # Extract sections with continuations from this part's tables
                sections = self.extract_sections_with_continuations(section_tables)
                
                # Add this part to our list
                parts.append({
                    "part_number": part_number,
                    "part_title": part_title,
                    "sections": sections
                })
        
        return parts

    def extract_section_tables_between_parts(self, soup, current_part_element, next_part_element):
        """
        Extract all section tables between two part elements.
        
        Args:
            soup (BeautifulSoup): Parsed HTML
            current_part_element: The current part element
            next_part_element: The next part element or None if this is the last part
            
        Returns:
            list: List of section table elements
        """
        section_tables = []
        current_element = current_part_element.find_parent('table')
        
        while current_element and current_element.find_next('table'):
            current_element = current_element.find_next('table')
            
            # If we've reached the next part heading, break
            if next_part_element and (
                current_element.find('font', class_='sectionpart') == next_part_element or
                current_element.find('font', class_='sectionparttitle') == next_part_element
            ):
                break
            
            # If this is a section table, add it to our list
            if current_element.get('cellspacing') == "4mm" and current_element.get('width') == "70%":
                section_tables.append(current_element)
        
        return section_tables

    def extract_single_section(self, table, idx):
        """
        Extract data for a single section.
        
        Args:
            table (BeautifulSoup element): Table containing section data
            idx (int): Index of the section
            
        Returns:
            dict: Section data
        """
        
        # Extract section heading
        heading = self.extract_section_heading(table, idx)
        
        # Extract section number
        section_num = self.extract_section_number(table, idx)
        
        # Extract section content and subsections
        content, subsections = self.extract_section_content(table)
        
        return {
            "section_number": section_num,
            "heading": heading,
            "content": content,
            "subsections": subsections,
            "continuation": []  # Initialize empty continuation list    }
        }

    def extract_section_heading(self, table, idx):
        """
        Extract the heading for a section.
        
        Args:
            table (BeautifulSoup element): Table containing section data
            idx (int): Index of the section
            
        Returns:
            str: Section heading
        """
        heading_element = table.find('font', style="font-size: 12px")
        
        if heading_element:
            return self.clean_text(heading_element.text)
        
        # If no heading element found, check for alternative heading formats
        alt_heading = table.find('div', align="left")
        if alt_heading and alt_heading.find('font'):
            return self.clean_text(alt_heading.find('font').text)
        
        return None

    def extract_section_number(self, table, idx):
        """
        Extract the section number.
        
        Args:
            table (BeautifulSoup element): Table containing section data
            idx (int): Index of the section
            
        Returns:
            str: Section number
        """
        section_num_element = table.find('font', style="font-family: Times New Roman; font-size: 14pt; color: black; font-weight: bold;")
        
        if section_num_element and section_num_element.find('a'):
            return self.clean_text(section_num_element.find('a').text)
        elif section_num_element:
            return self.clean_text(section_num_element.text)
        
        return None

    def extract_section_content(self, table):
        """
        Extract content and subsections for a section.

        Args:
            table (BeautifulSoup element): Table containing section data

        Returns:
            tuple: (content text, subsections list)
        """
        content_element = table.find('font', class_='sectioncontent')

        if not content_element:
            return "No content available", []

        # Get ALL the text first, before any processing
        full_content = content_element.get_text(separator=" ", strip=True)

        # Process only the direct text content, not including nested subsections
        direct_content = ""
        for child in content_element.children:
            if isinstance(child, str):
                direct_content += child
            elif child.name == 'table':
                break
            elif child.name != 'table' and not child.find('table'):
                direct_content += child.get_text(separator=" ", strip=True)

        # Clean the text
        direct_content = self.clean_text(direct_content)

        # Extract any section numbers at the beginning
        direct_content = re.sub(r'^\d+\.', '', direct_content).strip()

        # Check if this is an interpretation/definitions section
        is_interpretation_section = self.is_interpretation_section(table, full_content)

        # Extract nested subsections recursively
        subsections = self.extract_nested_subsections(content_element)

        # If this is an interpretation section and no table-based subsections found,
        # try to extract definitions as subsections from the text
        if is_interpretation_section and not subsections:
            direct_content, subsections = self.extract_definitions_as_subsections(full_content)

        return direct_content, subsections

    def is_interpretation_section(self, table, content):
        """
        Check if a section is an interpretation/definitions section.

        Args:
            table (BeautifulSoup element): Table containing section data
            content (str): Section content text

        Returns:
            bool: True if this is an interpretation section
        """
        # Check section heading/title
        heading_element = table.find('font', style="font-size: 12px")
        if heading_element:
            heading = self.clean_text(heading_element.text)
            if heading:
                heading = heading.lower()
                if 'interpretation' in heading or 'definition' in heading:
                    return True

        # Ensure content is a string
        if not content or not isinstance(content, str):
            return False

        # Check content for interpretation indicators
        definition_indicators = [
            r'unless\s+the\s+context\s+otherwise\s+requires',
            r'following\s+definitions?\s+shall\s+apply',
            r'following\s+expressions?\s+shall\s+have',
            r'words\s+and\s+expressions?\s+shall\s+have',
            r'In\s+this\s+(?:Chapter|Part|Act|Ordinance)',
        ]

        for pattern in definition_indicators:
            if re.search(pattern, content[:500], re.I):
                return True

        # Check if content contains multiple definition patterns
        def_pattern = re.compile(r'["\']([a-zA-Z][a-zA-Z\s\-]*?)["\'][\s,]+(?:means|includes|shall\s+mean|shall\s+include)', re.I)
        matches = def_pattern.findall(content)
        if len(matches) >= 2:  # If 2+ definitions, treat as definitions section
            return True

        return False

    def extract_definitions_as_subsections(self, full_content):
        """
        Extract definitions from text as subsections.

        Args:
            full_content (str): Full section content

        Returns:
            tuple: (preface content, list of subsections)
        """
        subsections = []

        # Pattern for definitions: "term" means/includes...
        # Updated pattern to handle various formats:
        # - "term" means...
        # - "term", in relation to..., means...
        # - "term"with reference to... means/includes... (note: sometimes no space after quote)
        definition_pattern = re.compile(
            r'["\']([^"\']+?)["\'][\s,]*((?:(?:in\s+relation\s+to|with\s+reference\s+to)[^;]*?[,;]?\s*)?(?:means|includes|shall\s+mean|shall\s+include|has\s+the\s+same\s+meaning))',
            re.I
        )

        matches = list(definition_pattern.finditer(full_content))

        if not matches:
            # No definitions found, return content as is
            return full_content, []

        # Extract preface (content before first definition)
        preface = full_content[:matches[0].start()].strip()
        preface = self.clean_text(preface)

        # Extract each definition as a subsection
        for i, match in enumerate(matches):
            term = match.group(1).strip()

            # Find the content of this definition
            # It starts from the match and goes until the next definition or end
            start_pos = match.start()
            if i + 1 < len(matches):
                end_pos = matches[i + 1].start()
            else:
                end_pos = len(full_content)

            definition_content = full_content[start_pos:end_pos].strip()

            # Clean the definition content
            definition_content = self.clean_text(definition_content)

            # Remove the quotes and term from the beginning if present
            # Keep the full definition including "in relation to" clause
            definition_content = re.sub(r'^["\']' + re.escape(term) + r'["\'][\s,]*', '', definition_content)

            # Check for nested subsections within the definition (e.g., (a), (b), (i), (ii))
            nested_subsections = self.extract_nested_subsections_from_text(definition_content)

            # If nested subsections found, extract just the preface
            if nested_subsections:
                # Find where the first nested subsection starts
                # Updated to handle both newline-separated and inline formats
                first_nested_pattern = re.search(r'(?:^|(?<=[;.\-:])\s+|\n\s*|(?:\band\b\s+))(\([a-z0-9ivxlcdm]+\))\s+', definition_content, re.I)
                if first_nested_pattern:
                    definition_content = definition_content[:first_nested_pattern.start()].strip()
                    # Remove trailing separator if present
                    definition_content = re.sub(r'[;.]\s*$', '', definition_content).strip()
                    definition_content = self.clean_text(definition_content)

            subsections.append({
                "identifier": f'"{term}"',
                "content": definition_content,
                "subsections": nested_subsections
            })

        return preface, subsections

    def extract_nested_subsections_from_text(self, text):
        """
        Extract nested subsections from plain text (e.g., (a), (b), (i), (ii)).

        Args:
            text (str): Text content to parse

        Returns:
            list: List of nested subsection dictionaries
        """
        if not text:
            return []

        nested_subsections = []

        # Pattern for nested subsections: (a), (b), (i), (ii), (1), (2), etc.
        # Updated to handle both newline-separated and inline (semicolon-separated) formats
        # Match after: start, newline, after semicolon/period/dash/colon+space, or after "and"
        nested_pattern = re.compile(r'(?:^|(?<=[;.\-:])\s+|\n\s*|(?:\band\b\s+))(\([a-z0-9ivxlcdm]+\))\s+', re.I | re.M)
        nested_matches = list(nested_pattern.finditer(text))

        # Use a set to track identifiers we've already added (to avoid duplicates)
        seen_identifiers = set()

        for j, match in enumerate(nested_matches):
            identifier = match.group(1).strip()

            # Skip if we've already processed this identifier
            if identifier in seen_identifiers:
                continue
            seen_identifiers.add(identifier)

            # Find content: from after the identifier to before the next identifier or end
            nested_start = match.end()
            if j + 1 < len(nested_matches):
                # Find the next UNIQUE identifier
                next_match_idx = j + 1
                while next_match_idx < len(nested_matches):
                    next_identifier = nested_matches[next_match_idx].group(1).strip()
                    if next_identifier not in seen_identifiers:
                        nested_end = nested_matches[next_match_idx].start()
                        break
                    next_match_idx += 1
                else:
                    nested_end = len(text)
            else:
                nested_end = len(text)

            content = text[nested_start:nested_end].strip()
            # Remove trailing separators
            content = re.sub(r'[;.]\s*$', '', content).strip()
            content = self.clean_text(content)

            if content:
                nested_subsections.append({
                    "identifier": identifier,
                    "content": content,
                    "subsections": []
                })

        return nested_subsections

    def extract_nested_subsections(self, parent_element, max_depth=10, current_depth=0):
        """
        Recursively extracts nested subsections from HTML content.
        
        Args:
            parent_element: The BeautifulSoup element to extract from
            max_depth: Maximum recursion depth to prevent infinite recursion
            current_depth: Current recursion depth
            
        Returns:
            list: Extracted subsections
        """
        
        if not parent_element or current_depth >= max_depth:
            return []
        
        subsection_tables = parent_element.find_all('table', cellspacing="2mm", recursive=False)
        
        subsections = []
        for table in subsection_tables:
            subsection_data = self.extract_single_subsection(table, max_depth, current_depth)
            if subsection_data:
                subsections.append(subsection_data)
        
        return subsections

    def extract_single_subsection(self, table, max_depth, current_depth):
        """
        Extract data for a single subsection.
        
        Args:
            table (BeautifulSoup element): Table containing subsection data
            max_depth: Maximum recursion depth
            current_depth: Current recursion depth
            
        Returns:
            dict: Subsection data or None if extraction fails
        """
        # Find the subsection content
        subsection_content = table.find('font', class_='subsectioncontent')
        if not subsection_content:
            return None
        
        # Extract title if present
        title = None
        title_font = table.find('font', style="font-size: 11px")
        if title_font:
            title = self.clean_text(title_font.text)
        
        # Get the direct text content (not nested in other elements)
        direct_text = ""
        for child in subsection_content.children:
            if isinstance(child, str):
                direct_text += child
            elif child.name == 'table':
                break
            elif child.name != 'table' and not child.find('table'):
                direct_text += child.get_text(separator=" ", strip=True)
        
        # Clean the text
        direct_text = self.clean_text(direct_text)
        
        # Remove quotes at beginning and end
        direct_text = re.sub(r'^["\']\s*', '', direct_text)
        direct_text = re.sub(r'\s*["\']$', '', direct_text)
        
        # Extract identifier
        identifier = ""
        identifier_match = re.match(r'^[\s"\']*(\([A-Za-z0-9]+\)|\d+\.|\w+\))', direct_text)
        if identifier_match:
            identifier = identifier_match.group(1)
            # Remove the identifier from content
            content = direct_text[direct_text.find(identifier) + len(identifier):].strip()
        else:
            content = direct_text
        
        # Get nested subsections
        nested_subsections = self.extract_nested_subsections(
            subsection_content,
            max_depth=max_depth,
            current_depth=current_depth + 1
        )
        
        result = {
            "identifier": identifier,
            "content": content,
            "subsections": nested_subsections
        }
        
        if title:
            result["title"] = title
        
        return result