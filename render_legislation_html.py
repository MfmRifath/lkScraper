#!/usr/bin/env python3
"""
Generate HTML view of legislation with repeal info displayed under the title
"""

import json
import sys
import os

def render_legislation_html(json_data):
    """Render legislation JSON to HTML with repeal info under title"""

    html = ['<!DOCTYPE html>']
    html.append('<html lang="en">')
    html.append('<head>')
    html.append('    <meta charset="UTF-8">')
    html.append('    <meta name="viewport" content="width=device-width, initial-scale=1.0">')
    html.append(f'    <title>{json_data.get("title", "Legislation")}</title>')
    html.append('    <style>')
    html.append('        body { font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; line-height: 1.6; }')
    html.append('        .header { border-bottom: 3px solid #333; padding-bottom: 20px; margin-bottom: 20px; }')
    html.append('        .title { font-size: 28px; font-weight: bold; color: #1a1a1a; margin: 10px 0; }')
    html.append('        .repeal-notice { background-color: #fff3cd; border-left: 4px solid #dc3545; padding: 15px 20px; margin: 20px 0; border-radius: 4px; }')
    html.append('        .repeal-notice .repeal-header { color: #dc3545; font-weight: bold; font-size: 18px; margin-bottom: 8px; }')
    html.append('        .repeal-notice .repeal-text { color: #856404; font-size: 16px; }')
    html.append('        .repeal-notice .repealing-act { margin-top: 8px; font-size: 14px; color: #666; }')
    html.append('        .description { font-style: italic; color: #555; margin: 15px 0; font-size: 15px; }')
    html.append('        .metadata { color: #666; font-size: 14px; margin: 10px 0; }')
    html.append('        .section { margin: 25px 0; padding: 15px; background: #f8f9fa; border-radius: 5px; }')
    html.append('        .section-header { font-weight: bold; color: #0066cc; margin-bottom: 10px; }')
    html.append('        .section-content { margin-left: 20px; }')
    html.append('        .subsection { margin: 10px 0 10px 30px; }')
    html.append('        .subsection-identifier { font-weight: bold; color: #555; }')
    html.append('    </style>')
    html.append('</head>')
    html.append('<body>')

    # Header section
    html.append('    <div class="header">')
    html.append(f'        <div class="title">{json_data.get("title", "")}</div>')

    # Repeal notice - DISPLAYED PROMINENTLY UNDER THE TITLE
    if json_data.get('repeal_info') and json_data['repeal_info'].get('repealed'):
        repeal_info = json_data['repeal_info']
        html.append('        <div class="repeal-notice">')
        html.append('            <div class="repeal-header">⚠️ REPEALED LEGISLATION</div>')
        html.append(f'            <div class="repeal-text">{repeal_info.get("repeal_text", "")}</div>')

        if repeal_info.get('repealing_act'):
            act = repeal_info['repealing_act']
            html.append('            <div class="repealing-act">')
            html.append(f'                Repealed by: <strong>{act.get("name", "")}</strong>, ')
            html.append(f'                No. {act.get("number", "")} of {act.get("year", "")}')
            html.append('            </div>')

        html.append('        </div>')

    # Description
    if json_data.get('description'):
        html.append(f'        <div class="description">{json_data["description"]}</div>')

    # Metadata
    html.append('        <div class="metadata">')
    if json_data.get('enactment_date'):
        html.append(f'            Enacted: {json_data["enactment_date"]}')
    if json_data.get('metadata', {}).get('id'):
        html.append(f'            | ID: {json_data["metadata"]["id"]}')
    html.append('        </div>')

    html.append('    </div>')

    # Sections (simplified - just show first few sections as example)
    if json_data.get('parts'):
        for part in json_data['parts'][:1]:  # Just first part for example
            for group in part.get('section_groups', [])[:3]:  # First 3 groups
                for section in group.get('sections', [])[:5]:  # First 5 sections
                    html.append('    <div class="section">')
                    html.append(f'        <div class="section-header">Section {section.get("number", "")}: {section.get("title", "")}</div>')
                    html.append('        <div class="section-content">')

                    # Section content
                    for content in section.get('content', []):
                        html.append(f'            <p>{content}</p>')

                    # Subsections
                    for subsection in section.get('subsections', [])[:3]:  # First 3 subsections
                        html.append('            <div class="subsection">')
                        html.append(f'                <span class="subsection-identifier">{subsection.get("identifier", "")}</span>')
                        html.append(f'                {subsection.get("content", "")}')
                        html.append('            </div>')

                    html.append('        </div>')
                    html.append('    </div>')

    html.append('</body>')
    html.append('</html>')

    return '\n'.join(html)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 render_legislation_html.py <json_file> [output_html]")
        print("Example: python3 render_legislation_html.py data/legislations/legislation_A/legislation_A_143.json output.html")
        sys.exit(1)

    json_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'legislation_view.html'

    # Load JSON
    with open(json_file, 'r') as f:
        data = json.load(f)

    # Generate HTML
    html = render_legislation_html(data)

    # Save HTML
    with open(output_file, 'w') as f:
        f.write(html)

    print(f"✓ HTML rendered successfully: {output_file}")
    print(f"  Title: {data.get('title')}")
    if data.get('repeal_info', {}).get('repealed'):
        print(f"  ⚠️  REPEALED: {data['repeal_info'].get('repeal_text')}")


if __name__ == '__main__':
    main()
