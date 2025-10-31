#!/usr/bin/env python3
"""
HTML Image Analyzer - Find downloadable images in scraped HTML files

This script analyzes HTML files scraped by the MainHTMLScraper to identify
which files contain downloadable images within the <body> tag and their status.
Images in the <head> section (like favicons) are ignored.

Requirements:
    pip install requests beautifulsoup4

Usage:
    # Interactive mode (recommended for beginners)
    python html_image_analyzer.py
    
    # Command line mode
    python html_image_analyzer.py --mode detailed --folder your_folder_name
    python html_image_analyzer.py --mode quick --directory data/html
    python html_image_analyzer.py --mode urls --output report.txt --urls-output images.json
    python html_image_analyzer.py --mode names  # Just get HTML file names (fastest)
    
    # Help
    python html_image_analyzer.py --help
"""

import os
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pathlib import Path
import mimetypes
from collections import defaultdict

class HTMLImageAnalyzer:
    def __init__(self, base_directory="data/html", timeout=10, skip_images=None):
        """Initialize the HTML Image Analyzer.
        
        This analyzer only checks for images within the <body> tag of HTML files.
        Images in the <head> section (like favicons) are ignored.
        
        Args:
            base_directory: Base directory containing scraped HTML files
            timeout: Timeout for checking remote image availability
            skip_images: List of image filenames to skip (not consider as downloadable)
        """
        self.base_directory = base_directory
        self.timeout = timeout
        self.session = requests.Session()
        self.results = {}
        
        # Skip list functionality (same as MainHTMLScraper)
        self.skip_images = skip_images or []
    
    def add_skip_images(self, image_names):
        """Add image names to the skip list.
        
        Args:
            image_names: List of image filenames to skip, or single filename string
        """
        if isinstance(image_names, str):
            image_names = [image_names]
        
        self.skip_images.extend(image_names)
        print(f"Added {len(image_names)} images to skip list. Total skip list: {len(self.skip_images)}")
    
    def remove_skip_images(self, image_names):
        """Remove image names from the skip list.
        
        Args:
            image_names: List of image filenames to remove from skip list, or single filename string
        """
        if isinstance(image_names, str):
            image_names = [image_names]
        
        for name in image_names:
            if name in self.skip_images:
                self.skip_images.remove(name)
        
        print(f"Skip list updated. Current skip list: {len(self.skip_images)} images")
    
    def clear_skip_images(self):
        """Clear all images from the skip list."""
        self.skip_images.clear()
        print("Skip list cleared.")
    
    def show_skip_list(self):
        """Display current skip list."""
        if self.skip_images:
            print(f"Current skip list ({len(self.skip_images)} images):")
            for i, img_name in enumerate(self.skip_images, 1):
                print(f"  {i}. {img_name}")
        else:
            print("Skip list is empty.")
    
    def load_skip_list_from_file(self, file_path):
        """Load skip list from a text file (one image name per line)."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                skip_images = [line.strip() for line in f.readlines() if line.strip()]
            
            # Remove duplicates and filter empty lines
            skip_images = list(set([img for img in skip_images if img]))
            
            self.skip_images.extend(skip_images)
            print(f"✅ Loaded {len(skip_images)} images to skip list from {file_path}")
            print(f"📊 Total skip list: {len(self.skip_images)} images")
            
            # Show first few items for verification
            if len(skip_images) > 0:
                print(f"🔍 First few items loaded: {skip_images[:5]}")
            
        except Exception as e:
            print(f"❌ Failed to load skip list from {file_path}: {str(e)}")
    
    def debug_skip_list_matching(self, filename):
        """Debug helper to check skip list matching."""
        if not self.skip_images:
            return f"❌ Skip list is empty"
        
        # Check exact match
        if filename in self.skip_images:
            return f"✅ EXACT match found: '{filename}'"
        
        # Check case-insensitive match
        filename_lower = filename.lower()
        for skip_img in self.skip_images:
            if skip_img.lower() == filename_lower:
                return f"⚠️  CASE mismatch: '{filename}' vs '{skip_img}'"
        
        # Check for partial matches
        partial_matches = [skip_img for skip_img in self.skip_images if skip_img in filename or filename in skip_img]
        if partial_matches:
            return f"🔍 Partial matches: {partial_matches}"
        
    def verify_skip_list(self):
        """Verify skip list is loaded and working correctly."""
        print("🔍 SKIP LIST VERIFICATION")
        print("=" * 30)
        print(f"📊 Skip list size: {len(self.skip_images)}")
        
        if not self.skip_images:
            print("❌ Skip list is EMPTY!")
            print("💡 To add items to skip list:")
            print("   - Use --skip-list filename.txt")
            print("   - Use --skip-images item1.gif item2.png")
            print("   - Use interactive mode and manage skip list")
            return False
        
        print("📋 Skip list contents:")
        for i, img_name in enumerate(self.skip_images, 1):
            print(f"   {i:2d}. '{img_name}'")
        
        return True
    
    def save_skip_list_to_file(self, file_path):
        """Save current skip list to a text file."""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                for img_name in self.skip_images:
                    f.write(f"{img_name}\n")
            print(f"Skip list saved to {file_path} ({len(self.skip_images)} images)")
        except Exception as e:
            print(f"Failed to save skip list to {file_path}: {str(e)}")
    
    def _get_image_filename(self, img_url, base_url):
        """Extract filename from image URL (same logic as MainHTMLScraper)."""
        # Convert relative URLs to absolute URLs first
        if not img_url.startswith(('http://', 'https://')):
            img_url = urljoin(base_url, img_url)
        
        # Parse the URL to get filename
        parsed_url = urlparse(img_url)
        filename = os.path.basename(parsed_url.path)
        
        # If no filename, generate one based on URL
        if not filename or '.' not in filename:
            filename = f"image_{hash(img_url) % 10000}.jpg"
        
        return filename
        
    def find_html_files(self, folder_name=None):
        """Find all HTML files in the directory structure.
        
        Args:
            folder_name: Specific folder to analyze, or None for all folders
            
        Returns:
            Dictionary with folder_name -> list of HTML file paths
        """
        html_files = defaultdict(list)
        
        if not os.path.exists(self.base_directory):
            print(f"Base directory not found: {self.base_directory}")
            return html_files
        
        if folder_name:
            # Analyze specific folder
            folder_path = os.path.join(self.base_directory, folder_name)
            if os.path.exists(folder_path):
                html_files[folder_name] = self._scan_folder_for_html(folder_path)
        else:
            # Analyze all folders
            for item in os.listdir(self.base_directory):
                item_path = os.path.join(self.base_directory, item)
                if os.path.isdir(item_path):
                    html_files[item] = self._scan_folder_for_html(item_path)
        
        return dict(html_files)
    
    def _scan_folder_for_html(self, folder_path):
        """Scan a folder for HTML files recursively."""
        html_files = []
        
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file.endswith('.html'):
                    html_files.append(os.path.join(root, file))
        
        return html_files
    
    def analyze_html_file(self, html_file_path, base_url=None):
        """Analyze a single HTML file for images within the <body> tag only.
        
        Images in the <head> section (like favicons) are ignored.
        
        Args:
            html_file_path: Path to the HTML file
            base_url: Base URL for resolving relative image URLs (optional)
            
        Returns:
            Dictionary with image analysis results
        """
        try:
            with open(html_file_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        except Exception as e:
            return {
                'error': f"Failed to read HTML file: {str(e)}",
                'images': []
            }
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Get the directory of the HTML file for checking local images
        html_dir = os.path.dirname(html_file_path)
        
        # Try to extract base URL from HTML if not provided
        if not base_url:
            # Look for base tag or try to infer from file structure
            base_tag = soup.find('base')
            if base_tag and base_tag.get('href'):
                base_url = base_tag.get('href')
            else:
                # Default fallback - you might want to set this based on your scraping pattern
                base_url = "https://example.com"  # This should be set properly
        
        # Find the body tag first - only check images inside body
        body_tag = soup.find('body')
        if not body_tag:
            print(f"  ⚠️  No <body> tag found in {os.path.basename(html_file_path)} - skipping image analysis")
            return {
                'error': f"No <body> tag found in HTML file",
                'images': []
            }
        
        # Find all image tags within the body only
        img_tags = body_tag.find_all('img')
        
        image_analysis = {
            'total_images': len(img_tags),
            'local_images': 0,
            'remote_images': 0,
            'accessible_remote': 0,
            'inaccessible_remote': 0,
            'missing_local': 0,
            'skipped_images': 0,  # New: count of images in skip list
            'downloadable_images': 0,  # New: count of actually downloadable images
            'images': []
        }
        
        print(f"  🔍 Analyzing {len(img_tags)} images found in body tag")
        if self.skip_images:
            print(f"  🚫 Skip list active: {len(self.skip_images)} images will be excluded")
            print(f"  📋 Skip list contents: {self.skip_images[:10]}{'...' if len(self.skip_images) > 10 else ''}")
        else:
            print(f"  ⚠️  WARNING: Skip list is EMPTY - no images will be skipped")
        
        for img_tag in img_tags:
            src = img_tag.get('src')
            if not src:
                continue
                
            original_src = img_tag.get('data-original-src', src)
            
            # Get the filename that would be used for this image
            filename = self._get_image_filename(src, base_url)
            
            # DEBUG: Print filename extraction and skip check
            if self.skip_images:  # Only debug if skip list exists
                debug_result = self.debug_skip_list_matching(filename)
                print(f"    🔍 {filename} -> {debug_result}")
            
            image_info = {
                'src': src,
                'original_src': original_src,
                'filename': filename,
                'alt': img_tag.get('alt', ''),
                'status': 'unknown',
                'local_path': None,
                'remote_accessible': None,
                'in_skip_list': filename in self.skip_images,
                'downloadable': False  # Will be set based on analysis
            }
            
            # Check if image is in skip list
            if image_info['in_skip_list']:
                image_info['status'] = 'skipped'
                image_analysis['skipped_images'] += 1
                image_info['downloadable'] = False
            else:
                # Check if it's a local image (relative path)
                if not src.startswith(('http://', 'https://')):
                    # Local image path
                    local_image_path = os.path.join(html_dir, src)
                    image_info['local_path'] = local_image_path
                    
                    if os.path.exists(local_image_path):
                        image_info['status'] = 'local_exists'
                        image_analysis['local_images'] += 1
                        image_info['downloadable'] = False  # Already downloaded
                    else:
                        image_info['status'] = 'local_missing'
                        image_analysis['missing_local'] += 1
                        image_info['downloadable'] = True  # Could be re-downloaded
                        image_analysis['downloadable_images'] += 1
                else:
                    # Remote image
                    image_info['status'] = 'remote'
                    image_analysis['remote_images'] += 1
                    
                    # Check if remote image is accessible
                    if self._check_remote_image_accessible(src):
                        image_info['remote_accessible'] = True
                        image_analysis['accessible_remote'] += 1
                        image_info['downloadable'] = True  # Can be downloaded
                        image_analysis['downloadable_images'] += 1
                    else:
                        image_info['remote_accessible'] = False
                        image_analysis['inaccessible_remote'] += 1
                        image_info['downloadable'] = False  # Cannot be downloaded
            
            image_analysis['images'].append(image_info)
        
        return image_analysis
    
    def _check_remote_image_accessible(self, url):
        """Check if a remote image URL is accessible."""
        try:
            response = self.session.head(url, timeout=self.timeout)
            return response.status_code == 200
        except:
            try:
                # Fallback to GET request with limited data
                response = self.session.get(url, timeout=self.timeout, stream=True)
                # Read just a small chunk to verify it's accessible
                next(response.iter_content(1024))
                return response.status_code == 200
            except:
                return False
    
    def analyze_all(self, folder_name=None, check_remote=True):
        """Analyze all HTML files for images.
        
        Args:
            folder_name: Specific folder to analyze, or None for all
            check_remote: Whether to check if remote images are accessible
            
        Returns:
            Comprehensive analysis results
        """
        if not check_remote:
            # Disable remote checking for faster analysis
            self._check_remote_image_accessible = lambda url: None
        
        # Verify skip list first
        if self.skip_images:
            print("🔍 Verifying skip list...")
            self.verify_skip_list()
            print()
        
        html_files = self.find_html_files(folder_name)
        
        if not html_files:
            print("No HTML files found to analyze")
            return {}
        
        print(f"Found HTML files in {len(html_files)} folders")
        
        analysis_results = {}
        total_files = sum(len(files) for files in html_files.values())
        processed = 0
        
        for folder, files in html_files.items():
            print(f"\nAnalyzing folder: {folder} ({len(files)} HTML files)")
            analysis_results[folder] = {}
            
            for html_file in files:
                processed += 1
                file_name = os.path.basename(html_file).replace('.html', '')
                
                print(f"  [{processed}/{total_files}] Analyzing: {file_name}")
                
                analysis = self.analyze_html_file(html_file)
                analysis_results[folder][file_name] = {
                    'file_path': html_file,
                    'analysis': analysis
                }
        
        self.results = analysis_results
        return analysis_results
    
    def get_files_with_downloadable_images(self, results=None):
        """Get list of HTML files that have downloadable images (excluding skip list).
        
        Args:
            results: Analysis results, or None to use last analysis
            
        Returns:
            Dictionary of files with downloadable images
        """
        if results is None:
            results = self.results
        
        files_with_images = {}
        
        for folder, files in results.items():
            files_with_images[folder] = {}
            
            for file_name, data in files.items():
                analysis = data['analysis']
                
                # Skip files with errors
                if 'error' in analysis:
                    continue
                
                # Check if file has downloadable images (NEW LOGIC: respect skip list)
                has_downloadable = analysis.get('downloadable_images', 0) > 0
                
                if has_downloadable or analysis['total_images'] > 0:
                    files_with_images[folder][file_name] = {
                        'file_path': data['file_path'],
                        'total_images': analysis['total_images'],
                        'downloadable_images': analysis.get('downloadable_images', 0),
                        'skipped_images': analysis.get('skipped_images', 0),
                        'remote_images': analysis['remote_images'],
                        'accessible_remote': analysis['accessible_remote'],
                        'missing_local': analysis['missing_local'],
                        'local_images': analysis['local_images'],
                        'has_downloadable': has_downloadable
                    }
        
        return files_with_images
    
    def generate_report(self, results=None, save_to_file=None):
        """Generate a comprehensive report of the analysis.
        
        Args:
            results: Analysis results, or None to use last analysis
            save_to_file: Optional file path to save the report
        """
        if results is None:
            results = self.results
        
        if not results:
            print("No analysis results available. Run analyze_all() first.")
            return
        
        report_lines = ["HTML IMAGE ANALYSIS REPORT (Body Images Only)", "=" * 60, ""]
        
        # Add skip list information
        if self.skip_images:
            report_lines.extend([
                f"🚫 SKIP LIST: {len(self.skip_images)} images will be ignored",
                f"   (Images in skip list are not considered downloadable)",
                ""
            ])
        else:
            report_lines.extend([
                "✅ No skip list active - all body images will be considered",
                ""
            ])
        
        report_lines.extend([
            "ℹ️  NOTE: Only images within <body> tags are analyzed.",
            "   Images in <head> sections (favicons, etc.) are ignored.",
            ""
        ])
        
        total_files = 0
        total_images = 0
        total_downloadable = 0
        total_skipped = 0
        files_with_images = 0
        files_with_downloadable = 0
        
        for folder, files in results.items():
            folder_files = len(files)
            folder_images = 0
            folder_downloadable = 0
            folder_skipped = 0
            folder_with_images = 0
            folder_downloadable_files = 0
            
            report_lines.append(f"FOLDER: {folder}")
            report_lines.append("-" * (len(folder) + 8))
            
            for file_name, data in files.items():
                analysis = data['analysis']
                
                if 'error' in analysis:
                    report_lines.append(f"  ❌ {file_name}: {analysis['error']}")
                    continue
                
                file_images = analysis['total_images']
                file_downloadable = analysis.get('downloadable_images', 0)
                file_skipped = analysis.get('skipped_images', 0)
                
                folder_images += file_images
                folder_downloadable += file_downloadable
                folder_skipped += file_skipped
                
                if file_images > 0:
                    folder_with_images += 1
                    
                    if file_downloadable > 0:
                        folder_downloadable_files += 1
                        status_icon = "📥"  # Has downloadable images
                        skip_info = f", Skipped: {file_skipped}" if file_skipped > 0 else ""
                        report_lines.append(
                            f"  {status_icon} {file_name}: "
                            f"{file_images} images "
                            f"(Downloadable: {file_downloadable}, "
                            f"Local: {analysis['local_images']}{skip_info})"
                        )
                    else:
                        status_icon = "✅" if file_skipped == 0 else "🚫"  # All local or all skipped
                        skip_info = f" (all {file_skipped} skipped)" if file_skipped > 0 else ""
                        report_lines.append(f"  {status_icon} {file_name}: {file_images} images - no downloads needed{skip_info}")
                else:
                    report_lines.append(f"  ⚪ {file_name}: No images")
            
            total_files += folder_files
            total_images += folder_images
            total_downloadable += folder_downloadable
            total_skipped += folder_skipped
            files_with_images += folder_with_images
            files_with_downloadable += folder_downloadable_files
            
            skip_summary = f", {folder_skipped} skipped" if folder_skipped > 0 else ""
            report_lines.extend([
                f"  Folder Summary: {folder_files} files, "
                f"{folder_images} images, "
                f"{folder_downloadable} downloadable{skip_summary}, "
                f"{folder_downloadable_files} files need downloads",
                ""
            ])
        
        # Overall summary
        report_lines.extend([
            "OVERALL SUMMARY",
            "=" * 15,
            f"Total HTML files analyzed: {total_files}",
            f"Total body images found: {total_images}",
            f"Images that can be downloaded: {total_downloadable}",
            f"Images in skip list: {total_skipped}",
            f"Files containing body images: {files_with_images}",
            f"Files needing image downloads: {files_with_downloadable}",
            ""
        ])
        
        if self.skip_images:
            report_lines.extend([
                "SKIP LIST DETAILS",
                "=" * 17,
                f"The following {len(self.skip_images)} images are being ignored:",
            ])
            for i, img_name in enumerate(self.skip_images[:20], 1):  # Show first 20
                report_lines.append(f"  {i}. {img_name}")
            if len(self.skip_images) > 20:
                report_lines.append(f"  ... and {len(self.skip_images) - 20} more")
            report_lines.append("")
        
        report_text = "\n".join(report_lines)
        print(report_text)
        
        if save_to_file:
            try:
                with open(save_to_file, 'w', encoding='utf-8') as f:
                    f.write(report_text)
                print(f"Report saved to: {save_to_file}")
            except Exception as e:
                print(f"Failed to save report: {str(e)}")
        
        return report_text
    
    def get_image_urls_for_download(self, results=None, folder_name=None, file_name=None):
        """Extract image URLs that can be downloaded (excluding skip list).
        
        Args:
            results: Analysis results, or None to use last analysis
            folder_name: Specific folder to get URLs from
            file_name: Specific file to get URLs from
            
        Returns:
            Dictionary of downloadable image URLs
        """
        if results is None:
            results = self.results
        
        downloadable_urls = {}
        
        for folder, files in results.items():
            if folder_name and folder != folder_name:
                continue
                
            downloadable_urls[folder] = {}
            
            for fname, data in files.items():
                if file_name and fname != file_name:
                    continue
                
                analysis = data['analysis']
                
                if 'error' in analysis:
                    continue
                
                file_urls = []
                
                for img_info in analysis['images']:
                    # Only include images that are downloadable (not in skip list)
                    if img_info.get('downloadable', False) and not img_info.get('in_skip_list', False):
                        
                        # Include accessible remote images
                        if (img_info['status'] == 'remote' and 
                            img_info['remote_accessible'] is True):
                            file_urls.append({
                                'url': img_info['src'],
                                'filename': img_info['filename'],
                                'alt': img_info['alt'],
                                'type': 'remote_accessible'
                            })
                        
                        # Include remote images that haven't been checked
                        elif (img_info['status'] == 'remote' and 
                              img_info['remote_accessible'] is None):
                            file_urls.append({
                                'url': img_info['src'],
                                'filename': img_info['filename'],
                                'alt': img_info['alt'],
                                'type': 'remote_unchecked'
                            })
                        
                        # Include missing local images (could be re-downloaded from original source)
                        elif img_info['status'] == 'local_missing' and img_info.get('original_src'):
                            file_urls.append({
                                'url': img_info['original_src'],
                                'filename': img_info['filename'],
                                'alt': img_info['alt'],
                                'type': 'missing_local'
                            })
                
                if file_urls:
                    downloadable_urls[folder][fname] = {
                        'file_path': data['file_path'],
                        'total_downloadable': len(file_urls),
                        'urls': file_urls
                    }
        
        return downloadable_urls
    
    def get_html_files_with_downloadable_images(self, results=None):
        """Get simple list of HTML file names that have downloadable images.
        
        Args:
            results: Analysis results, or None to use last analysis
            
        Returns:
            Dictionary with folder -> list of HTML file names with downloadable images
        """
        if results is None:
            results = self.results
        
        downloadable_files = {}
        
        for folder, files in results.items():
            file_names = []
            
            for file_name, data in files.items():
                analysis = data['analysis']
                
                # Skip files with errors
                if 'error' in analysis:
                    continue
                
                # Check if file has downloadable images (excluding skip list)
                if analysis.get('downloadable_images', 0) > 0:
                    file_names.append(file_name)
            
            if file_names:
                downloadable_files[folder] = sorted(file_names)
        
        return downloadable_files
    
    def print_downloadable_file_names(self, results=None):
        """Print detailed information about HTML files that have downloadable images."""
        if results is None:
            results = self.results
        
        if not results:
            print("📭 No analysis results available.")
            return {}
        
        # Filter files with downloadable images and get detailed info
        files_with_details = {}
        
        for folder, files in results.items():
            folder_details = {}
            
            for file_name, data in files.items():
                analysis = data['analysis']
                
                # Skip files with errors
                if 'error' in analysis:
                    continue
                
                # Check if file has downloadable images (excluding skip list)
                if analysis.get('downloadable_images', 0) > 0:
                    # Get detailed image information
                    downloadable_images = []
                    skipped_images = []
                    
                    for img_info in analysis['images']:
                        if img_info.get('in_skip_list', False):
                            skipped_images.append(img_info['filename'])
                        elif img_info.get('downloadable', False):
                            downloadable_images.append({
                                'filename': img_info['filename'],
                                'status': img_info['status'],
                                'src': img_info['src']
                            })
                    
                    if downloadable_images:  # Only include if there are downloadable images
                        folder_details[file_name] = {
                            'downloadable_images': downloadable_images,
                            'skipped_images': skipped_images,
                            'total_images': analysis['total_images']
                        }
            
            if folder_details:
                files_with_details[folder] = folder_details
        
        if not files_with_details:
            print("📭 No HTML files found with downloadable images.")
            if self.skip_images:
                print(f"   (Note: {len(self.skip_images)} images are being skipped)")
            return {}
        
        print("📥 HTML FILES WITH DOWNLOADABLE IMAGES (DETAILED)")
        print("=" * 55)
        
        if self.skip_images:
            print(f"🚫 Skip list active: {len(self.skip_images)} images excluded")
            print()
        
        total_files = 0
        total_downloadable = 0
        total_skipped = 0
        
        for folder, folder_files in files_with_details.items():
            total_files += len(folder_files)
            print(f"📁 {folder} ({len(folder_files)} files):")
            print()
            
            for i, (file_name, details) in enumerate(folder_files.items(), 1):
                downloadable_count = len(details['downloadable_images'])
                skipped_count = len(details['skipped_images'])
                total_images = details['total_images']
                
                total_downloadable += downloadable_count
                total_skipped += skipped_count
                
                print(f"   {i:2d}. 📄 {file_name}")
                print(f"       📊 Total body images: {total_images} | Downloadable: {downloadable_count} | Skipped: {skipped_count}")
                
                if details['downloadable_images']:
                    print(f"       📥 DOWNLOADABLE IMAGES:")
                    for j, img in enumerate(details['downloadable_images'], 1):
                        status_icon = "🌐" if img['status'] == 'remote' else "📁"
                        print(f"           {j}. {status_icon} {img['filename']}")
                        if len(img['src']) > 80:
                            print(f"              📎 {img['src'][:77]}...")
                        else:
                            print(f"              📎 {img['src']}")
                
                if details['skipped_images']:
                    print(f"       🚫 SKIPPED IMAGES:")
                    for j, img_name in enumerate(details['skipped_images'], 1):
                        print(f"           {j}. ⏭️  {img_name}")
                
                print()  # Empty line between files
            
            print("-" * 55)
            print()
        
        print(f"📊 SUMMARY:")
        print(f"   📄 Total HTML files with downloadable images: {total_files}")
        print(f"   📥 Total downloadable images: {total_downloadable}")
        print(f"   🚫 Total skipped images: {total_skipped}")
        
        # Return simplified structure for compatibility
        simple_structure = {}
        for folder, folder_files in files_with_details.items():
            simple_structure[folder] = list(folder_files.keys())
        
        return simple_structure
    
    def print_simple_file_names(self, results=None):
        """Print just the names of HTML files that have downloadable images (simple version)."""
        downloadable_files = self.get_html_files_with_downloadable_images(results)
        
        if not downloadable_files:
            print("📭 No HTML files found with downloadable images.")
            if self.skip_images:
                print(f"   (Note: {len(self.skip_images)} images are being skipped)")
            return downloadable_files
        
        print("📥 HTML FILES WITH DOWNLOADABLE IMAGES")
        print("=" * 45)
        
        if self.skip_images:
            print(f"🚫 Skip list active: {len(self.skip_images)} images excluded")
            print()
        
        total_files = 0
        for folder, file_names in downloadable_files.items():
            total_files += len(file_names)
            print(f"📁 {folder} ({len(file_names)} files):")
            for i, file_name in enumerate(file_names, 1):
                print(f"   {i:2d}. {file_name}")
            print()
        
        print(f"📊 Total: {total_files} HTML files have downloadable images")
        return downloadable_files

# Utility functions
def quick_analysis(folder_name=None, base_directory="data/html", skip_images=None, skip_list_file=None):
    """Quick analysis of HTML files for images."""
    analyzer = HTMLImageAnalyzer(base_directory, skip_images=skip_images)
    
    # Load skip list from file if provided
    if skip_list_file and os.path.exists(skip_list_file):
        analyzer.load_skip_list_from_file(skip_list_file)
    
    results = analyzer.analyze_all(folder_name, check_remote=False)  # Fast analysis
    analyzer.generate_report(results)
    return analyzer.get_files_with_downloadable_images(results)

def detailed_analysis(folder_name=None, base_directory="data/html", skip_images=None, skip_list_file=None):
    """Detailed analysis including remote image accessibility check."""
    analyzer = HTMLImageAnalyzer(base_directory, skip_images=skip_images)
    
    # Load skip list from file if provided
    if skip_list_file and os.path.exists(skip_list_file):
        analyzer.load_skip_list_from_file(skip_list_file)
    
    results = analyzer.analyze_all(folder_name, check_remote=True)  # Full analysis
    analyzer.generate_report(results)
    return analyzer.get_files_with_downloadable_images(results)

def get_downloadable_file_names(folder_name=None, base_directory="data/html", 
                               skip_images=None, skip_list_file=None, print_results=True):
    """Get names of HTML files that have downloadable images.
    
    Args:
        folder_name: Specific folder to analyze, or None for all folders
        base_directory: Base directory containing HTML files
        skip_images: List of image filenames to skip
        skip_list_file: Path to skip list file
        print_results: Whether to print the results
        
    Returns:
        Dictionary with folder -> list of HTML file names with downloadable images
    """
    analyzer = HTMLImageAnalyzer(base_directory, skip_images=skip_images)
    
    # Load skip list from file if provided
    if skip_list_file and os.path.exists(skip_list_file):
        analyzer.load_skip_list_from_file(skip_list_file)
    
    print("🔍 Analyzing HTML files for downloadable images...")
    if analyzer.skip_images:
        print(f"🚫 Skip list active: {len(analyzer.skip_images)} images will be excluded")
    
    # Quick analysis (no remote checking for speed)
    results = analyzer.analyze_all(folder_name, check_remote=False)
    
    if print_results:
        return analyzer.print_downloadable_file_names(results)
    else:
        return analyzer.get_html_files_with_downloadable_images(results)

def save_downloadable_file_names(folder_name=None, output_file="downloadable_html_files.txt", 
                                base_directory="data/html", skip_images=None, skip_list_file=None):
    """Save names of HTML files with downloadable images to a text file."""
    downloadable_files = get_downloadable_file_names(
        folder_name, base_directory, skip_images, skip_list_file, print_results=False
    )
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("HTML FILES WITH DOWNLOADABLE IMAGES\n")
            f.write("=" * 45 + "\n\n")
            
            total_files = 0
            for folder, file_names in downloadable_files.items():
                total_files += len(file_names)
                f.write(f"📁 {folder} ({len(file_names)} files):\n")
                for i, file_name in enumerate(file_names, 1):
                    f.write(f"   {i:2d}. {file_name}\n")
                f.write("\n")
            
            f.write(f"📊 Total: {total_files} HTML files have downloadable images\n")
        
        print(f"✅ File names saved to: {output_file}")
        print(f"📊 Total files with downloadable images: {sum(len(files) for files in downloadable_files.values())}")
        
    except Exception as e:
        print(f"❌ Failed to save file names: {str(e)}")
    
    return downloadable_files

def test_skip_list(skip_images=None, skip_list_file=None, base_directory="data/html"):
    """Test skip list functionality with sample data."""
    print("🧪 TESTING SKIP LIST FUNCTIONALITY")
    print("=" * 40)
    
    # Create test analyzer
    analyzer = HTMLImageAnalyzer(base_directory, skip_images=skip_images)
    
    # Load skip list from file if provided
    if skip_list_file and os.path.exists(skip_list_file):
        analyzer.load_skip_list_from_file(skip_list_file)
    
    # Verify skip list
    if not analyzer.verify_skip_list():
        return
    
    # Test some sample filenames
    test_filenames = [
        'logo.gif', 'constitution_2022.png', 'back_new1.gif', 'print.png',
        'bullet1.gif', 'test_image.jpg', 'random_file.png'
    ]
    
    print(f"\n🔍 Testing filename matching:")
    for filename in test_filenames:
        result = analyzer.debug_skip_list_matching(filename)
        print(f"   {filename} -> {result}")
    
    print(f"\n✅ Skip list test complete!")
    print(f"💡 If images aren't being skipped, check:")
    print(f"   1. Skip list file exists and is readable")
    print(f"   2. Filenames match exactly (including case)")
    print(f"   3. No extra whitespace in skip list file")
    
    return analyzer


def main():
    """Main function to run the HTML Image Analyzer."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Analyze HTML files for downloadable images (respects skip list)')
    parser.add_argument('--directory', '-d', default='data/html', 
                       help='Base directory containing HTML files (default: data/html)')
    parser.add_argument('--folder', '-f', default=None,
                       help='Specific folder to analyze (default: analyze all folders)')
    parser.add_argument('--mode', '-m', choices=['quick', 'detailed', 'urls', 'names', 'names-detailed'], default='detailed',
                       help='Analysis mode: quick (fast, no remote check), detailed (full analysis), urls (save URLs to file), names (just show HTML file names), names-detailed (show file names with image details)')
    parser.add_argument('--output', '-o', default='image_analysis_report.txt',
                       help='Output file for report (default: image_analysis_report.txt)')
    parser.add_argument('--urls-output', '-u', default='downloadable_images.json',
                       help='Output file for downloadable URLs JSON (default: downloadable_images.json)')
    parser.add_argument('--names-output', '-n', default='downloadable_html_files.txt',
                       help='Output file for HTML file names (default: downloadable_html_files.txt)')
    parser.add_argument('--no-remote-check', action='store_true',
                       help='Skip checking if remote images are accessible (faster)')
    parser.add_argument('--skip-list', '-s', default=None,
                       help='Path to skip list file (one image filename per line)')
    parser.add_argument('--skip-images', nargs='*',
                       help='Individual image filenames to skip (space separated)')
    
    args = parser.parse_args()
    
    print("🔍 HTML Image Analyzer")
    print("=" * 50)
    print(f"Directory: {args.directory}")
    print(f"Folder: {args.folder or 'All folders'}")
    print(f"Mode: {args.mode}")
    
    # Check if directory exists
    if not os.path.exists(args.directory):
        print(f"❌ Directory not found: {args.directory}")
        print("Please make sure you've run the HTML scraper first and the directory exists.")
        return
    
    # Initialize analyzer
    skip_images = args.skip_images or []
    analyzer = HTMLImageAnalyzer(args.directory, skip_images=skip_images)
    
    # Load skip list from file if provided
    if args.skip_list and os.path.exists(args.skip_list):
        analyzer.load_skip_list_from_file(args.skip_list)
    
    # Show skip list status
    if analyzer.skip_images:
        print(f"🚫 Skip list active: {len(analyzer.skip_images)} images will be ignored")
    else:
        print("✅ No skip list - all images will be considered downloadable")
    print()
    
    try:
        if args.mode == 'quick':
            print("🚀 Running quick analysis (no remote image checking)...")
            results = analyzer.analyze_all(args.folder, check_remote=False)
            files_with_images = analyzer.get_files_with_downloadable_images(results)
            analyzer.generate_report(results, save_to_file=args.output)
            
            print(f"\n📊 Quick Summary:")
            total_downloadable = sum(len([f for f in files.values() if f['has_downloadable']]) 
                                   for files in files_with_images.values())
            print(f"Files needing downloads: {total_downloadable}")
            
        elif args.mode == 'detailed':
            check_remote = not args.no_remote_check
            print(f"🔍 Running detailed analysis (remote check: {'enabled' if check_remote else 'disabled'})...")
            results = analyzer.analyze_all(args.folder, check_remote=check_remote)
            files_with_images = analyzer.get_files_with_downloadable_images(results)
            analyzer.generate_report(results, save_to_file=args.output)
            
            # Show summary of files with downloadable images
            print(f"\n📥 Files with downloadable images:")
            for folder, files in files_with_images.items():
                downloadable_count = sum(1 for f in files.values() if f['has_downloadable'])
                if downloadable_count > 0:
                    total_downloadable = sum(f['downloadable_images'] for f in files.values())
                    print(f"  📁 {folder}: {downloadable_count} files with {total_downloadable} downloadable images")
            
        elif args.mode == 'urls':
            print("🔗 Extracting downloadable image URLs...")
            results = analyzer.analyze_all(args.folder, check_remote=True)
            urls = analyzer.get_image_urls_for_download(results)
            
            # Save URLs to JSON
            try:
                with open(args.urls_output, 'w', encoding='utf-8') as f:
                    json.dump(urls, f, indent=2, ensure_ascii=False)
                print(f"✅ Downloadable URLs saved to: {args.urls_output}")
            except Exception as e:
                print(f"❌ Failed to save URLs: {str(e)}")
            
            # Also generate report
            analyzer.generate_report(results, save_to_file=args.output)
            
            # Show URL summary
            total_urls = 0
            for folder_urls in urls.values():
                for file_urls in folder_urls.values():
                    total_urls += file_urls['total_downloadable']
            print(f"📊 Total downloadable image URLs found: {total_urls}")
            print(f"    (Images in skip list are excluded)")
            
        elif args.mode == 'names':
            print("📝 Finding HTML files with downloadable images (simple view)...")
            results = analyzer.analyze_all(args.folder, check_remote=False)  # Quick analysis
            downloadable_files = analyzer.print_simple_file_names(results)
            
            # Save to file
            if downloadable_files:
                try:
                    with open(args.names_output, 'w', encoding='utf-8') as f:
                        f.write("HTML FILES WITH DOWNLOADABLE IMAGES\n")
                        f.write("=" * 45 + "\n\n")
                        
                        if analyzer.skip_images:
                            f.write(f"🚫 Skip list active: {len(analyzer.skip_images)} images excluded\n\n")
                        
                        total_files = 0
                        for folder, file_names in downloadable_files.items():
                            total_files += len(file_names)
                            f.write(f"📁 {folder} ({len(file_names)} files):\n")
                            for i, file_name in enumerate(file_names, 1):
                                f.write(f"   {i:2d}. {file_name}\n")
                            f.write("\n")
                        
                        f.write(f"📊 Total: {total_files} HTML files have downloadable images\n")
                    
                    print(f"\n✅ File names saved to: {args.names_output}")
                except Exception as e:
                    print(f"❌ Failed to save file names: {str(e)}")
            
        elif args.mode == 'names-detailed':
            print("📝 Finding HTML files with downloadable images (detailed view with image names)...")
            results = analyzer.analyze_all(args.folder, check_remote=False)  # Quick analysis
            downloadable_files = analyzer.print_downloadable_file_names(results)
            
            # Save to file (simple structure for now)
            if downloadable_files:
                try:
                    with open(args.names_output, 'w', encoding='utf-8') as f:
                        f.write("HTML FILES WITH DOWNLOADABLE IMAGES (DETAILED)\n")
                        f.write("=" * 55 + "\n\n")
                        
                        if analyzer.skip_images:
                            f.write(f"🚫 Skip list active: {len(analyzer.skip_images)} images excluded\n\n")
                        
                        total_files = 0
                        for folder, file_names in downloadable_files.items():
                            total_files += len(file_names)
                            f.write(f"📁 {folder} ({len(file_names)} files):\n")
                            for i, file_name in enumerate(file_names, 1):
                                f.write(f"   {i:2d}. {file_name}\n")
                            f.write("\n")
                        
                        f.write(f"📊 Total: {total_files} HTML files have downloadable images\n")
                        f.write("Note: See console output for detailed image information\n")
                    
                    print(f"\n✅ File names saved to: {args.names_output}")
                    print("📋 Console shows detailed image information")
                except Exception as e:
                    print(f"❌ Failed to save file names: {str(e)}")
    
    except KeyboardInterrupt:
        print("\n⏹️  Analysis interrupted by user")
    except Exception as e:
        print(f"❌ Error during analysis: {str(e)}")
        import traceback
        traceback.print_exc()


def save_downloadable_urls(folder_name=None, output_file="downloadable_images.json", 
                          base_directory="data/html", skip_images=None, skip_list_file=None):
    """Analyze and save downloadable image URLs to a JSON file."""
    analyzer = HTMLImageAnalyzer(base_directory, skip_images=skip_images)
    
    # Load skip list from file if provided
    if skip_list_file and os.path.exists(skip_list_file):
        analyzer.load_skip_list_from_file(skip_list_file)
    
    results = analyzer.analyze_all(folder_name, check_remote=True)
    urls = analyzer.get_image_urls_for_download(results)
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(urls, f, indent=2, ensure_ascii=False)
        
        # Count total URLs
        total_urls = sum(file_data['total_downloadable'] 
                        for folder_urls in urls.values() 
                        for file_data in folder_urls.values())
        
        print(f"✅ Downloadable URLs saved to: {output_file}")
        print(f"📊 Total URLs: {total_urls} (excludes {len(analyzer.skip_images)} skipped images)")
    except Exception as e:
        print(f"❌ Failed to save URLs: {str(e)}")
    
    return urls


def interactive_mode():
    """Interactive mode for easier usage."""
    print("🔍 HTML Image Analyzer - Interactive Mode")
    print("=" * 50)
    
    # Get base directory
    base_dir = input("Enter base directory (press Enter for 'data/html'): ").strip()
    if not base_dir:
        base_dir = "data/html"
    
    if not os.path.exists(base_dir):
        print(f"❌ Directory not found: {base_dir}")
        return
    
    # Initialize analyzer
    analyzer = HTMLImageAnalyzer(base_dir)
    
    # Skip list management
    print(f"\n🚫 Skip List Management:")
    print("Do you want to load a skip list? (Images in skip list won't be considered downloadable)")
    print("  1. No skip list (analyze all images)")
    print("  2. Load skip list from file")
    print("  3. Enter skip list manually")
    
    try:
        skip_choice = input("Select option (1-3, default=1): ").strip() or "1"
        
        if skip_choice == "2":
            skip_file = input("Enter path to skip list file: ").strip()
            if skip_file and os.path.exists(skip_file):
                analyzer.load_skip_list_from_file(skip_file)
            else:
                print(f"❌ File not found: {skip_file}")
        
        elif skip_choice == "3":
            print("Enter image filenames to skip (one per line, empty line to finish):")
            while True:
                img_name = input("Image filename: ").strip()
                if not img_name:
                    break
                analyzer.add_skip_images(img_name)
        
    except:
        print("Using no skip list")
    
    # Show current skip list status
    if analyzer.skip_images:
        print(f"\n✅ Skip list loaded: {len(analyzer.skip_images)} images will be ignored")
        show_list = input("Show skip list? (y/n, default=n): ").strip().lower()
        if show_list == 'y':
            analyzer.show_skip_list()
    else:
        print(f"\n✅ No skip list - all images will be considered downloadable")
    
    # Show available folders
    html_files = analyzer.find_html_files()
    
    if not html_files:
        print("❌ No HTML files found in the directory")
        return
    
    print(f"\n📁 Available folders:")
    folders = list(html_files.keys())
    for i, folder in enumerate(folders, 1):
        file_count = len(html_files[folder])
        print(f"  {i}. {folder} ({file_count} HTML files)")
    
    print(f"  {len(folders) + 1}. Analyze ALL folders")
    
    # Get folder choice
    try:
        choice = input(f"\nSelect folder (1-{len(folders) + 1}): ").strip()
        if choice == str(len(folders) + 1):
            selected_folder = None
        else:
            selected_folder = folders[int(choice) - 1]
    except (ValueError, IndexError):
        print("❌ Invalid selection")
        return
    
    # Get analysis type
    print(f"\n📊 Analysis options:")
    print("  1. Quick analysis (fast, no remote image checking)")
    print("  2. Detailed analysis (checks if remote images are accessible)")
    print("  3. Extract downloadable URLs to JSON file")
    print("  4. Get HTML file names with downloadable images - SIMPLE")
    print("  5. Get HTML file names with downloadable images - DETAILED (shows image names)")
    print("  6. Skip list management and testing")
    print("  7. Test skip list functionality (debug mode)")
    
    try:
        analysis_choice = input("Select analysis type (1-7): ").strip()
    except:
        print("❌ Invalid selection")
        return
    
    if analysis_choice == "6":
        # Skip list management mode
        print(f"\n🚫 Skip List Management Mode")
        while True:
            print(f"\nCurrent skip list: {len(analyzer.skip_images)} images")
            print("  1. Show skip list")
            print("  2. Add images to skip list")
            print("  3. Remove images from skip list")
            print("  4. Clear skip list")
            print("  5. Save skip list to file")
            print("  6. Test skip list functionality")
            print("  7. Exit")
            
            try:
                mgmt_choice = input("Select option (1-7): ").strip()
                
                if mgmt_choice == "1":
                    analyzer.show_skip_list()
                elif mgmt_choice == "2":
                    print("Enter image filenames to add (one per line, empty line to finish):")
                    while True:
                        img_name = input("Image filename: ").strip()
                        if not img_name:
                            break
                        analyzer.add_skip_images(img_name)
                elif mgmt_choice == "3":
                    img_names = input("Enter image filenames to remove (space separated): ").strip().split()
                    if img_names:
                        analyzer.remove_skip_images(img_names)
                elif mgmt_choice == "4":
                    confirm = input("Clear all images from skip list? (y/n): ").strip().lower()
                    if confirm == 'y':
                        analyzer.clear_skip_images()
                elif mgmt_choice == "5":
                    filename = input("Enter filename to save skip list (default: skip_list.txt): ").strip() or "skip_list.txt"
                    analyzer.save_skip_list_to_file(filename)
                elif mgmt_choice == "6":
                    # Test skip list functionality
                    if analyzer.skip_images:
                        analyzer.verify_skip_list()
                        test_filenames = input("Enter test filenames (space separated, or press Enter for defaults): ").strip()
                        if test_filenames:
                            filenames = test_filenames.split()
                        else:
                            filenames = ['logo.gif', 'constitution_2022.png', 'back_new1.gif', 'print.png', 'bullet1.gif']
                        
                        print(f"\n🔍 Testing filename matching:")
                        for filename in filenames:
                            result = analyzer.debug_skip_list_matching(filename)
                            print(f"   {filename} -> {result}")
                    else:
                        print("❌ Skip list is empty - nothing to test!")
                elif mgmt_choice == "7":
                    break
                else:
                    print("❌ Invalid option")
            except KeyboardInterrupt:
                break
        return
    
    elif analysis_choice == "7":
        # Test skip list functionality only
        print(f"\n🧪 Testing Skip List Functionality")
        if analyzer.skip_images:
            analyzer.verify_skip_list()
            test_filenames = input("Enter test filenames (space separated, or press Enter for defaults): ").strip()
            if test_filenames:
                filenames = test_filenames.split()
            else:
                filenames = ['logo.gif', 'constitution_2022.png', 'back_new1.gif', 'print.png', 'bullet1.gif']
            
            print(f"\n🔍 Testing filename matching:")
            for filename in filenames:
                result = analyzer.debug_skip_list_matching(filename)
                print(f"   {filename} -> {result}")
        else:
            print("❌ Skip list is empty - nothing to test!")
            print("💡 Load a skip list first using options 1-6")
        return
    
    print(f"\n🚀 Starting analysis...")
    
    if analysis_choice == "1":
        results = analyzer.analyze_all(selected_folder, check_remote=False)
        analyzer.generate_report(results)
    elif analysis_choice == "2":
        results = analyzer.analyze_all(selected_folder, check_remote=True)
        analyzer.generate_report(results)
    elif analysis_choice == "3":
        results = analyzer.analyze_all(selected_folder, check_remote=True)
        urls = analyzer.get_image_urls_for_download(results)
        
        output_file = "downloadable_images.json"
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(urls, f, indent=2, ensure_ascii=False)
            print(f"✅ URLs saved to: {output_file}")
            
            # Show summary
            total_urls = sum(file_data['total_downloadable'] 
                           for folder_urls in urls.values() 
                           for file_data in folder_urls.values())
            print(f"📊 Total downloadable URLs: {total_urls} (skip list excluded)")
        except Exception as e:
            print(f"❌ Failed to save URLs: {str(e)}")
        
        analyzer.generate_report(results)
    elif analysis_choice == "4":
        # Simple file names - fastest option
        print("📝 Finding HTML files with downloadable images (simple view)...")
        results = analyzer.analyze_all(selected_folder, check_remote=False)
        downloadable_files = analyzer.print_simple_file_names(results)
        
        if downloadable_files:
            save_choice = input("\nSave file names to text file? (y/n, default=n): ").strip().lower()
            if save_choice == 'y':
                filename = input("Enter filename (default: downloadable_html_files.txt): ").strip() or "downloadable_html_files.txt"
                try:
                    with open(filename, 'w', encoding='utf-8') as f:
                        f.write("HTML FILES WITH DOWNLOADABLE IMAGES\n")
                        f.write("=" * 45 + "\n\n")
                        
                        if analyzer.skip_images:
                            f.write(f"🚫 Skip list active: {len(analyzer.skip_images)} images excluded\n\n")
                        
                        total_files = 0
                        for folder, file_names in downloadable_files.items():
                            total_files += len(file_names)
                            f.write(f"📁 {folder} ({len(file_names)} files):\n")
                            for i, file_name in enumerate(file_names, 1):
                                f.write(f"   {i:2d}. {file_name}\n")
                            f.write("\n")
                        
                        f.write(f"📊 Total: {total_files} HTML files have downloadable images\n")
                    
                    print(f"✅ File names saved to: {filename}")
                except Exception as e:
                    print(f"❌ Failed to save file names: {str(e)}")
    
    elif analysis_choice == "5":
        # Detailed file names with image information
        print("📝 Finding HTML files with downloadable images (detailed view with image names)...")
        results = analyzer.analyze_all(selected_folder, check_remote=False)
        downloadable_files = analyzer.print_downloadable_file_names(results)
        
        if downloadable_files:
            save_choice = input("\nSave detailed information to text file? (y/n, default=n): ").strip().lower()
            if save_choice == 'y':
                filename = input("Enter filename (default: detailed_downloadable_files.txt): ").strip() or "detailed_downloadable_files.txt"
                try:
                    # For saving detailed info, we'd need to recreate the detailed structure
                    # For now, let's save the simple structure
                    with open(filename, 'w', encoding='utf-8') as f:
                        f.write("HTML FILES WITH DOWNLOADABLE IMAGES (DETAILED)\n")
                        f.write("=" * 55 + "\n\n")
                        
                        if analyzer.skip_images:
                            f.write(f"🚫 Skip list active: {len(analyzer.skip_images)} images excluded\n\n")
                        
                        total_files = 0
                        for folder, file_names in downloadable_files.items():
                            total_files += len(file_names)
                            f.write(f"📁 {folder} ({len(file_names)} files):\n")
                            for i, file_name in enumerate(file_names, 1):
                                f.write(f"   {i:2d}. {file_name}\n")
                            f.write("\n")
                        
                        f.write(f"📊 Total: {total_files} HTML files have downloadable images\n")
                        f.write("Note: See console output for detailed image information\n")
                    
                    print(f"✅ File names saved to: {filename}")
                except Exception as e:
                    print(f"❌ Failed to save file names: {str(e)}")
    else:
        print("❌ Invalid analysis type")


def example_usage():
    """Example usage scenarios."""
    print("📚 Example Usage Scenarios")
    print("=" * 30)
    
    # Example 1: Test skip list functionality
    print("\n1️⃣  Create and test skip list:")
    skip_list_example = [
        'back_new1.gif', 'iNote1.gif', 'print.png', 'relatedCases.gif',
        'speaker.gif', 'search_small.gif', 'logo.gif', 'bullet1.gif',
        'top.gif', 'subscribe.gif', 'helpUs_Img.gif', 'constitution_2022.png'
    ]
    
    try:
        # Create example skip list file
        with open('example_skip_list.txt', 'w') as f:
            for img in skip_list_example:
                f.write(f"{img}\n")
        print(f"✅ Created example_skip_list.txt with {len(skip_list_example)} images")
        
        # Test the skip list
        analyzer = test_skip_list(skip_list_file='example_skip_list.txt')
        print(f"🔍 Example skip list created and tested!")
        
    except Exception as e:
        print(f"Error: {e}")
    
    # Example 2: Quick analysis
    print("\n2️⃣  Quick analysis with skip list:")
    try:
        downloadable_files = get_downloadable_file_names(
            skip_list_file='example_skip_list.txt', 
            print_results=False
        )
        if downloadable_files:
            total = sum(len(files) for files in downloadable_files.values())
            print(f"Found {total} HTML files with downloadable images (after skip list)")
        else:
            print("No files found with downloadable images")
    except Exception as e:
        print(f"Error: {e}")
    
    print("\n💡 TIP: If skip list isn't working:")
    print("   1. Check your skip list file format (one filename per line)")
    print("   2. Verify filenames match exactly")  
    print("   3. Use interactive mode option 7 to test skip list")
    print("   4. Run: test_skip_list(skip_list_file='your_file.txt')")


if __name__ == "__main__":
    import sys
    
    # If no arguments provided, run interactive mode
    if len(sys.argv) == 1:
        try:
            interactive_mode()
        except KeyboardInterrupt:
            print("\n👋 Goodbye!")
    else:
        # Run with command line arguments
        main()
    
    # Uncomment below to run example usage instead:
    # example_usage()


# Quick usage examples for copy-paste:
"""
QUICK USAGE EXAMPLES:

1. Simple HTML file names with downloadable images:
   python html_image_analyzer.py --mode names

2. Detailed view with specific image filenames:
   python html_image_analyzer.py --mode names-detailed

3. Test your skip list is working:
   python -c "from html_image_analyzer import test_skip_list; test_skip_list(skip_list_file='my_skip_list.txt')"

4. Get file names for specific folder with skip list:
   python html_image_analyzer.py --mode names-detailed --folder my_folder --skip-list skip_list.txt

5. Interactive mode (easiest):
   python html_image_analyzer.py

6. Debug skip list in interactive mode:
   python html_image_analyzer.py
   # Then select option 6 for skip list management

TROUBLESHOOTING SKIP LIST:
- Make sure skip list file exists and has one filename per line
- Check exact filename matches (case sensitive)
- No extra spaces or special characters
- Use test function to verify: test_skip_list(skip_list_file='your_file.txt')
"""