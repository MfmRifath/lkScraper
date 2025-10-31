import json
import re
import base64
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import os
import mimetypes
import boto3
from botocore.exceptions import ClientError

class SchedulePDFProcessor:
    def __init__(self, base_input_dir, output_directory, s3_bucket_name=None, s3_region='us-east-1', s3_base_path='schedules'):
        """
        Initialize the SchedulePDFProcessor with Playwright and image support.

        Args:
            base_input_dir: Directory containing legislation folders with schedules subdirectories
                           (e.g., "data/html" which contains "legislation_A", "legislation_B", etc.)
            output_directory: Directory containing legislation folders where PDFs will be saved
                             (e.g., "data/legislations" which contains "legislation_A", "legislation_B", etc.)
            s3_bucket_name: S3 bucket name for uploading PDFs (optional)
            s3_region: AWS region for S3 bucket (default: 'us-east-1')
            s3_base_path: Base path in S3 bucket for storing PDFs (default: 'schedules')
        """
        self.base_input_dir = base_input_dir
        self.output_directory = output_directory
        self.debug_mode = True

        # S3 Configuration
        self.s3_bucket_name = s3_bucket_name
        self.s3_region = s3_region
        self.s3_base_path = s3_base_path.strip('/')
        self.s3_client = None

        # Initialize S3 client if bucket name is provided
        if self.s3_bucket_name:
            try:
                self.s3_client = boto3.client('s3', region_name=self.s3_region)
                if self.debug_mode:
                    print(f"S3 client initialized for bucket: {self.s3_bucket_name}")
            except Exception as e:
                print(f"Warning: Could not initialize S3 client: {e}")
                print("PDFs will be saved locally only.")
                self.s3_client = None

        # Supported image extensions
        self.image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.tiff', '.tif'}

        # PDF generation options for Playwright
        self.pdf_options = {
            'format': 'A4',
            'margin': {
                'top': '0.75in',
                'right': '0.75in',
                'bottom': '0.75in',
                'left': '0.75in'
            },
            'print_background': True,
            'prefer_css_page_size': True
        }

        # Initialize Playwright (will be done per operation to avoid connection issues)
        self.playwright = None
        self.browser = None

        # Store metadata for schedules (populated during processing)
        self.schedules_metadata = {}

    def upload_pdf_to_s3(self, pdf_file_path, legislation_name, pdf_filename):
        """
        Upload a PDF file to S3 bucket.

        Args:
            pdf_file_path: Local path to the PDF file
            legislation_name: Name of the legislation folder (e.g., 'legislation_A')
            pdf_filename: Name of the PDF file

        Returns:
            S3 URL if successful, None otherwise
        """
        if not self.s3_client or not self.s3_bucket_name:
            if self.debug_mode:
                print(f"      S3 upload skipped (S3 not configured)")
            return None

        try:
            # Construct S3 key (path in bucket)
            s3_key = f"{self.s3_base_path}/{legislation_name}/{pdf_filename}"

            # Upload file to S3
            with open(pdf_file_path, 'rb') as pdf_file:
                self.s3_client.upload_fileobj(
                    pdf_file,
                    self.s3_bucket_name,
                    s3_key,
                    ExtraArgs={
                        'ContentType': 'application/pdf',
                        'ACL': 'public-read'  # Make PDF publicly accessible
                    }
                )

            # Construct S3 URL
            s3_url = f"https://{self.s3_bucket_name}.s3.{self.s3_region}.amazonaws.com/{s3_key}"

            if self.debug_mode:
                print(f"      Uploaded to S3: {s3_url}")

            return s3_url

        except ClientError as e:
            if self.debug_mode:
                print(f"      Error uploading to S3: {e}")
            return None
        except Exception as e:
            if self.debug_mode:
                print(f"      Unexpected error during S3 upload: {e}")
            return None

    def encode_image_to_base64(self, image_path):
        """Convert an image file to base64 data URL for embedding in HTML."""
        try:
            if not os.path.exists(image_path):
                if self.debug_mode:
                    print(f"        Image file not found: {image_path}")
                return None

            # Get MIME type
            mime_type, _ = mimetypes.guess_type(image_path)
            if not mime_type or not mime_type.startswith('image/'):
                mime_type = 'image/jpeg'  # Default fallback

            # Read and encode image
            with open(image_path, 'rb') as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                data_url = f"data:{mime_type};base64,{encoded_string}"

                if self.debug_mode:
                    print(f"        Successfully encoded image: {os.path.basename(image_path)} ({mime_type})")

                return data_url

        except Exception as e:
            if self.debug_mode:
                print(f"        Error encoding image {image_path}: {e}")
            return None

    def create_html_from_images(self, image_files, title="Schedule"):
        """
        Create an HTML document from a list of image files.

        Args:
            image_files: List of image file paths
            title: Title for the HTML document

        Returns:
            HTML content as string
        """
        if not image_files:
            return None

        # Sort image files by the last two digits before the file extension
        def extract_last_two_digits(filename):
            # Extract filename without extension
            name_without_ext = filename.stem
            # Extract last two characters that are digits
            match = re.search(r'(\d{2})$', name_without_ext)
            if match:
                return int(match.group(1))
            # If no two-digit number at end, try single digit
            match = re.search(r'(\d)$', name_without_ext)
            if match:
                return int(match.group(1))
            # Default to 0 if no digits found
            return 0

        image_files = sorted(image_files, key=extract_last_two_digits)
        
        html_parts = [
            '<!DOCTYPE html>',
            '<html>',
            '<head>',
            '<meta charset="UTF-8">',
            f'<title>{title}</title>',
            '<style>',
            '    body {',
            '        font-family: Arial, sans-serif;',
            '        margin: 0;',
            '        padding: 20px;',
            '        text-align: center;',
            '    }',
            '    .image-container {',
            '        margin: 20px 0;',
            '        page-break-inside: avoid;',
            '        page-break-after: auto;',
            '    }',
            '    img {',
            '        max-width: 100%;',
            '        height: auto;',
            '        display: block;',
            '        margin: 0 auto;',
            '        border: 1px solid #ddd;',
            '        box-shadow: 0 2px 4px rgba(0,0,0,0.1);',
            '    }',
            '    .image-title {',
            '        font-size: 10pt;',
            '        color: #666;',
            '        margin-top: 10px;',
            '        font-style: italic;',
            '    }',
            '    h1 {',
            '        font-size: 18pt;',
            '        margin-bottom: 30px;',
            '        color: #333;',
            '    }',
            '</style>',
            '</head>',
            '<body>',
            f'<h1>{title}</h1>'
        ]
        
        # Add each image
        for i, image_file in enumerate(image_files, 1):
            data_url = self.encode_image_to_base64(str(image_file))
            if data_url:
                html_parts.append('<div class="image-container">')
                html_parts.append(f'    <img src="{data_url}" alt="{image_file.name}" />')
                html_parts.append('</div>')
                
                if self.debug_mode:
                    print(f"        Added image {i}/{len(image_files)}: {image_file.name}")
        
        html_parts.extend([
            '</body>',
            '</html>'
        ])
        
        return '\n'.join(html_parts)

    def find_image_only_schedules(self, schedules_dir):
        """
        Find schedule folders or directories that contain only images (no HTML files).
        Recursively searches for image directories at any depth.

        Returns:
            List of tuples: (folder_path, image_files, schedule_name)
        """
        image_only_schedules = []

        if not schedules_dir.exists():
            return image_only_schedules

        # Check direct image files in schedules_dir
        direct_images = [f for f in schedules_dir.iterdir()
                        if f.is_file() and f.suffix.lower() in self.image_extensions]

        if direct_images:
            image_only_schedules.append((schedules_dir, direct_images, "images"))
            if self.debug_mode:
                print(f"      Found {len(direct_images)} direct image files in schedules directory")

        # Recursively search for all directories named 'images' or 'Images'
        def find_image_dirs(base_path, depth=0, max_depth=10):
            """Recursively find directories containing images."""
            image_dirs = []

            if depth > max_depth:
                return image_dirs

            try:
                for item in base_path.iterdir():
                    if not item.is_dir():
                        continue

                    # Check if this is an 'images' or 'Images' directory
                    if item.name.lower() == 'images':
                        # Check if there are HTML files in the same directory or parent
                        html_in_same = list(item.glob("*.html")) + list(item.glob("*.htm"))
                        html_in_parent = list(item.parent.glob("*.html")) + list(item.parent.glob("*.htm"))

                        # Only process if no HTML files found
                        if not html_in_same and not html_in_parent:
                            image_files = [f for f in item.iterdir()
                                         if f.is_file() and f.suffix.lower() in self.image_extensions]

                            if image_files:
                                image_dirs.append((item, image_files))
                                if self.debug_mode:
                                    rel_path = item.relative_to(schedules_dir)
                                    print(f"      Found image-only directory at depth {depth}: {rel_path} ({len(image_files)} images)")

                    # Recursively search subdirectories
                    image_dirs.extend(find_image_dirs(item, depth + 1, max_depth))

            except PermissionError:
                pass

            return image_dirs

        # Find all image directories recursively
        found_image_dirs = find_image_dirs(schedules_dir)

        # Add found directories to results
        for img_path, image_files in found_image_dirs:
            rel_path = img_path.relative_to(schedules_dir)
            schedule_name = str(rel_path).replace(os.sep, '_')
            image_only_schedules.append((img_path, image_files, schedule_name))

        # Also check subdirectories for image-only folders (not named 'images')
        for item in schedules_dir.iterdir():
            if not item.is_dir() or item.name.lower() == 'images':
                continue

            # Check if folder has HTML files
            html_files = list(item.glob("*.html")) + list(item.glob("*.htm"))

            # If no HTML files, check for images directly in this folder
            if not html_files:
                image_files = [f for f in item.iterdir()
                             if f.is_file() and f.suffix.lower() in self.image_extensions]

                if image_files:
                    # Make sure we haven't already added this
                    already_added = any(img_path == item for img_path, _, _ in image_only_schedules)
                    if not already_added:
                        image_only_schedules.append((item, image_files, item.name))
                        if self.debug_mode:
                            print(f"      Found image-only schedule folder: {item.name} ({len(image_files)} images)")

        return image_only_schedules

    def process_images_in_html(self, html_content, html_file_path):
        """Process images in HTML content for PDF conversion."""
        soup = BeautifulSoup(html_content, 'html.parser')
        html_dir = Path(html_file_path).parent
        
        # Find all image tags
        img_tags = soup.find_all('img')
        
        if not img_tags:
            if self.debug_mode:
                print(f"        No images found in HTML")
            return str(soup)
        
        if self.debug_mode:
            print(f"        Found {len(img_tags)} images to process")
        
        images_processed = 0
        images_embedded = 0
        
        for i, img_tag in enumerate(img_tags, 1):
            src = img_tag.get('src')
            if not src:
                continue
            
            if self.debug_mode:
                print(f"        Processing image {i}/{len(img_tags)}: {src}")
            
            # Handle different src formats
            if src.startswith('data:'):
                # Already a data URL, skip
                if self.debug_mode:
                    print(f"          Already base64 encoded, skipping")
                images_processed += 1
                continue
            elif src.startswith(('http://', 'https://')):
                # External URL - leave as is (may not work in PDF)
                if self.debug_mode:
                    print(f"          External URL, leaving as-is (may not render in PDF)")
                images_processed += 1
                continue
            else:
                # Local file path - convert to absolute path and embed
                if src.startswith('./'):
                    src = src[2:]  # Remove './' prefix
                elif src.startswith('/'):
                    src = src[1:]  # Remove leading '/'
                
                # Construct absolute image path
                image_path = html_dir / src
                
                # Try different possible paths if the direct path doesn't work
                possible_paths = [
                    image_path,
                    html_dir / 'images' / os.path.basename(src),
                    html_dir / 'Schedules' / 'images' / os.path.basename(src),
                    html_dir / os.path.basename(src),
                    html_dir.parent / src,
                    html_dir.parent / 'images' / os.path.basename(src),
                    html_dir.parent / 'Schedules' / 'images' / os.path.basename(src)
                ]
                
                embedded = False
                for possible_path in possible_paths:
                    if possible_path.exists():
                        # Convert image to base64 data URL
                        data_url = self.encode_image_to_base64(str(possible_path))
                        if data_url:
                            # Update img src to use base64 data URL
                            img_tag['src'] = data_url
                            # Keep original src in data attribute for reference
                            img_tag['data-original-src'] = src
                            images_embedded += 1
                            embedded = True
                            if self.debug_mode:
                                print(f"          Successfully embedded from: {possible_path}")
                            break
                        else:
                            if self.debug_mode:
                                print(f"          Failed to encode: {possible_path}")
                    else:
                        if self.debug_mode:
                            print(f"          Path not found: {possible_path}")
                
                if not embedded:
                    if self.debug_mode:
                        print(f"          Could not find or embed image: {src}")
                
                images_processed += 1
        
        if self.debug_mode:
            print(f"        Images summary: {images_processed} processed, {images_embedded} embedded")
        
        return str(soup)

    def clean_html_for_pdf(self, html_content, html_file_path=None):
        """Clean and prepare HTML content for PDF conversion with image support."""
        # First process images if html_file_path is provided
        if html_file_path:
            html_content = self.process_images_in_html(html_content, html_file_path)
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Remove script and style tags that might cause issues
        for script in soup(["script"]):
            script.decompose()
        
        # Clean up HTML structure
        if not soup.find('html'):
            # Wrap in basic HTML structure if missing
            clean_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <title>Schedule Document</title>
                <style>
                    body {{ 
                        font-family: 'Times New Roman', Times, serif; 
                        font-size: 12pt;
                        line-height: 1.4; 
                        color: #000; 
                        margin: 0;
                        padding: 20px;
                    }}
                    
                    /* Image styling for PDF */
                    img {{
                        max-width: 100%;
                        height: auto;
                        display: block;
                        margin: 15px auto;
                        page-break-inside: avoid;
                        border: 1px solid #ddd;
                        padding: 5px;
                        box-sizing: border-box;
                    }}
                    
                    /* Small images should be inline */
                    img[width] {{
                        display: inline-block;
                        margin: 5px;
                    }}
                    
                    /* Figure and caption support */
                    figure {{
                        margin: 20px 0;
                        text-align: center;
                        page-break-inside: avoid;
                    }}
                    
                    figcaption {{
                        font-size: 10pt;
                        font-style: italic;
                        margin-top: 8px;
                        color: #666;
                    }}
                    
                    table {{ 
                        border-collapse: collapse; 
                        width: 100%; 
                        margin: 15px 0; 
                        page-break-inside: avoid;
                    }}
                    
                    th, td {{ 
                        border: 1px solid #000; 
                        padding: 8px 12px; 
                        text-align: left; 
                        vertical-align: top;
                    }}
                    
                    th {{ 
                        background-color: #f0f0f0; 
                        font-weight: bold; 
                        text-align: center;
                    }}
                    
                    .chapter {{ 
                        font-size: 16pt; 
                        font-weight: bold; 
                        margin: 25px 0 15px 0; 
                        text-align: center;
                        page-break-before: auto;
                    }}
                    
                    .article {{ 
                        font-size: 14pt; 
                        font-weight: bold; 
                        margin: 20px 0 10px 0; 
                    }}
                    
                    .section {{ 
                        margin: 12px 0; 
                        text-align: justify;
                    }}
                    
                    .subsection {{ 
                        margin-left: 30px; 
                        margin: 8px 0 8px 30px;
                    }}
                    
                    h1, h2, h3, h4, h5, h6 {{
                        font-family: 'Times New Roman', Times, serif;
                        font-weight: bold;
                        margin: 20px 0 10px 0;
                        page-break-after: avoid;
                    }}
                    
                    h1 {{ font-size: 18pt; text-align: center; }}
                    h2 {{ font-size: 16pt; }}
                    h3 {{ font-size: 14pt; }}
                    
                    p {{ 
                        margin: 10px 0; 
                        text-align: justify;
                        orphans: 2;
                        widows: 2;
                    }}
                    
                    .schedule-title {{
                        font-size: 18pt;
                        font-weight: bold;
                        text-align: center;
                        margin: 30px 0;
                        text-transform: uppercase;
                    }}
                    
                    /* Page break controls */
                    .no-break {{ page-break-inside: avoid; }}
                    .page-break {{ page-break-before: always; }}
                    
                    /* Print-specific styles */
                    @media print {{
                        body {{ margin: 0; }}
                        .no-print {{ display: none; }}
                    }}
                    
                    /* Handle blockquotes for legal text */
                    blockquote {{
                        margin: 15px 30px;
                        padding: 10px 15px;
                        border-left: 3px solid #ccc;
                        font-style: normal;
                    }}
                    
                    /* List styling */
                    ol, ul {{
                        margin: 10px 0;
                        padding-left: 30px;
                    }}
                    
                    li {{
                        margin: 5px 0;
                        text-align: justify;
                    }}
                    
                    /* Responsive image containers */
                    .image-container {{
                        text-align: center;
                        margin: 20px 0;
                        page-break-inside: avoid;
                    }}
                    
                    /* Handle tables with images */
                    td img {{
                        margin: 5px;
                        max-width: calc(100% - 10px);
                    }}
                </style>
            </head>
            <body>
                {soup}
            </body>
            </html>
            """
        else:
            # Add enhanced CSS styles if not present
            head = soup.find('head')
            if not head:
                head = soup.new_tag('head')
                soup.html.insert(0, head)
            
            # Remove existing styles that might conflict and add our enhanced styles
            for style in soup.find_all('style'):
                style.decompose()
            
            style_tag = soup.new_tag('style')
            style_tag.string = """
                body { 
                    font-family: 'Times New Roman', Times, serif; 
                    font-size: 12pt;
                    line-height: 1.4; 
                    color: #000; 
                    margin: 0;
                    padding: 20px;
                }
                
                /* Image styling for PDF */
                img {
                    max-width: 100%;
                    height: auto;
                    display: block;
                    margin: 15px auto;
                    page-break-inside: avoid;
                    border: 1px solid #ddd;
                    padding: 5px;
                    box-sizing: border-box;
                }
                
                /* Small images should be inline */
                img[width] {
                    display: inline-block;
                    margin: 5px;
                }
                
                /* Figure and caption support */
                figure {
                    margin: 20px 0;
                    text-align: center;
                    page-break-inside: avoid;
                }
                
                figcaption {
                    font-size: 10pt;
                    font-style: italic;
                    margin-top: 8px;
                    color: #666;
                }
                
                table { 
                    border-collapse: collapse; 
                    width: 100%; 
                    margin: 15px 0; 
                    page-break-inside: avoid;
                }
                
                th, td { 
                    border: 1px solid #000; 
                    padding: 8px 12px; 
                    text-align: left; 
                    vertical-align: top;
                }
                
                th { 
                    background-color: #f0f0f0; 
                    font-weight: bold; 
                    text-align: center;
                }
                
                .chapter { 
                    font-size: 16pt; 
                    font-weight: bold; 
                    margin: 25px 0 15px 0; 
                    text-align: center;
                    page-break-before: auto;
                }
                
                .article { 
                    font-size: 14pt; 
                    font-weight: bold; 
                    margin: 20px 0 10px 0; 
                }
                
                .section { 
                    margin: 12px 0; 
                    text-align: justify;
                }
                
                .subsection { 
                    margin-left: 30px; 
                    margin: 8px 0 8px 30px;
                }
                
                h1, h2, h3, h4, h5, h6 {
                    font-family: 'Times New Roman', Times, serif;
                    font-weight: bold;
                    margin: 20px 0 10px 0;
                    page-break-after: avoid;
                }
                
                h1 { font-size: 18pt; text-align: center; }
                h2 { font-size: 16pt; }
                h3 { font-size: 14pt; }
                
                p { 
                    margin: 10px 0; 
                    text-align: justify;
                    orphans: 2;
                    widows: 2;
                }
                
                .schedule-title {
                    font-size: 18pt;
                    font-weight: bold;
                    text-align: center;
                    margin: 30px 0;
                    text-transform: uppercase;
                }
                
                /* Page break controls */
                .no-break { page-break-inside: avoid; }
                .page-break { page-break-before: always; }
                
                /* Print-specific styles */
                @media print {
                    body { margin: 0; }
                    .no-print { display: none; }
                }
                
                /* Handle blockquotes for legal text */
                blockquote {
                    margin: 15px 30px;
                    padding: 10px 15px;
                    border-left: 3px solid #ccc;
                    font-style: normal;
                }
                
                /* List styling */
                ol, ul {
                    margin: 10px 0;
                    padding-left: 30px;
                }
                
                li {
                    margin: 5px 0;
                    text-align: justify;
                }
                
                /* Responsive image containers */
                .image-container {
                    text-align: center;
                    margin: 20px 0;
                    page-break-inside: avoid;
                }
                
                /* Handle tables with images */
                td img {
                    margin: 5px;
                    max-width: calc(100% - 10px);
                }
            """
            head.append(style_tag)
            
            clean_content = str(soup)
        
        return clean_content

    def start_browser_session(self):
        """Start a Playwright browser session."""
        try:
            if not self.playwright:
                self.playwright = sync_playwright().start()
                self.browser = self.playwright.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-gpu',
                        '--disable-background-timer-throttling',
                        '--disable-backgrounding-occluded-windows',
                        '--disable-renderer-backgrounding',
                        '--allow-file-access-from-files',  # Allow local file access
                        '--disable-web-security'  # For local images
                    ]
                )
                if self.debug_mode:
                    print(f"      Started Playwright browser session")
            return True
        except Exception as e:
            if self.debug_mode:
                print(f"      Error starting browser: {e}")
            return False

    def stop_browser_session(self):
        """Stop the Playwright browser session."""
        try:
            if self.browser:
                self.browser.close()
                self.browser = None
            if self.playwright:
                self.playwright.stop()
                self.playwright = None
            if self.debug_mode:
                print(f"      Stopped Playwright browser session")
        except Exception as e:
            if self.debug_mode:
                print(f"      Error stopping browser: {e}")

    def convert_html_to_pdf(self, html_content, output_pdf_path, html_file_path=None):
        """Convert HTML content to PDF file using Playwright with image support."""
        try:
            if not self.browser:
                if not self.start_browser_session():
                    return False
            
            # Clean HTML for better PDF rendering and handle images
            clean_html = self.clean_html_for_pdf(html_content, html_file_path)
            
            # Create a new page
            page = self.browser.new_page()
            
            try:
                # Set content and wait for it to load completely (including images)
                page.set_content(clean_html, wait_until='networkidle', timeout=60000)
                
                # Wait a bit more for images to fully load
                page.wait_for_timeout(2000)
                
                # Generate PDF
                page.pdf(path=output_pdf_path, **self.pdf_options)
                
                if self.debug_mode:
                    print(f"      PDF generated successfully: {output_pdf_path}")
                
                return True
                
            finally:
                page.close()
            
        except Exception as e:
            if self.debug_mode:
                print(f"      Error converting to PDF: {e}")
                import traceback
                traceback.print_exc()
            return False

    def convert_images_to_pdf(self, image_files, output_pdf_path, title="Schedule"):
        """
        Convert a list of image files to a single PDF.
        
        Args:
            image_files: List of Path objects pointing to image files
            output_pdf_path: Output PDF file path
            title: Title for the PDF document
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if self.debug_mode:
                print(f"      Creating PDF from {len(image_files)} images...")
            
            # Create HTML from images
            html_content = self.create_html_from_images(image_files, title)
            
            if not html_content:
                if self.debug_mode:
                    print(f"      Failed to create HTML from images")
                return False
            
            # Convert HTML to PDF
            success = self.convert_html_to_pdf(html_content, output_pdf_path)
            
            if success:
                if self.debug_mode:
                    print(f"      Successfully created PDF from images: {output_pdf_path}")
            
            return success
            
        except Exception as e:
            if self.debug_mode:
                print(f"      Error converting images to PDF: {e}")
                import traceback
                traceback.print_exc()
            return False

    def process_schedule_html_files(self, schedules_dir, legislation_output_dir):
        """Process individual HTML files or folders within the schedules directory."""
        processed_files = []
        
        # Handle both individual HTML files and folders containing HTML files
        schedule_items = list(schedules_dir.iterdir())
        
        for item in schedule_items:
            if item.is_file() and item.suffix in ['.html', '.htm']:
                # Individual HTML file
                processed_files.append(item)
                if self.debug_mode:
                    print(f"      - {item.name} (file)")
            elif item.is_dir():
                # Directory containing HTML file and potentially images
                html_files = list(item.glob("*.html")) + list(item.glob("*.htm"))
                for html_file in html_files:
                    processed_files.append(html_file)
                    if self.debug_mode:
                        print(f"      - {html_file.name} (from folder {item.name})")
        
        if self.debug_mode and processed_files:
            print(f"   Found {len(processed_files)} HTML schedule files:")
        
        return processed_files

    def process_legislation_folders(self):
        """
        Processes HTML files and image-only schedules from each legislation folder,
        converts them to PDF format using Playwright with image support, and saves them in the output directory.
        Returns a dictionary mapping legislation names to their schedule metadata.
        """
        input_path = Path(self.base_input_dir)
        output_path = Path(self.output_directory)

        print(f"Starting schedule PDF conversion with Playwright and image support...")
        print(f"   Input directory: {input_path}")
        print(f"   Output directory: {output_path}")

        if not input_path.exists():
            print(f"Input directory {input_path} does not exist.")
            return

        if not output_path.exists():
            print(f"Output directory {output_path} does not exist.")
            return

        legislation_folders = [f for f in input_path.iterdir() if f.is_dir()]
        print(f"Found {len(legislation_folders)} legislation folders:")
        for folder in legislation_folders:
            print(f"   - {folder.name}")

        processed_count = 0
        total_pdfs_created = 0
        total_image_pdfs_created = 0

        # Dictionary to store schedule metadata for each legislation
        schedules_metadata = {}

        # Start browser session once for all conversions
        if not self.start_browser_session():
            print("Failed to start browser session. Aborting.")
            return

        try:
            for legislation_folder in legislation_folders:
                folder_name = legislation_folder.name
                schedules_dir = legislation_folder / "schedules"

                print(f"\nProcessing folder: {folder_name}")

                if not schedules_dir.exists() or not schedules_dir.is_dir():
                    print(f"   No schedules directory found: {schedules_dir}")
                    continue

                # Create output directory for this legislation's PDFs
                legislation_output_dir = output_path / folder_name / "schedules_pdf"
                legislation_output_dir.mkdir(parents=True, exist_ok=True)

                print(f"   PDF output directory: {legislation_output_dir}")

                # Process HTML files
                schedule_files = self.process_schedule_html_files(schedules_dir, legislation_output_dir)

                # Find image-only schedules
                image_only_schedules = self.find_image_only_schedules(schedules_dir)

                if not schedule_files and not image_only_schedules:
                    print(f"   No schedule files (HTML or images) found in {schedules_dir}")
                    continue

                # Initialize metadata list for this legislation
                schedules_metadata[folder_name] = []

                # Convert each HTML file to PDF
                pdfs_created_for_legislation = 0

                for html_file in schedule_files:
                    try:
                        print(f"\n   Converting HTML schedule: {html_file.name}")

                        with open(html_file, 'r', encoding='utf-8', errors='replace') as f:
                            html_content = f.read()

                        print(f"      HTML file size: {len(html_content)} characters")

                        # Check for images in the same directory
                        html_dir = html_file.parent
                        images_dir = html_dir / "images"
                        if images_dir.exists():
                            image_files = list(images_dir.glob("*.*"))
                            image_count = len([f for f in image_files if f.suffix.lower() in self.image_extensions])
                            if self.debug_mode and image_count > 0:
                                print(f"      Found {image_count} image files in {images_dir}")

                        # Generate PDF filename
                        pdf_filename = html_file.stem + '.pdf'
                        pdf_output_path = legislation_output_dir / pdf_filename

                        # Convert to PDF with image support
                        success = self.convert_html_to_pdf(html_content, str(pdf_output_path), str(html_file))

                        if success:
                            # Verify PDF was created and has content
                            if pdf_output_path.exists() and pdf_output_path.stat().st_size > 0:
                                print(f"      Successfully created PDF: {pdf_filename}")
                                print(f"         File size: {pdf_output_path.stat().st_size} bytes")
                                pdfs_created_for_legislation += 1
                                total_pdfs_created += 1

                                # Upload to S3 if configured
                                s3_url = self.upload_pdf_to_s3(str(pdf_output_path), folder_name, pdf_filename)

                                # Store metadata with source file path and S3 URL
                                metadata_entry = {
                                    'pdf_filename': pdf_filename,
                                    'pdf_path': f"schedules_pdf/{pdf_filename}",
                                    'source_path': str(html_file.relative_to(legislation_folder)),
                                    'source_type': 'html'
                                }
                                if s3_url:
                                    metadata_entry['s3_url'] = s3_url

                                schedules_metadata[folder_name].append(metadata_entry)
                            else:
                                print(f"      PDF created but appears to be empty: {pdf_filename}")
                        else:
                            print(f"      Failed to convert {html_file.name} to PDF")

                    except Exception as e:
                        print(f"      Error processing {html_file.name}: {e}")
                        if self.debug_mode:
                            import traceback
                            traceback.print_exc()

                # Convert image-only schedules to PDF
                for folder_path, image_files, schedule_name in image_only_schedules:
                    try:
                        print(f"\n   Converting image-only schedule: {schedule_name}")
                        print(f"      Found {len(image_files)} image files")

                        # Generate PDF filename
                        pdf_filename = f"{schedule_name}.pdf"
                        pdf_output_path = legislation_output_dir / pdf_filename

                        # Convert images to PDF
                        success = self.convert_images_to_pdf(
                            image_files,
                            str(pdf_output_path),
                            title=f"Schedule - {schedule_name}"
                        )

                        if success:
                            # Verify PDF was created and has content
                            if pdf_output_path.exists() and pdf_output_path.stat().st_size > 0:
                                print(f"      Successfully created PDF: {pdf_filename}")
                                print(f"         File size: {pdf_output_path.stat().st_size} bytes")
                                pdfs_created_for_legislation += 1
                                total_pdfs_created += 1
                                total_image_pdfs_created += 1

                                # Upload to S3 if configured
                                s3_url = self.upload_pdf_to_s3(str(pdf_output_path), folder_name, pdf_filename)

                                # Store metadata with source folder path and S3 URL
                                metadata_entry = {
                                    'pdf_filename': pdf_filename,
                                    'pdf_path': f"schedules_pdf/{pdf_filename}",
                                    'source_path': str(folder_path.relative_to(legislation_folder)),
                                    'source_type': 'images',
                                    'image_count': len(image_files)
                                }
                                if s3_url:
                                    metadata_entry['s3_url'] = s3_url

                                schedules_metadata[folder_name].append(metadata_entry)
                            else:
                                print(f"      PDF created but appears to be empty: {pdf_filename}")
                        else:
                            print(f"      Failed to convert images to PDF")

                    except Exception as e:
                        print(f"      Error processing image schedule {schedule_name}: {e}")
                        if self.debug_mode:
                            import traceback
                            traceback.print_exc()

                if pdfs_created_for_legislation > 0:
                    print(f"   Successfully created {pdfs_created_for_legislation} PDF files for {folder_name}")
                    processed_count += 1
                else:
                    print(f"   No PDFs created for {folder_name}")

        finally:
            # Always stop browser session
            self.stop_browser_session()

        print(f"\nSchedule PDF conversion completed!")
        print(f"   Successfully processed {processed_count} legislation folders")
        print(f"   Total PDFs created: {total_pdfs_created}")
        print(f"   PDFs from images only: {total_image_pdfs_created}")

        # Store metadata for later use
        self.schedules_metadata = schedules_metadata

        return processed_count, total_pdfs_created

    def test_single_schedule_pdf(self, html_file_path, output_pdf_path=None):
        """
        Test function to convert a single HTML schedule file to PDF using Playwright with image support.
        Useful for debugging and testing.
        """
        print(f"Testing single schedule PDF conversion with Playwright and images: {html_file_path}")
        
        try:
            if not output_pdf_path:
                html_path = Path(html_file_path)
                output_pdf_path = html_path.parent / f"{html_path.stem}_playwright_test.pdf"
            
            with open(html_file_path, 'r', encoding='utf-8', errors='replace') as f:
                html_content = f.read()
            
            print(f"HTML file size: {len(html_content)} characters")
            
            # Check for images
            soup = BeautifulSoup(html_content, 'html.parser')
            img_tags = soup.find_all('img')
            print(f"Found {len(img_tags)} image tags in HTML")
            
            # Start browser session for test
            if not self.start_browser_session():
                print("Failed to start browser session for test")
                return False
            
            try:
                # Convert to PDF with image support
                success = self.convert_html_to_pdf(html_content, str(output_pdf_path), html_file_path)
                
                if success and Path(output_pdf_path).exists():
                    file_size = Path(output_pdf_path).stat().st_size
                    print(f"Successfully created PDF: {output_pdf_path}")
                    print(f"PDF file size: {file_size} bytes")
                    return True
                else:
                    print(f"Failed to create PDF")
                    return False
            
            finally:
                self.stop_browser_session()
            
        except Exception as e:
            print(f"Error testing PDF conversion: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_images_to_pdf(self, images_folder, output_pdf_path=None):
        """
        Test function to convert a folder of images to a single PDF.
        Useful for debugging and testing image-only schedules.
        """
        print(f"Testing image-to-PDF conversion: {images_folder}")
        
        try:
            folder_path = Path(images_folder)
            if not folder_path.exists():
                print(f"Folder does not exist: {images_folder}")
                return False
            
            # Find all image files
            image_files = [f for f in folder_path.iterdir() 
                          if f.is_file() and f.suffix.lower() in self.image_extensions]
            
            if not image_files:
                print(f"No image files found in {images_folder}")
                return False
            
            print(f"Found {len(image_files)} image files")
            
            if not output_pdf_path:
                output_pdf_path = folder_path / f"{folder_path.name}_images_test.pdf"
            
            # Start browser session for test
            if not self.start_browser_session():
                print("Failed to start browser session for test")
                return False
            
            try:
                # Convert images to PDF
                success = self.convert_images_to_pdf(
                    image_files, 
                    str(output_pdf_path),
                    title=f"Test Schedule - {folder_path.name}"
                )
                
                if success and Path(output_pdf_path).exists():
                    file_size = Path(output_pdf_path).stat().st_size
                    print(f"Successfully created PDF: {output_pdf_path}")
                    print(f"PDF file size: {file_size} bytes")
                    return True
                else:
                    print(f"Failed to create PDF")
                    return False
            
            finally:
                self.stop_browser_session()
            
        except Exception as e:
            print(f"Error testing image-to-PDF conversion: {e}")
            import traceback
            traceback.print_exc()
            return False

    def update_json_with_pdf_references(self, generate_html=True):
        """
        Update JSON files to reference PDF files and optionally generate HTML index.
        PDFs are sorted in ascending order by the last two digits of their name.

        Args:
            generate_html: If True, also generate an HTML file with PDF links
        """
        output_path = Path(self.output_directory)

        print(f"\nUpdating JSON files with PDF references...")

        def extract_last_two_digits(filename):
            """Extract last two digits from filename for sorting."""
            name_without_ext = Path(filename).stem
            # Extract last two characters that are digits
            match = re.search(r'(\d{2})$', name_without_ext)
            if match:
                return int(match.group(1))
            # If no two-digit number at end, try single digit
            match = re.search(r'(\d)$', name_without_ext)
            if match:
                return int(match.group(1))
            # Default to 0 if no digits found
            return 0

        for legislation_folder in output_path.iterdir():
            if not legislation_folder.is_dir():
                continue

            # Look for JSON file
            json_file = legislation_folder / f"{legislation_folder.name}.json"
            if not json_file.exists():
                continue

            # Check for PDF directory
            pdf_dir = legislation_folder / "schedules_pdf"
            if not pdf_dir.exists():
                continue

            # Get list of PDF files and sort by last two digits
            pdf_files = list(pdf_dir.glob("*.pdf"))
            pdf_files = sorted(pdf_files, key=lambda f: extract_last_two_digits(f.name))

            if not pdf_files:
                continue

            try:
                # Load existing JSON
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Get metadata from stored metadata if available
                folder_name = legislation_folder.name
                metadata_dict = {}
                if hasattr(self, 'schedules_metadata') and folder_name in self.schedules_metadata:
                    for meta in self.schedules_metadata[folder_name]:
                        metadata_dict[meta['pdf_filename']] = meta

                # Update schedules section to reference PDFs
                schedule_references = []
                for pdf_file in pdf_files:
                    schedule_ref = {
                        "title": pdf_file.stem,
                        "filename": pdf_file.name,
                        "pdf_path": f"schedules_pdf/{pdf_file.name}",
                        "local_file_path": str(pdf_file.relative_to(output_path)),
                        "type": "pdf",
                        "generator": "playwright",
                        "includes_images": True  # Flag indicating image support
                    }

                    # Add source path, type, and S3 URL if available
                    if pdf_file.name in metadata_dict:
                        meta = metadata_dict[pdf_file.name]
                        schedule_ref["source_path"] = meta['source_path']
                        schedule_ref["source_type"] = meta['source_type']
                        if 'image_count' in meta:
                            schedule_ref["image_count"] = meta['image_count']
                        if 's3_url' in meta:
                            schedule_ref["s3_url"] = meta['s3_url']

                    schedule_references.append(schedule_ref)

                data["schedules"] = schedule_references

                # Save updated JSON
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)

                print(f"   Updated {json_file.name} with {len(schedule_references)} PDF references (sorted by last 2 digits)")

                # Generate HTML index if requested
                if generate_html:
                    html_file = legislation_folder / "schedules_index.html"
                    self._generate_schedules_html(
                        legislation_folder.name,
                        schedule_references,
                        html_file,
                        data.get('title', legislation_folder.name)
                    )
                    print(f"   Generated HTML index: {html_file.name}")

            except Exception as e:
                print(f"   Error updating {json_file}: {e}")

        print("JSON update completed!")

    def _generate_schedules_html(self, legislation_name, schedule_references, output_file, legislation_title=None):
        """
        Generate an HTML index file with links to schedule PDFs.

        Args:
            legislation_name: Name of the legislation
            schedule_references: List of schedule reference dictionaries
            output_file: Path to output HTML file
            legislation_title: Full title of the legislation
        """
        title = legislation_title or legislation_name

        html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Schedules - {title}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            color: #333;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 15px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: white;
            padding: 40px 30px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            font-weight: 600;
        }}

        .header p {{
            font-size: 1.1em;
            opacity: 0.9;
        }}

        .stats {{
            background: #f8f9fa;
            padding: 20px 30px;
            border-bottom: 1px solid #e0e0e0;
            display: flex;
            justify-content: space-around;
            flex-wrap: wrap;
        }}

        .stat-item {{
            text-align: center;
            padding: 10px 20px;
        }}

        .stat-number {{
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }}

        .stat-label {{
            color: #666;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        .content {{
            padding: 30px;
        }}

        .search-box {{
            margin-bottom: 30px;
        }}

        .search-box input {{
            width: 100%;
            padding: 15px 20px;
            font-size: 1em;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            transition: all 0.3s;
        }}

        .search-box input:focus {{
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }}

        .schedules-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }}

        .schedule-card {{
            background: white;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            padding: 20px;
            transition: all 0.3s;
            cursor: pointer;
        }}

        .schedule-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.1);
            border-color: #667eea;
        }}

        .schedule-icon {{
            width: 60px;
            height: 60px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 15px;
        }}

        .schedule-icon svg {{
            width: 32px;
            height: 32px;
            fill: white;
        }}

        .schedule-title {{
            font-size: 1.1em;
            font-weight: 600;
            color: #333;
            margin-bottom: 8px;
            word-wrap: break-word;
        }}

        .schedule-meta {{
            color: #666;
            font-size: 0.85em;
            display: flex;
            align-items: center;
            gap: 5px;
            margin-top: 10px;
        }}

        .badge {{
            display: inline-block;
            background: #667eea;
            color: white;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 0.75em;
            font-weight: 600;
            text-transform: uppercase;
        }}

        .badge.images {{
            background: #52c41a;
        }}

        .no-results {{
            text-align: center;
            padding: 60px 20px;
            color: #999;
        }}

        .no-results svg {{
            width: 80px;
            height: 80px;
            fill: #ddd;
            margin-bottom: 20px;
        }}

        .footer {{
            background: #f8f9fa;
            padding: 20px 30px;
            text-align: center;
            color: #666;
            font-size: 0.9em;
            border-top: 1px solid #e0e0e0;
        }}

        @media (max-width: 768px) {{
            .header h1 {{
                font-size: 1.8em;
            }}

            .schedules-grid {{
                grid-template-columns: 1fr;
            }}

            .stats {{
                flex-direction: column;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📋 Schedules</h1>
            <p>{title}</p>
        </div>

        <div class="stats">
            <div class="stat-item">
                <div class="stat-number" id="total-schedules">{len(schedule_references)}</div>
                <div class="stat-label">Total Schedules</div>
            </div>
            <div class="stat-item">
                <div class="stat-number">{sum(1 for s in schedule_references if s.get('includes_images'))}</div>
                <div class="stat-label">With Images</div>
            </div>
            <div class="stat-item">
                <div class="stat-number">PDF</div>
                <div class="stat-label">Format</div>
            </div>
        </div>

        <div class="content">
            <div class="search-box">
                <input type="text" id="searchInput" placeholder="🔍 Search schedules..." onkeyup="filterSchedules()">
            </div>

            <div class="schedules-grid" id="schedulesGrid">
'''

        # Add schedule cards
        for schedule in schedule_references:
            title_text = schedule.get('title', 'Untitled')
            filename = schedule.get('filename', '')
            pdf_path = schedule.get('pdf_path', '')
            includes_images = schedule.get('includes_images', False)

            html_content += f'''
                <div class="schedule-card" onclick="window.open('{pdf_path}', '_blank')">
                    <div class="schedule-icon">
                        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z"/>
                            <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>
                        </svg>
                    </div>
                    <div class="schedule-title">{title_text}</div>
                    <div class="schedule-meta">
                        <span class="badge">PDF</span>
                        {f'<span class="badge images">Images</span>' if includes_images else ''}
                    </div>
                </div>
'''

        # Close HTML
        html_content += '''
            </div>

            <div class="no-results" id="noResults" style="display: none;">
                <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path d="M10 18a7.952 7.952 0 0 0 4.897-1.688l4.396 4.396 1.414-1.414-4.396-4.396A7.952 7.952 0 0 0 18 10c0-4.411-3.589-8-8-8s-8 3.589-8 8 3.589 8 8 8zm0-14c3.309 0 6 2.691 6 6s-2.691 6-6 6-6-2.691-6-6 2.691-6 6-6z"/>
                </svg>
                <h3>No schedules found</h3>
                <p>Try adjusting your search terms</p>
            </div>
        </div>

        <div class="footer">
            Generated with SchedulePDFProcessor • {len(schedule_references)} schedule(s) available
        </div>
    </div>

    <script>
        function filterSchedules() {{
            const searchTerm = document.getElementById('searchInput').value.toLowerCase();
            const cards = document.querySelectorAll('.schedule-card');
            const noResults = document.getElementById('noResults');
            let visibleCount = 0;

            cards.forEach(card => {{
                const title = card.querySelector('.schedule-title').textContent.toLowerCase();
                if (title.includes(searchTerm)) {{
                    card.style.display = 'block';
                    visibleCount++;
                }} else {{
                    card.style.display = 'none';
                }}
            }});

            noResults.style.display = visibleCount === 0 ? 'block' : 'none';
        }}
    </script>
</body>
</html>
'''

        # Write HTML file
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_content)

    def get_processing_statistics(self):
        """Get detailed statistics about the processing results."""
        output_path = Path(self.output_directory)
        
        stats = {
            'total_legislations': 0,
            'legislations_with_pdfs': 0,
            'total_pdfs': 0,
            'total_pdf_size': 0,
            'legislations': []
        }
        
        for legislation_folder in output_path.iterdir():
            if not legislation_folder.is_dir():
                continue
            
            stats['total_legislations'] += 1
            pdf_dir = legislation_folder / "schedules_pdf"
            
            if pdf_dir.exists():
                pdf_files = list(pdf_dir.glob("*.pdf"))
                if pdf_files:
                    stats['legislations_with_pdfs'] += 1
                    stats['total_pdfs'] += len(pdf_files)
                    
                    folder_pdf_size = sum(pdf.stat().st_size for pdf in pdf_files)
                    stats['total_pdf_size'] += folder_pdf_size
                    
                    stats['legislations'].append({
                        'name': legislation_folder.name,
                        'pdf_count': len(pdf_files),
                        'total_size': folder_pdf_size,
                        'pdf_files': [pdf.name for pdf in pdf_files]
                    })
        
        return stats

    def print_processing_report(self):
        """Print a detailed processing report."""
        stats = self.get_processing_statistics()
        
        print(f"\n{'='*60}")
        print("SCHEDULE PDF PROCESSING REPORT")
        print(f"{'='*60}")
        print(f"Total legislation folders: {stats['total_legislations']}")
        print(f"Legislations with PDFs: {stats['legislations_with_pdfs']}")
        print(f"Total PDFs created: {stats['total_pdfs']}")
        print(f"Total PDF size: {stats['total_pdf_size'] / (1024*1024):.2f} MB")
        
        if stats['legislations']:
            print(f"\nDetailed breakdown:")
            print(f"{'Legislation':<30} {'PDFs':<8} {'Size (MB)':<12} {'Files'}")
            print(f"{'-'*30} {'-'*8} {'-'*12} {'-'*30}")
            
            for leg in stats['legislations']:
                size_mb = leg['total_size'] / (1024*1024)
                files_str = ', '.join(leg['pdf_files'][:3])
                if len(leg['pdf_files']) > 3:
                    files_str += f", ... (+{len(leg['pdf_files'])-3} more)"
                
                print(f"{leg['name']:<30} {leg['pdf_count']:<8} {size_mb:<12.2f} {files_str}")
        
        print(f"{'='*60}")

    def check_playwright_installation(self):
        """Check if Playwright is properly installed and configured."""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                # Try to get browser info
                browser = p.chromium.launch()
                browser.close()
            print("Playwright is properly installed and configured.")
            return True
        except Exception as e:
            print(f"Playwright installation issue: {e}")
            print("Please run: pip install playwright && playwright install")
            return False