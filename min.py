import requests
import subprocess
import sys
from save_legislation_html import MainHTMLScraper
from scrape_full_legislations import MainHTMLProcessor
from save_page_part_html import ExtendedPageScraper
from scrape_page_parts import ExtendedLegislationMerger
from save_other_htmls import AmendmentScheduleHTMLScraper
from scrape_amendment import AmendmentProcessor
from scrape_schedules import SchedulePDFProcessor  # Updated import

def check_and_install_playwright():
    """Check if Playwright is installed and install it if needed."""
    try:
        import playwright
        print("✓ playwright package is installed")
        
        # Check if browsers are installed
        result = subprocess.run([sys.executable, '-m', 'playwright', 'install', '--dry-run'], 
                              capture_output=True, text=True)
        
        if "is already installed" in result.stdout or result.returncode == 0:
            print("✓ Playwright browsers are installed")
            return True
        else:
            print("⚠️  Playwright browsers not found, installing...")
            result = subprocess.run([sys.executable, '-m', 'playwright', 'install'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                print("✓ Playwright browsers installed successfully")
                return True
            else:
                print(f"❌ Failed to install Playwright browsers: {result.stderr}")
                return False
                
    except ImportError:
        print("⚠️  playwright not found, installing...")
        try:
            # Install playwright
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'playwright'])
            print("✓ playwright package installed")
            
            # Install browsers
            subprocess.check_call([sys.executable, '-m', 'playwright', 'install'])
            print("✓ Playwright browsers installed")
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to install Playwright: {e}")
            return False

def install_requirements():
    """Install and check all required packages for PDF conversion."""
    print("Checking requirements for PDF generation...")
    
    # Check Playwright
    if not check_and_install_playwright():
        print("❌ Playwright installation failed!")
        return False
    
    # Check other required packages
    required_packages = ['beautifulsoup4', 'pathlib']
    
    for package in required_packages:
        try:
            if package == 'pathlib':
                # pathlib is built-in for Python 3.4+, but check anyway
                from pathlib import Path
                print(f"✓ {package} is available")
            elif package == 'beautifulsoup4':
                from bs4 import BeautifulSoup
                print(f"✓ {package} is available")
        except ImportError:
            print(f"⚠️  {package} not found, installing...")
            try:
                subprocess.check_call([sys.executable, '-m', 'pip', 'install', package])
                print(f"✓ {package} installed")
            except subprocess.CalledProcessError as e:
                print(f"❌ Failed to install {package}: {e}")
                return False
    
    # Test Playwright functionality
    print("Testing Playwright functionality...")
    try:
        processor = SchedulePDFProcessor(".", ".")
        if processor.check_playwright_installation():
            print("✓ Playwright is working correctly")
            return True
        else:
            print("❌ Playwright test failed")
            return False
    except Exception as e:
        print(f"❌ Playwright test error: {e}")
        return False

def main():
    """Main processing pipeline for legislation scraping and parsing with Playwright PDF schedule conversion."""
    
    # Check requirements first
    print("=" * 60)
    print("LEGISLATION PROCESSING PIPELINE WITH PLAYWRIGHT PDFs")
    print("=" * 60)
    print()
    
    if not install_requirements():
        print("\n❌ Requirements check failed!")
        print("Please resolve the installation issues and run again.")
        print("\nManual installation commands:")
        print("  pip install playwright beautifulsoup4")
        print("  playwright install")
        return False
    
    print("\n✅ All requirements satisfied!")
    print()
    
    # Headers from curl request
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "max-age=0",
        "priority": "u=0, i",
        "sec-ch-ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    }

    # Cookies from curl request
    cookies = {
        "ui-tabs-144": "0",
        "ui-tabs-236": "0",
        "JSESSIONID": "849C10D80B404E890395708EC928887B"
    }
    
    # Define paths - Update these as needed for your specific legislation folder
    json_file_path = "data/json/legislation_A.json"
    html_folder = "data/html/legislation_test"
    data_folder = "data/legislations/legislation_test"
    
    print("Pipeline Configuration:")
    print(f"  JSON file: {json_file_path}")
    print(f"  HTML folder: {html_folder}")
    print(f"  Data folder: {data_folder}")
    print()
    skip_list = [
        "back_new1.gif",
        "iNote1.gif",
        "print.png",
        "relatedCases.gif",
        "speaker.gif",
        "search_small.gif",
        "logo.gif",
        "bullet1.gif",
        "top.gif",
        "subscribe.gif",
        "helpUs_Img.gif",
        "constitution_2022.png",
        "close.gif"
    ]
    # Step 1: Scrape initial HTML files
    # print("Step 1: Scraping initial HTML files...")
    # try:
    #     scraper = MainHTMLScraper(headers=headers, cookies=cookies, skip_images=skip_list)
    #     scraper.process_json_file(json_file_path)
    #     print("✓ Step 1 completed successfully")
    # except Exception as e:
    #     print(f"✗ Step 1 failed: {e}")
    #     return False
    # print()
    
    # Step 2: Process the HTML files into structured JSON
    print("Step 2: Processing HTML files into structured JSON...")
    try:
        main_page_processor = MainHTMLProcessor(html_folder, data_folder)
        main_page_processor.debug_mode = True
        main_page_processor.process_html_files()
        print("✓ Step 2 completed successfully")
    except Exception as e:
        print(f"✗ Step 2 failed: {e}")
        return False
    print()
    
    # Step 3: Scrape extended pages (for legislation with multiple parts)
    print("Step 3: Scraping extended pages...")
    try:
        extended_page_scraper = ExtendedPageScraper(headers, cookies)
        extended_page_scraper.set_paths(data_folder, html_folder)
        extended_page_scraper.process_legislation_files()
        print("✓ Step 3 completed successfully")
    except Exception as e:
        print(f"✗ Step 3 failed: {e}")
        return False
    print()
    
    # Step 4: Merge legislation parts into complete documents
    print("Step 4: Merging legislation parts...")
    try:
        extended_page_merger = ExtendedLegislationMerger(html_folder, data_folder)
        extended_page_merger.set_paths(data_folder, html_folder)
        extended_page_merger.process_legislation_folders()
        print("✓ Step 4 completed successfully")
    except Exception as e:
        print(f"✗ Step 4 failed: {e}")
        return False
    print()

    # Step 5: Scrape Amendment and Schedule HTML files
    print("Step 5: Scraping Amendment and Schedule HTML files...")
    try:
        schedule_amendment_scraper = AmendmentScheduleHTMLScraper(headers, cookies, data_folder, html_folder)
        schedule_amendment_scraper.process_legislation_files()
        print("✓ Step 5 completed successfully")
    except Exception as e:
        print(f"✗ Step 5 failed: {e}")
        return False
    print()

    # Step 6: Process amendment HTML files into structured data
    print("Step 6: Processing amendment HTML files...")
    try:
        amendment_processor = AmendmentProcessor(html_folder, data_folder)
        amendment_processor.process_legislation_folders()
        print("✓ Step 6 completed successfully")
    except Exception as e:
        print(f"✗ Step 6 failed: {e}")
        return False
    print()

    # Step 7: Convert schedule HTML files to PDF using Playwright
    # print("Step 7: Converting schedule HTML files to PDF with Playwright...")
    # try:
    #     pdf_processor = SchedulePDFProcessor(html_folder, data_folder)
    #     pdf_processor.debug_mode = True
        
    #     print("   Starting Playwright PDF conversion...")
    #     processed_count, total_pdfs = pdf_processor.process_legislation_folders()
        
    #     if total_pdfs > 0:
    #         print("   Updating JSON files with PDF references...")
    #         pdf_processor.update_json_with_pdf_references()
            
    #         print(f"✓ Step 7 completed successfully")
    #         print(f"  - Processed {processed_count} legislation folders")
    #         print(f"  - Created {total_pdfs} PDF files using Playwright")
    #     else:
    #         print("⚠️  No PDF files were created. Check if schedule HTML files exist.")
            
    # except Exception as e:
    #     print(f"✗ Step 7 failed: {e}")
    #     import traceback
    #     traceback.print_exc()
    #     return False
    # print()
    
    print("=" * 60)
    print("ALL STEPS COMPLETED SUCCESSFULLY!")
    print("=" * 60)
    print()
    print("Summary of what was processed:")
    print(f"  📁 HTML source folder: {html_folder}")
    print(f"  📁 JSON output folder: {data_folder}")
    print(f"  📄 Schedule PDFs created: {total_pdfs} files")
    print(f"  🔧 PDF Generator: Playwright (modern browser-based)")
    print()
    print("Your legislation data now contains:")
    print("  ✅ Complete section structure with subsections")
    print("  ✅ Amendment data integrated")
    print("  ✅ Schedule data converted to high-quality PDF format")
    print("  ✅ JSON files reference PDF locations")
    print("  ✅ Modern, maintainable PDF generation system")
    
    return True

def process_specific_legislation_playwright(legislation_folder_name):
    """
    Process a specific legislation folder with Playwright PDF conversion.
    
    Args:
        legislation_folder_name (str): Name like "legislation_A_105"
    """
    
    print(f"Processing specific legislation with Playwright: {legislation_folder_name}")
    
    # Check requirements first
    if not install_requirements():
        print("Requirements check failed!")
        return False
    
    # Define paths for specific legislation
    html_folder = "data/html/legislation_A"
    data_folder = "data/legislations/legislation_A"
    
    # Test with Playwright processor
    pdf_processor = SchedulePDFProcessor(html_folder, data_folder)
    pdf_processor.debug_mode = True
    
    # Test with a single file
    html_file_path = f"{html_folder}/{legislation_folder_name}/schedules/Schedules.html"
    output_pdf_path = f"debug_{legislation_folder_name}_schedule_playwright.pdf"
    
    print(f"Testing single schedule file: {html_file_path}")
    result = pdf_processor.test_single_schedule_pdf(html_file_path, output_pdf_path)
    
    if result:
        print(f"✅ Successfully converted schedule to PDF for {legislation_folder_name}")
        print(f"   PDF saved to: {output_pdf_path}")
    else:
        print(f"❌ Failed to convert schedule to PDF for {legislation_folder_name}")
    
    return result

def batch_convert_schedules_to_pdf():
    """Convert all existing schedule HTML files to PDF format using Playwright."""
    
    html_folder = "data/html/legislation_A"
    data_folder = "data/legislations/legislation_A"
    
    print("Starting batch conversion of schedules to PDF with Playwright...")
    
    try:
        if not install_requirements():
            print("Cannot proceed without proper Playwright installation")
            return False
            
        pdf_processor = SchedulePDFProcessor(html_folder, data_folder)
        pdf_processor.debug_mode = True
        
        # Process all folders
        processed_count, total_pdfs = pdf_processor.process_legislation_folders()
        
        if total_pdfs > 0:
            # Update JSON references
            pdf_processor.update_json_with_pdf_references()
            
            print(f"\n✅ Batch conversion completed!")
            print(f"   📊 Processed {processed_count} legislation folders")
            print(f"   📄 Created {total_pdfs} PDF files")
            print(f"   🔄 Updated JSON files with PDF references")
            print(f"   🚀 Using Playwright for modern, high-quality PDF generation")
        else:
            print(f"\n⚠️  No PDF files were created")
            print("   Check if schedule HTML files exist in the expected directories")
        
        return total_pdfs > 0
        
    except Exception as e:
        print(f"❌ Error during batch conversion: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_playwright_installation():
    """Test Playwright installation and functionality."""
    print("Testing Playwright installation...")
    
    if not install_requirements():
        print("❌ Installation test failed")
        return False
    
    try:
        # Create a simple test
        processor = SchedulePDFProcessor(".", ".")
        
        # Test HTML content
        test_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Test Document</title>
        </head>
        <body>
            <h1>Test PDF Generation</h1>
            <p>This is a test document to verify Playwright PDF generation works correctly.</p>
            <table border="1">
                <tr><th>Column 1</th><th>Column 2</th></tr>
                <tr><td>Data 1</td><td>Data 2</td></tr>
            </table>
        </body>
        </html>
        """
        
        # Save test HTML
        with open("test_playwright.html", "w", encoding="utf-8") as f:
            f.write(test_html)
        
        # Convert to PDF
        result = processor.test_single_schedule_pdf("test_playwright.html", "test_playwright.pdf")
        
        # Cleanup
        import os
        if os.path.exists("test_playwright.html"):
            os.remove("test_playwright.html")
        
        if result and os.path.exists("test_playwright.pdf"):
            file_size = os.path.getsize("test_playwright.pdf")
            print(f"✅ Playwright test successful!")
            print(f"   📄 Test PDF created: test_playwright.pdf ({file_size} bytes)")
            return True
        else:
            print("❌ Playwright test failed")
            return False
            
    except Exception as e:
        print(f"❌ Playwright test error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    import os
    import sys
    
    # Check command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            # Test Playwright installation
            test_playwright_installation()
        elif sys.argv[1] == "batch_pdf":
            # Convert existing schedules to PDF
            batch_convert_schedules_to_pdf()
        else:
            # Process specific legislation with Playwright
            legislation_folder = sys.argv[1]
            process_specific_legislation_playwright(legislation_folder)
    else:
        # Run the full pipeline
        success = main()
        if not success:
            print("\n❌ Pipeline failed. Please check the errors above.")
            sys.exit(1)
        else:
            print("\n🎉 Pipeline completed successfully!")