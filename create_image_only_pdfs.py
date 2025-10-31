#!/usr/bin/env python3
"""
Create PDFs from image-only schedule directories.
This script finds schedules that either have no HTML or where we want to create
a separate PDF from just the images (sorted by last 2 digits).
"""

from scrape_schedules import SchedulePDFProcessor
from pathlib import Path
import argparse

def find_schedules_with_images(base_dir, include_with_html=False):
    """
    Find all schedules that have images.

    Args:
        base_dir: Base directory containing legislation folders
        include_with_html: If True, also process schedules that have HTML files

    Returns:
        List of (legislation_name, images_path, image_files)
    """
    base_path = Path(base_dir)
    results = []

    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.tiff', '.tif'}

    for legislation_dir in sorted(base_path.glob("legislation_*")):
        if not legislation_dir.is_dir():
            continue

        schedules_dir = legislation_dir / "schedules"
        if not schedules_dir.exists():
            continue

        # Find all image directories
        image_dirs = []

        # Check common locations
        possible_image_dirs = [
            schedules_dir / "images",
            schedules_dir / "Schedules" / "images",
            schedules_dir / "Images",
        ]

        for img_dir in possible_image_dirs:
            if img_dir.exists() and img_dir.is_dir():
                image_files = [f for f in img_dir.iterdir()
                             if f.is_file() and f.suffix.lower() in image_extensions]

                if image_files:
                    # Check for HTML files
                    html_files = list(schedules_dir.rglob("*.html")) + list(schedules_dir.rglob("*.htm"))

                    if not html_files or include_with_html:
                        image_dirs.append((img_dir, image_files))

        # Also check for direct images in schedules dir
        direct_images = [f for f in schedules_dir.iterdir()
                        if f.is_file() and f.suffix.lower() in image_extensions]

        if direct_images:
            html_files = list(schedules_dir.rglob("*.html")) + list(schedules_dir.rglob("*.htm"))
            if not html_files or include_with_html:
                image_dirs.append((schedules_dir, direct_images))

        # Add results
        for img_dir, img_files in image_dirs:
            results.append({
                'legislation': legislation_dir.name,
                'images_dir': img_dir,
                'image_files': img_files,
                'image_count': len(img_files)
            })

    return results

def create_pdfs_from_images(base_input_dir, base_output_dir, include_with_html=False, dry_run=False):
    """
    Create PDFs from all image directories in schedules.

    Args:
        base_input_dir: Input directory (e.g., "data/html")
        base_output_dir: Output directory (e.g., "data/legislations")
        include_with_html: Also process schedules that have HTML files
        dry_run: If True, only show what would be done without creating PDFs
    """
    processor = SchedulePDFProcessor(base_input_dir, base_output_dir)

    print(f"Scanning for schedules with images...")
    print(f"  Base input directory: {base_input_dir}")
    print(f"  Base output directory: {base_output_dir}")
    print(f"  Include schedules with HTML: {include_with_html}")
    print(f"  Dry run mode: {dry_run}")
    print()

    schedules_with_images = find_schedules_with_images(base_input_dir, include_with_html)

    if not schedules_with_images:
        print("No schedules with images found.")
        return

    print(f"Found {len(schedules_with_images)} schedule(s) with images:")
    for item in schedules_with_images:
        print(f"  - {item['legislation']}: {item['image_count']} images in {item['images_dir'].relative_to(base_input_dir)}")
    print()

    if dry_run:
        print("DRY RUN MODE - No PDFs will be created")
        return

    # Start browser session
    if not processor.start_browser_session():
        print("ERROR: Failed to start browser session")
        return

    try:
        created_count = 0
        failed_count = 0

        for item in schedules_with_images:
            legislation_name = item['legislation']
            images_dir = item['images_dir']
            image_files = item['image_files']

            print(f"\nProcessing {legislation_name}...")
            print(f"  Images directory: {images_dir}")
            print(f"  Image count: {len(image_files)}")

            # Create output directory
            output_dir = Path(base_output_dir) / legislation_name / "schedules_pdf"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Generate PDF filename
            pdf_name = f"{legislation_name}_images_schedule.pdf"
            pdf_output_path = output_dir / pdf_name

            print(f"  Output PDF: {pdf_output_path}")

            # Convert images to PDF
            success = processor.convert_images_to_pdf(
                image_files,
                str(pdf_output_path),
                title=f"{legislation_name} - Schedule"
            )

            if success and pdf_output_path.exists():
                file_size_mb = pdf_output_path.stat().st_size / (1024 * 1024)
                print(f"  ✓ SUCCESS: Created PDF ({file_size_mb:.2f} MB)")
                created_count += 1
            else:
                print(f"  ✗ FAILED: Could not create PDF")
                failed_count += 1

        print(f"\n{'='*70}")
        print(f"PDF Creation Summary:")
        print(f"  Total processed: {len(schedules_with_images)}")
        print(f"  Successfully created: {created_count}")
        print(f"  Failed: {failed_count}")
        print(f"{'='*70}")

    finally:
        processor.stop_browser_session()

def main():
    parser = argparse.ArgumentParser(
        description="Create PDFs from image-only schedules with proper sorting"
    )
    parser.add_argument(
        '--input-dir',
        default='data/html',
        help='Input directory containing legislation folders (default: data/html)'
    )
    parser.add_argument(
        '--output-dir',
        default='data/legislations',
        help='Output directory for PDFs (default: data/legislations)'
    )
    parser.add_argument(
        '--include-with-html',
        action='store_true',
        help='Also process schedules that have HTML files (create separate image PDFs)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without actually creating PDFs'
    )

    args = parser.parse_args()

    create_pdfs_from_images(
        args.input_dir,
        args.output_dir,
        args.include_with_html,
        args.dry_run
    )

if __name__ == "__main__":
    main()
