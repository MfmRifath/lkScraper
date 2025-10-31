#!/usr/bin/env python3
"""
Schedule Image Organizer - Automatically classify and organize schedule images

This script uses Google's Gemini API to analyze downloaded images from scraped HTML files
and automatically move schedule-related images to organized folders.

Schedule images include: tables, charts, schedules, forms, diagrams, organizational charts, etc.

Requirements:
    pip install google-generativeai pillow requests beautifulsoup4

Setup:
    1. Get Gemini API key from Google AI Studio
    2. Set environment variable: export GEMINI_API_KEY="your_api_key_here"
    3. Or pass API key as parameter

Usage:
    # Interactive mode
    python schedule_image_organizer.py
    
    # Command line mode
    python schedule_image_organizer.py --folder legislation_folder --api-key YOUR_KEY
    python schedule_image_organizer.py --dry-run --folder legislation_folder
    
    # Batch process all folders
    python schedule_image_organizer.py --all-folders --api-key YOUR_KEY
"""

import os
import sys
import json
import shutil
import time
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import argparse

try:
    import google.generativeai as genai
    from PIL import Image
    import requests
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"❌ Missing required packages. Please install:")
    print(f"pip install google-generativeai pillow requests beautifulsoup4")
    sys.exit(1)


class ScheduleImageOrganizer:
    def __init__(self, base_directory="data/html", api_key=None, dry_run=False):
        """Initialize the Schedule Image Organizer.
        
        Args:
            base_directory: Base directory containing HTML files and images
            api_key: Gemini API key (or set GEMINI_API_KEY environment variable)
            dry_run: If True, only simulate actions without moving files
        """
        self.base_directory = base_directory
        self.dry_run = dry_run
        self.results = {}
        self.session_log = []
        
        # Setup logging
        self.setup_logging()
        
        # Initialize Gemini API
        if api_key:
            self.api_key = api_key
        else:
            self.api_key = os.getenv('GEMINI_API_KEY')
            
        if not self.api_key:
            raise ValueError("Gemini API key required! ...")
            
        if not self.api_key:
            raise ValueError(
                "Gemini API key required! Set GEMINI_API_KEY environment variable "
                "or pass api_key parameter. Get key from: https://makersuite.google.com/app/apikey"
            )
        
        # Configure Gemini
        genai.configure(api_key=self.api_key)
        
        # Use current working model names (updated for 2025)
        model_names = [
        'gemini-2.5-flash',   # The latest general-purpose model
        'gemini-1.5-flash',   # Good fallback for vision
        'gemini-1.0-pro-vision', # Ensure vision access with this specific name
        'gemini-1.0-pro'      # Final reliable fallback
    ]

        
        self.model = None
        self.model_name = None
        
        for model_name in model_names:
            try:
                self.model = genai.GenerativeModel(model_name)
                # Test the model with a simple request
                test_response = self.model.generate_content("Hello")
                self.model_name = model_name
                self.logger.info(f"✅ Successfully initialized Gemini model: {model_name}")
                break
            except Exception as e:
                self.logger.warning(f"⚠️  Failed to initialize {model_name}: {str(e)}")
                continue
        
        if not self.model:
            raise ValueError("❌ Could not initialize any Gemini model. Please check your API key and try again.")
        
        self.logger.info("Schedule Image Organizer initialized")
        if self.dry_run:
            self.logger.info("🔍 DRY RUN MODE - No files will be moved")
        
    def test_api_connection(self):
        """Test the API connection and list available models."""
        try:
            print("🔍 Testing Gemini API connection...")
            
            # Test with a simple text prompt first
            test_response = self.model.generate_content("Hello, respond with 'API working'")
            print(f"✅ API Response: {test_response.text.strip()}")
            print(f"✅ Using model: {self.model_name}")
            
            # Test image capabilities if available
            try:
                from PIL import Image, ImageDraw
                # Create a simple test image
                test_img = Image.new('RGB', (100, 100), color='white')
                draw = ImageDraw.Draw(test_img)
                draw.rectangle([10, 10, 90, 90], outline='black', width=2)
                draw.text((30, 40), "TEST", fill='black')
                
                # Test image analysis
                image_response = self.model.generate_content(["Describe this test image in one word:", test_img])
                print(f"✅ Image analysis working: {image_response.text.strip()}")
                
            except Exception as e:
                print(f"⚠️  Image analysis test failed: {str(e)}")
                print("   This might indicate your API key doesn't have vision access")
            
            # Try to list available models (this might not work with all API keys)
            try:
                models = list(genai.list_models())
                print(f"\n📋 Found {len(models)} total models")
                
                # Show vision-capable models
                vision_models = [m for m in models if hasattr(m, 'name') and 
                               ('vision' in m.name.lower() or 'gemini' in m.name.lower())]
                
                if vision_models:
                    print(f"📊 Vision-capable models ({len(vision_models)}):")
                    for i, model in enumerate(vision_models[:5], 1):  # Show first 5
                        print(f"   {i}. {model.name}")
                    if len(vision_models) > 5:
                        print(f"   ... and {len(vision_models) - 5} more")
                else:
                    print("⚠️  No vision models found in available list")
                    
            except Exception as e:
                print(f"⚠️  Could not list models: {str(e)}")
            
            return True
        
        except Exception as e:
            print(f"❌ API connection test failed: {str(e)}")
            
            # Provide specific guidance based on error type
            error_str = str(e).lower()
            if "404" in error_str:
                print("💡 This appears to be a model access issue.")
                print("   Try getting a new API key from: https://makersuite.google.com/app/apikey")
            elif "403" in error_str:
                print("💡 This appears to be an authentication issue.")
                print("   Check your API key permissions")
            elif "429" in error_str:
                print("💡 This appears to be a rate limit issue.")
                print("   Wait a moment and try again")
            
            return False
    
    def troubleshoot_setup(self):
        """Run comprehensive troubleshooting checks."""
        print("🔧 TROUBLESHOOTING GEMINI API SETUP")
        print("=" * 50)
        
        # Check 1: API Key
        print("1️⃣  Checking API key...")
        if self.api_key:
            print(f"   ✅ API key found: {self.api_key[:10]}...")
        else:
            print("   ❌ No API key found")
            print("   💡 Get your key from: https://makersuite.google.com/app/apikey")
            print("   💡 Set environment variable: export GEMINI_API_KEY='your_key'")
            return False
        
        # Check 2: Model initialization
        print("2️⃣  Checking model initialization...")
        if self.model and self.model_name:
            print(f"   ✅ Model initialized: {self.model_name}")
        else:
            print("   ❌ Model initialization failed")
            return False
        
        # Check 3: Basic API call
        print("3️⃣  Testing basic API call...")
        try:
            response = self.model.generate_content("Say 'Hello' in JSON format: {'message': 'Hello'}")
            print(f"   ✅ Basic API call successful")
            print(f"   📝 Response preview: {response.text[:50]}...")
        except Exception as e:
            print(f"   ❌ Basic API call failed: {str(e)}")
            
            # Provide specific guidance
            error_str = str(e).lower()
            if "404" in error_str and "model" in error_str:
                print("   💡 Model not found - your API key may not have access to this model")
                print("   💡 Try getting a fresh API key from Google AI Studio")
            elif "quota" in error_str or "429" in error_str:
                print("   💡 Rate limit or quota exceeded - wait and try again")
            elif "403" in error_str:
                print("   💡 Authentication failed - check your API key")
            
            return False
        
        # Check 4: List available models
        print("4️⃣  Checking available models...")
        try:
            models = list(genai.list_models())
            if models:
                vision_models = [m for m in models if hasattr(m, 'name') and 
                               ('vision' in m.name.lower() or 'gemini' in m.name.lower())]
                
                if vision_models:
                    print(f"   ✅ Found {len(vision_models)} vision-capable models")
                    print(f"   📋 Examples: {[m.name for m in vision_models[:3]]}")
                else:
                    print("   ⚠️  No vision models found, but basic API works")
                    
                # Show current working models
                current_models = ['gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-pro-vision']
                print(f"   📊 Recommended models to try: {current_models}")
            else:
                print("   ⚠️  Could not retrieve model list, but API connection works")
                
        except Exception as e:
            print(f"   ⚠️  Could not list models: {str(e)}")
            print("   💡 This is normal - model listing isn't always available")
        
        # Check 5: Image analysis capability
        print("5️⃣  Testing image analysis capability...")
        try:
            from PIL import Image, ImageDraw
            
            # Create a simple test image
            test_img = Image.new('RGB', (100, 100), color='white')
            draw = ImageDraw.Draw(test_img)
            draw.rectangle([10, 10, 90, 90], outline='black', width=2)
            draw.text((25, 40), "TEST", fill='black')
            
            response = self.model.generate_content(["Describe this simple test image in one sentence:", test_img])
            print(f"   ✅ Image analysis successful")
            print(f"   📝 Response: {response.text[:100]}...")
            
        except Exception as e:
            error_str = str(e).lower()
            print(f"   ❌ Image analysis failed: {str(e)}")
            
            if "model" in error_str and "vision" in error_str:
                print(f"   💡 Your current model ({self.model_name}) may not support vision")
                print(f"   💡 Try using: gemini-pro-vision or gemini-1.5-pro")
            elif "quota" in error_str:
                print(f"   💡 Vision API quota may be exceeded")
            else:
                print(f"   💡 Your API key might not have access to vision models")
                print(f"   💡 Vision requires a different API key tier")
            
            return False
        
        # Check 6: Rate limits and quotas
        print("6️⃣  Checking rate limits...")
        try:
            # Make a few quick requests to test rate limits
            for i in range(3):
                response = self.model.generate_content(f"Count to {i+1}")
                time.sleep(0.5)  # Small delay
            print("   ✅ Rate limits appear normal")
        except Exception as e:
            print(f"   ⚠️  Rate limit issue detected: {str(e)}")
            print("   💡 Your account may have strict rate limits")
        
        print("\n🎉 Setup troubleshooting complete!")
        print(f"✅ Your API is working with model: {self.model_name}")
        print("💡 You should be able to run the image organizer now")
        return True
    
    def setup_logging(self):
        """Setup logging configuration."""
        log_filename = f"schedule_organizer_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_filename),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.log_file = log_filename
    
    def find_image_directories(self, folder_name=None):
        """Find all directories containing images.
        
        Args:
            folder_name: Specific folder to process, or None for all folders
            
        Returns:
            Dictionary with folder_name -> list of image directories
        """
        image_dirs = {}
        
        if not os.path.exists(self.base_directory):
            self.logger.error(f"Base directory not found: {self.base_directory}")
            return image_dirs
        
        if folder_name:
            # Process specific folder
            folder_path = os.path.join(self.base_directory, folder_name)
            if os.path.exists(folder_path):
                image_dirs[folder_name] = self._scan_folder_for_images(folder_path)
        else:
            # Process all folders
            for item in os.listdir(self.base_directory):
                item_path = os.path.join(self.base_directory, item)
                if os.path.isdir(item_path):
                    image_dirs[item] = self._scan_folder_for_images(item_path)
        
        return {k: v for k, v in image_dirs.items() if v}  # Remove empty results
    
    def _scan_folder_for_images(self, folder_path):
        """Scan a folder for image directories."""
        image_dirs = []
        
        for root, dirs, files in os.walk(folder_path):
            if 'images' in os.path.basename(root).lower():
                # Check if directory contains image files
                image_files = [f for f in files if self._is_image_file(f)]
                if image_files:
                    image_dirs.append({
                        'path': root,
                        'images': image_files,
                        'parent': os.path.dirname(root)
                    })
        
        return image_dirs
    
    def _is_image_file(self, filename):
        """Check if file is an image."""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'}
        return Path(filename).suffix.lower() in image_extensions
    
    def analyze_image_with_gemini(self, image_path, max_retries=3):
        """Analyze image using Gemini API to determine if it's a schedule/table/chart.
        
        Args:
            image_path: Path to the image file
            max_retries: Maximum number of retry attempts
            
        Returns:
            Dictionary with analysis results
        """
        
        original_image_path = image_path
        temp_png_path = None
        
        # 1. Handle Unsupported GIF files by converting to PNG
        if original_image_path.lower().endswith('.gif'):
            try:
                img = Image.open(original_image_path)
                
                # Use the first frame for analysis if it's an animated GIF.
                if img.is_animated:
                    img.seek(0)
                
                # Create a temporary path for the PNG version
                temp_png_path = Path(original_image_path).with_suffix('.png')
                img.save(temp_png_path, 'PNG')
                
                self.logger.info(f"    🔄 Converted GIF to temp PNG: {temp_png_path.name}")
                image_path = str(temp_png_path) # Use the temporary PNG path for analysis

            except Exception as e:
                self.logger.error(f"Failed to convert GIF {original_image_path}: {str(e)}")
                return {
                    'success': False,
                    'error': f'GIF conversion failed: {str(e)}',
                    'is_schedule': False,
                    'confidence': 0.0,
                    'attempt': 1,
                    'model_used': self.model_name
                }
        
        # Store the final result dictionary to handle cleanup outside the try/except
        final_result = None

        for attempt in range(max_retries):
            try:
                # Load and prepare image (uses temp PNG path if conversion happened)
                img = Image.open(image_path)
                
                # Resize image if too large (Gemini has size limits)
                max_size = (1024, 1024)
                if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
                    img.thumbnail(max_size, Image.Resampling.LANCZOS)
                    self.logger.debug(f"Resized image {os.path.basename(image_path)} to {img.size}")
                
                # Create focused prompt for schedule detection
                prompt = """
                Analyze this image carefully. I need to determine if it contains structured information that should be organized as a "schedule" document.

                Look for:
                - Tables, charts, or data grids
                - Schedules, timelines, or calendars  
                - Forms with structured fields
                - Diagrams, flowcharts, or organizational charts
                - Legal document schedules/appendices
                - Mathematical formulas in structured format
                - Any organized data layout

                Do NOT classify as schedule:
                - Simple logos, banners, or decorative images
                - Plain text without structure
                - Single photographs or illustrations
                - Navigation buttons or UI elements

                Respond with ONLY this JSON format:
                {
                    "is_schedule": true/false,
                    "confidence": 0.0-1.0,
                    "type": "table/chart/diagram/form/schedule/text/logo/other",
                    "description": "brief description of what you see",
                    "reasoning": "why you classified it this way"
                }
                """
                
                # Analyze with Gemini
                response = self.model.generate_content([prompt, img])
                response_text = response.text.strip()
                
                # Clean and parse response
                try:
                    # Remove code block markers if present
                    if response_text.startswith('```json'):
                        response_text = response_text.split('```json')[1].split('```')[0].strip()
                    elif response_text.startswith('```'):
                        response_text = response_text.split('```')[1].split('```')[0].strip()
                    
                    # Remove any extra text before/after JSON
                    start_idx = response_text.find('{')
                    end_idx = response_text.rfind('}') + 1
                    if start_idx >= 0 and end_idx > start_idx:
                        response_text = response_text[start_idx:end_idx]
                    
                    result = json.loads(response_text)
                    
                    # Validate required fields
                    required_fields = ['is_schedule', 'confidence', 'type', 'description', 'reasoning']
                    for field in required_fields:
                        if field not in result:
                            raise ValueError(f"Missing required field: {field}")
                    
                    # Ensure confidence is a float between 0 and 1
                    result['confidence'] = max(0.0, min(1.0, float(result['confidence'])))
                    result['is_schedule'] = bool(result['is_schedule'])
                    
                    result['success'] = True
                    result['error'] = None
                    result['attempt'] = attempt + 1
                    result['model_used'] = self.model_name
                    
                    self.logger.debug(f"Gemini analysis for {os.path.basename(original_image_path)}: {result}")
                    final_result = result
                    break # Success, exit retry loop
                    
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    if attempt < max_retries - 1:
                        self.logger.warning(f"Parse error (attempt {attempt + 1}), retrying: {str(e)}")
                        self.logger.debug(f"Raw response: {response_text}")
                        time.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    else:
                        self.logger.warning(f"Failed to parse Gemini response after {max_retries} attempts")
                        self.logger.debug(f"Final raw response: {response_text}")
                        final_result = {
                            'success': False,
                            'error': f'JSON parse error after {max_retries} attempts: {str(e)}',
                            'is_schedule': False,
                            'confidence': 0.0,
                            'raw_response': response_text,
                            'attempt': attempt + 1,
                            'model_used': self.model_name
                        }
                        break # Failure, exit retry loop
            
            except Exception as e:
                error_msg = str(e)
                
                # Handle specific error types
                if "400" in error_msg and "mime type" in error_msg.lower():
                     final_result = {
                        'success': False,
                        'error': f'Unsupported MIME type error: {error_msg}',
                        'is_schedule': False,
                        'confidence': 0.0,
                        'attempt': attempt + 1,
                        'model_used': self.model_name
                    }
                     break
                
                if "404" in error_msg and "model" in error_msg.lower():
                    final_result = {
                        'success': False,
                        'error': f'Model access error: {self.model_name} is not available with your API key',
                        'is_schedule': False,
                        'confidence': 0.0,
                        'attempt': attempt + 1,
                        'model_used': self.model_name
                    }
                    break
                elif "429" in error_msg or "quota" in error_msg.lower() or "rate" in error_msg.lower():
                    if attempt < max_retries - 1:
                        wait_time = (2 ** attempt) * 5  # Longer wait for rate limits
                        self.logger.warning(f"Rate limit hit (attempt {attempt + 1}), waiting {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        final_result = {
                            'success': False,
                            'error': f'Rate limit exceeded after {max_retries} attempts',
                            'is_schedule': False,
                            'confidence': 0.0,
                            'attempt': attempt + 1,
                            'model_used': self.model_name
                        }
                        break
                elif "403" in error_msg:
                    final_result = {
                        'success': False,
                        'error': 'API access denied. Your API key may not have vision access.',
                        'is_schedule': False,
                        'confidence': 0.0,
                        'attempt': attempt + 1,
                        'model_used': self.model_name
                    }
                    break
                
                # Generic retry for other errors
                if attempt < max_retries - 1:
                    self.logger.warning(f"Error analyzing image (attempt {attempt + 1}): {error_msg}, retrying...")
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                else:
                    self.logger.error(f"Error analyzing image {original_image_path} after {max_retries} attempts: {error_msg}")
                    final_result = {
                        'success': False,
                        'error': f'Analysis failed after {max_retries} attempts: {error_msg}',
                        'is_schedule': False,
                        'confidence': 0.0,
                        'attempt': attempt + 1,
                        'model_used': self.model_name
                    }
                    break
        
        # 2. Clean up the temporary PNG file if it was created
        if temp_png_path and os.path.exists(temp_png_path):
            try:
                os.remove(temp_png_path)
                self.logger.debug(f"    🧹 Cleaned up temp PNG: {temp_png_path.name}")
            except Exception as e:
                self.logger.warning(f"Failed to clean up temp file {temp_png_path}: {str(e)}")
        
        # Return the final result (either success or failure)
        if final_result:
            return final_result
        
        # This is a final safety net for unexpected loop termination
        return {
            'success': False,
            'error': 'Unexpected error in analysis process',
            'is_schedule': False,
            'confidence': 0.0,
            'attempt': max_retries,
            'model_used': self.model_name
        }
    
    def create_schedule_directory(self, parent_dir):
        """Create the schedules directory structure.
        
        Args:
            parent_dir: Parent directory (same level as original images folder)
            
        Returns:
            Path to the created schedules/images directory
        """
        # Create: parent_dir/schedules/Schedules/images
        schedule_path = os.path.join(parent_dir, "schedules", "Schedules", "images")
        
        if not self.dry_run:
            os.makedirs(schedule_path, exist_ok=True)
            self.logger.info(f"📁 Created schedule directory: {schedule_path}")
        else:
            self.logger.info(f"📁 [DRY RUN] Would create: {schedule_path}")
        
        return schedule_path
    
    def move_schedule_image(self, image_path, destination_dir, image_name):
        """Move a schedule image to the schedules directory.
        
        Args:
            image_path: Source image path
            destination_dir: Destination directory path
            image_name: Image filename
            
        Returns:
            New path of the moved image, or None if failed
        """
        destination_path = os.path.join(destination_dir, image_name)
        
        if not self.dry_run:
            try:
                # Handle duplicate filenames
                if os.path.exists(destination_path):
                    base_name, ext = os.path.splitext(image_name)
                    counter = 1
                    while os.path.exists(destination_path):
                        new_name = f"{base_name}_{counter}{ext}"
                        destination_path = os.path.join(destination_dir, new_name)
                        counter += 1
                    image_name = os.path.basename(destination_path)
                
                shutil.move(image_path, destination_path)
                self.logger.info(f"📦 Moved: {os.path.basename(image_path)} -> schedules/Schedules/images/{image_name}")
                return destination_path
                
            except Exception as e:
                self.logger.error(f"Failed to move {image_path}: {str(e)}")
                return None
        else:
            self.logger.info(f"📦 [DRY RUN] Would move: {os.path.basename(image_path)} -> schedules/Schedules/images/{image_name}")
            return destination_path
    
    def update_html_references(self, html_file_path, old_image_path, new_image_path):
        """Update HTML file to reference moved images.
        
        Args:
            html_file_path: Path to the HTML file
            old_image_path: Old image path (relative to HTML)
            new_image_path: New image path (relative to HTML)
        """
        if self.dry_run:
            self.logger.info(f"📝 [DRY RUN] Would update HTML references in {os.path.basename(html_file_path)}")
            return
        
        try:
            with open(html_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Create relative paths
            old_rel_path = os.path.relpath(old_image_path, os.path.dirname(html_file_path))
            new_rel_path = os.path.relpath(new_image_path, os.path.dirname(html_file_path))
            
            # Update references
            updated_content = content.replace(old_rel_path, new_rel_path)
            
            if content != updated_content:
                with open(html_file_path, 'w', encoding='utf-8') as f:
                    f.write(updated_content)
                self.logger.info(f"📝 Updated HTML references in {os.path.basename(html_file_path)}")
            
        except Exception as e:
            self.logger.error(f"Failed to update HTML file {html_file_path}: {str(e)}")
    
    def process_image_directory(self, image_dir_info, confidence_threshold=0.7):
        """Process all images in a directory.
        
        Args:
            image_dir_info: Dictionary containing directory info
            confidence_threshold: Minimum confidence to classify as schedule
            
        Returns:
            Dictionary with processing results
        """
        results = {
            'total_images': len(image_dir_info['images']),
            'processed': 0,
            'schedule_images': 0,
            'moved_images': 0,
            'errors': 0,
            'details': []
        }
        
        image_dir = image_dir_info['path']
        parent_dir = image_dir_info['parent']
        
        self.logger.info(f"🖼️  Processing {results['total_images']} images in {image_dir}")
        
        # Find corresponding HTML file for reference updates
        html_file = None
        html_name = os.path.basename(parent_dir)
        potential_html = os.path.join(parent_dir, f"{html_name}.html")
        if os.path.exists(potential_html):
            html_file = potential_html
        
        schedule_dir_created = False
        schedule_destination = None
        
        for i, image_name in enumerate(image_dir_info['images'], 1):
            image_path = os.path.join(image_dir, image_name)
            
            self.logger.info(f"  📸 [{i}/{results['total_images']}] Analyzing: {image_name}")
            
            # Analyze with Gemini
            analysis = self.analyze_image_with_gemini(image_path)
            
            result_detail = {
                'image': image_name,
                'path': image_path,
                'analysis': analysis,
                'moved': False,
                'new_path': None
            }
            
            results['processed'] += 1
            
            if analysis['success']:
                is_schedule = analysis.get('is_schedule', False)
                confidence = analysis.get('confidence', 0.0)
                
                if is_schedule and confidence >= confidence_threshold:
                    results['schedule_images'] += 1
                    
                    # Create schedule directory if needed
                    if not schedule_dir_created:
                        schedule_destination = self.create_schedule_directory(parent_dir)
                        schedule_dir_created = True
                    
                    # Move the image
                    old_path = image_path
                    new_path = self.move_schedule_image(image_path, schedule_destination, image_name)
                    
                    if new_path:
                        results['moved_images'] += 1
                        result_detail['moved'] = True
                        result_detail['new_path'] = new_path
                        
                        # Update HTML references if HTML file exists
                        if html_file:
                            self.update_html_references(html_file, old_path, new_path)
                    
                    self.logger.info(f"    ✅ Schedule detected: {analysis.get('type', 'unknown')} (confidence: {confidence:.2f})")
                else:
                    self.logger.info(f"    ⚪ Not a schedule (confidence: {confidence:.2f})")
            else:
                results['errors'] += 1
                self.logger.error(f"    ❌ Analysis failed: {analysis.get('error', 'Unknown error')}")
            
            results['details'].append(result_detail)
            
            # Small delay to respect API rate limits
            time.sleep(0.5)
        
        return results
    
    def process_all_folders(self, folder_name=None, confidence_threshold=0.7):
        """Process all folders or a specific folder.
        
        Args:
            folder_name: Specific folder to process, or None for all
            confidence_threshold: Minimum confidence to classify as schedule
            
        Returns:
            Complete processing results
        """
        image_dirs = self.find_image_directories(folder_name)
        
        if not image_dirs:
            self.logger.warning("No image directories found to process")
            return {}
        
        self.logger.info(f"🚀 Starting schedule image organization")
        self.logger.info(f"📊 Found image directories in {len(image_dirs)} folders")
        self.logger.info(f"🎯 Confidence threshold: {confidence_threshold}")
        
        total_results = {
            'folders_processed': 0,
            'total_images': 0,
            'total_schedule_images': 0,
            'total_moved': 0,
            'total_errors': 0,
            'folder_details': {}
        }
        
        for folder, dir_list in image_dirs.items():
            self.logger.info(f"\n📁 Processing folder: {folder}")
            folder_results = {
                'directories': len(dir_list),
                'total_images': 0,
                'schedule_images': 0,
                'moved_images': 0,
                'errors': 0,
                'directory_details': []
            }
            
            for image_dir_info in dir_list:
                dir_results = self.process_image_directory(image_dir_info, confidence_threshold)
                
                folder_results['total_images'] += dir_results['total_images']
                folder_results['schedule_images'] += dir_results['schedule_images']
                folder_results['moved_images'] += dir_results['moved_images']
                folder_results['errors'] += dir_results['errors']
                folder_results['directory_details'].append({
                    'directory': image_dir_info['path'],
                    'results': dir_results
                })
            
            total_results['folders_processed'] += 1
            total_results['total_images'] += folder_results['total_images']
            total_results['total_schedule_images'] += folder_results['schedule_images']
            total_results['total_moved'] += folder_results['moved_images']
            total_results['total_errors'] += folder_results['errors']
            total_results['folder_details'][folder] = folder_results
            
            self.logger.info(f"✅ Folder {folder} complete: {folder_results['moved_images']}/{folder_results['total_images']} images moved")
        
        self.results = total_results
        return total_results
    
    def generate_report(self, save_to_file=None):
        """Generate a comprehensive report of the processing results."""
        if not self.results:
            print("No results available. Run process_all_folders() first.")
            return
        
        report_lines = [
            "SCHEDULE IMAGE ORGANIZATION REPORT",
            "=" * 50,
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Mode: {'DRY RUN' if self.dry_run else 'LIVE MODE'}",
            ""
        ]
        
        # Overall summary
        results = self.results
        report_lines.extend([
            "OVERALL SUMMARY",
            "-" * 20,
            f"📁 Folders processed: {results['folders_processed']}",
            f"🖼️  Total images analyzed: {results['total_images']}",
            f"📊 Schedule images detected: {results['total_schedule_images']}",
            f"📦 Images moved: {results['total_moved']}",
            f"❌ Errors encountered: {results['total_errors']}",
            ""
        ])
        
        if results['total_images'] > 0:
            detection_rate = (results['total_schedule_images'] / results['total_images']) * 100
            report_lines.append(f"📈 Schedule detection rate: {detection_rate:.1f}%")
            report_lines.append("")
        
        # Folder details
        for folder, folder_data in results['folder_details'].items():
            report_lines.extend([
                f"📁 FOLDER: {folder}",
                "-" * (len(folder) + 10),
                f"   Images analyzed: {folder_data['total_images']}",
                f"   Schedule images: {folder_data['schedule_images']}",
                f"   Images moved: {folder_data['moved_images']}",
                f"   Errors: {folder_data['errors']}",
                ""
            ])
            
            # Show directory details
            for dir_detail in folder_data['directory_details']:
                dir_path = dir_detail['directory']
                dir_results = dir_detail['results']
                if dir_results['schedule_images'] > 0:
                    report_lines.append(f"   📂 {os.path.basename(dir_path)}: {dir_results['moved_images']} schedules moved")
        
        report_lines.extend([
            "",
            "PROCESSING LOG",
            "-" * 15,
            f"Log file: {self.log_file}",
            ""
        ])
        
        report_text = "\n".join(report_lines)
        print(report_text)
        
        if save_to_file:
            try:
                with open(save_to_file, 'w', encoding='utf-8') as f:
                    f.write(report_text)
                print(f"📄 Report saved to: {save_to_file}")
            except Exception as e:
                print(f"❌ Failed to save report: {str(e)}")
        
        return report_text
    
    def create_backup(self):
        """Create a backup of the current state before processing."""
        if self.dry_run:
            print("📋 [DRY RUN] Backup creation skipped")
            return None
        
        backup_dir = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_path = os.path.join(os.path.dirname(self.base_directory), backup_dir)
        
        try:
            shutil.copytree(self.base_directory, backup_path)
            self.logger.info(f"💾 Backup created: {backup_path}")
            return backup_path
        except Exception as e:
            self.logger.error(f"Failed to create backup: {str(e)}")
            return None


def main():
    """Main function for command line usage."""
    parser = argparse.ArgumentParser(
        description='Organize schedule images using Gemini AI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python schedule_image_organizer.py --folder legislation_2024
  python schedule_image_organizer.py --all-folders --dry-run
  python schedule_image_organizer.py --api-key YOUR_KEY --confidence 0.8
  python schedule_image_organizer.py --test-api
  python schedule_image_organizer.py --troubleshoot

Common Issues:
  - Model not found: Run --troubleshoot to check setup
  - Rate limits: Use lower confidence or add delays
  - API key issues: Ensure key has Gemini vision access
  - 404 errors: Try --troubleshoot to find available models
        """
    )
    
    parser.add_argument('--directory', '-d', default='data/html',
                       help='Base directory containing HTML files (default: data/html)')
    parser.add_argument('--folder', '-f', default=None,
                       help='Specific folder to process (default: all folders)')
    parser.add_argument('--all-folders', action='store_true',
                       help='Process all folders in the directory')
    parser.add_argument('--api-key', default=None,
                       help='Gemini API key (or set GEMINI_API_KEY env var)')
    parser.add_argument('--confidence', '-c', type=float, default=0.7,
                       help='Minimum confidence threshold (0.0-1.0, default: 0.7)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Simulate actions without moving files')
    parser.add_argument('--test-api', action='store_true',
                       help='Test API connection and exit')
    parser.add_argument('--troubleshoot', action='store_true',
                       help='Run comprehensive troubleshooting checks')
    parser.add_argument('--backup', action='store_true',
                       help='Create backup before processing')
    parser.add_argument('--report', '-r', default=None,
                       help='Save report to specified file')
    
    args = parser.parse_args()
    
    print("🤖 Schedule Image Organizer")
    print("=" * 50)
    
    # Validate API key
    api_key = args.api_key or os.getenv('GEMINI_API_KEY')
    if not api_key:
        print("❌ Gemini API key required!")
        print("   Get your key from: https://makersuite.google.com/app/apikey")
        print("   Set environment variable: export GEMINI_API_KEY='your_key'")
        print("   Or use --api-key parameter")
        return
    
    try:
        # Initialize organizer
        organizer = ScheduleImageOrganizer(
            base_directory=args.directory,
            api_key=api_key,
            dry_run=args.dry_run
        )
        
        # Test API if requested
        if args.test_api:
            print("🔍 Testing Gemini API connection...")
            if organizer.test_api_connection():
                print("✅ API connection successful!")
            else:
                print("❌ API connection failed!")
            return
        
        # Run troubleshooting if requested
        if args.troubleshoot:
            if organizer.troubleshoot_setup():
                print("💡 Your setup looks good! Try running with --dry-run first.")
            else:
                print("💡 Fix the issues above and try again.")
            return
        
        print(f"📁 Directory: {args.directory}")
        print(f"🎯 Confidence threshold: {args.confidence}")
        if args.dry_run:
            print("🔍 DRY RUN MODE - No files will be moved")
        
        # Test API connection before processing
        print("🔍 Testing API connection...")
        if not organizer.test_api_connection():
            print("❌ API test failed. Please check your API key and model access.")
            print("💡 Try running with --test-api flag to debug the connection.")
            return
        
        # Create backup if requested
        if args.backup:
            backup_path = organizer.create_backup()
            if backup_path:
                print(f"💾 Backup created: {backup_path}")
        
        # Process images
        folder_to_process = args.folder if not args.all_folders else None
        results = organizer.process_all_folders(
            folder_name=folder_to_process,
            confidence_threshold=args.confidence
        )
        
        # Generate report
        if results:
            print("\n" + "="*50)
            report_file = args.report or f"schedule_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            organizer.generate_report(save_to_file=report_file)
        else:
            print("❌ No results to report")
    
    except KeyboardInterrupt:
        print("\n⏹️  Processing interrupted by user")
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()


def interactive_mode():
    """Interactive mode for easier usage."""
    print("🤖 Schedule Image Organizer - Interactive Mode")
    print("=" * 55)
    
    # Get API key
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        print("🔑 Gemini API Key Setup")
        print("Get your free API key from: https://makersuite.google.com/app/apikey")
        api_key = input("Enter your Gemini API key: ").strip()
        if not api_key:
            print("❌ API key required to continue")
            return
    else:
        print("✅ Using Gemini API key from environment")
    
    # Get directory
    base_dir = input("\nEnter base directory (press Enter for 'data/html'): ").strip()
    if not base_dir:
        base_dir = "data/html"
    
    if not os.path.exists(base_dir):
        print(f"❌ Directory not found: {base_dir}")
        return
    
    try:
        # Initialize organizer
        organizer = ScheduleImageOrganizer(base_directory=base_dir, api_key=api_key)
        
        # Test API connection first
        print("\n🔍 Testing API connection...")
        if not organizer.test_api_connection():
            print("❌ API test failed. Please check your API key and try again.")
            return
        
        # Show available folders
        image_dirs = organizer.find_image_directories()
        if not image_dirs:
            print("❌ No image directories found")
            return
        
        print(f"\n📁 Found image directories in {len(image_dirs)} folders:")
        folders = list(image_dirs.keys())
        for i, folder in enumerate(folders, 1):
            total_images = sum(len(dir_info['images']) for dir_info in image_dirs[folder])
            print(f"   {i}. {folder} ({total_images} images)")
        print(f"   {len(folders) + 1}. Process ALL folders")
        
        # Get folder choice
        try:
            choice = input(f"\nSelect folder (1-{len(folders) + 1}): ").strip()
            if choice == str(len(folders) + 1):
                selected_folder = None
                print("🚀 Processing ALL folders")
            else:
                selected_folder = folders[int(choice) - 1]
                print(f"🚀 Processing folder: {selected_folder}")
        except (ValueError, IndexError):
            print("❌ Invalid selection")
            return
        
        # Get options
        confidence = input("\nConfidence threshold (0.0-1.0, default=0.7): ").strip()
        try:
            confidence = float(confidence) if confidence else 0.7
        except ValueError:
            confidence = 0.7
        
        dry_run = input("\nDry run? (y/n, default=n): ").strip().lower() == 'y'
        create_backup = input("Create backup? (y/n, default=y): ").strip().lower() != 'n'
        
        if dry_run:
            organizer.dry_run = True
            print("🔍 DRY RUN MODE enabled")
        
        # Create backup
        if create_backup and not dry_run:
            print("💾 Creating backup...")
            backup_path = organizer.create_backup()
            if backup_path:
                print(f"✅ Backup created: {backup_path}")
        
        # Process images
        print(f"\n🤖 Starting AI analysis (confidence: {confidence})...")
        results = organizer.process_all_folders(
            folder_name=selected_folder,
            confidence_threshold=confidence
        )
        
        # Generate report
        if results:
            print("\n" + "="*50)
            organizer.generate_report()
            
            save_report = input("\nSave detailed report to file? (y/n): ").strip().lower() == 'y'
            if save_report:
                report_file = f"schedule_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                organizer.generate_report(save_to_file=report_file)
        
    except KeyboardInterrupt:
        print("\n⏹️  Processing interrupted by user")
    except Exception as e:
        print(f"❌ Error: {str(e)}")


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


# Example usage for testing
def quick_test():
    """Quick test function to verify the setup works"""
    print("🧪 QUICK TEST MODE")
    print("=" * 30)
    
    # Test API connection only
    try:
        organizer = ScheduleImageOrganizer(api_key=os.getenv('GEMINI_API_KEY'))
        if organizer.test_api_connection():
            print("✅ Your setup is working correctly!")
            print("💡 You can now run the full image organizer")
        else:
            print("❌ Setup test failed")
            print("💡 Run with --troubleshoot to diagnose issues")
    except Exception as e:
        print(f"❌ Setup error: {str(e)}")
        print("💡 Make sure you have set GEMINI_API_KEY environment variable")

# Uncomment the line below to run a quick test
# quick_test()


"""
WORKING USAGE EXAMPLES FOR 2025:

1. Test your setup first:
   python schedule_image_organizer.py --troubleshoot

2. Run with your API key:
   export GEMINI_API_KEY="your_actual_api_key_here"
   python schedule_image_organizer.py --folder legislation_A --dry-run

3. Interactive mode (easiest):
   python schedule_image_organizer.py

4. Full processing:
   python schedule_image_organizer.py --folder legislation_A --confidence 0.7 --backup

5. Process all folders:
   python schedule_image_organizer.py --all-folders --confidence 0.6

TROUBLESHOOTING:
- If you get 404 errors: Run --troubleshoot to check your API setup
- If rate limited: Use lower confidence (0.5) or add delays
- If no images detected: Try confidence 0.5 instead of 0.7

API KEY SETUP:
1. Go to: https://makersuite.google.com/app/apikey
2. Create new API key
3. Set: export GEMINI_API_KEY="your_key"
4. Test: python schedule_image_organizer.py --test-api
"""