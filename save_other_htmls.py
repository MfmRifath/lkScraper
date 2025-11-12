import os
import json
import requests
from bs4 import BeautifulSoup
import time
import random
from urllib.parse import urljoin, urlparse
import mimetypes

class AmendmentScheduleHTMLScraper:
    def __init__(self, headers, cookies, data_folder, html_folder, download_images=True):
        self.headers = headers
        self.cookies = cookies
        self.data_folder = data_folder
        self.html_folder = html_folder
        self.download_images = download_images
        
        # Create the HTML output base directory if it doesn't exist
        os.makedirs(self.html_folder, exist_ok=True)
        
        # Initialize session for better connection management
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.cookies.update(self.cookies)

    def download_image(self, img_url, save_folder, base_url):
        """Download an image from the given URL."""
        # DISABLED: Image downloading is disabled to skip image downloads
        print(f"    ⏭️  Skipping image download (disabled): {img_url}")
        return None

        # ============================================================
        # ORIGINAL CODE BELOW (COMMENTED OUT TO DISABLE IMAGE DOWNLOADS)
        # ============================================================
        # try:
        #     # Convert relative URLs to absolute URLs
        #     if not img_url.startswith(('http://', 'https://')):
        #         img_url = urljoin(base_url, img_url)
        #
        #     print(f"    Downloading image: {img_url}")
        #
        #     # Get image content with timeout
        #     response = self.session.get(img_url, timeout=30)
        #     if response.status_code == 200:
        #         # Parse the URL to get filename
        #         parsed_url = urlparse(img_url)
        #         filename = os.path.basename(parsed_url.path)
        #
        #         # If no filename, generate one based on URL
        #         if not filename or '.' not in filename:
        #             # Try to get extension from content type
        #             content_type = response.headers.get('content-type', '')
        #             extension = mimetypes.guess_extension(content_type) or '.jpg'
        #             filename = f"image_{abs(hash(img_url)) % 10000}{extension}"
        #
        #         # Ensure we have a valid filename
        #         if not filename:
        #             filename = f"image_{abs(hash(img_url)) % 10000}.jpg"
        #
        #         # Create images folder
        #         images_folder = os.path.join(save_folder, "images")
        #         os.makedirs(images_folder, exist_ok=True)
        #
        #         # Save image
        #         image_path = os.path.join(images_folder, filename)
        #         with open(image_path, 'wb') as f:
        #             f.write(response.content)
        #
        #         print(f"    ✓ Image saved: {filename}")
        #         return f"images/{filename}"  # Return relative path for HTML update
        #     else:
        #         print(f"    ✗ Failed to download image: {img_url} (Status: {response.status_code})")
        #         return None
        #
        # except Exception as e:
        #     print(f"    ✗ Error downloading image {img_url}: {str(e)}")
        #     return None

    def process_images_in_html(self, html_content, base_url, save_folder):
        """Find and download all images in HTML content, then update HTML with local paths."""
        if not self.download_images:
            return html_content
            
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Find all image tags
        img_tags = soup.find_all('img')
        
        if not img_tags:
            print("    No images found in HTML")
            return html_content
        
        print(f"    Found {len(img_tags)} images to download")
        
        # Download each image and update src attribute
        for i, img_tag in enumerate(img_tags, 1):
            src = img_tag.get('src')
            if not src:
                continue
                
            print(f"    Processing image {i}/{len(img_tags)}")
            
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

    def save_schedules_html(self, legislation_data, json_file_name, key):
        try:
            # Check if the key exists in the legislation data
            if key not in legislation_data:
                print(f"Key '{key}' not found in {json_file_name}")
                return
                
            # Get the list of schedule parts
            schedule_parts = legislation_data[key]
            
            # Create folder name from JSON file name (without .json extension)
            folder_name = json_file_name.replace('.json', '')
            
            # Create a corresponding folder in the 'html' directory
            html_folder_path = os.path.join(self.html_folder, folder_name, 'schedules')
            os.makedirs(html_folder_path, exist_ok=True)
            
            # Loop through each schedule part
            for schedule_part in schedule_parts:
                url = schedule_part.get('url')
                title = schedule_part.get('title')
                
                if url and title:
                    self.scrape_html_content(url, html_folder_path, title)
                    # Add a random delay between requests
                    time.sleep(random.uniform(5, 15))
                else:
                    print(f"Missing URL or title for schedule in {json_file_name}")
        except Exception as e:
            print(f"Error processing schedules for {json_file_name}: {e}")

    def scrape_html_content(self, link, folder_path, file_name):
        try:
            # Replace spaces with underscores in the file name
            safe_file_name = file_name.replace(" ", "_")
            
            # Create individual folder for this item (for images organization)
            item_folder_path = os.path.join(folder_path, safe_file_name)
            os.makedirs(item_folder_path, exist_ok=True)

            print(f"Scraping: {safe_file_name} from {link}")
            
            # Send a request to fetch the HTML content using session
            response = self.session.get(link)
            
            if response.status_code == 200:
                html_content = response.text
                
                # Process images if enabled
                if self.download_images:
                    print(f"  Processing images for {safe_file_name}...")
                    html_content = self.process_images_in_html(html_content, link, item_folder_path)
                
                # Save the HTML content to a file
                file_path = os.path.join(item_folder_path, f"{safe_file_name}.html")
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)
                print(f"Saved HTML for {safe_file_name} at {file_path}")
                
            elif response.status_code == 429:
                print(f"Rate limited when accessing {link}. Waiting longer before retry.")
                time.sleep(60)  # Wait longer if rate limited
                # Retry once after waiting
                response = self.session.get(link)
                if response.status_code == 200:
                    html_content = response.text
                    
                    if self.download_images:
                        print(f"  Processing images for {safe_file_name}...")
                        html_content = self.process_images_in_html(html_content, link, item_folder_path)
                    
                    file_path = os.path.join(item_folder_path, f"{safe_file_name}.html")
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(html_content)
                    print(f"Saved HTML for {safe_file_name} at {file_path} (after retry)")
                else:
                    print(f"Still failed after retry. Status code: {response.status_code}")
            else:
                print(f"Failed to retrieve content for {safe_file_name} from {link}. Status code: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            print(f"Request error occurred while scraping {safe_file_name}: {e}")
        except Exception as e:
            print(f"Error occurred while scraping {safe_file_name}: {e}")

    def extract_all_amendments(self, data):
        """Recursively extract all amendment links from any level in the legislation data."""
        amendments = []
        if isinstance(data, dict):
            # If there's an 'amendment' key, extract it
            if 'amendment' in data and isinstance(data['amendment'], list):
                amendments.extend(data['amendment'])
            
            # Recurse into all values
            for value in data.values():
                amendments.extend(self.extract_all_amendments(value))
        elif isinstance(data, list):
            for item in data:
                amendments.extend(self.extract_all_amendments(item))
        return amendments
    
    def save_amendment_html(self, legislation_data, json_file_name):
        try:
            print(f"🔍 Searching for amendments in {json_file_name}...")
            
            # Collect all amendment entries recursively
            amendments = self.extract_all_amendments(legislation_data)
            print(f"  ➜ Found {len(amendments)} amendment(s) total")
            
            # Create folder name from JSON file name (without .json extension)
            folder_name = json_file_name.replace('.json', '')
            html_folder_path = os.path.join(self.html_folder, folder_name, 'amendment')
            os.makedirs(html_folder_path, exist_ok=True)
            
            # Loop through each amendment link
            for amendment in amendments:
                amendment_link = amendment.get('link') or amendment.get('amendment_link')
                text = amendment.get('text') or amendment.get('number')
                
                if amendment_link and text:
                    safe_name = text.replace("[", "").replace("]", "").replace(" ", "_")
                    self.scrape_html_content(amendment_link, html_folder_path, safe_name)
                    time.sleep(random.uniform(5, 15))
                else:
                    print(f"  ⚠️ Missing amendment link or text: {amendment}")
                    
        except Exception as e:
            print(f"Error processing amendments for {json_file_name}: {e}")

    def process_legislation_files(self):
        # Check if the data folder path exists
        if not os.path.exists(self.data_folder):
            print(f"Error: The folder {self.data_folder} does not exist.")
            return
        
        print(f"Starting to process legislation files from {self.data_folder}")
        if self.download_images:
            print("Image downloading is ENABLED")
        else:
            print("Image downloading is DISABLED")
        
        # Process each JSON file directly in the data folder
        for json_file in os.listdir(self.data_folder):
            if json_file.endswith('.json'):
                json_file_path = os.path.join(self.data_folder, json_file)
                
                print(f"\n{'='*60}")
                print(f"Processing file: {json_file}")
                print(f"{'='*60}")
                
                try:
                    # Read the JSON file
                    with open(json_file_path, 'r', encoding='utf-8') as f:
                        legislation_data = json.load(f)
                    
                    # Process the legislation data
                    print("\nProcessing amendments...")
                    self.save_amendment_html(legislation_data, json_file)
                    
                    print("\nProcessing schedule parts...")
                    self.save_schedules_html(legislation_data, json_file, 'schedule_parts')
                    
                    print("\nProcessing schedules...")
                    self.save_schedules_html(legislation_data, json_file, 'schedules')
                    
                    # Add a delay between processing different files
                    time.sleep(random.uniform(5, 15))
                    
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
        
        for root, dirs, files in os.walk(self.html_folder):
            for file in files:
                if file.endswith('.html'):
                    total_html_files += 1
                elif file.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp')):
                    total_images += 1
            
            if 'images' in dirs:
                total_folders += 1
        
        stats = f"""
Statistics:
- Total HTML files: {total_html_files}
- Total images downloaded: {total_images}
- Folders with images: {total_folders}
- Output directory: {self.html_folder}
        """
        return stats.strip()

# Example usage function
def main():
    """Example usage of the enhanced AmendmentScheduleHTMLScraper."""
    
    # Configuration
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    cookies = {}  # Add your cookies if needed
    
    data_folder = "path/to/your/json/files"
    html_folder = "path/to/output/html"
    
    # Example 1: With image downloading (default)
    scraper = AmendmentScheduleHTMLScraper(
        headers=headers,
        cookies=cookies,
        data_folder=data_folder,
        html_folder=html_folder,
        download_images=True
    )
    
    # Process all legislation files
    scraper.process_legislation_files()
    
    # Get statistics
    print(scraper.get_statistics())
    
    # Example 2: Without image downloading
    # scraper_no_images = AmendmentScheduleHTMLScraper(
    #     headers=headers,
    #     cookies=cookies,
    #     data_folder=data_folder,
    #     html_folder=html_folder,
    #     download_images=False
    # )
    # scraper_no_images.process_legislation_files()

