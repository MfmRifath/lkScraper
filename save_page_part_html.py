import os
import json
import requests
import time
import random
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import mimetypes

class ExtendedPageScraper:
    def __init__(self, headers=None, cookies=None, download_images=True):
        """Initialize the ExtendedPageScraper with optional headers, cookies, and image downloading."""
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.download_images = download_images
        self.data_folder = None
        self.html_folder = None
        
        # Initialize session for better connection management
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.cookies.update(self.cookies)
    
    def set_paths(self, data_folder, html_folder):
        """Set the paths for the data and HTML folders."""
        self.data_folder = data_folder
        self.html_folder = html_folder
        
        # Create the HTML output base directory if it doesn't exist
        os.makedirs(html_folder, exist_ok=True)
    
    def download_image(self, img_url, save_folder, base_url):
        """Download an image from the given URL."""
        try:
            # Convert relative URLs to absolute URLs
            if not img_url.startswith(('http://', 'https://')):
                img_url = urljoin(base_url, img_url)
            
            print(f"      Downloading image: {img_url}")
            
            # Get image content with timeout
            response = self.session.get(img_url, timeout=30)
            if response.status_code == 200:
                # Parse the URL to get filename
                parsed_url = urlparse(img_url)
                filename = os.path.basename(parsed_url.path)
                
                # If no filename, generate one based on URL
                if not filename or '.' not in filename:
                    # Try to get extension from content type
                    content_type = response.headers.get('content-type', '')
                    extension = mimetypes.guess_extension(content_type) or '.jpg'
                    filename = f"image_{abs(hash(img_url)) % 10000}{extension}"
                
                # Ensure we have a valid filename
                if not filename:
                    filename = f"image_{abs(hash(img_url)) % 10000}.jpg"
                
                # Create images folder
                images_folder = os.path.join(save_folder, "images")
                os.makedirs(images_folder, exist_ok=True)
                
                # Save image
                image_path = os.path.join(images_folder, filename)
                with open(image_path, 'wb') as f:
                    f.write(response.content)
                
                print(f"      ✓ Image saved: {filename}")
                return f"images/{filename}"  # Return relative path for HTML update
            else:
                print(f"      ✗ Failed to download image: {img_url} (Status: {response.status_code})")
                return None
                
        except Exception as e:
            print(f"      ✗ Error downloading image {img_url}: {str(e)}")
            return None

    def process_images_in_html(self, html_content, base_url, save_folder):
        """Find and download all images in HTML content, then update HTML with local paths."""
        if not self.download_images:
            return html_content
            
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Find all image tags
        img_tags = soup.find_all('img')
        
        if not img_tags:
            print("      No images found in HTML")
            return html_content
        
        print(f"      Found {len(img_tags)} images to download")
        
        # Download each image and update src attribute
        for i, img_tag in enumerate(img_tags, 1):
            src = img_tag.get('src')
            if not src:
                continue
                
            print(f"      Processing image {i}/{len(img_tags)}")
            
            # Download image
            local_path = self.download_image(src, save_folder, base_url)
            
            if local_path:
                # Update the src attribute to point to local file
                img_tag['src'] = local_path
                # Add a data attribute to keep track of original URL
                img_tag['data-original-src'] = src
            
            # Small delay between image downloads
            time.sleep(random.uniform(1, 3))
        
        return str(soup)

    def save_extended_page_html(self, legislation_data, json_file_name):
        """Save HTML content for all connected pages in a legislation."""
        try:
            # Get the list of connected pages
            if 'connected_pages' in legislation_data:
                extended_pages = legislation_data['connected_pages']
                
                # Create folder name from JSON file name (without .json extension)
                folder_name = json_file_name.replace('.json', '')
                
                # Create a corresponding folder in the 'html' directory with a 'parts' subfolder
                html_folder_path = os.path.join(self.html_folder, folder_name, 'parts')
                os.makedirs(html_folder_path, exist_ok=True)
                
                # Skip the first item if there are any pages
                if len(extended_pages) > 0:
                    extended_pages = extended_pages[1:]
                    print(f"Processing {len(extended_pages)} connected pages for {folder_name}")
                    if self.download_images:
                        print("  Image downloading is ENABLED for this legislation")
                else:
                    print(f"No connected pages found for {folder_name}")
                    return
                
                # Loop through each extended page
                for i, extended_page in enumerate(extended_pages, 1):
                    url = extended_page.get('url')
                    index = extended_page.get('index')
                    
                    if url and index is not None:
                        print(f"  [{i}/{len(extended_pages)}] Processing page {index}...")
                        self.scrape_extended_html(url, html_folder_path, folder_name, index)
                        # Add a random delay between requests
                        time.sleep(random.uniform(5, 15))
                    else:
                        print(f"  Missing URL or index for connected page in {json_file_name}")
            else:
                print(f"Missing 'connected_pages' key in {json_file_name}")
        except Exception as e:
            print(f"Error processing {json_file_name}: {e}")
    
    def scrape_extended_html(self, link, folder_path, folder_name, index):
        """Scrape HTML content for a specific extended page."""
        try:
            # Create filename and individual folder for this page
            number = f"{folder_name}_{index}"
            
            # Create individual folder for this page (for images organization)
            page_folder_path = os.path.join(folder_path, number)
            os.makedirs(page_folder_path, exist_ok=True)

            print(f"    Scraping: {number} from {link}")

            # Send a request to fetch the HTML content using session
            response = self.session.get(link)
            
            # Check for various HTTP status codes
            if response.status_code == 200:
                html_content = response.text
                
                # Process images if enabled
                if self.download_images:
                    print(f"    Processing images for {number}...")
                    html_content = self.process_images_in_html(html_content, link, page_folder_path)
                
                # Save the HTML content to a file
                file_path = os.path.join(page_folder_path, f"{number}.html")
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                print(f"    ✓ Saved HTML for {number} at {file_path}")
                
            elif response.status_code == 429:
                print(f"    Rate limited when accessing {link}. Waiting longer before retry.")
                time.sleep(60)  # Wait longer if rate limited
                
                # Retry once after waiting
                response = self.session.get(link)
                if response.status_code == 200:
                    html_content = response.text
                    
                    if self.download_images:
                        print(f"    Processing images for {number} (after retry)...")
                        html_content = self.process_images_in_html(html_content, link, page_folder_path)
                    
                    file_path = os.path.join(page_folder_path, f"{number}.html")
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(html_content)
                    print(f"    ✓ Saved HTML for {number} at {file_path} (after retry)")
                else:
                    print(f"    Still failed after retry. Status code: {response.status_code}")
            else:
                print(f"    Failed to retrieve content for {number} from {link}. Status code: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            print(f"    Request error occurred while scraping {number}: {e}")
        except Exception as e:
            print(f"    Error occurred while scraping {number}: {e}")
    
    def process_legislation_files(self):
        """Process each JSON file in the data folder."""
        # Check if the path exists
        if not os.path.exists(self.data_folder):
            print(f"Error: The folder {self.data_folder} does not exist.")
            return
        
        # Get all JSON files
        json_files = [f for f in os.listdir(self.data_folder) if f.endswith('.json')]
        
        print(f"Starting to process {len(json_files)} JSON files from {self.data_folder}")
        if self.download_images:
            print("Image downloading is ENABLED globally")
        else:
            print("Image downloading is DISABLED globally")
        
        # Process each JSON file directly in the data folder
        for i, json_file in enumerate(json_files, 1):
            json_file_path = os.path.join(self.data_folder, json_file)
            
            print(f"\n{'='*60}")
            print(f"[{i}/{len(json_files)}] Processing file: {json_file}")
            print(f"{'='*60}")
            
            try:
                # Read the JSON file
                with open(json_file_path, 'r', encoding='utf-8') as f:
                    legislation_data = json.load(f)
                
                # Process the legislation data
                self.save_extended_page_html(legislation_data, json_file)
                
                # Add a delay between processing different files
                time.sleep(random.uniform(2, 5))
                
            except json.JSONDecodeError:
                print(f"Error: {json_file} is not a valid JSON file.")
            except Exception as e:
                print(f"Error processing {json_file}: {e}")
        
        print(f"\n{'='*60}")
        print("Finished processing all legislation files")
        print(f"{'='*60}")
    
    def get_statistics(self):
        """Get statistics about processed files and images."""
        if not os.path.exists(self.html_folder):
            return "No HTML folder found"
        
        total_html_files = 0
        total_images = 0
        total_folders = 0
        total_parts_folders = 0
        
        for root, dirs, files in os.walk(self.html_folder):
            # Count parts folders
            if 'parts' in os.path.basename(root):
                total_parts_folders += 1
            
            for file in files:
                if file.endswith('.html'):
                    total_html_files += 1
                elif file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp')):
                    total_images += 1
            
            if 'images' in dirs:
                total_folders += 1
        
        stats = f"""
Extended Page Scraper Statistics:
- Total HTML files: {total_html_files}
- Total images downloaded: {total_images}
- Folders with images: {total_folders}
- Parts folders processed: {total_parts_folders}
- Output directory: {self.html_folder}
        """
        return stats.strip()
    
    def test_single_url(self, url, test_name="test_page"):
        """Test scraping a single URL with image downloading."""
        print(f"Testing single URL: {url}")
        
        # Create test output folder
        test_folder = os.path.join(self.html_folder, "test_output", test_name)
        os.makedirs(test_folder, exist_ok=True)
        
        try:
            response = self.session.get(url)
            if response.status_code == 200:
                html_content = response.text
                
                if self.download_images:
                    print("Processing images...")
                    html_content = self.process_images_in_html(html_content, url, test_folder)
                
                # Save test HTML
                file_path = os.path.join(test_folder, f"{test_name}.html")
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                
                print(f"✓ Test completed successfully. File saved: {file_path}")
                
                # Show basic stats
                soup = BeautifulSoup(html_content, 'html.parser')
                img_count = len(soup.find_all('img'))
                print(f"  - HTML content length: {len(html_content)} characters")
                print(f"  - Images found: {img_count}")
                
            else:
                print(f"✗ Failed to retrieve URL. Status code: {response.status_code}")
                
        except Exception as e:
            print(f"✗ Error testing URL: {str(e)}")

# Example usage function
def main():
    """Example usage of the enhanced ExtendedPageScraper."""
    
    # Configuration
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    cookies = {}  # Add your cookies if needed
    
    # Example 1: With image downloading (default)
    scraper = ExtendedPageScraper(
        headers=headers,
        cookies=cookies,
        download_images=True
    )
    
    # Set paths
    scraper.set_paths(
        data_folder="path/to/your/json/files",
        html_folder="path/to/output/html"
    )
    
    # Process all legislation files
    scraper.process_legislation_files()
    
    # Get statistics
    print(scraper.get_statistics())
    
    # Example 2: Test a single URL
    # scraper.test_single_url("https://example.com/legislation", "example_test")
    
    # Example 3: Without image downloading
    # scraper_no_images = ExtendedPageScraper(
    #     headers=headers,
    #     cookies=cookies,
    #     download_images=False
    # )
    # scraper_no_images.set_paths(data_folder, html_folder)
    # scraper_no_images.process_legislation_files()

