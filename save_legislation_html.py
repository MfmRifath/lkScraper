import os
import json
import requests
import random
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import mimetypes
from pathlib import Path

class MainHTMLScraper:
    def __init__(self, headers=None, cookies=None, session=None, skip_images=None):
        """Initialize the HTMLScraper with optional headers, cookies, session, and images to skip."""
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.session = session or requests.Session()
        
        # List of image names to skip downloading (initially empty)
        self.skip_images = skip_images or []
        
        # Update session with headers and cookies
        self.session.headers.update(self.headers)
        self.session.cookies.update(self.cookies)
    
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
    
    def load_json(self, json_file):
        """Load the JSON file containing links."""
        with open(json_file, "r", encoding="utf-8") as file:
            return json.load(file)
    
    def scrape_html(self, url):
        """Scrape the entire HTML content of the given URL."""
        try:
            # Use session for request to maintain cookies
            response = self.session.get(url)
            
            if response.status_code == 200:
                return response.text
            else:
                print(f"Failed to retrieve {url}. Status Code: {response.status_code}")
                return None
        except Exception as e:
            print(f"Error scraping {url}: {str(e)}")
            return None
    
    def download_image(self, img_url, save_folder, base_url):
        """Download an image from the given URL, but skip if in skip list."""
        try:
            # Convert relative URLs to absolute URLs
            if not img_url.startswith(('http://', 'https://')):
                img_url = urljoin(base_url, img_url)
            
            # Parse the URL to get filename first
            parsed_url = urlparse(img_url)
            filename = os.path.basename(parsed_url.path)
            
            # If no filename, generate one based on URL (we need to make a request to get content type)
            if not filename or '.' not in filename:
                try:
                    # Make a HEAD request to get content type without downloading full image
                    head_response = self.session.head(img_url, timeout=10)
                    content_type = head_response.headers.get('content-type', '')
                    extension = mimetypes.guess_extension(content_type) or '.jpg'
                    filename = f"image_{hash(img_url) % 10000}{extension}"
                except:
                    filename = f"image_{hash(img_url) % 10000}.jpg"
            
            # Ensure we have a valid filename
            if not filename:
                filename = f"image_{hash(img_url) % 10000}.jpg"
            
            # Check if this image should be skipped
            if filename in self.skip_images:
                print(f"  ⏭️  Skipping image (in skip list): {filename}")
                return None
            
            print(f"  Downloading image: {img_url}")
            
            # Get image content
            response = self.session.get(img_url, timeout=30)
            if response.status_code == 200:
                # Create images folder
                images_folder = os.path.join(save_folder, "images")
                os.makedirs(images_folder, exist_ok=True)
                
                # Save image
                image_path = os.path.join(images_folder, filename)
                with open(image_path, 'wb') as f:
                    f.write(response.content)
                
                print(f"  ✓ Image saved: {filename}")
                return f"images/{filename}"  # Return relative path for HTML update
            else:
                print(f"  ✗ Failed to download image: {img_url} (Status: {response.status_code})")
                return None
                
        except Exception as e:
            print(f"  ✗ Error downloading image {img_url}: {str(e)}")
            return None
    
    def process_images_in_html(self, html_content, base_url, save_folder):
        """Find and download only images in the HTML body, then update HTML with local paths."""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Find the article tag first
        body_tag = soup.find('body')
        if not body_tag:
            print("  No <body> tag found in HTML - checking entire document")
            # Fallback to entire document if no body tag exists
            search_area = soup
        else:
            print("  Found <body> tag - will only process images within body")
            search_area = body_tag
        
        # Find all image tags within the body (or entire document if no body)
        img_tags = search_area.find_all('img')
        
        if not img_tags:
            print("  No images found in HTML body")
            return html_content
        
        print(f"  Found {len(img_tags)} images in body to download")
        if self.skip_images:
            print(f"  Skip list contains {len(self.skip_images)} images to avoid")
        
        # Download each image and update src attribute
        downloaded_count = 0
        skipped_count = 0
        
        for i, img_tag in enumerate(img_tags, 1):
            src = img_tag.get('src')
            if not src:
                continue
                
            print(f"  Processing image {i}/{len(img_tags)}")
            
            # Download image (will check skip list internally)
            local_path = self.download_image(src, save_folder, base_url)
            
            if local_path:
                # Update the src attribute to point to local file
                img_tag['src'] = local_path
                # Add a data attribute to keep track of original URL
                img_tag['data-original-src'] = src
                downloaded_count += 1
            else:
                # Check if it was skipped or failed
                parsed_url = urlparse(src if src.startswith(('http://', 'https://')) else urljoin(base_url, src))
                filename = os.path.basename(parsed_url.path)
                if not filename or '.' not in filename:
                    filename = f"image_{hash(src) % 10000}.jpg"
                
                if filename in self.skip_images:
                    skipped_count += 1
            
            # Small delay between image downloads
            time.sleep(random.uniform(1, 3))
        
        print(f"  Image processing complete: {downloaded_count} downloaded, {skipped_count} skipped")
        return str(soup)
    
    def save_html(self, content, folder_name, file_name, base_url=None, download_images=True):
        """Save the scraped HTML content to an HTML file inside the specified folder."""
        # Create the full path structure
        base_path = os.path.join("data", "html", folder_name, file_name)
        os.makedirs(base_path, exist_ok=True)  # Create the folder if it doesn't exist
        
        # Process images if requested
        if download_images and base_url:
            print(f"  Processing images in body only...")
            content = self.process_images_in_html(content, base_url, base_path)
        
        # Save HTML file
        file_path = os.path.join(base_path, f"{file_name}.html")
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(content)
        
        print(f"Saved: {file_path}")
    
    def process_json_file(self, json_file, download_images=True):
        """Process the JSON file and scrape HTML content with optional image downloading."""
        # Load JSON data
        data = self.load_json(json_file)
        folder_name = os.path.splitext(os.path.basename(json_file))[0]  # Folder name from JSON file name
        
        total_items = len(data)
        processed = 0
        
        print(f"Starting to process {total_items} legislations...")
        if download_images:
            print("Image downloading is ENABLED (body images only)")
            if self.skip_images:
                print(f"Skip list active with {len(self.skip_images)} images to avoid")
        else:
            print("Image downloading is DISABLED")
        
        # Process each legislation entry
        for key, entry in data.items():
            processed += 1
            url = entry.get("link_to_text")
            
            if url:
                print(f"\n[{processed}/{total_items}] Processing {key}...")
                print(f"URL: {url}")
                
                # Check if file already exists
                file_path = os.path.join("data", "html", folder_name, key, f"{key}.html")
                if os.path.exists(file_path):
                    print(f"File already exists, skipping: {file_path}")
                    continue
                
                html_content = self.scrape_html(url)
                if html_content:
                    self.save_html(html_content, folder_name, key, url, download_images)
                    
                    # Random delay between requests
                    delay = random.uniform(5, 600)
                    print(f"Waiting {delay:.2f} seconds before the next request...")
                    time.sleep(delay)
                else:
                    print(f"Failed to scrape content for {key}")
            else:
                print(f"No URL found for {key}")
        
        print(f"\nCompleted processing {processed} items.")

# Utility function to test individual URLs
def test_single_url(url, headers=None, cookies=None, download_images=True, skip_images=None):
    """Test scraping a single URL to debug issues."""
    print(f"Testing URL: {url}")
    
    scraper = MainHTMLScraper(headers=headers, cookies=cookies, skip_images=skip_images)
    html_content = scraper.scrape_html(url)
    
    if html_content:
        print("Successfully scraped content!")
        
        # Parse and check for section 7
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Look for section 7
        section_7 = soup.find('font', {'size': '4', 'face': 'Times New Roman'}, text='7')
        if section_7:
            print("✓ Section 7 found in HTML!")
        else:
            print("✗ Section 7 not found in HTML structure")
        
        # Check for images in body specifically
        body_tag = soup.find('body')
        if body_tag:
            img_tags = body_tag.find_all('img')
            print(f"Found {len(img_tags)} images in the HTML body")
        else:
            img_tags = soup.find_all('img')
            print(f"No body tag found. Found {len(img_tags)} images in entire HTML")
        
        if download_images and img_tags:
            # Test image downloading
            test_folder = "test_output"
            os.makedirs(test_folder, exist_ok=True)
            updated_html = scraper.process_images_in_html(html_content, url, test_folder)
            
            # Save test HTML with updated image paths
            with open(os.path.join(test_folder, "test.html"), "w", encoding="utf-8") as f:
                f.write(updated_html)
            print(f"Test HTML with images saved to {test_folder}/test.html")
        
        return html_content
    else:
        print("Failed to scrape content")
        return None
