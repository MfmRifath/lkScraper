#!/usr/bin/env python3
"""
Enhanced Legislation Analyzer - Production Version with Repealed Section Support

Analyzes:
1. Missing sections within individual legislation JSON files
2. Repealed sections and their status
3. Missing files in directory sequences based on naming patterns

Author: Legislative Analysis Tool
Version: 3.0.0
License: MIT
"""

import json
import csv
import logging
import argparse
import sys
import os
from typing import List, Dict, Tuple, Set, Optional, Union
from pathlib import Path
from datetime import datetime
import re
from dataclasses import dataclass, asdict, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from collections import defaultdict

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('legislation_analyzer.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class RepealedSection:
    """Data class to store repealed section information."""
    number: int
    repealing_ordinance: Optional[str] = None
    repealing_year: Optional[str] = None
    has_content: bool = False
    note: Optional[str] = None

@dataclass
class LegislationAnalysis:
    """Data class to store individual legislation analysis results."""
    name: str
    title: str
    enactment_year: str
    file_path: str
    existing_sections: List[int]
    missing_sections: List[int]
    repealed_sections: List[RepealedSection]
    has_missing_sections: bool
    has_repealed_sections: bool
    total_sections_expected: int
    total_sections_found: int
    missing_count: int
    repealed_count: int
    repealed_with_content_count: int
    completeness_percentage: float
    analysis_timestamp: str
    error_message: Optional[str] = None

@dataclass
class DirectoryAnalysis:
    """Data class to store directory-level analysis results."""
    directory_path: str
    pattern_name: str
    total_files_found: int
    missing_files: List[str]
    missing_file_numbers: List[int]
    has_missing_files: bool
    expected_file_range: str
    file_completeness_percentage: float
    analysis_timestamp: str

@dataclass
class ComprehensiveReport:
    """Data class for comprehensive analysis report."""
    directory_analyses: List[DirectoryAnalysis]
    individual_analyses: List[LegislationAnalysis]
    summary_stats: Dict
    analysis_timestamp: str

class LegislationAnalyzer:
    """Enhanced analyzer class for processing legislation data with repealed section support."""
    
    def __init__(self, config: Optional[Dict] = None):
        """Initialize the analyzer with optional configuration."""
        self.config = config or {}
        self.processed_count = 0
        self.error_count = 0
        self.start_time = None
        
    def extract_section_numbers_and_repealed(self, legislation_data: Dict) -> Tuple[List[int], List[RepealedSection]]:
        """Extract all section numbers and repealed section information from a legislation JSON structure."""
        try:
            section_numbers = []
            repealed_sections = []
            
            if 'parts' not in legislation_data:
                logger.warning("No 'parts' found in legislation data")
                return section_numbers, repealed_sections
            
            for part_idx, part in enumerate(legislation_data['parts']):
                if not isinstance(part, dict):
                    logger.warning(f"Invalid part structure at index {part_idx}")
                    continue
                    
                if 'section_groups' not in part:
                    logger.warning(f"No 'section_groups' found in part {part_idx}")
                    continue
                    
                for group_idx, section_group in enumerate(part['section_groups']):
                    if not isinstance(section_group, dict):
                        logger.warning(f"Invalid section_group structure at part {part_idx}, group {group_idx}")
                        continue
                        
                    if 'sections' not in section_group:
                        logger.warning(f"No 'sections' found in part {part_idx}, group {group_idx}")
                        continue
                        
                    for sec_idx, section in enumerate(section_group['sections']):
                        if not isinstance(section, dict):
                            logger.warning(f"Invalid section structure at part {part_idx}, group {group_idx}, section {sec_idx}")
                            continue
                            
                        if 'number' not in section:
                            logger.warning(f"No 'number' found in section at part {part_idx}, group {group_idx}, section {sec_idx}")
                            continue
                            
                        section_num = section['number']
                        # Extract numeric part from section number
                        numeric_match = re.match(r'^(\d+)', str(section_num))
                        if numeric_match:
                            num = int(numeric_match.group(1))
                            section_numbers.append(num)
                            
                            # Check if this section is repealed
                            if section.get('status') == 'repealed':
                                repealed_section = RepealedSection(
                                    number=num,
                                    repealing_ordinance=section.get('repealing_ordinance'),
                                    repealing_year=section.get('repealing_year'),
                                    has_content=section.get('has_repealed_content', False),
                                    note=section.get('note')
                                )
                                repealed_sections.append(repealed_section)
                        else:
                            logger.warning(f"Could not extract numeric value from section number: {section_num}")
            
            # Also check for repealed sections summary if available
            if 'repealed_sections_summary' in legislation_data:
                summary = legislation_data['repealed_sections_summary']
                if 'sections' in summary:
                    for rep_section in summary['sections']:
                        if 'number' in rep_section:
                            try:
                                num = int(rep_section['number'])
                                # Check if we already have this repealed section
                                if not any(r.number == num for r in repealed_sections):
                                    repealed_section = RepealedSection(
                                        number=num,
                                        repealing_ordinance=rep_section.get('repealing_ordinance'),
                                        repealing_year=rep_section.get('repealing_year'),
                                        has_content=rep_section.get('has_content', False),
                                        note=rep_section.get('note')
                                    )
                                    repealed_sections.append(repealed_section)
                                    # Make sure the number is in section_numbers
                                    if num not in section_numbers:
                                        section_numbers.append(num)
                            except ValueError:
                                logger.warning(f"Invalid section number in repealed summary: {rep_section['number']}")
            
            return sorted(list(set(section_numbers))), repealed_sections
            
        except Exception as e:
            logger.error(f"Error extracting section numbers: {str(e)}")
            raise ValueError(f"Failed to extract section numbers: {str(e)}")

    def find_missing_sections(self, section_numbers: List[int], repealed_numbers: List[int]) -> List[int]:
        """Find missing sections in a sequence of section numbers, excluding repealed sections."""
        if not section_numbers:
            return []
        
        missing_sections = []
        min_section = min(section_numbers)
        max_section = max(section_numbers)
        
        for i in range(min_section, max_section + 1):
            if i not in section_numbers and i not in repealed_numbers:
                missing_sections.append(i)
        
        return missing_sections

    def analyze_single_legislation(self, legislation_data: Dict, file_path: str = "") -> LegislationAnalysis:
        """Analyze a single legislation for missing and repealed sections."""
        try:
            name = legislation_data.get('name', f'unknown_legislation_{int(time.time())}')
            title = legislation_data.get('title', 'Unknown Title')
            year = str(legislation_data.get('enactment_year', 'Unknown Year'))
            
            existing_sections, repealed_sections = self.extract_section_numbers_and_repealed(legislation_data)
            repealed_numbers = [r.number for r in repealed_sections]
            missing_sections = self.find_missing_sections(existing_sections, repealed_numbers)
            
            total_expected = (max(existing_sections) - min(existing_sections) + 1) if existing_sections else 0
            total_found = len(existing_sections)
            missing_count = len(missing_sections)
            repealed_count = len(repealed_sections)
            repealed_with_content = len([r for r in repealed_sections if r.has_content])
            
            # Calculate completeness considering repealed sections
            # Repealed sections are considered "complete" for the purpose of this metric
            sections_accounted_for = total_found
            completeness = ((sections_accounted_for / total_expected) * 100) if total_expected > 0 else 100.0
            
            analysis = LegislationAnalysis(
                name=name,
                title=title,
                enactment_year=year,
                file_path=file_path,
                existing_sections=existing_sections,
                missing_sections=missing_sections,
                repealed_sections=repealed_sections,
                has_missing_sections=missing_count > 0,
                has_repealed_sections=repealed_count > 0,
                total_sections_expected=total_expected,
                total_sections_found=total_found,
                missing_count=missing_count,
                repealed_count=repealed_count,
                repealed_with_content_count=repealed_with_content,
                completeness_percentage=round(completeness, 2),
                analysis_timestamp=datetime.now().isoformat()
            )
            
            self.processed_count += 1
            logger.debug(f"Successfully analyzed: {name} (Missing: {missing_count}, Repealed: {repealed_count})")
            return analysis
            
        except Exception as e:
            self.error_count += 1
            error_msg = f"Error analyzing legislation: {str(e)}"
            logger.error(f"{error_msg} (file: {file_path})")
            
            return LegislationAnalysis(
                name=legislation_data.get('name', 'error_legislation'),
                title=legislation_data.get('title', 'Error in Analysis'),
                enactment_year=str(legislation_data.get('enactment_year', 'Unknown')),
                file_path=file_path,
                existing_sections=[],
                missing_sections=[],
                repealed_sections=[],
                has_missing_sections=False,
                has_repealed_sections=False,
                total_sections_expected=0,
                total_sections_found=0,
                missing_count=0,
                repealed_count=0,
                repealed_with_content_count=0,
                completeness_percentage=0.0,
                analysis_timestamp=datetime.now().isoformat(),
                error_message=error_msg
            )

    def detect_file_patterns(self, directory: Union[str, Path]) -> Dict[str, List[Path]]:
        """Detect file naming patterns in a directory."""
        directory = Path(directory)
        patterns = defaultdict(list)
        
        for file_path in directory.glob("*.json"):
            # Extract pattern from filename
            # Examples: legislation_A_1.json -> legislation_A_*, file_123.json -> file_*
            filename = file_path.stem
            
            # Look for patterns like: prefix_number.json, prefix_letter_number.json
            pattern_matches = [
                (r'^(.+?)_(\d+)$', r'\1_*'),  # prefix_number
                (r'^(.+?)_([a-zA-Z]+)_(\d+)$', r'\1_\2_*'),  # prefix_letter_number
                (r'^(.+?)(\d+)$', r'\1*'),  # prefixnumber
            ]
            
            for regex_pattern, replacement in pattern_matches:
                match = re.match(regex_pattern, filename)
                if match:
                    if len(match.groups()) >= 2:
                        if len(match.groups()) == 2:  # prefix_number
                            pattern_key = f"{match.group(1)}_*"
                        else:  # prefix_letter_number
                            pattern_key = f"{match.group(1)}_{match.group(2)}_*"
                    else:
                        pattern_key = replacement
                    patterns[pattern_key].append(file_path)
                    break
            else:
                # No pattern matched, treat as individual file
                patterns[filename].append(file_path)
        
        return dict(patterns)

    def analyze_directory_for_missing_files(self, directory: Union[str, Path]) -> List[DirectoryAnalysis]:
        """Analyze a directory for missing files based on naming patterns."""
        directory = Path(directory)
        
        if not directory.exists() or not directory.is_dir():
            logger.error(f"Invalid directory: {directory}")
            return []
        
        patterns = self.detect_file_patterns(directory)
        analyses = []
        
        for pattern_name, file_paths in patterns.items():
            if len(file_paths) <= 1:
                continue  # Skip single files or empty patterns
            
            # Extract numbers from filenames
            file_numbers = []
            for file_path in file_paths:
                filename = file_path.stem
                # Extract the last number in the filename
                numbers = re.findall(r'\d+', filename)
                if numbers:
                    file_numbers.append(int(numbers[-1]))
            
            if not file_numbers:
                continue
            
            file_numbers.sort()
            min_num = min(file_numbers)
            max_num = max(file_numbers)
            
            # Find missing numbers
            expected_numbers = set(range(min_num, max_num + 1))
            missing_numbers = sorted(list(expected_numbers - set(file_numbers)))
            
            # Generate missing filenames
            missing_files = []
            if missing_numbers:
                # Use the pattern to generate missing filenames
                sample_file = file_paths[0].stem
                for missing_num in missing_numbers:
                    # Replace the number in the sample filename
                    missing_filename = re.sub(r'\d+', str(missing_num), sample_file, count=1)
                    missing_files.append(f"{missing_filename}.json")
            
            completeness = ((len(file_numbers) / len(expected_numbers)) * 100) if expected_numbers else 100.0
            
            analysis = DirectoryAnalysis(
                directory_path=str(directory),
                pattern_name=pattern_name,
                total_files_found=len(file_paths),
                missing_files=missing_files,
                missing_file_numbers=missing_numbers,
                has_missing_files=len(missing_numbers) > 0,
                expected_file_range=f"{min_num}-{max_num}",
                file_completeness_percentage=round(completeness, 2),
                analysis_timestamp=datetime.now().isoformat()
            )
            
            analyses.append(analysis)
        
        return analyses

    def load_legislation_from_file(self, file_path: Union[str, Path]) -> Optional[Dict]:
        """Load legislation data from a JSON file with robust error handling."""
        file_path = Path(file_path)
        
        try:
            if not file_path.exists():
                logger.error(f"File not found: {file_path}")
                return None
                
            if not file_path.is_file():
                logger.error(f"Path is not a file: {file_path}")
                return None
                
            if file_path.stat().st_size == 0:
                logger.error(f"File is empty: {file_path}")
                return None
                
            with open(file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
                logger.debug(f"Successfully loaded: {file_path}")
                return data
                
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error in {file_path}: {str(e)}")
        except UnicodeDecodeError as e:
            logger.error(f"Encoding error in {file_path}: {str(e)}")
        except PermissionError as e:
            logger.error(f"Permission denied for {file_path}: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error loading {file_path}: {str(e)}")
            
        return None

    def analyze_multiple_files(self, file_paths: List[Union[str, Path]], 
                             max_workers: int = 4) -> List[LegislationAnalysis]:
        """Analyze multiple legislation files with parallel processing."""
        results = []
        self.start_time = time.time()
        
        logger.info(f"Starting content analysis of {len(file_paths)} files with {max_workers} workers")
        
        def analyze_file(file_path):
            legislation_data = self.load_legislation_from_file(file_path)
            if legislation_data:
                return self.analyze_single_legislation(legislation_data, str(file_path))
            else:
                return LegislationAnalysis(
                    name=f"failed_load_{Path(file_path).stem}",
                    title="Failed to Load File",
                    enactment_year="Unknown",
                    file_path=str(file_path),
                    existing_sections=[],
                    missing_sections=[],
                    repealed_sections=[],
                    has_missing_sections=False,
                    has_repealed_sections=False,
                    total_sections_expected=0,
                    total_sections_found=0,
                    missing_count=0,
                    repealed_count=0,
                    repealed_with_content_count=0,
                    completeness_percentage=0.0,
                    analysis_timestamp=datetime.now().isoformat(),
                    error_message="Failed to load file"
                )
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {executor.submit(analyze_file, path): path for path in file_paths}
            
            for future in as_completed(future_to_path):
                try:
                    result = future.result()
                    results.append(result)
                    
                    if len(results) % 10 == 0 or len(results) == len(file_paths):
                        logger.info(f"Processed {len(results)}/{len(file_paths)} files")
                        
                except Exception as e:
                    path = future_to_path[future]
                    logger.error(f"Error processing {path}: {str(e)}")
                    self.error_count += 1
        
        elapsed_time = time.time() - self.start_time
        logger.info(f"Content analysis completed in {elapsed_time:.2f} seconds")
        
        return results

    def comprehensive_analysis(self, directory: Union[str, Path], 
                             recursive: bool = True,
                             max_workers: int = 4) -> ComprehensiveReport:
        """Perform comprehensive analysis of both missing files and missing sections."""
        directory = Path(directory)
        logger.info(f"Starting comprehensive analysis of: {directory}")
        
        # Phase 1: Analyze directory structure for missing files
        logger.info("Phase 1: Analyzing directory structure for missing files...")
        if recursive:
            all_dirs = [directory] + [d for d in directory.rglob("*") if d.is_dir()]
        else:
            all_dirs = [directory]
        
        directory_analyses = []
        for dir_path in all_dirs:
            dir_analysis = self.analyze_directory_for_missing_files(dir_path)
            directory_analyses.extend(dir_analysis)
        
        # Phase 2: Analyze individual files for missing sections
        logger.info("Phase 2: Analyzing individual files for missing sections...")
        pattern = "**/*.json" if recursive else "*.json"
        json_files = list(directory.glob(pattern))
        
        individual_analyses = self.analyze_multiple_files(json_files, max_workers)
        
        # Generate summary statistics
        total_files = len(json_files)
        successful_content_analyses = len([a for a in individual_analyses if a.error_message is None])
        files_with_missing_sections = len([a for a in individual_analyses if a.has_missing_sections and a.error_message is None])
        files_with_repealed_sections = len([a for a in individual_analyses if a.has_repealed_sections and a.error_message is None])
        total_repealed_sections = sum(a.repealed_count for a in individual_analyses if a.error_message is None)
        total_repealed_with_content = sum(a.repealed_with_content_count for a in individual_analyses if a.error_message is None)
        
        directories_with_missing_files = len([a for a in directory_analyses if a.has_missing_files])
        total_missing_files = sum(len(a.missing_files) for a in directory_analyses)
        
        summary_stats = {
            "total_directories_analyzed": len(all_dirs),
            "total_files_found": total_files,
            "successful_content_analyses": successful_content_analyses,
            "failed_content_analyses": total_files - successful_content_analyses,
            "files_with_missing_sections": files_with_missing_sections,
            "files_with_repealed_sections": files_with_repealed_sections,
            "total_repealed_sections": total_repealed_sections,
            "total_repealed_with_content": total_repealed_with_content,
            "directories_with_missing_files": directories_with_missing_files,
            "total_missing_files": total_missing_files,
            "avg_section_completeness": sum(a.completeness_percentage for a in individual_analyses if a.error_message is None) / successful_content_analyses if successful_content_analyses > 0 else 0
        }
        
        return ComprehensiveReport(
            directory_analyses=directory_analyses,
            individual_analyses=individual_analyses,
            summary_stats=summary_stats,
            analysis_timestamp=datetime.now().isoformat()
        )

    def find_json_files(self, directory: Union[str, Path], recursive: bool = True) -> List[Path]:
        """Find all JSON files in a directory."""
        directory = Path(directory)
        
        if not directory.exists():
            logger.error(f"Directory not found: {directory}")
            return []
            
        if not directory.is_dir():
            logger.error(f"Path is not a directory: {directory}")
            return []
        
        pattern = "**/*.json" if recursive else "*.json"
        json_files = list(directory.glob(pattern))
        
        logger.info(f"Found {len(json_files)} JSON files in {directory}")
        return json_files

class ReportGenerator:
    """Enhanced report generator for comprehensive analysis results."""
    
    @staticmethod
    def generate_console_report(report: ComprehensiveReport, detailed: bool = True):
        """Generate a comprehensive console report."""
        print("=" * 120)
        print("COMPREHENSIVE LEGISLATION ANALYSIS REPORT")
        print("=" * 120)
        
        # Executive Summary
        stats = report.summary_stats
        print(f"\nEXECUTIVE SUMMARY:")
        print(f"{'Directories analyzed:':<40} {stats['total_directories_analyzed']}")
        print(f"{'Total JSON files found:':<40} {stats['total_files_found']}")
        print(f"{'Successful content analyses:':<40} {stats['successful_content_analyses']}")
        print(f"{'Failed content analyses:':<40} {stats['failed_content_analyses']}")
        print(f"{'Files with missing sections:':<40} {stats['files_with_missing_sections']}")
        print(f"{'Files with repealed sections:':<40} {stats['files_with_repealed_sections']}")
        print(f"{'Total repealed sections found:':<40} {stats['total_repealed_sections']}")
        print(f"{'Repealed sections with content:':<40} {stats['total_repealed_with_content']}")
        print(f"{'Directories with missing files:':<40} {stats['directories_with_missing_files']}")
        print(f"{'Total missing files:':<40} {stats['total_missing_files']}")
        print(f"{'Average section completeness:':<40} {stats['avg_section_completeness']:.2f}%")
        
        # Missing Files Analysis
        if report.directory_analyses:
            print(f"\n📁 MISSING FILES ANALYSIS:")
            print("-" * 120)
            missing_file_dirs = [d for d in report.directory_analyses if d.has_missing_files]
            
            if missing_file_dirs:
                for i, dir_analysis in enumerate(missing_file_dirs, 1):
                    print(f"\n{i:2d}. Directory: {dir_analysis.directory_path}")
                    print(f"    Pattern: {dir_analysis.pattern_name}")
                    print(f"    Files found: {dir_analysis.total_files_found}")
                    print(f"    Expected range: {dir_analysis.expected_file_range}")
                    print(f"    Completeness: {dir_analysis.file_completeness_percentage}%")
                    print(f"    ⚠️  MISSING FILES ({len(dir_analysis.missing_files)}): {dir_analysis.missing_files}")
            else:
                print("✅ No missing files found in any directory")
        
        # Repealed Sections Analysis
        if detailed and report.individual_analyses:
            print(f"\n🔄 REPEALED SECTIONS ANALYSIS:")
            print("-" * 120)
            
            files_with_repealed = [a for a in report.individual_analyses if a.has_repealed_sections and a.error_message is None]
            
            if files_with_repealed:
                # Sort by repealed count (descending)
                files_with_repealed.sort(key=lambda x: x.repealed_count, reverse=True)
                
                for i, analysis in enumerate(files_with_repealed[:10], 1):  # Show top 10
                    print(f"\n{i:2d}. {analysis.name} ({analysis.enactment_year})")
                    print(f"    File: {Path(analysis.file_path).name}")
                    print(f"    Total repealed sections: {analysis.repealed_count}")
                    print(f"    Repealed with content: {analysis.repealed_with_content_count}")
                    
                    # Show details of repealed sections
                    for rep_section in analysis.repealed_sections[:5]:  # Show first 5
                        print(f"    - Section {rep_section.number}: Repealed by Ordinance {rep_section.repealing_ordinance or 'N/A'} of {rep_section.repealing_year or 'N/A'}")
                        if rep_section.has_content:
                            print(f"      ✓ Original content available")
                        else:
                            print(f"      ✗ Original content unavailable")
                    
                    if len(analysis.repealed_sections) > 5:
                        print(f"    ... and {len(analysis.repealed_sections) - 5} more repealed sections")
                
                if len(files_with_repealed) > 10:
                    print(f"\n... and {len(files_with_repealed) - 10} more files with repealed sections")
            else:
                print("No repealed sections found in any file")
        
        # Missing Sections Analysis
        if detailed and report.individual_analyses:
            print(f"\n📄 MISSING SECTIONS ANALYSIS:")
            print("-" * 120)
            
            # Show only files with missing sections
            files_with_issues = [a for a in report.individual_analyses if a.has_missing_sections and a.error_message is None]
            
            if files_with_issues:
                # Sort by missing count (descending)
                files_with_issues.sort(key=lambda x: x.missing_count, reverse=True)
                
                for i, analysis in enumerate(files_with_issues[:20], 1):  # Show top 20
                    print(f"\n{i:2d}. {analysis.name} ({analysis.enactment_year})")
                    print(f"    File: {Path(analysis.file_path).name}")
                    print(f"    Sections found: {len(analysis.existing_sections)} (Range: {min(analysis.existing_sections) if analysis.existing_sections else 'N/A'}-{max(analysis.existing_sections) if analysis.existing_sections else 'N/A'})")
                    print(f"    Completeness: {analysis.completeness_percentage}%")
                    print(f"    ⚠️  MISSING SECTIONS ({analysis.missing_count}): {analysis.missing_sections}")
                    if analysis.repealed_count > 0:
                        print(f"    ℹ️  Note: {analysis.repealed_count} sections are repealed (not counted as missing)")
                
                if len(files_with_issues) > 20:
                    print(f"\n... and {len(files_with_issues) - 20} more files with missing sections")
            else:
                print("✅ No missing sections found in any file")
        
        # Error Summary
        error_analyses = [a for a in report.individual_analyses if a.error_message is not None]
        if error_analyses:
            print(f"\n❌ ERRORS ({len(error_analyses)} files):")
            print("-" * 120)
            for analysis in error_analyses[:10]:  # Show first 10 errors
                print(f"   {Path(analysis.file_path).name}: {analysis.error_message}")
            if len(error_analyses) > 10:
                print(f"   ... and {len(error_analyses) - 10} more errors")

    @staticmethod
    def generate_json_report(report: ComprehensiveReport, output_file: Union[str, Path]):
        """Generate a comprehensive JSON report."""
        output_file = Path(output_file)
        
        # Convert dataclasses to dictionaries
        report_data = {
            "analysis_metadata": {
                "report_type": "comprehensive_legislation_analysis",
                "analysis_timestamp": report.analysis_timestamp,
                **report.summary_stats
            },
            "directory_analyses": [asdict(d) for d in report.directory_analyses],
            "individual_analyses": []
        }
        
        # Handle individual analyses with custom serialization for repealed sections
        for analysis in report.individual_analyses:
            analysis_dict = asdict(analysis)
            # Convert repealed sections to dictionaries
            analysis_dict['repealed_sections'] = [
                {
                    'number': r.number,
                    'repealing_ordinance': r.repealing_ordinance,
                    'repealing_year': r.repealing_year,
                    'has_content': r.has_content,
                    'note': r.note
                } for r in analysis.repealed_sections
            ]
            report_data['individual_analyses'].append(analysis_dict)
        
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(report_data, f, indent=2, ensure_ascii=False)
            logger.info(f"Comprehensive JSON report saved to: {output_file}")
        except Exception as e:
            logger.error(f"Failed to save JSON report: {str(e)}")

    @staticmethod
    def generate_csv_report(report: ComprehensiveReport, output_file: Union[str, Path]):
        """Generate CSV reports for missing files, sections, and repealed sections."""
        output_file = Path(output_file)
        base_name = output_file.stem
        
        # Missing Files CSV
        missing_files_csv = output_file.parent / f"{base_name}_missing_files.csv"
        try:
            with open(missing_files_csv, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['directory_path', 'pattern_name', 'total_files_found', 
                            'missing_files', 'missing_file_numbers', 'has_missing_files',
                            'expected_file_range', 'file_completeness_percentage']
                
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for analysis in report.directory_analyses:
                    row = asdict(analysis)
                    row['missing_files'] = ', '.join(row['missing_files'])
                    row['missing_file_numbers'] = ', '.join(map(str, row['missing_file_numbers']))
                    writer.writerow(row)
                    
            logger.info(f"Missing files CSV report saved to: {missing_files_csv}")
        except Exception as e:
            logger.error(f"Failed to save missing files CSV: {str(e)}")
        
        # Missing Sections CSV
        missing_sections_csv = output_file.parent / f"{base_name}_missing_sections.csv"
        try:
            with open(missing_sections_csv, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['name', 'title', 'enactment_year', 'file_path',
                            'total_sections_found', 'total_sections_expected', 'missing_count',
                            'repealed_count', 'repealed_with_content_count',
                            'completeness_percentage', 'has_missing_sections', 'has_repealed_sections',
                            'missing_sections', 'existing_sections', 'analysis_timestamp', 'error_message']
                
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for analysis in report.individual_analyses:
                    row = asdict(analysis)
                    row['missing_sections'] = ', '.join(map(str, row['missing_sections']))
                    row['existing_sections'] = ', '.join(map(str, row['existing_sections']))
                    # Remove the repealed_sections complex object
                    row.pop('repealed_sections', None)
                    writer.writerow(row)
                    
            logger.info(f"Missing sections CSV report saved to: {missing_sections_csv}")
        except Exception as e:
            logger.error(f"Failed to save missing sections CSV: {str(e)}")
        
        # Repealed Sections CSV
        repealed_sections_csv = output_file.parent / f"{base_name}_repealed_sections.csv"
        try:
            with open(repealed_sections_csv, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['legislation_name', 'file_path', 'section_number', 
                            'repealing_ordinance', 'repealing_year', 'has_content', 'note']
                
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for analysis in report.individual_analyses:
                    if analysis.has_repealed_sections and analysis.error_message is None:
                        for rep_section in analysis.repealed_sections:
                            writer.writerow({
                                'legislation_name': analysis.name,
                                'file_path': analysis.file_path,
                                'section_number': rep_section.number,
                                'repealing_ordinance': rep_section.repealing_ordinance or '',
                                'repealing_year': rep_section.repealing_year or '',
                                'has_content': rep_section.has_content,
                                'note': rep_section.note or ''
                            })
                    
            logger.info(f"Repealed sections CSV report saved to: {repealed_sections_csv}")
        except Exception as e:
            logger.error(f"Failed to save repealed sections CSV: {str(e)}")

def main():
    """Main function with enhanced command-line interface."""
    parser = argparse.ArgumentParser(
        description="Comprehensive Legislation Analyzer - Files, Sections & Repealed Sections",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Comprehensive analysis (missing files, sections, and repealed sections)
  python legislation_analyzer.py --directory data/legislations/ --comprehensive
  
  # Analyze specific directory for your file structure
  python legislation_analyzer.py --directory data/legislations/legislation_A/ --comprehensive
  
  # Only check for missing files in directory
  python legislation_analyzer.py --directory data/legislations/ --files-only
  
  # Only check for missing and repealed sections within files
  python legislation_analyzer.py --directory data/legislations/ --sections-only
  
  # Generate comprehensive reports
  python legislation_analyzer.py --directory data/legislations/ --comprehensive \\
      --json-output comprehensive_report.json --csv-output comprehensive_report
        """
    )
    
    # Input options
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--file', '-f', type=str, help='Single JSON file to analyze')
    input_group.add_argument('--directory', '-d', type=str, help='Directory containing JSON files')
    
    # Analysis mode options
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--comprehensive', '-c', action='store_true', 
                           help='Perform comprehensive analysis (files + sections + repealed) - DEFAULT')
    mode_group.add_argument('--files-only', action='store_true', 
                           help='Only analyze directory for missing files')
    mode_group.add_argument('--sections-only', action='store_true', 
                           help='Only analyze individual files for missing and repealed sections')
    
    # Output options
    parser.add_argument('--json-output', '-j', type=str, help='Output JSON report file')
    parser.add_argument('--csv-output', type=str, help='Output CSV report files (base name)')
    parser.add_argument('--no-console', action='store_true', help='Disable console output')
    
    # Processing options
    parser.add_argument('--workers', '-w', type=int, default=4, help='Number of worker threads (default: 4)')
    parser.add_argument('--recursive', '-r', action='store_true', help='Search directory recursively')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    # Configure logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Set default mode to comprehensive if none specified
    if not args.files_only and not args.sections_only:
        args.comprehensive = True
    
    # Initialize analyzer
    analyzer = LegislationAnalyzer()
    
    try:
        if args.file:
            # Single file mode - only sections analysis
            file_path = Path(args.file)
            if not file_path.exists():
                logger.error(f"File not found: {args.file}")
                sys.exit(1)
            
            legislation_data = analyzer.load_legislation_from_file(file_path)
            if not legislation_data:
                logger.error("Failed to load file")
                sys.exit(1)
            
            result = analyzer.analyze_single_legislation(legislation_data, str(file_path))
            
            # Create minimal report for single file
            report = ComprehensiveReport(
                directory_analyses=[],
                individual_analyses=[result],
                summary_stats={
                    "total_files_found": 1, 
                    "files_with_missing_sections": 1 if result.has_missing_sections else 0,
                    "files_with_repealed_sections": 1 if result.has_repealed_sections else 0,
                    "total_repealed_sections": result.repealed_count,
                    "total_repealed_with_content": result.repealed_with_content_count
                },
                analysis_timestamp=datetime.now().isoformat()
            )
            
        else:
            # Directory mode
            directory = Path(args.directory)
            if not directory.exists():
                logger.error(f"Directory not found: {args.directory}")
                sys.exit(1)
            
            if args.comprehensive:
                report = analyzer.comprehensive_analysis(directory, args.recursive, args.workers)
            elif args.files_only:
                dir_analyses = analyzer.analyze_directory_for_missing_files(directory)
                if args.recursive:
                    subdirs = [d for d in directory.rglob("*") if d.is_dir()]
                    for subdir in subdirs:
                        dir_analyses.extend(analyzer.analyze_directory_for_missing_files(subdir))
                
                report = ComprehensiveReport(
                    directory_analyses=dir_analyses,
                    individual_analyses=[],
                    summary_stats={"directories_with_missing_files": len([d for d in dir_analyses if d.has_missing_files])},
                    analysis_timestamp=datetime.now().isoformat()
                )
            else:  # sections_only
                json_files = analyzer.find_json_files(directory, args.recursive)
                individual_analyses = analyzer.analyze_multiple_files(json_files, args.workers)
                
                report = ComprehensiveReport(
                    directory_analyses=[],
                    individual_analyses=individual_analyses,
                    summary_stats={
                        "total_files_found": len(json_files), 
                        "files_with_missing_sections": len([a for a in individual_analyses if a.has_missing_sections]),
                        "files_with_repealed_sections": len([a for a in individual_analyses if a.has_repealed_sections]),
                        "total_repealed_sections": sum(a.repealed_count for a in individual_analyses if a.error_message is None),
                        "total_repealed_with_content": sum(a.repealed_with_content_count for a in individual_analyses if a.error_message is None)
                    },
                    analysis_timestamp=datetime.now().isoformat()
                )
        
        # Generate reports
        reporter = ReportGenerator()
        
        # Console report
        if not args.no_console:
            reporter.generate_console_report(report, detailed=True)
        
        # JSON report
        if args.json_output:
            reporter.generate_json_report(report, args.json_output)
        
        # CSV report
        if args.csv_output:
            reporter.generate_csv_report(report, args.csv_output)
        
        # Summary
        if args.comprehensive:
            missing_files = sum(len(d.missing_files) for d in report.directory_analyses)
            missing_sections = len([a for a in report.individual_analyses if a.has_missing_sections])
            repealed_sections = sum(a.repealed_count for a in report.individual_analyses if a.error_message is None)
            logger.info(f"Analysis complete: {missing_files} missing files, {missing_sections} files with missing sections, {repealed_sections} total repealed sections")
        
        sys.exit(0)
        
    except KeyboardInterrupt:
        logger.info("Analysis interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()