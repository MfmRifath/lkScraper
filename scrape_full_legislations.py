import os
import json
import time
import random
from bs4 import BeautifulSoup, NavigableString
from typing import List, Dict
import re
import urllib.parse
import traceback

_ALNUM_RE = re.compile(r'^(?P<num>\d+)(?P<alpha>[A-Za-z\-]+)?$')

class MainHTMLProcessor:
    def __init__(self, html_folder=None, data_folder=None):
        """Initialize the HTMLProcessor with complete paths for HTML files and output JSON."""
        self.html_folder = html_folder
        self.data_folder = data_folder
        self.debug_mode = False
        self.section_count = 0
        self.last_section_number = 0
        self.sections_found = set()
        self.section_range = {"min": float('inf'), "max": 0}  # Track actual range
        self._ALNUM_RE = _ALNUM_RE

    def update_section_range(self, section_num: int):
        """Update the tracked section range"""
        if section_num < self.section_range["min"]:
            self.section_range["min"] = section_num
        if section_num > self.section_range["max"]:
            self.section_range["max"] = section_num

    
        # --- NEW: rescue “hidden blob” sections (also works for high numbers like 71A, 760A, etc.)
    def extract_high_number_sections(self, html_fragment: str):
        """
        Rescue sections from hidden 'selectedhtml' blobs, now with Illustrations + Explanations.
        Only add sections that don't already exist in the main extraction.
        """
        from bs4 import BeautifulSoup
        import re

        # ---- Local helpers for Explanations (same behavior as in process_section_table) ----
        def _split_off_explanations_blocks(raw: str):
            if not raw:
                return raw, []
            t = raw.replace("\r\n", "\n")
            tok = re.compile(r'(?im)^\s*Explanations?\b\s*[:\-–—]?\s*')
            matches = list(tok.finditer(t))
            if not matches:
                return raw, []
            blocks, main_segments = [], []
            prev_end = 0
            for i, m in enumerate(matches):
                main_segments.append(t[prev_end:m.start()])
                block_start = m.end()
                block_end = matches[i+1].start() if i+1 < len(matches) else len(t)
                blocks.append(t[block_start:block_end].strip())
                prev_end = block_end
            if prev_end < len(t):
                main_segments.append(t[prev_end:])

            main = "\n".join(seg.rstrip() for seg in main_segments if seg and seg.strip())
            return main, [b for b in blocks if b.strip()]

        def _parse_explanations_block(block: str):
            if not block:
                return {"title":"Explanation","content":[], "subsections":[]}
            norm = block.replace("\r\n","\n")
            item_rx = re.compile(
                r'(?m)^\s*\(?(\d+)\)?\s*[\.\-–—]?\s*(.*?)(?=^\s*\(?\d+\)?\s*[\.\-–—]?\s*|\Z)',
                re.S
            )
            out = []
            items = list(item_rx.finditer(norm))
            if items:
                for m in items:
                    num = m.group(1)
                    body = self.clean_text(m.group(2) or "")
                    if body:
                        out.append(f"{num}.- {body}")
            else:
                s = self.clean_text(block)
                if s:
                    out.append(s)
            return {"title":"Explanation","content":out, "subsections":[]}

        def _merge_parsed_explanations(expl_list):
            merged = {"title":"Explanation","content":[], "subsections":[]}
            seen=set()
            for ex in expl_list or []:
                for c in (ex.get("content") or []):
                    if c and c not in seen:
                        seen.add(c); merged["content"].append(c)
            return merged

        soup = BeautifulSoup(html_fragment or "", "html.parser")
        text = soup.get_text("\n")
        text = text.replace("\r\n", "\n").replace("\xa0", " ")
        text = re.sub(r"[ \t]+", " ", text)

        def _amend_list(block: str):
            return [m.strip() for m in re.findall(r'\[\s*§([^\]]+)\]', block or '')]

        def _trim_trailing_headers(raw: str) -> str:
            if not raw:
                return raw
            patterns = [
                r'(?im)^\s*CHAPTER\s+[A-Z0-9IVXLCDM]+\b',
                r'(?im)^\s*PART\s+[A-Z0-9IVXLCDM]+\b',
                r'(?m)^[^\S\r\n]*[A-Z][A-Z0-9 ,.&\-\[\]\(\)\/]{3,}$',
                r'(?m)^[^\n]*\b\d{1,4}[A-Za-z\-]*\.\s*$',
                # NEW: Subchapter patterns - filter out standalone subchapter headings
                r'(?m)^\s*(?:Mode|Method|Process|Procedure)\s+(?:of|for|to)\s+[A-Z][a-z]+.*$',  # "Mode of Seizure"
                r'(?m)^\s*Claims\s+to\s+[A-Z][a-z]+.*$',  # "Claims to Property seized"
                r'(?m)^\s*\(\d+\)\s+Of\s+[A-Z][a-z]+.*$',  # "(2) Of Sales of Movable Property"
                r'(?m)^\s*[A-Z][a-z]+\s+(?:of|to|for)\s+[A-Z][a-z]+(?:\s+[a-z]+)*\s*$',  # Title case subchapter headings
            ]
            earliest = -1
            for pat in patterns:
                m = re.search(pat, raw)
                if m:
                    earliest = m.start() if earliest == -1 else min(earliest, m.start())
            if earliest != -1:
                return raw[:earliest].rstrip()
            m2 = re.search(r'\b(?:CHAPTER|PART)\s+[A-Z0-9IVXLCDM]+\b', raw)
            return raw[:m2.start()].rstrip() if m2 else raw

        headers = []
        title_header_re = re.compile(
            r'(?m)^(?P<title>(?!\d+\.)(?!Explanations?\s*$)(?!Illustrations?\s*$)[^\n].*?)\s*(?P<amend_block>(?:\[\s*§?[^\]]+\]\s*)*)\n\s*(?P<num>\d+[A-Za-z\-]*)\.\s*'
        )
        for m in title_header_re.finditer(text):
            # Extract title and clean it, removing any amendment references
            raw_title = (m.group("title") or "").strip()
            # Remove amendment patterns like [ 2,50 of 1968], [§2, 53 of 1980], [109,20 of 1977]
            # This handles multiple amendments and various spacing patterns
            # Pattern: [ optional_spaces optional_§ digits comma optional_spaces digits space+ of space+ 4digits ]
            title = re.sub(r'\[\s*§?\s*\d+\s*,\s*\d+\s+of\s+\d{4}\s*\]', '', raw_title)
            title = self.clean_text(title.strip())
            num = (m.group("num") or "").strip()

            # Debug: log if we're about to add an Explanation section
            if self.debug_mode and title == "Explanation":
                print(f"\n  [DEBUG] Matched Explanation section: num={num}, title={title}")
                print(f"  [DEBUG] Context: {text[max(0, m.start()-50):min(len(text), m.end()+50)][:100]}")

            headers.append({
                "start": m.start(), "end": m.end(),
                "num": num,
                "amends": _amend_list(m.group("amend_block") or ""),
                "title": title,
                "kind": "title_first"
            })

        num_header_re = re.compile(r'(?m)^(?P<num>\d+[A-Za-z\-]*)\.\s*(?P<amend_block>(?:\[\s*§[^\]]+\]\s*)*)')
        for m in num_header_re.finditer(text):
            headers.append({
                "start": m.start(), "end": m.end(),
                "num": (m.group("num") or "").strip(),
                "amends": _amend_list(m.group("amend_block") or ""),
                "title": "", "kind": "num_first"
            })

        if not headers:
            return []

        title_ranges = [(h["start"], h["end"]) for h in headers if h["kind"] == "title_first"]
        filtered = []
        for h in headers:
            if h["kind"] == "num_first" and any(s <= h["start"] < e for (s, e) in title_ranges):
                continue
            filtered.append(h)
        headers = sorted(filtered, key=lambda h: h["start"])

        def _infer_title_from_body(body_text: str) -> str:
            first_line = (body_text or "").lstrip().split("\n", 1)[0].strip()
            if (first_line.endswith(".")
                and not re.match(r'^\(\s*(\d+|[a-z]|[ivxlcdm]+)\s*\)', first_line, flags=re.I)
                and len(first_line) <= 180):
                return self.clean_text(first_line)
            return ""

        def _sort_key(num_str: str):
            m = re.match(r'^(?P<num>\d+)(?P<alpha>[A-Za-z\-]+)?$', num_str or '')
            if m:
                return (int(m.group('num')), m.group('alpha') or '')
            return (10**9, num_str or '')

        TOP_NUMERIC_RE = re.compile(r'(?s)^\s*\((\d+)\)\s*(.*?)(?=^\s*\(\d+\)\s*|\Z)', re.M)
        TOP_LETTER_RE  = re.compile(r'(?s)^\s*\(([a-z])\)\s*(.*?)(?=^\s*\([a-z]\)\s*|\Z)', re.M | re.I)
        TOP_ROMAN_RE   = re.compile(r'(?s)^\s*\(([ivxlcdm]+)\)\s*(.*?)(?=^\s*\([ivxlcdm]+\)\s*|\Z)', re.M | re.I)

        BRIDGE_HEADS = r'(and that such persons may be cited|In cases falling under paragraphs|And in any case)'
        PEEL_FROM_LAST_RE = re.compile(r'(?:;|\.)\s*(?=(?:' + BRIDGE_HEADS + r')\b)', re.I)

        TAIL_RIDER_RE = re.compile(
            r'(?is)(^|[.;:]\s+)(?P<rider>('
            r'But no such item shall be allowed.*'
            r'|Provided that.*'
            r'|Provided further that.*'
            r'|In no case.*'
            r'|Nothing in this section.*'
            r'))$'
        )
        TAIL_COPY_ORDER_RE = re.compile(r'(?is)(^|[.;:]\s+)(A copy of such order\s+shall\s+be\s+affixed.*)$')

        # Get existing section numbers to avoid duplicates
        existing_sections = set()
        if hasattr(self, '_existing_section_numbers'):
            existing_sections = self._existing_section_numbers

        sections = []
        for idx, h in enumerate(headers):
            # Skip if this section already exists
            if h["num"] in existing_sections:
                continue
                
            body_start = h["end"]
            body_end   = headers[idx + 1]["start"] if idx + 1 < len(headers) else len(text)
            body_raw   = _trim_trailing_headers(text[body_start:body_end].strip())
            title_final = h["title"] if h["kind"] == "title_first" else _infer_title_from_body(body_raw)

            body_main, illu_block = self._split_off_illustrations_block(body_raw)
            if illu_block:
                illu_parsed = self._parse_illustrations_block(illu_block)
            else:
                illu_parsed = None

            body_main, expl_blocks = _split_off_explanations_blocks(body_main)
            expl_parsed = None
            if expl_blocks:
                expl_parsed = _merge_parsed_explanations([_parse_explanations_block(b) for b in expl_blocks])

            subsections = self.extract_subsections_from_text(body_main)

            preface_txt = ""
            tail_txt    = ""
            block_spans = []
            for rx in (TOP_NUMERIC_RE, TOP_LETTER_RE, TOP_ROMAN_RE):
                block_spans = [(m.start(), m.end()) for m in rx.finditer(body_main)]
                if block_spans:
                    break
            if block_spans:
                preface_txt = body_main[:block_spans[0][0]].strip()
                tail_txt    = body_main[block_spans[-1][1]:].strip()
            else:
                preface_txt = body_main.strip()
                tail_txt    = ""

            preface_clean = self.strip_leading_section_number(self.clean_text(preface_txt), h["num"]) if preface_txt else ""

            bridge_text = ""
            if not tail_txt and subsections:
                last = subsections[-1]
                raw_last = (last.get("content") or "")
                raw_last_norm = re.sub(r';\s*\.(?=\s*)', '; ', raw_last)
                m_bridge = PEEL_FROM_LAST_RE.search(raw_last_norm)
                if m_bridge:
                    last["content"] = raw_last_norm[:m_bridge.start()].rstrip(" ;:.-")
                    bridge_text     = raw_last_norm[m_bridge.end():].strip()

            tail_source = tail_txt or bridge_text
            tail_clean  = self.clean_text(tail_source) if tail_source else ""

            continuation_list = None
            if subsections:
                last = subsections[-1]
                last_text = (last.get("content") or "")

                m_fee = re.search(r'and\s+such\s+fee\s+shall\s+be\s+brought\s+to\s+account', last_text, flags=re.I)
                m_but = re.search(r'But\s+if\s+the\s+sale\s+was\s+effected\s+in\s+execution\s+of\s+a\s+decree', last_text, flags=re.I)
                cut_positions = [m.start() for m in (m_fee, m_but) if m]
                if cut_positions:
                    split_at = min(cut_positions)
                    cont_chunk = self.clean_text(last_text[split_at:].strip())
                    kept       = last_text[:split_at].rstrip(" \n.;:")
                    last["content"] = kept
                    if cont_chunk:
                        continuation_list = (continuation_list or []) + [{"content": [cont_chunk], "subsections": []}]
                    last_text = kept

                m_copy = TAIL_COPY_ORDER_RE.search(last_text)
                if m_copy:
                    rider = self.clean_text(m_copy.group(2).strip())
                    kept  = last_text[:m_copy.start(2)].rstrip(" \n.;:")
                    last["content"] = kept
                    continuation_list = (continuation_list or []) + [{"content": [rider], "subsections": []}]
                    last_text = kept

                m_rider2 = TAIL_RIDER_RE.search(last_text)
                if m_rider2:
                    rider = self.clean_text(m_rider2.group('rider').strip())
                    kept  = last_text[:m_rider2.start('rider')].rstrip(" \n.;:")
                    last["content"] = kept
                    continuation_list = (continuation_list or []) + [{"content": [rider], "subsections": []}]

            content_list = []
            if preface_clean:
                content_list.append(preface_clean)
            if tail_clean:
                if content_list:
                    content_list[0] = content_list[0] + "\n" + tail_clean
                else:
                    content_list = [tail_clean]

            amendment_info = [{"text": a, "link": None} for a in (h.get("amends") or [])] or None
            sec_obj = {
                "number": h["num"],
                "title": title_final or "",
                "content": content_list,
                "subsections": subsections,
                "amendment": amendment_info
            }
            if continuation_list:
                uniq = []
                seen = set()
                for c in continuation_list:
                    s = " ".join(c.get("content") or []).strip()
                    if s and s not in seen:
                        seen.add(s); uniq.append(c)
                if uniq:
                    sec_obj["continuation"] = uniq

            if illu_parsed and (illu_parsed.get("content") or illu_parsed.get("subsections")):
                self._attach_illustrations_to_section(sec_obj, illu_parsed)
            if expl_parsed and (expl_parsed.get("content") or expl_parsed.get("subsections")):
                sec_obj["Explanations"] = {
                    "title": "Explanation",
                    "content": expl_parsed.get("content") or [],
                    "subsections": []
                }

            # FILTER: Skip sections that look like standalone Explanations or Illustrations
            # These should be part of their parent section, not standalone sections
            if title_final in ['Explanation', 'Illustration', 'Explanations', 'Illustrations']:
                if self.debug_mode:
                    print(f"  [FILTER] Skipping standalone {title_final} section {h['num']}")
                continue

            sections.append(sec_obj)

        # merge by number (prefer richer)
        def _score(s):
            return (
                int(bool(s.get("title"))) +
                int(bool(s.get("content"))) +
                len(s.get("subsections") or []) +
                int(bool(s.get("continuation"))) +
                (2 if "Explanations" in s else 0) +
                (2 if "Illustrations" in s else 0)
            )
        unique = {}
        for s in sections:
            k = s["number"]
            if k not in unique or _score(s) > _score(unique[k]):
                unique[k] = s
        return sorted(unique.values(), key=lambda s: _sort_key(s["number"]))
            
        # --- NEW: robust textual subsection extraction (numeric → letters → roman)
    def extract_subsections_from_text(self, text: str):
        """
        Parse subsections from text.
        Preserves definition blocks as content, not subsections.
        """
        import re
        
        if not text:
            self._last_subsections_end = None
            return []

        work = text.replace("\r\n", "\n").replace("\xa0", " ")
        work = re.sub(r"\n{3,}", "\n\n", work)

        AMEND_RX = re.compile(r'\[\s*§[^\]]+\]\s*')

        def _clean_preserve_full(s: str) -> str:
            """Clean but preserve FULL content"""
            s = re.sub(r'\n+', ' ', s)
            s = re.sub(r'\s+', ' ', s).strip()
            s = AMEND_RX.sub('', s)
            return s

        # Check if this is a definitions/interpretation section
        # These patterns indicate the content should NOT be split into subsections
        definition_indicators = [
            r'unless\s+the\s+context\s+otherwise\s+requires',
            r'following\s+definitions?\s+shall\s+apply',
            r'following\s+expressions?\s+shall\s+have',
            r'words\s+and\s+expressions?\s+shall\s+have',
            r'In\s+this\s+(?:Chapter|Part|Act|section)',
        ]
        
        # Check if this looks like a definitions section
        is_definitions_section = False
        for pattern in definition_indicators:
            if re.search(pattern, work[:500], re.I):  # Check first 500 chars
                is_definitions_section = True
                break
        
        # Also check if it contains definition patterns
        if not is_definitions_section:
            # Count how many definition-like patterns exist
            def_pattern = re.compile(r'["\'\s]([a-zA-Z][a-zA-Z\s\-]*?)["\'\s]\s+(?:means|includes|shall\s+mean|shall\s+include)', re.I)
            matches = def_pattern.findall(work)
            if len(matches) >= 3:  # If 3+ definitions, treat as definitions section
                is_definitions_section = True
        
        # If this is a definitions section, don't extract subsections
        if is_definitions_section:
            self._last_subsections_end = None
            return []
        
        # Otherwise, proceed with normal subsection extraction
        out = []
        last_end = None
        
        # Standard subsection patterns
        # NOTE: Letter patterns now match any letter (a-z, case insensitive)
        patterns = [
            # Primary patterns (most common)
            (re.compile(r'(?s)^\s*\((\d+)\)\s*(.*?)(?=^\s*\(\d+\)\s*|\Z)', re.M), 'numeric'),
            # Uppercase letter pattern: (A), (B), (C) - for amendment clauses
            # IMPORTANT: This must come BEFORE lowercase letters in hierarchy for amendment laws
            # Example: legislation_B_27 section 3 has (A), (B), (C)... amendment clauses
            (re.compile(r'(?s)^\s*\(([A-Z])\)\s*(.*?)(?=^\s*\([A-Z]\)\s*|\Z)', re.M), 'upper_letter'),
            # Lowercase letter pattern: (a), (b), (c) - for normal subsections
            (re.compile(r'(?s)^\s*\(([a-z])\)\s*(.*?)(?=^\s*\([a-z]\)\s*|\Z)', re.M), 'lower_letter'),
            (re.compile(r'(?s)^\s*\(([ivxlcdm]+)\)\s*(.*?)(?=^\s*\([ivxlcdm]+\)\s*|\Z)', re.M | re.I), 'roman'),

            # Alternative formats
            (re.compile(r'(?s)^\s*([A-Z])\.\s*(.*?)(?=^\s*[A-Z]\.\s*|\Z)', re.M), 'upper_dot'),
            (re.compile(r'(?s)^\s*(\d+)\.\s*(?!\d)(.*?)(?=^\s*\d+\.\s*(?!\d)|\Z)', re.M), 'number_dot'),
            (re.compile(r'(?s)^\s*([a-z])\.\s*(.*?)(?=^\s*[a-z]\.\s*|\Z)', re.M), 'lower_dot'),
        ]
        
        best_pattern = None
        best_matches = []

        # Hierarchy order - prioritize patterns by hierarchy position, not just count
        # IMPORTANT: upper_letter comes before lower_letter - capital letters (A), (B), (C) are top-level in amendment laws
        hierarchy_priority = ['numeric', 'upper_letter', 'lower_letter', 'roman', 'upper_dot', 'number_dot', 'lower_dot']

        # Try each pattern - prioritize by which appears first in text, then hierarchy
        # This handles amendment laws where (A), (B), (C) appear before (1), (2), (3)
        for pattern, pattern_type in patterns:
            matches = list(pattern.finditer(work))
            if len(matches) >= 2:  # Need at least 2 matches
                if not best_pattern:
                    # First valid pattern
                    best_matches = matches
                    best_pattern = (pattern, pattern_type)
                else:
                    current_priority = hierarchy_priority.index(pattern_type)
                    best_priority = hierarchy_priority.index(best_pattern[1])

                    # IMPORTANT: For competing patterns at similar hierarchy levels,
                    # prefer the one that appears FIRST in the text
                    # Example: legislation_B_27 has (A), (B), (C) before (1), (2), (3)
                    current_first_pos = matches[0].start() if matches else float('inf')
                    best_first_pos = best_matches[0].start() if best_matches else float('inf')

                    # Only compare positions for patterns at adjacent hierarchy levels
                    # This preserves hierarchy (numeric before letters) while allowing position-based choice
                    if abs(current_priority - best_priority) <= 2:
                        # Close in hierarchy - use position as tiebreaker
                        if current_first_pos < best_first_pos:
                            # Appears earlier in text - use this pattern
                            best_matches = matches
                            best_pattern = (pattern, pattern_type)
                    elif current_priority < best_priority:
                        # Much earlier in hierarchy wins (regardless of position)
                        best_matches = matches
                        best_pattern = (pattern, pattern_type)
                    elif current_priority == best_priority and len(matches) > len(best_matches):
                        # Same hierarchy level, more matches wins
                        best_matches = matches
                        best_pattern = (pattern, pattern_type)
        
        if best_matches and len(best_matches) >= 2:  # Only extract if 2+ subsections
            pattern, pattern_type = best_pattern
            for m in best_matches:
                ident = m.group(1)
                block = m.group(2) or ""

                # Recursively parse nested subsections from the raw block (before cleaning)
                # But exclude the current pattern type to enforce proper hierarchy
                nested_subsections = self._extract_nested_subsections_with_hierarchy(block.strip(), pattern_type)

                # Clean content for this subsection
                content = _clean_preserve_full(block.strip())

                # If we found nested subsections, extract the preface (content before first nested subsection)
                if nested_subsections:
                    # Find where first nested subsection starts in the raw block
                    first_marker = re.search(r'(?m)^\s*(\(\s*[a-z0-9ivxlcdm]+\s*\)|\d+\.)\s+', block, re.I)
                    if first_marker:
                        preface = block[:first_marker.start()].strip()
                        content = _clean_preserve_full(preface)

                # Format identifier based on pattern type
                if pattern_type in ['upper_dot', 'number_dot', 'lower_dot']:
                    identifier = f"{ident}."
                else:
                    identifier = f"({ident})"

                if content or nested_subsections:  # Add if there's content OR nested subsections
                    out.append({
                        "identifier": identifier,
                        "content": content,
                        "subsections": nested_subsections
                    })

            # Record where subsections ended
            if best_matches:
                last_end = best_matches[-1].end()
                self._last_subsections_end = last_end
        
        self._last_subsections_end = last_end
        return out

    def _extract_nested_subsections_with_hierarchy(self, text: str, parent_pattern_type: str):
        """
        Extract nested subsections while respecting hierarchical structure.
        Pattern hierarchy: numeric (1) -> letter (a) -> roman (i) -> upper_dot (A.) -> number_dot (1.) -> lower_dot (a.)
        Only extracts patterns that are "deeper" in the hierarchy than the parent.
        """
        import re

        if not text:
            return []

        work = text.replace("\r\n", "\n").replace("\xa0", " ")
        work = re.sub(r"\n{3,}", "\n\n", work)

        AMEND_RX = re.compile(r'\[\s*§[^\]]+\]\s*')

        def _clean_preserve_full(s: str) -> str:
            s = re.sub(r'\n+', ' ', s)
            s = re.sub(r'\s+', ' ', s).strip()
            s = AMEND_RX.sub('', s)
            return s

        # Define pattern hierarchy - only allow patterns deeper than parent
        # IMPORTANT: Match the updated hierarchy with upper_letter and lower_letter separated
        hierarchy_order = ['numeric', 'upper_letter', 'lower_letter', 'roman', 'upper_dot', 'number_dot', 'lower_dot']

        try:
            parent_index = hierarchy_order.index(parent_pattern_type)
            allowed_patterns = hierarchy_order[parent_index + 1:]  # Only patterns deeper in hierarchy
        except (ValueError, IndexError):
            # If parent pattern not found or at end, allow all patterns
            allowed_patterns = hierarchy_order

        # Define all patterns but only use allowed ones
        all_patterns = [
            (re.compile(r'(?s)^\s*\((\d+)\)\s*(.*?)(?=^\s*\(\d+\)\s*|\Z)', re.M), 'numeric'),
            # Uppercase letter pattern: (A), (B), (C) - for amendment clauses
            (re.compile(r'(?s)^\s*\(([A-Z])\)\s*(.*?)(?=^\s*\([A-Z]\)\s*|\Z)', re.M), 'upper_letter'),
            # Lowercase letter pattern: (a), (b), (c) - for normal subsections
            (re.compile(r'(?s)^\s*\(([a-z])\)\s*(.*?)(?=^\s*\([a-z]\)\s*|\Z)', re.M), 'lower_letter'),
            (re.compile(r'(?s)^\s*\(([ivxlcdm]+)\)\s*(.*?)(?=^\s*\([ivxlcdm]+\)\s*|\Z)', re.M | re.I), 'roman'),
            (re.compile(r'(?s)^\s*([A-Z])\.\s*(.*?)(?=^\s*[A-Z]\.\s*|\Z)', re.M), 'upper_dot'),
            (re.compile(r'(?s)^\s*(\d+)\.\s*(?!\d)(.*?)(?=^\s*\d+\.\s*(?!\d)|\Z)', re.M), 'number_dot'),
            (re.compile(r'(?s)^\s*([a-z])\.\s*(.*?)(?=^\s*[a-z]\.\s*|\Z)', re.M), 'lower_dot'),
        ]

        # Filter to only allowed patterns
        patterns = [(p, t) for p, t in all_patterns if t in allowed_patterns]

        if not patterns:
            return []

        best_pattern = None
        best_matches = []

        # Try each allowed pattern - prioritize by hierarchy, use count as tiebreaker
        for pattern, pattern_type in patterns:
            matches = list(pattern.finditer(work))
            if len(matches) >= 2:  # Need at least 2 matches
                if not best_pattern:
                    # First valid pattern
                    best_matches = matches
                    best_pattern = (pattern, pattern_type)
                else:
                    # Compare: prefer earlier in hierarchy, or if same level prefer more matches
                    current_priority = hierarchy_order.index(pattern_type)
                    best_priority = hierarchy_order.index(best_pattern[1])

                    if current_priority < best_priority:
                        # Earlier in hierarchy wins
                        best_matches = matches
                        best_pattern = (pattern, pattern_type)
                    elif current_priority == best_priority and len(matches) > len(best_matches):
                        # Same hierarchy level, more matches wins
                        best_matches = matches
                        best_pattern = (pattern, pattern_type)

        out = []
        if best_matches and len(best_matches) >= 2:
            pattern, pattern_type = best_pattern
            for m in best_matches:
                ident = m.group(1)
                block = m.group(2) or ""

                # Recursively parse deeper nested subsections
                nested_subsections = self._extract_nested_subsections_with_hierarchy(block.strip(), pattern_type)

                # Clean content
                content = _clean_preserve_full(block.strip())

                # Extract preface if nested subsections exist
                if nested_subsections:
                    first_marker = re.search(r'(?m)^\s*(\(\s*[a-z0-9ivxlcdm]+\s*\)|\d+\.)\s+', block, re.I)
                    if first_marker:
                        preface = block[:first_marker.start()].strip()
                        content = _clean_preserve_full(preface)

                # Format identifier
                if pattern_type in ['upper_dot', 'number_dot', 'lower_dot']:
                    identifier = f"{ident}."
                else:
                    identifier = f"({ident})"

                if content or nested_subsections:
                    out.append({
                        "identifier": identifier,
                        "content": content,
                        "subsections": nested_subsections
                    })

        return out

    def _prep_full_content(self, raw: str, sec_no: str = None) -> str:
        """
        Prepare content WITHOUT truncating at punctuation marks.
        Preserves full text including commas, hyphens, etc.
        """
        if not raw:
            return ""
        
        # Remove headers but keep all content
        s = self._cut_headers(raw or "")
        
        # Clean the text but preserve ALL content
        s = self.clean_text(s)
        
        # Remove amendment references
        AMEND_RX = re.compile(r'\[\s*§?[^\]]+\]\s*')
        s = AMEND_RX.sub("", s)
        
        # Strip leading section number if present
        s = self.strip_leading_section_number(s, sec_no or "")
        
        # Remove leading dots
        s = re.sub(r'^\s*\.+\s*', '', s)
        s = s.strip()
        
        # Return the FULL content without any truncation
        return s if s and not self._is_trivial(s) else ""

    def _is_trivial(self, s: str) -> bool:
        """Check if content is trivial (only punctuation/whitespace)"""
        if not s:
            return True
        t = s.strip()
        return bool(re.fullmatch(r'[\.\-–—:;,\(\)\[\]•·\u2022\s]*', t))
    # In the clean_text method, add quote normalization:
    def clean_text(self, text):
        """Remove unnecessary line breaks and extra spaces while preserving content"""
        if not text:
            return ""
        
        # First, normalize escaped quotes
        text = text.replace('\\"', '"')
        text = text.replace("\\'", "'")
        
        # Replace newlines with spaces (but preserve the text)
        text = re.sub(r'\n+', ' ', text)
        
        # Consolidate multiple spaces into one
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Remove periods and spaces at the beginning ONLY
        text = re.sub(r'^[.\s]+', '', text)
        
        # Fix misplaced quotation marks
        text = re.sub(r'"\s+', '"', text)
        text = re.sub(r'\s+"', '"', text)
        
        # Fix spacing around colons and semicolons (but don't remove content)
        text = re.sub(r'\s*:\s*', ': ', text)
        text = re.sub(r'\s*;\s*', '; ', text)
        
        # DO NOT split or truncate at commas or hyphens
        # Keep the full text intact
        
        return text
    def extract_title_and_description(self, soup):
        """Extract title and description from the soup."""
        title = soup.find("font", class_="actname").text.strip() if soup.find("font", class_="actname") else "N/A"
        title = self.clean_text(title)

        # Remove section range from title (e.g., "(1 - 91)" or "(1-91)")
        # Pattern: space + (number + dash + number) at the end
        import re
        title = re.sub(r'\s*\(\s*\d+\s*[-–]\s*\d+\s*\)\s*$', '', title).strip()

        description_div = soup.find("div", align="justify")
        description = description_div.text.strip() if description_div else "N/A"
        return title, description

    def extract_repeal_info(self, soup):
        """
        Extract repeal information if the legislation has been repealed.
        Returns dict with repeal details or None if not repealed.
        """
        import re

        # Look for repeal notice in red font
        # Pattern: <font color="red">Repealed By...</font>
        red_fonts = soup.find_all("font", color="red")

        for font_tag in red_fonts:
            text = font_tag.get_text(strip=True)
            # Check if this contains repeal information
            if re.search(r'Repealed\s+By', text, re.I):
                # Extract the repealing act information
                # Example: "Repealed By The Ayurveda Act, No. 31 of 1961"
                repeal_text = self.clean_text(text)

                # Try to parse out the act name and number
                # Pattern: "Repealed By [Act Name], No. [Number] of [Year]"
                match = re.search(r'Repealed\s+By\s+(.+?),?\s*No\.\s*(\d+)\s+of\s+(\d+)', repeal_text, re.I)

                if match:
                    act_name = match.group(1).strip()
                    act_number = match.group(2).strip()
                    year = match.group(3).strip()

                    return {
                        "repealed": True,
                        "repeal_text": repeal_text,
                        "repealing_act": {
                            "name": act_name,
                            "number": act_number,
                            "year": year
                        }
                    }
                else:
                    # Couldn't parse structured info, just return the text
                    return {
                        "repealed": True,
                        "repeal_text": repeal_text
                    }

        # Not repealed
        return None

    def extract_preamble(self, soup):
        """Extracts all preamble texts from the soup object and cleans them."""
        preamble_list = []
        
        # Find all preamble paragraphs
        preamble_paragraphs = soup.find_all("p", class_="descriptioncontent")
        
        for p_tag in preamble_paragraphs:
            text = p_tag.get_text(strip=True)
            # Apply the clean_text function to clean each preamble item
            cleaned_text = self.clean_text(text)
            if cleaned_text:  # Only add non-empty texts
                preamble_list.append(cleaned_text)
        
        return preamble_list   

    def extract_numbers_and_amendment(self, soup, type, numbers):
        """Extract law, act, or ordinance numbers and their amendment links."""
        base_link = "https://www.lawlanka.com/lal_v2/pages/popUp/actPopUp.jsp?actId="
        section = soup.find("font", class_="ordinancestitle", string=lambda text: text and type in text)
        
        if section:
            table = section.find_parent("table")  # Get the parent table
            if table:
                for index, tr in enumerate(table.find_all("tr", language="Javascript1.2")):
                    link_tag = tr.find("a")  # Find the link element
                    number = link_tag.text.strip() if link_tag else tr.get_text(strip=True)

                    # Extract act ID if link exists
                    amendment_link = None
                    if link_tag and "href" in link_tag.attrs:
                        id = link_tag["href"].split("'")[1]
                        amendment_link = base_link + id  

                    # Always append the entry, even if there's no link
                    numbers.append({
                        "index": index + 1,
                        "number": number,
                        "amendment_link": amendment_link
                    })

    def extract_law_act_ordinance_data(self, soup):
        """Extract Law Nos, Act Nos, Ordinance Nos, and Enactment Date."""
        law_numbers = []
        act_numbers = []
        ordinance_numbers = []

        # Extract Act Nos
        self.extract_numbers_and_amendment(soup, "Act Nos", act_numbers)

        # Extract Law Nos
        self.extract_numbers_and_amendment(soup, "Law Nos", law_numbers)

        # Extract Ordinance Nos
        self.extract_numbers_and_amendment(soup, "Ordinance Nos", ordinance_numbers)

        return {
            "law_numbers": law_numbers,
            "act_numbers": act_numbers,
            "ordinance_numbers": ordinance_numbers,
        }

    def extract_enachment_date(self, soup):
        """Extract the enactment date from the soup object."""
        enactment_date = None
        date_section = soup.find("sup", class_="datesup")
        if date_section:
            date_text = date_section.find_parent("b").get_text(strip=True, separator=" ")
            enactment_date = " ".join(date_text.split())  

        return enactment_date

    def extract_enactment_year(self, enactment_date):
        """Extract the year from the enactment date."""
        if enactment_date:
            match = re.search(r"\b(\d{4})\b", enactment_date)
            if match:
                return match.group(1)
        return None    
    def _parse_repealed_ranges_from_text(self, raw_text: str):
        """
        Find patterns like:
        [§3, 36 of 2022]
        801 to 833R Repealed Sections.
        Returns: [{"start":801,"end":833,"end_alpha":"R","amend":"3, 36 of 2022",
                "start_label":"801","end_label":"833R"}]
        """
        if not raw_text:
            return []
        t = raw_text.replace("\r\n", "\n")
        # normalize spaces
        t = re.sub(r"[ \t]+", " ", t)

        patt_with_amend = re.compile(
            r'\[\s*§\s*([^\]]+?)\s*\]\s*[\n ]*'
            r'(\d+[A-Za-z\-]*)\s*(?:to|–|-|—)\s*'
            r'(\d+[A-Za-z\-]*)\s+Repealed\s+Sections?\.?',
            re.IGNORECASE
        )
        patt_no_amend = re.compile(
            r'(\d+[A-Za-z\-]*)\s*(?:to|–|-|—)\s*'
            r'(\d+[A-Za-z\-]*)\s+Repealed\s+Sections?\.?',
            re.IGNORECASE
        )

        ranges = []

        def _mk(start_s, end_s, amend):
            m_end = self._ALNUM_RE.match(end_s)
            end_alpha = m_end.group("alpha") if m_end else ""
            start_num = int(re.match(r"^(\d+)", start_s).group(1))
            end_num   = int(re.match(r"^(\d+)", end_s).group(1))
            ranges.append({
                "start": start_num,
                "end": end_num,
                "end_alpha": end_alpha or "",
                "amend": (amend or "").strip() or None,
                "start_label": start_s.strip(),
                "end_label": end_s.strip(),
            })

        for m in patt_with_amend.finditer(t):
            _mk(m.group(2), m.group(3), m.group(1))

        # Also allow cases without the bracket line (belt-and-suspenders)
        for m in patt_no_amend.finditer(t):
            _mk(m.group(1), m.group(2), None)

        return ranges


    def _apply_repealed_ranges(self, parts, ranges, containers=None):
        """
        For each range, mark existing sections as repealed and create numeric placeholders
        that are missing. Lettered variants (like 833A..833R) are handled by marking any
        section whose integer part is 833 as repealed.
        """
        if not parts or not ranges:
            return

        def _ensure_part(parts_list, number, title=None):
            for p in parts_list:
                if (p.get("number") or "") == (number or ""):
                    if title and not p.get("title"):
                        p["title"] = title
                    return p
            newp = {"number": number, "title": title, "section_groups": []}
            parts_list.append(newp)
            return newp

        def _ensure_group(part_obj, title=None):
            for g in part_obj.get("section_groups", []):
                if (g.get("title") or None) == (title or None):
                    g.setdefault("sections", [])
                    return g
            g = {"title": title, "sections": []}
            part_obj.setdefault("section_groups", []).append(g)
            return g

        def _pick_container(nint):
            if not containers:
                return None
            cands = [c for c in containers if isinstance(c.get("min"), int) and isinstance(c.get("max"), int)
                    and c["min"] <= nint <= c["max"]]
            if not cands:
                return None
            def _is_chapter(c): return str(c.get("number") or "").upper().startswith("CHAPTER")
            cands.sort(key=lambda c: (c["max"] - c["min"], 0 if _is_chapter(c) else 1))
            return cands[0]

        # Build a quick index of existing numbers by their *string* and *int* forms
        existing_by_str = set()
        existing_ints   = set()
        for p in parts:
            for g in p.get("section_groups", []) or []:
                for s in g.get("sections", []) or []:
                    nstr = s.get("number") or ""
                    if nstr:
                        existing_by_str.add(nstr)
                        m = re.match(r"^(\d+)", nstr)
                        if m:
                            existing_ints.add(int(m.group(1)))

        for r in ranges:
            start_n, end_n, end_alpha = r["start"], r["end"], r["end_alpha"]
            amend_txt = (f"§{r['amend']} — Repealed {r['start_label']} to {r['end_label']}"
                        if r.get("amend") else f"Repealed {r['start_label']} to {r['end_label']}")

            # 1) Mark all existing sections in range as repealed
            for p in parts:
                for g in p.get("section_groups", []) or []:
                    for s in g.get("sections", []) or []:
                        nstr = s.get("number") or ""
                        if not nstr:
                            continue
                        mm = self._ALNUM_RE.match(nstr)
                        if not mm:
                            continue
                        nint = int(mm.group("num"))
                        if nint < start_n or nint > end_n:
                            continue
                        # If it's the end number with/without letter, still repeal
                        # (covers 833, 833A..833R, etc.)
                        s["content"] = ["Repealed"]
                        s["subsections"] = []
                        s["amendment"] = self._merge_amendments(
                            s.get("amendment"),
                            [{"text": amend_txt, "link": None}]
                        )

            # 2) Create placeholders for *numeric* sections that don't exist
            for n in range(start_n, end_n + 1):
                num_str = str(n)
                if num_str in existing_by_str:
                    continue
                # Create placeholder
                dst = _pick_container(n)
                if dst:
                    dst_part = _ensure_part(parts, dst["number"], dst.get("title"))
                else:
                    dst_part = _ensure_part(parts, "MAIN PART", None)
                dst_group = _ensure_group(dst_part, None)
                placeholder = {
                    "number": num_str,
                    "title": None,
                    "content": ["Repealed"],
                    "subsections": [],
                    "amendment": [{"text": amend_txt, "link": None}]
                }
                dst_group.setdefault("sections", []).append(placeholder)

        # Keep everything ordered
        self._sort_sections_in_all_parts(parts)
    def extract_nested_subsections(self, parent_element):
        """
        Parses subsection blocks that are rendered as nested tables.
        NOTE: This does NOT try to split plain text like (a)(b)... — that's handled
        by extract_subsections_from_text(). We keep this purely for real nested <table> layouts.
        """
        if not parent_element:
            return []

        subsections = []
        section_start_re = re.compile(r'^\s*\d{3,4}[A-Za-z\-]*\.\s+')  # e.g., 833A., 760, 71A.

        # Only consider direct nested tables (one level down)
        subsection_tables = parent_element.find_all('table', cellspacing="2mm", recursive=False)

        for table in subsection_tables:
            identifier = ""
            subsection_content = table.find('font', class_='subsectioncontent')
            if not subsection_content:
                continue

            # IMPORTANT: Extract amendment from marginal notes in this subsection table
            # Amendments are in <tr class="morginalnotes"> within the left column
            subsection_amendment = self.extract_amendment_info(table)

            # Recursively parse nested <table>-based subsections under this block
            nested_subsections = self.extract_nested_subsections(subsection_content)

            # Extract direct text (text nodes directly under this element, not in nested tables)
            direct_text_parts = []
            for child in subsection_content.children:
                if isinstance(child, str):
                    direct_text_parts.append(child)
                elif child.name != 'table':  # Include non-table elements
                    direct_text_parts.append(child.get_text())

            direct_text = ''.join(direct_text_parts).strip()
            direct_text = self.clean_text(direct_text)

            # Try to extract a leading identifier from the direct text
            # IMPROVED: Handle leading quotes, spaces, and optional trailing spaces
            # Examples: "(a) text", " (a) text", '" (a) text', "(2)(a)...", "(e)(i)..."
            id_match = re.match(r'^[\s"\']*(\(\s*[a-z0-9ivxlcdm]+\s*\)|[A-Z]\.)\s*', direct_text, flags=re.IGNORECASE)
            if id_match:
                identifier = id_match.group(1)
                content = direct_text[id_match.end():].strip()
            else:
                content = direct_text

            # If this "subsection" actually starts a new section (e.g., "833R."),
            # skip it here; the high-number rescuer will promote it to a real section.
            if section_start_re.match(content):
                continue

            # Only append if we have meaningful content or nested subsections
            if content or nested_subsections:
                subsection_obj = {
                    "identifier": identifier,
                    "content": content,
                    "subsections": nested_subsections
                }

                # Add amendment if present
                if subsection_amendment:
                    subsection_obj["amendment"] = subsection_amendment

                subsections.append(subsection_obj)

        return subsections
    def _extract_num_alpha(self, num_str: str):
        """Return (int_num, alpha_suffix) from a section '763', '760A', etc."""
        if not num_str:
            return (None, "")
        m = self._ALNUM_RE.match(num_str)
        if not m:
            return (None, "")
        try:
            return (int(m.group("num")), (m.group("alpha") or ""))
        except Exception:
            return (None, "")

    def _ensure_part(self, parts, number, title=None):
        for p in parts:
            if p.get("number") == number:
                if title and not p.get("title"):
                    p["title"] = title
                return p
        newp = {"number": number, "title": title, "section_groups": []}
        parts.append(newp)
        return newp

    def _ensure_group(self, part_obj, title=None):
        # Reuse an existing group with same title (incl. None), else create
        for g in part_obj.get("section_groups", []):
            if (g.get("title") or None) == (title or None):
                g.setdefault("sections", [])
                return g
        g = {"title": title, "sections": []}
        part_obj.setdefault("section_groups", []).append(g)
        return g

    def _default_group(self, part_obj):
        # Get or create the default (untitled) group
        for g in part_obj.get("section_groups", []):
            if g.get("title") in (None, ""):
                g.setdefault("sections", [])
                return g
        g = {"title": None, "sections": []}
        part_obj.setdefault("section_groups", []).insert(0, g)
        return g

    def _absorb_unnumbered_as_continuations(self, parts):
        """
        Convert number==None fragments into 'continuation' of the closest numbered
        section in the same group. Leading fragments are attached to the first
        numbered section (so no stray null sections remain).
        """
        for part in parts:
            for grp in part.get("section_groups", []):
                sections = grp.get("sections", []) or []
                new_list, last_numbered, preface = [], None, []

                def _append_cont(target, frag):
                    cont = {"content": frag.get("content", [])[:], "subsections": frag.get("subsections", [])[:]}
                    if frag.get("amendment"):
                        cont["amendment"] = frag["amendment"]
                    target.setdefault("continuation", []).append(cont)

                for s in sections:
                    num = s.get("number")
                    has_payload = (s.get("content") or s.get("subsections") or s.get("amendment"))
                    if num is None:
                        if has_payload:
                            if last_numbered is not None:
                                _append_cont(last_numbered, s)
                            else:
                                preface.append(s)
                        # silently drop empty fragments
                        continue

                    # attach any accumulated preface to this first numbered section
                    if preface:
                        for frag in preface:
                            _append_cont(s, frag)
                        preface = []

                    new_list.append(s)
                    last_numbered = s

                grp["sections"] = new_list
    def collapse_main_part_into_textual(self, parts, textual_parts):
        """
        Re-home any sections left in MAIN PART into the best matching textual container
        (prefer the narrowest range covering the section; if none covers it, use the
        nearest container by numeric distance). Remove MAIN PART if it becomes empty.
        """
        if not parts or not textual_parts:
            return

        # Build a normalized textual index with numeric ranges
        tindex = []
        for tp in (textual_parts or []):
            try:
                if isinstance(tp.get("min"), int) and isinstance(tp.get("max"), int):
                    tindex.append({
                        "number": tp.get("number"),
                        "title": tp.get("title"),
                        "min": int(tp["min"]),
                        "max": int(tp["max"]),
                        "groups": tp.get("groups") or []
                    })
            except Exception:
                continue
        if not tindex:
            return

        def _is_chapter(tp): 
            return str(tp.get("number") or "").upper().startswith("CHAPTER")

        def _nearest_textual_for(nint):
            best, best_key = None, (10**9, 10**9, 1)  # (distance, width, part-preference)
            for tp in tindex:
                width = tp["max"] - tp["min"]
                if tp["min"] <= nint <= tp["max"]:
                    # Inside: zero distance; prefer narrower; prefer CHAPTER
                    key = (0, width, 0 if _is_chapter(tp) else 1)
                else:
                    dist = min(abs(nint - tp["min"]), abs(nint - tp["max"]))
                    key = (dist, width, 0 if _is_chapter(tp) else 1)
                if key < best_key:
                    best, best_key = tp, key
            return best

        # Local helpers (reuse your existing ones if they’re class methods)
        def _ensure_part(parts_list, number, title=None):
            for p in parts_list:
                if p.get("number") == number:
                    if title and not p.get("title"):
                        p["title"] = title
                    return p
            newp = {"number": number, "title": title, "section_groups": []}
            parts_list.append(newp)
            return newp

        def _ensure_group_by_title(part_obj, title):
            for g in part_obj.get("section_groups", []):
                if (g.get("title") or None) == (title or None):
                    g.setdefault("sections", [])
                    return g
            g = {"title": title, "sections": []}
            part_obj.setdefault("section_groups", []).append(g)
            return g

        def _default_group(part_obj):
            for g in part_obj.get("section_groups", []):
                if g.get("title") in (None, ""):
                    g.setdefault("sections", [])
                    return g
            g = {"title": None, "sections": []}
            part_obj.setdefault("section_groups", []).insert(0, g)
            return g

        main = next((p for p in parts if p.get("number") == "MAIN PART"), None)
        if not main:
            return

        # Move each numbered section out of MAIN PART
        for g in list(main.get("section_groups", [])):
            for s in list(g.get("sections", []) or []):
                nint, _ = self._extract_num_alpha(s.get("number"))
                if not isinstance(nint, int):
                    continue
                tp = _nearest_textual_for(nint)
                if not tp:
                    continue

                dst_part = _ensure_part(parts, tp["number"], tp.get("title"))

                # Prefer a textual subgroup whose [min,max] contains nint
                dst_group = None
                for tg in (tp.get("groups") or []):
                    if isinstance(tg.get("min"), int) and isinstance(tg.get("max"), int) and tg["min"] <= nint <= tg["max"]:
                        dst_group = _ensure_group_by_title(dst_part, tg.get("title"))
                        break
                if not dst_group:
                    dst_group = _default_group(dst_part)

                # Move
                try:
                    g["sections"].remove(s)
                except ValueError:
                    pass
                dst_group.setdefault("sections", []).append(s)

        # Drop MAIN PART if now empty
        is_empty = True
        for grp in main.get("section_groups", []):
            if grp.get("sections"):
                is_empty = False
                break
        if is_empty:
            try:
                parts.remove(main)
            except ValueError:
                pass
    def _sort_sections_in_all_parts(self, parts):
        def _key(sec):
            n, a = self._extract_num_alpha(sec.get("number"))
            # push None to far end but keep stable
            return (float('inf') if n is None else n, a or "")
        for part in parts:
            for grp in part.get("section_groups", []):
                grp["sections"].sort(key=_key)
    def assemble_part_chapter_group_structure(self, parts, textual_parts):
        """
        FLAT output: each PART/CHAPTER has section_groups only (no 'chapters' key).
        Preserves SubChapter structure when present within CHAPTER groups.
        """
        if not parts:
            return []

        def _nint(section_obj):
            n, _ = self._extract_num_alpha(section_obj.get("number"))
            return n

        def _sec_sort_key(sec):
            n, a = self._extract_num_alpha(sec.get("number"))
            return (10**9 if n is None else n, a or "")

        flat_parts = []
        for p in parts or []:
            out_groups = []
            for g in p.get("section_groups", []) or []:
                # Check if this group has SubChapters
                if "SubChapter" in g and g.get("SubChapter"):
                    # This is a CHAPTER with SubChapters - create the proper structure
                    out_group = {
                        "number": g.get("number"),
                        "title": g.get("title"),
                        "sections": [],  # Any remaining top-level sections
                        "SubChapter": g.get("SubChapter")  # Pass through the SubChapter structure as-is
                    }
                    
                    # Include any top-level sections that weren't moved to SubChapters
                    if g.get("sections"):
                        secs = g.get("sections")[:]
                        secs.sort(key=_sec_sort_key)
                        out_group["sections"] = secs
                    
                    # Sort sections within each SubChapter's section_groups
                    for sc in out_group["SubChapter"]:
                        for sg in sc.get("section_groups", []):
                            if sg.get("sections"):
                                sg["sections"].sort(key=_sec_sort_key)
                    
                    out_groups.append(out_group)
                else:
                    # Regular section group without SubChapters
                    secs = (g.get("sections") or [])[:]
                    if not secs:
                        continue
                    secs.sort(key=_sec_sort_key)
                    out_groups.append({
                        "number": g.get("number"),
                        "title": g.get("title"), 
                        "sections": secs
                    })
                    
            if out_groups:
                flat_parts.append({
                    "number": p.get("number") or "MAIN PART", 
                    "title": p.get("title"), 
                    "section_groups": out_groups
                })

        if not flat_parts:
            collected = []
            for p in parts or []:
                for g in p.get("section_groups", []) or []:
                    collected.extend(g.get("sections", []) or [])
            if collected:
                collected.sort(key=_sec_sort_key)
                flat_parts = [{
                    "number": "MAIN PART", 
                    "title": None, 
                    "section_groups": [{"title": None, "sections": collected}]
                }]

        def _first_num_in_part(pobj):
            nums = []
            for g in pobj.get("section_groups", []):
                # Check both direct sections and SubChapter sections
                for s in g.get("sections", []) or []:
                    n = _nint(s)
                    if isinstance(n, int):
                        nums.append(n)
                # Also check SubChapter sections
                if "SubChapter" in g:
                    for sc in g.get("SubChapter", []) or []:
                        for sg in sc.get("section_groups", []) or []:
                            for s in sg.get("sections", []) or []:
                                n = _nint(s)
                                if isinstance(n, int):
                                    nums.append(n)
            return min(nums) if nums else 10**9

        flat_parts.sort(key=_first_num_in_part)
        return flat_parts
    
    # In _sanitize_short_title_section, ensure it returns a new content array:
    # In _sanitize_short_title_section, ensure it returns a new content array:
    def _sanitize_short_title_section(self, section):
        """
        If the section title is 'Short title' (or similar), keep only the citation
        sentence and remove any subsections/aux blocks.
        """
        import re

        title = (section.get("title") or "").strip().lower()
        if not re.search(r'\bshort\s*title\b', title):
            return section

        # Work with a copy to avoid modifying the original
        section = section.copy()
        
        joined = " ".join([c for c in (section.get("content") or []) if c]).strip()
        
        if not joined:
            section["content"] = []
            section["subsections"] = []
            section.pop("Explanations", None)
            section.pop("Illustrations", None)
            return section

        # Find citation
        m = re.search(
            r'(?is)\b(This\s+(?:Act|Ordinance|Law|Regulation|Regulations)\s+may\s+be\s+cited\s+as\s+)',
            joined
        )

        citation = None
        if m:
            start = m.start()
            window = joined[start:start + 600]

            # IMPROVED: Look for sentence-ending period, not abbreviations like "No."
            # Match periods that are followed by:
            # - End of string, OR
            # - Space + capital letter (new sentence), OR
            # - Space + "and" (continuation clause)
            # But NOT periods in: No., Vol., etc.

            # Find all periods and check which one ends the sentence
            period_pos = -1
            for match in re.finditer(r'\.', window):
                pos = match.start()
                # Check what follows the period
                after = window[pos+1:pos+10].lstrip()

                # Skip if it's an abbreviation (No., Vol., etc.)
                before = window[max(0,pos-5):pos]
                if re.search(r'(?i)\b(No|Vol|Art|Sec|Ch)\s*$', before):
                    continue

                # This looks like a sentence-ending period if:
                # - Nothing after it (end of text)
                # - Followed by capital letter or "and"
                # - Followed by newline/paragraph break
                if not after or after[0].isupper() or after.lower().startswith('and ') or after.startswith('\n'):
                    period_pos = pos
                    break

            if period_pos != -1:
                citation = self.clean_text(window[:period_pos + 1])

        if not citation and joined:
            # IMPROVED FALLBACK: Don't split at first period blindly
            # Look for the full citation sentence, handling "No." abbreviations
            if "cited as" in joined.lower():
                # Try to extract the full citation including "No. X of YEAR"
                citation_match = re.search(
                    r'(?is)(This\s+(?:Act|Ordinance|Law|Regulation|Regulations)\s+may\s+be\s+cited\s+as\s+[^.]+(?:No\.\s*\d+\s+of\s+\d{4})?[^.]*\.)',
                    joined
                )
                if citation_match:
                    citation = self.clean_text(citation_match.group(1))

        # Create new content array instead of modifying existing
        section["content"] = [citation] if citation else []
        section["subsections"] = []
        section.pop("Explanations", None)
        section.pop("Illustrations", None)
        return section
    def route_sections_to_related_parts(self, parts, textual_parts):
        """
        Re-home sections into the correct textual container (CHAPTER/PART),
        preferring the *narrowest* matching container. If two containers have
        the same width, prefer CHAPTER over PART. Works across *all* groups,
        not just MAIN PART.
        """
        tparts = textual_parts or []
        if not tparts:
            self._absorb_unnumbered_as_continuations(parts)
            for p in parts:
                for g in p.get("section_groups", []):
                    g.pop("_range_hint", None)
            self._sort_sections_in_all_parts(parts)
            return

        # Normalize textual items with ranges
        tindex = []
        for tp in tparts:
            if isinstance(tp.get("min"), int) and isinstance(tp.get("max"), int):
                tindex.append({
                    "number": tp.get("number"),
                    "title": tp.get("title"),
                    "min": int(tp["min"]), "max": int(tp["max"]),
                    "groups": tp.get("groups") or []
                })

        if not tindex:
            self._absorb_unnumbered_as_continuations(parts)
            for p in parts:
                for g in p.get("section_groups", []):
                    g.pop("_range_hint", None)
            self._sort_sections_in_all_parts(parts)
            return

        def _pick_textual(nint):
            cands = [tp for tp in tindex if tp["min"] <= nint <= tp["max"]]
            if not cands:
                return None
            def _is_chapter(tp): return str(tp.get("number") or "").upper().startswith("CHAPTER")
            # Narrowest span first; tie: CHAPTER before PART; final tie: original order
            cands.sort(key=lambda tp: (tp["max"] - tp["min"], 1 if _is_chapter(tp) else 0))
            return cands[0]

        def _ensure_part(parts_list, number, title=None):
            for p in parts_list:
                if p.get("number") == number:
                    if title and not p.get("title"):
                        p["title"] = title
                    return p
            newp = {"number": number, "title": title, "section_groups": []}
            parts_list.append(newp)
            return newp

        def _ensure_group_by_title(part_obj, title):
            for g in part_obj.get("section_groups", []):
                if (g.get("title") or None) == (title or None):
                    g.setdefault("sections", [])
                    return g
            g = {"title": title, "sections": []}
            part_obj.setdefault("section_groups", []).append(g)
            return g

        moves = []  # (src_group, section, dst_group)

        # Decide destination for every numbered section across ALL groups/parts
        for p in parts:
            for g in p.get("section_groups", []):
                # If group has a range hint and it matches, we keep it
                rh = g.get("_range_hint")
                has_hint = isinstance(rh, dict) and isinstance(rh.get("min"), int) and isinstance(rh.get("max"), int)

                for s in list(g.get("sections", [])):
                    num_str = s.get("number")
                    nint, _ = self._extract_num_alpha(num_str)
                    if nint is None:
                        continue

                    if has_hint and rh["min"] <= nint <= rh["max"]:
                        continue

                    tp = _pick_textual(nint)
                    if not tp:
                        continue

                    # Create/find textual container
                    dst_part = _ensure_part(parts, tp["number"], tp.get("title"))

                    # Prefer subgroup whose range covers nint
                    dst_group = None
                    for tg in tp["groups"]:
                        if isinstance(tg.get("min"), int) and isinstance(tg.get("max"), int) and tg["min"] <= nint <= tg["max"]:
                            # reuse by title
                            for eg in dst_part.get("section_groups", []):
                                if (eg.get("title") or None) == (tg.get("title") or None):
                                    dst_group = eg
                                    break
                            if not dst_group:
                                dst_group = {"title": tg.get("title"), "sections": [], "_range_hint": {"min": tg["min"], "max": tg["max"]}}
                                dst_part.setdefault("section_groups", []).append(dst_group)
                            break

                    # Else use default group of the textual part and seed a broad hint
                    if not dst_group:
                        dst_group = self._default_group(dst_part)
                        dst_group.setdefault("_range_hint", {"min": tp["min"], "max": tp["max"]})

                    if g is not dst_group:
                        moves.append((g, s, dst_group))

        # Execute moves
        for src_g, s, dst_g in moves:
            try:
                src_g["sections"].remove(s)
            except ValueError:
                pass
            dst_g.setdefault("sections", []).append(s)

        # Fold unnumbered fragments into 'continuation'
        self._absorb_unnumbered_as_continuations(parts)

        # Cleanup: drop hints and sort sections
        for p in parts:
            for g in p.get("section_groups", []):
                g.pop("_range_hint", None)
        self._sort_sections_in_all_parts(parts)
    def _shape_named_continuation(self, tail_text: str) -> str:
        """
        From a big 'tail' blob, keep ONLY the named narrative that starts with:
        'When one of two or more courts may entertain an action.'
        and include the following paragraph(s), especially the 'Provided that ...' line.
        Returns a single cleaned paragraph or '' if not found.
        """
        import re
        if not tail_text:
            return ""
        t = self.clean_text(tail_text)

        # 1) Exact, safe capture for §9 case
        m = re.search(
            r'(?is)\b(When one of two or more courts may entertain an action\.\s*'
            r'When it is alleged.*?jurisdiction:?\s*'
            r'(?:Provided that.*?\.)?)',
            t
        )
        if m:
            block = self.clean_text(m.group(1))
            # strip stray amendments and repeated bullets just in case
            block = re.sub(r'\[[^\]]+\]', '', block)                  # [§…]
            block = re.sub(r'\b\d{1,4}\s*\.\s*', '', block)           # '9 .'
            block = re.sub(r'\(\s*[a-z]\s*\)\s*[^;]+;?\s*', '', block, flags=re.I)  # (a) …; (b) …
            return block.strip()

        # 2) Generic fallback anchored on key phrases (keeps it tight)
        m2 = re.search(r'(?is)\b(When one of two|When it is alleged|Provided that)\b.*', t)
        if not m2:
            return ""
        block = self.clean_text(m2.group(0))
        block = re.sub(r'\[[^\]]+\]', '', block)
        block = re.sub(r'\b\d{1,4}\s*\.\s*', '', block)
        block = re.sub(r'(?:\(\s*[a-z]\s*\)\s*[^;]+;?\s*)+', '', block, flags=re.I)
        # Heuristic: keep only the first 2–3 sentences (prevents runaway grabs)
        sentences = re.split(r'(?<=[\.\?\!])\s+', block)
        block = ' '.join(sentences[:3]).strip()
        return block


    def _clean_continuation_chunks(self, chunks, base_text: str):
        """
        De-duplicate, strip amendments/section numbers/bullets, and drop anything
        that largely repeats the base section content.
        """
        import re
        if not chunks:
            return []

        base = self.clean_text(base_text or "")
        base_tokens = set(base.split())

        out, seen = [], set()
        for c in chunks:
            cc = self.clean_text(c)
            # strip obvious noise
            cc = re.sub(r'\[[^\]]+\]', '', cc)               # [§…]
            cc = re.sub(r'\b\d{1,4}\s*\.\s*', '', cc)        # '9 .' '745 .'
            cc = re.sub(r'(?:\(\s*[a-z]\s*\)\s*[^;]+;?\s*)+', '', cc, flags=re.I)  # bullets
            cc = cc.strip()
            if not cc:
                continue
            if cc in seen or cc in base:
                continue
            # token overlap filter (drop if >60% of cont tokens are already in base)
            cont_tokens = set(cc.split())
            if cont_tokens and (len(cont_tokens & base_tokens) / max(1, len(cont_tokens)) > 0.60):
                continue
            seen.add(cc)
            out.append(cc)
        return out
    def _extract_text_excluding_tables(self, element):
        """
        Extract text from an element while excluding text from nested tables.
        This prevents amendment references (which are often in nested tables) from
        being included in section titles.
        """
        from bs4 import NavigableString, Tag

        if not element:
            return ""

        text_parts = []
        for child in element.children:
            if isinstance(child, NavigableString):
                text_parts.append(str(child))
            elif isinstance(child, Tag):
                # Skip nested tables (they often contain amendment references)
                if child.name == 'table':
                    continue
                # Skip nested <br> tags
                elif child.name == 'br':
                    text_parts.append(' ')
                else:
                    # Recursively extract text from other tags
                    text_parts.append(self._extract_text_excluding_tables(child))

        return ' '.join(text_parts)

    def process_section_table(self, table, previous_section=None):
        """
        Parse a single section table with complete content extraction.
        FIXED: Ensures interpretation sections capture content after hyphens.
        """
        import re
        from bs4 import Tag, NavigableString

        try:
            if not hasattr(self, "_skip_tables"):
                self._skip_tables = set()
            if id(table) in self._skip_tables:
                return None

            AMEND_RX = re.compile(r'\[\s*§?[^\]]+\]\s*')

            # Extract core fields
            section_number_tag = table.find("a", href=lambda href: href and "consSelectedSection" in href)
            section_title_tag = table.find("font", class_="sectionshorttitle")

            section_number = section_number_tag.text.strip() if section_number_tag else None

            amendment_info = self.extract_amendment_info(table)

            # IMPROVED: Check if amendment marker indicates a different section than the content link
            # This handles cases like legislation_A_17 section 7, where:
            # - Amendment says "[7, 4 of 1991]" or "[§6, 4 of 1991]"
            # - But content starts with "8." (belongs to next section)
            # In this case, the table is for section 7, and section 8 should be extracted separately
            # The § symbol indicates "section" in the amendment reference
            import re
            amendment_section_num = None
            if amendment_info:
                first_amendment = amendment_info[0].get("text", "")
                # Extract section number from pattern like "[7, 4 of 1991]" or "[§6, 4 of 1991]"
                match = re.match(r'\[\s*§?(\d+)\s*[,\]]', first_amendment)
                if match:
                    amendment_section_num = match.group(1)

            # Flag to skip content extraction (for mismatch cases)
            skip_content_extraction = False

            # CASE 1: Section link exists - always trust it over amendment marker
            # Amendment marker like "[7, 4 of 1991]" means "modified by section 7 of Act 4/1991"
            # It does NOT mean this is section 7 of the current Act
            if section_number and amendment_section_num and amendment_section_num != section_number:
                if self.debug_mode:
                    print(f"  [INFO] Section link says {section_number}, amendment marker says {amendment_section_num}")
                    print(f"    Amendment markers refer to the amending Act, not current section")
                    print(f"    Using section link: {section_number}")
                # Keep section_number as is (from the content link)

            # CASE 2: No content link but amendment marker exists
            # DON'T use amendment marker as section number - it refers to the amending Act, not this section
            # Instead, use sequential numbering if previous_section is available
            elif not section_number and amendment_section_num:
                if previous_section:
                    # Try to get numeric value of previous section
                    prev_num = previous_section.get('number', '')
                    if prev_num and prev_num.replace('.', '').isdigit():
                        try:
                            next_num = int(float(prev_num)) + 1
                            section_number = str(next_num)
                            if self.debug_mode:
                                print(f"  [SEQUENTIAL] No section link found. Previous section was {prev_num}, using sequential number {section_number}")
                        except ValueError:
                            section_number = amendment_section_num
                            if self.debug_mode:
                                print(f"  [FALLBACK] Could not parse previous section '{prev_num}', using amendment number '{section_number}'")
                    else:
                        section_number = amendment_section_num
                        if self.debug_mode:
                            print(f"  [FALLBACK] No valid previous section, using amendment number '{section_number}'")
                else:
                    section_number = amendment_section_num
                    if self.debug_mode:
                        print(f"  [FALLBACK] No previous section available, using amendment number '{section_number}' from '{first_amendment}'")

            # Extract title while excluding nested tables (which often contain amendment references)
            if section_title_tag:
                title_text = self._extract_text_excluding_tables(section_title_tag)
                title = self.clean_text(title_text.strip())
            else:
                title = None

            # Debug logging for sections 13-20 in legislation_B_71
            if section_number and section_number.isdigit() and 13 <= int(section_number) <= 20:
                if self.debug_mode:
                    print(f"  [DEBUG] Extracting section {section_number} from table")

            # CRITICAL FIX: Extract ALL content from the table AND continuation tables
            all_text_parts = []

            # Collect tables to process: main table + any continuation tables (always needed for later checks)
            tables_to_process = [table]

            # IMPORTANT: Detect interpretation sections BEFORE continuation check
            # This is needed so we know whether to treat amendment markers as new sections
            is_interpretation_section = False
            if title and 'interpretation' in title.lower():
                is_interpretation_section = True

            # Skip content extraction if amendment/content mismatch detected
            if not skip_content_extraction:

                # Check for continuation tables (tables immediately following without section number)
                # FIXED: Only check for section LINK to determine if it's a new section
                # sectionshorttitle can appear in continuation tables (e.g., definition terms)
                next_table = table.find_next_sibling("table")
                while next_table:
                    # Check if this is a NEW section (has section link)
                    # The definitive indicator of a new section is the consSelectedSection link
                    has_section_link = next_table.find("a", href=lambda href: href and "consSelectedSection" in href)

                    # If it has a section link, it's definitely a new section
                    if has_section_link:
                        break

                    # Check if it has an amendment marker
                    # IMPORTANT: For interpretation sections, amendments can appear within definitions
                    # and should NOT be treated as new sections. Only treat amendment markers as
                    # new sections if this is NOT an interpretation section.
                    has_amendment = next_table.find("a", href=lambda href: href and "openSectionOrdinanceWindow" in href)
                    if has_amendment and not is_interpretation_section:
                        # Only treat amendment marker as new section for non-interpretation sections
                        # Example: Section 7 "Repealed" has amendment [6, 4 of 1991] but no section link
                        if self.debug_mode:
                            print(f"  [CONTINUATION CHECK] Next table has amendment marker - treating as new section, not continuation")
                        break

                    # Check if it has section content (continuation table)
                    has_section_content = next_table.find("font", class_="sectioncontent")
                    has_subsection_content = next_table.find("font", class_="subsectioncontent")

                    # If it has content and matches section table attributes, it's a continuation
                    if has_section_content or has_subsection_content:
                        if (next_table.get("cellpadding") == "0" and
                            next_table.get("cellspacing") == "4mm" and
                            next_table.get("align") == "center" and
                            next_table.get("border") == "0"):
                            tables_to_process.append(next_table)
                            next_table = next_table.find_next_sibling("table")
                            continue
                    break

                # IMPORTANT: For interpretation sections, we'll process continuation tables separately
                # to preserve amendment-to-definition mapping. Don't collect amendments here.
                # Each definition will get its own amendment from its continuation table.

                # Extract content from all tables (main + continuations)
                for tbl in tables_to_process:
                    # Get content from sectioncontent tags
                    section_content_tags = tbl.find_all("font", class_="sectioncontent")
                    for tag in section_content_tags:
                        # Extract ALL text including nested subsections
                        # The regex will parse out the subsections later
                        tag_text = tag.get_text(separator="\n", strip=False)
                        if tag_text:
                            all_text_parts.append(tag_text)

                    # IMPORTANT: ALSO extract subsectioncontent tags (in addition to sectioncontent)
                    # This is needed for amendment laws where (A), (B), (C) clauses are in subsectioncontent
                    # Example: legislation_B_27 section 3 has main text in sectioncontent,
                    # and amendment clauses (A), (B), (C)... in subsectioncontent tags
                    subsection_tags = tbl.find_all("font", class_="subsectioncontent")
                    for tag in subsection_tags:
                        tag_text = tag.get_text(separator="\n", strip=False)
                        if tag_text:
                            all_text_parts.append(tag_text)

            # If no content tags found, get all text from main table only
            if not all_text_parts:
                for element in table.find_all(text=True):
                    text = str(element).strip()
                    if text and text not in ['', '\n', '\r\n']:
                        # Skip amendment text
                        parent = element.parent
                        if parent and parent.get('class'):
                            if 'morginalnotes' in str(parent.get('class')):
                                continue
                        all_text_parts.append(text)

            # Join all parts
            raw_content = "\n".join(all_text_parts)
            
            # Remove section number and title from the beginning
            if section_number:
                patterns_to_remove = [
                    rf'^\s*{re.escape(section_number)}\s*\.\s*',
                    rf'^\s*{re.escape(section_number)}\s+',
                ]
                for pattern in patterns_to_remove:
                    raw_content = re.sub(pattern, '', raw_content, count=1, flags=re.MULTILINE)
            
            if title:
                # Remove title if it appears at the start
                raw_content = raw_content.replace(title, '', 1).strip()

            # NOTE: is_interpretation_section was already detected earlier (before continuation check)
            # Now check for additional definition patterns in content if not already identified
            # NOTE: Don't hardcode section numbers as interpretation sections
            # Section 2 varies by legislation - only check title and content patterns

            # Check for definition patterns in content (only if not already identified by title)
            if not is_interpretation_section:
                # IMPROVED: Be more restrictive about interpretation section detection
                # Only treat as interpretation if it has STRONG indicators, not just
                # phrases that might appear in construction/application sections

                # Strong indicators: explicit definition language
                strong_definition_indicators = [
                    r'following\s+definitions?\s+shall\s+apply',
                    r'following\s+expressions?\s+shall\s+have',
                    r'words\s+and\s+expressions?\s+shall\s+have',
                ]

                for pattern in strong_definition_indicators:
                    if re.search(pattern, raw_content[:500], re.I):
                        is_interpretation_section = True
                        break

                # Weaker indicators: only treat as interpretation if combined with other signals
                if not is_interpretation_section:
                    # "In this Act" ONLY if followed by definition language
                    # Don't match phrases like "in this Act referred to as" which are just references
                    if re.search(r'^.{0,200}In\s+this\s+(?:Act|Ordinance|Law),?\s+(?:unless|the\s+following|these)', raw_content, re.I | re.DOTALL):
                        is_interpretation_section = True
                    # "unless the context otherwise requires" ONLY if:
                    # 1. It appears at the very start (within first 150 characters)
                    # 2. AND it's NOT inside a numbered subsection like "(1)"
                    # If it's inside "(1)", it's a construction section with subsections, not definitions
                    elif re.search(r'^.{0,150}unless\s+the\s+context\s+otherwise\s+requires', raw_content, re.I | re.DOTALL):
                        # Check if it's inside a numbered subsection
                        # Look for pattern like "(1)" before the phrase
                        text_before_phrase = raw_content[:raw_content.lower().find('unless the context') + 50]
                        has_subsection_marker = re.search(r'\(\s*\d+\s*\)', text_before_phrase)
                        if not has_subsection_marker:
                            # No subsection marker found, treat as interpretation
                            is_interpretation_section = True
            
            final_content = []
            subsections = []
            
            if is_interpretation_section:
                # IMPORTANT: For interpretation sections, process each table separately to preserve
                # the one-to-one mapping between definitions and their amendments

                # Extract preface from main table (first table)
                main_table_text = tables_to_process[0].get_text(separator='\n', strip=False) if tables_to_process else ''
                main_table_text = self.clean_text(main_table_text)

                # Remove section number and title
                if section_number:
                    main_table_text = re.sub(rf'^\s*{re.escape(section_number)}\s*\.\s*', '', main_table_text, count=1, flags=re.MULTILINE)
                if title:
                    main_table_text = main_table_text.replace(title, '', 1).strip()

                # Extract preface (text before first definition in main table)
                # Pattern matches both ASCII quotes ("') and Unicode curly quotes ("")
                definition_pattern = re.compile(
                    r'["\'\u201c\u201d]([^"\'\u201c\u201d]+?)["\'\u201c\u201d][\s,]*((?:(?:in\s+relation\s+to|with\s+reference\s+to)[^;]*?[,;]?\s*)?(?:means|includes|shall\s+mean|shall\s+include|has\s+the\s+same\s+meaning))',
                    re.I
                )

                first_def_match = definition_pattern.search(main_table_text)
                if first_def_match:
                    preface = main_table_text[:first_def_match.start()].strip()
                    final_content = [self.clean_text(preface)] if preface else []
                else:
                    final_content = [main_table_text] if main_table_text else []

                # Process each table to extract ONE definition + its amendment
                subsections = []
                for tbl in tables_to_process:
                    tbl_text = tbl.get_text(separator='\n', strip=False)
                    tbl_text = self.clean_text(tbl_text)

                    # Remove amendment markers from the text
                    tbl_text = AMEND_RX.sub('', tbl_text)

                    # Find definition in this table
                    tbl_match = definition_pattern.search(tbl_text)

                    # IMPORTANT: Also check for definitions with just a hyphen/dash (no immediate "means/includes")
                    # Example: "Chairman" - with nested subsections containing "means"
                    if not tbl_match:
                        hyphen_pattern = re.compile(
                            r'["\'\u201c\u201d]([^"\'\u201c\u201d]+?)["\'\u201c\u201d]\s*[-–—]\s*',
                            re.I
                        )
                        tbl_match = hyphen_pattern.search(tbl_text)

                    if tbl_match:
                        term = tbl_match.group(1).strip()

                        # Extract ONLY this definition's content (not other definitions that might follow)
                        # Find next definition or end of text
                        def_start = tbl_match.start()
                        def_end = len(tbl_text)

                        # Look for the next definition
                        next_match = definition_pattern.search(tbl_text, tbl_match.end())
                        if next_match:
                            def_end = next_match.start()

                        definition_content = tbl_text[def_start:def_end].strip()

                        # Extract the opening quote character to preserve it in the identifier
                        opening_quote_match = re.search(r'(["\'\u201c\u201d])' + re.escape(term), tbl_text)
                        opening_quote = opening_quote_match.group(1) if opening_quote_match else '"'

                        # Determine closing quote (match opening or use default)
                        quote_pairs = {'"': '"', "'": "'", '\u201c': '\u201d', '\u201d': '\u201c'}
                        closing_quote = quote_pairs.get(opening_quote, '"')

                        # Remove the term and quotes from the beginning
                        definition_content = re.sub(r'^["\'\u201c\u201d]' + re.escape(term) + r'["\'\u201c\u201d][\s,]*', '', definition_content)

                        # Extract amendment from this table
                        tbl_amendment = self.extract_amendment_info(tbl)

                        # IMPORTANT: Extract nested subsections from this table
                        # Example: "Chairman" - has nested (a), (b) subsections
                        # Look for sectioncontent font element
                        section_content_font = tbl.find('font', class_='sectioncontent')
                        nested_subsections = []
                        if section_content_font:
                            nested_subsections = self.extract_nested_subsections(section_content_font)

                        # Create subsection with preserved quote style
                        subsection_obj = {
                            "identifier": f'{opening_quote}{term}{closing_quote}',
                            "content": definition_content,
                            "subsections": nested_subsections
                        }

                        # Attach amendment if present
                        if tbl_amendment:
                            subsection_obj["amendment"] = tbl_amendment

                        subsections.append(subsection_obj)

                # Skip the full_content extraction logic - we've already processed everything
                full_content = None

                if full_content:
                    # Extract definition entries
                    # Pattern: quoted term followed by means/includes
                    # Updated to handle various formats:
                    # - "term" means...
                    # - "term", in relation to..., means...
                    # - "term"with reference to... means/includes... (note: sometimes no space after quote)
                    definition_pattern = re.compile(
                        r'["\']([^"\']+?)["\'][\s,]*((?:(?:in\s+relation\s+to|with\s+reference\s+to)[^;]*?[,;]?\s*)?(?:means|includes|shall\s+mean|shall\s+include|has\s+the\s+same\s+meaning))',
                        re.I
                    )

                    matches = list(definition_pattern.finditer(full_content))

                    if matches:
                        # Extract preface (content before first definition)
                        preface = full_content[:matches[0].start()].strip()
                        preface = self.clean_text(preface)
                        if preface:
                            final_content = [preface]
                        else:
                            final_content = []

                        # Extract each definition as a subsection
                        for i, match in enumerate(matches):
                            term = match.group(1).strip()

                            # Find the content of this definition
                            # It starts from the match and goes until the next definition or end
                            start_pos = match.start()
                            if i + 1 < len(matches):
                                end_pos = matches[i + 1].start()
                            else:
                                end_pos = len(full_content)

                            definition_content = full_content[start_pos:end_pos].strip()

                            # Clean the definition content
                            definition_content = self.clean_text(definition_content)

                            # Remove the quotes and term from the beginning if present
                            # This handles cases like: "commencement", in relation to this Act, means...
                            # We want to keep only: in relation to this Act, means...
                            definition_content = re.sub(r'^["\']' + re.escape(term) + r'["\'][\s,]*', '', definition_content)

                            # Extract nested subsections from this definition (e.g., (i), (ii), (a), (b))
                            # Pattern for nested subsections: (i), (ii), (a), (b), etc.
                            # Updated to handle both newline-separated and inline (space/semicolon-separated) formats
                            nested_subsections = []
                            # Pattern matches: start of string, after newline, after semicolon/period/dash+space, or after "and"
                            nested_pattern = re.compile(r'(?:^|(?<=[;.\-:])\s+|\n\s*|(?:\band\b\s+))(\([a-z0-9ivxlcdm]+\))\s+', re.I | re.M)
                            nested_matches = list(nested_pattern.finditer(definition_content))

                            if nested_matches:
                                # Extract preface (content before first nested subsection)
                                # Find the actual start of the first identifier (before the match start which includes the separator)
                                first_identifier_pos = nested_matches[0].start()
                                # Look back to find where the separator starts
                                preface_text = definition_content[:first_identifier_pos]
                                # Remove trailing separator if present
                                preface_text = re.sub(r'[;.]\s*$', '', preface_text).strip()
                                preface = self.clean_text(preface_text)

                                # Extract each nested subsection
                                # Use a set to track identifiers we've already added (to avoid duplicates)
                                seen_identifiers = set()

                                for j, nested_match in enumerate(nested_matches):
                                    identifier = nested_match.group(1).strip()

                                    # Skip if we've already processed this identifier
                                    if identifier in seen_identifiers:
                                        continue
                                    seen_identifiers.add(identifier)

                                    # Find content: from after the identifier to before the next identifier or end
                                    nested_start = nested_match.end()
                                    if j + 1 < len(nested_matches):
                                        # Find the next UNIQUE identifier
                                        next_match_idx = j + 1
                                        while next_match_idx < len(nested_matches):
                                            next_identifier = nested_matches[next_match_idx].group(1).strip()
                                            if next_identifier not in seen_identifiers:
                                                nested_end = nested_matches[next_match_idx].start()
                                                break
                                            next_match_idx += 1
                                        else:
                                            nested_end = len(definition_content)
                                    else:
                                        nested_end = len(definition_content)

                                    nested_content = definition_content[nested_start:nested_end].strip()
                                    # Remove trailing separators
                                    nested_content = re.sub(r'[;.]\s*$', '', nested_content).strip()
                                    nested_content = self.clean_text(nested_content)

                                    if nested_content:
                                        nested_subsections.append({
                                            "identifier": identifier,
                                            "content": nested_content,
                                            "subsections": []
                                        })

                                # Add definition with its nested subsections
                                if preface or nested_subsections:
                                    subsection_obj = {
                                        "identifier": f'"{term}"',
                                        "content": preface if preface else "",
                                        "subsections": nested_subsections
                                    }
                                    # Attach amendment if this definition has one
                                    if term in definition_amendments_map:
                                        subsection_obj["amendment"] = definition_amendments_map[term]
                                    subsections.append(subsection_obj)
                            else:
                                # No nested subsections, just add the definition (already cleaned above)
                                if definition_content:
                                    subsection_obj = {
                                        "identifier": f'"{term}"',
                                        "content": definition_content,
                                        "subsections": []
                                    }
                                    # Attach amendment if this definition has one
                                    if term in definition_amendments_map:
                                        subsection_obj["amendment"] = definition_amendments_map[term]
                                    subsections.append(subsection_obj)

            else:
                # Normal section processing (non-interpretation)
                # IMPROVED: Check if section has nested table structure for subsections
                # If so, use table-based extraction to preserve hierarchy
                has_nested_tables = False
                for tbl in tables_to_process:
                    nested_subsection_tables = tbl.find_all('table', cellspacing="2mm", recursive=True)
                    if len(nested_subsection_tables) > 0:
                        # Check if they contain subsectioncontent
                        for nested_tbl in nested_subsection_tables:
                            if nested_tbl.find('font', class_='subsectioncontent'):
                                has_nested_tables = True
                                break
                    if has_nested_tables:
                        break

                if has_nested_tables:
                    # Use table-based extraction to preserve hierarchy
                    # Extract subsections from the table structure directly
                    subsections = []
                    for tbl in tables_to_process:
                        # Get the parent font element that contains the section content
                        section_content_font = tbl.find('font', class_='sectioncontent')
                        if section_content_font:
                            # First try: Extract nested subsections from inside this font element
                            nested_subs = self.extract_nested_subsections(section_content_font)
                            if nested_subs:
                                subsections.extend(nested_subs)

                            # Second try: Extract subsection tables that are siblings to sectioncontent
                            # (e.g., legislation_A_7 where subsection tables are next to sectioncontent, not nested inside)
                            # Get the parent element that contains both sectioncontent and subsection tables
                            parent_elem = section_content_font.parent
                            if parent_elem:
                                # Find all subsection tables at this level
                                sibling_tables = parent_elem.find_all('table', cellspacing="2mm", recursive=False)
                                for sibling_tbl in sibling_tables:
                                    subsection_font = sibling_tbl.find('font', class_='subsectioncontent')
                                    if subsection_font:
                                        # IMPORTANT: Extract amendment from this subsection table
                                        sibling_amendment = self.extract_amendment_info(sibling_tbl)

                                        # Extract this subsection
                                        direct_text_parts = []
                                        for child in subsection_font.children:
                                            if isinstance(child, str):
                                                direct_text_parts.append(child)
                                            elif child.name != 'table':
                                                direct_text_parts.append(child.get_text())

                                        direct_text = ''.join(direct_text_parts).strip()
                                        direct_text = self.clean_text(direct_text)

                                        # Extract identifier
                                        id_match = re.match(r'^[\s"\']*(\(\s*[a-z0-9ivxlcdm]+\s*\)|[A-Z]\.)\s*', direct_text, flags=re.IGNORECASE)
                                        if id_match:
                                            identifier = id_match.group(1)
                                            content = direct_text[id_match.end():].strip()
                                        else:
                                            identifier = ""
                                            content = direct_text

                                        if content:
                                            subsection_obj = {
                                                "identifier": identifier,
                                                "content": content,
                                                "subsections": []
                                            }

                                            # Add amendment if present
                                            if sibling_amendment:
                                                subsection_obj["amendment"] = sibling_amendment

                                            subsections.append(subsection_obj)

                    # Extract preface (content before first subsection table)
                    preface_text = []
                    for tbl in tables_to_process:
                        # Get direct text from sectioncontent fonts (not subsectioncontent)
                        for font in tbl.find_all('font', class_='sectioncontent'):
                            # IMPORTANT: Only get text BEFORE first nested table (preface only)
                            # Don't include continuation text after subsections
                            direct_text_parts = []
                            found_table = False
                            for child in font.children:
                                # Stop when we hit the first nested table
                                if child.name == 'table':
                                    found_table = True
                                    break
                                # Only collect text before the first table
                                if isinstance(child, str):
                                    direct_text_parts.append(child)
                                elif child.name != 'table':  # Include non-table elements like <a>, <b> etc
                                    direct_text_parts.append(child.get_text())

                            font_text = ''.join(direct_text_parts).strip()
                            if font_text:
                                preface_text.append(font_text)

                    if preface_text:
                        preface = ' '.join(preface_text)

                        # Remove section number from the beginning (e.g., "7. ", "14A. ")
                        if section_number:
                            # Try to remove "7. " or "14A. " from the start
                            section_pattern = rf'^\s*{re.escape(section_number)}\s*\.\s*'
                            preface = re.sub(section_pattern, '', preface, count=1)

                        preface = self.clean_text(preface)
                        # Don't include if it's empty or just the section number
                        if preface and not re.match(r'^\d+[A-Z]?\.$', preface.strip()):
                            final_content = [preface]
                else:
                    # Fallback: Use text-based extraction (original logic)
                    # Process illustrations and explanations
                    main_block, illu = self._split_off_illustrations_block(raw_content)
                    main_block, expl_blocks = self._split_off_explanations_blocks(main_block)

                    # Try to extract subsections
                    subsections = self.extract_subsections_from_text(main_block)

                    if subsections:
                        # Has subsections - extract preface
                        first_marker = re.search(r'(?m)^\s*(\(\s*[a-z0-9]\s*\)|\d+\.)\s+', main_block, re.I)
                        if first_marker:
                            preface = main_block[:first_marker.start()].strip()
                            preface = self.clean_text(preface)
                            # Don't include if it's just the section number (e.g., "8.", "14.", etc.)
                            if preface and not re.match(r'^\d+[A-Z]?\.$', preface.strip()):
                                final_content = [preface]
                    else:
                        # No subsections - use full content
                        content = self.clean_text(main_block)
                        if content:
                            final_content = [content]
            
            # Build section object
            section_data = {
                "number": section_number,
                "title": title,
                "amendment": amendment_info,
                "content": final_content,
                "subsections": subsections
            }
            
            # Handle illustrations and explanations if present and not interpretation section
            if not is_interpretation_section:
                if 'illu' in locals() and illu:
                    parsed = self._parse_illustrations_block(illu)
                    self._attach_illustrations_to_section(section_data, parsed)
                
                if 'expl_blocks' in locals() and expl_blocks:
                    expl_parsed = self._merge_parsed_explanations([self._parse_explanations_block(b) for b in expl_blocks])
                    if expl_parsed and (expl_parsed.get("content") or expl_parsed.get("subsections")):
                        section_data["Explanations"] = {
                            "title": "Explanation",
                            "content": expl_parsed.get("content") or [],
                            "subsections": []
                        }
            
            # Debug output for interpretation sections
            if self.debug_mode and is_interpretation_section:
                print(f"\n=== INTERPRETATION SECTION {section_number} ===")
                print(f"  Title: {title}")
                print(f"  Content length: {len(final_content[0]) if final_content else 0}")
                if final_content:
                    print(f"  Content preview: {final_content[0][:200]}...")
                    if len(final_content[0]) < 100:
                        print(f"  WARNING: Content seems truncated!")
                        print(f"  Full content: {final_content[0]}")

            # Debug logging for sections 13-20 in legislation_B_71
            if section_number and section_number.isdigit() and 13 <= int(section_number) <= 20:
                if self.debug_mode:
                    print(f"  [DEBUG] Section {section_number} created successfully, returning section_data")

            return self.postprocess_section_payload(section_data) if section_data.get("number") else None

        except Exception as e:
            if self.debug_mode:
                print(f"[process_section_table] ERROR: {e}")
                import traceback
                traceback.print_exc()
            return None
    def extract_all_text_from_element(self, element):
        """
        Extract ALL text from an element and its children.
        Preserves structure with newlines between elements.
        """
        if not element:
            return ""
        
        text_parts = []
        
        # Recursively extract text from all children
        for item in element.descendants:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    text_parts.append(text)
        
        return "\n".join(text_parts)
    def _split_off_explanations_blocks(self, raw: str):
        """
        Split explanations from main text, preserving full content.
        Returns (main_text, [explanation_blocks])
        """
        if not raw:
            return raw, []
        
        import re
        t = raw.replace("\r\n", "\n")
        
        # Find explanation headers
        tok = re.compile(r'(?im)^\s*Explanations?\b\s*[:\-–—]?\s*')
        matches = list(tok.finditer(t))
        
        if not matches:
            return raw, []
        
        blocks = []
        main_segments = []
        prev_end = 0
        
        for i, m in enumerate(matches):
            # Add text before this explanation to main
            main_segments.append(t[prev_end:m.start()])
            
            # Extract explanation block
            block_start = m.end()
            block_end = matches[i+1].start() if i+1 < len(matches) else len(t)
            
            # Don't truncate - take full block
            block_content = t[block_start:block_end].strip()
            if block_content:
                blocks.append(block_content)
            
            prev_end = block_end
        
        # Add any remaining text
        if prev_end < len(t):
            main_segments.append(t[prev_end:])
        
        # Join main segments
        main = "\n".join(seg.rstrip() for seg in main_segments if seg and seg.strip())
        
        return main, blocks

    def _parse_explanations_block(self, block: str):
        """
        Parse an explanations block, preserving full content.
        """
        if not block:
            return {"title": "Explanation", "content": [], "subsections": []}
        
        import re
        norm = block.replace("\r\n", "\n")
        
        # Look for numbered items
        item_rx = re.compile(
            r'(?m)^\s*\(?(\d+)\)?\s*[\.\-–—]?\s*(.*?)(?=^\s*\(?\d+\)?\s*[\.\-–—]?\s*|\Z)',
            re.S
        )
        
        out = []
        items = list(item_rx.finditer(norm))
        
        if items:
            for m in items:
                num = m.group(1)
                # Clean but preserve FULL content
                body = self.clean_text(m.group(2) or "")
                if body:
                    out.append(f"{num}.- {body}")
        else:
            # No numbered items - treat as single block
            s = self.clean_text(block)
            if s:
                out.append(s)
        
        return {"title": "Explanation", "content": out, "subsections": []}

    def _merge_parsed_explanations(self, expl_list):
        """
        Merge multiple explanation objects, preserving all content.
        """
        merged = {"title": "Explanation", "content": [], "subsections": []}
        seen = set()
        
        for ex in expl_list or []:
            for c in (ex.get("content") or []):
                if c and c not in seen:
                    seen.add(c)
                    merged["content"].append(c)
        
        return merged
    def extract_amendment_info(self, table_or_soup):
        """
        Extract amendment marginal notes as a list of {"text","link"}.
        Converts javascript:openSectionOrdinanceWindow(...) into a stable popup URL.
        Dedupes identical entries.

        IMPORTANT: This function should be called with a specific table element (not the entire soup)
        to avoid collecting amendments from other sections.
        """
        amendment_info = []
        base_link = "https://www.lawlanka.com/lal_v2/pages/popUp/actPopUp.jsp?actId="

        # Search within the specific table/element passed, not the entire document
        # Look for td elements that contain marginal notes
        # Section-level amendments use width="100px", subsection amendments use width="16%"
        target_td_elements = table_or_soup.find_all("td", attrs={"valign": "top"})
        for td in target_td_elements:
            # Check if this td has the right width (100px for sections, 16% for subsections)
            width = td.get("width")
            if width not in ["100px", "16%"]:
                continue

            for row in td.find_all("tr", class_="morginalnotes"):
                text = self.clean_text(row.get_text(" ", strip=True))
                href = None
                a = row.find("a", href=True)
                if a:
                    raw = a["href"]
                    m = re.search(r"openSectionOrdinanceWindow\('([^']+)','[^']*'\)", raw)
                    if m:
                        act_id = m.group(1).strip()
                        href = f"{base_link}{act_id}"
                    else:
                        href = raw

                if text:
                    amendment_info.append({"text": text, "link": href})

        # dedupe while preserving order
        seen = set()
        out = []
        for it in amendment_info:
            key = (it["text"], it["link"])
            if key in seen:
                continue
            seen.add(key)
            out.append(it)

        return out or None

    def extract_parts_with_section_groups(self, soup):
        """Extract parts and sections with comprehensive debugging for missing sections."""
        
        if self.debug_mode:
            print("\n=== STARTING PART EXTRACTION ===")
        
        # Track all processed section numbers to prevent duplicates
        processed_sections = set()
        
        # 1) Primary - find PART headers
        part_headers = soup.find_all("font", class_="sectionpart")

        # 2) ALWAYS run fallback: find PART lines in *text* (merge with primary results)
        # This is needed because some legislations have PART headers as plain text,
        # not wrapped in <font class="sectionpart"> (e.g., Civil Procedure Code)
        fallback_headers = []
        if True:  # Always run fallback, merge results later
            # Pattern 1: PART at start of line (original pattern)
            RX_PART_LINE = re.compile(
                r'^\s*PART\s*\.?\s*'
                r'(?:\(|\[)?'
                r'(?P<num>(?:[IVXLCDM]+(?:\s+[IVXLCDM]+)*)|[ⅰ-ⅿⅠ-Ⅿ0-9]+)'
                r'(?:\)|\])?'
                r'(?:\s*(?:[\-–—]|:)\s*.*)?$',
                re.IGNORECASE | re.MULTILINE
            )

            # Pattern 2: Standalone PART line (for embedded PART headers)
            # Matches PART [ROMAN] as complete line or standalone text
            RX_PART_STANDALONE = re.compile(
                r'^\s*PART\s+([IVXLCDM]+)\s*$',
                re.IGNORECASE | re.MULTILINE
            )

            # CRITICAL: Check hidden input field (some legislations store full text there)
            # Example: Civil Procedure Code has PART headers in <input name="selectedhtml">
            hidden_input = soup.find('input', attrs={'name': 'selectedhtml', 'type': 'hidden'})
            if hidden_input:
                hidden_value = hidden_input.get('value', '')
                if hidden_value and 'PART' in hidden_value:
                    # Extract PART headers from hidden input
                    for line in hidden_value.splitlines():
                        line_stripped = line.strip()
                        if RX_PART_STANDALONE.match(line_stripped):
                            # Create a pseudo text node for this PART header
                            # We need to find the actual location in the DOM
                            # For now, just track that we found it
                            if self.debug_mode:
                                print(f"  Found PART header in hidden input: {line_stripped}")

            candidates = []
            for s in soup.find_all(string=True):
                txt = str(s)
                if not txt or txt.isspace():
                    continue
                # Try both patterns
                for line in txt.splitlines():
                    line_stripped = line.strip()
                    if RX_PART_LINE.match(line) or RX_PART_STANDALONE.match(line_stripped):
                        candidates.append(s)
                        break

            fallback_headers = []
            seen = set()
            for s in candidates:
                node = getattr(s, "parent", None)
                if not node:
                    continue
                hdr = node.find_parent(["font", "strong", "b", "td", "th", "div"]) or node
                tbl = hdr.find_parent("table")
                key = id(tbl) if tbl else id(hdr)
                if key in seen:
                    continue
                seen.add(key)
                fallback_headers.append(hdr)

        # 3) Merge primary and fallback headers, removing duplicates
        all_part_headers = list(part_headers) + fallback_headers
        # Deduplicate by table parent
        seen_tables = set()
        part_headers = []
        for hdr in all_part_headers:
            tbl = hdr.find_parent("table") if hasattr(hdr, "find_parent") else None
            key = id(tbl) if tbl else id(hdr)
            if key not in seen_tables:
                seen_tables.add(key)
                part_headers.append(hdr)

        if self.debug_mode:
            print(f"Found {len(part_headers)} PART headers")

        def _next_section_table(after_node):
            cur = after_node.find_next("table") if hasattr(after_node, "find_next") else None
            while cur and not self.is_section_table(cur):
                cur = cur.find_next("table")
            return cur

        # MAIN PART cutoff anchor
        first_part_header_table = None
        if part_headers:
            first_part_header_table = part_headers[0].find_parent("table") or _next_section_table(part_headers[0])

        # Check if first PART is truly at the beginning
        is_first_part_at_beginning = False
        if first_part_header_table:
            all_section_tables_before = []
            for table in soup.find_all("table"):
                if table == first_part_header_table:
                    break
                if self.is_section_table(table):
                    all_section_tables_before.append(table)
            
            # Only skip MAIN PART if there are NO section tables before the first PART
            if len(all_section_tables_before) == 0:
                is_first_part_at_beginning = True

        parts = []
        
        # Process MAIN PART (sections before first PART header)
        if not is_first_part_at_beginning:
            if self.debug_mode:
                print("Processing MAIN PART...")
            main_part = self.process_main_part(soup, first_part_header_table, processed_sections)
            if main_part:
                parts.append(main_part)

        # Process each PART
        for i, part_header in enumerate(part_headers):
            if self.debug_mode:
                print(f"Processing PART {i+1}...")
                
            next_part_start = None
            if i + 1 < len(part_headers):
                next_part_start = part_headers[i + 1].find_parent("table")
                if not next_part_start:
                    next_part_start = _next_section_table(part_headers[i + 1])

            part = self.process_part(part_header, next_part_start, soup, processed_sections)
            if part:
                parts.append(part)

        # Fallback if still nothing
        if not parts:
            if self.debug_mode:
                print("No parts found, creating fallback with all sections...")
                
            all_sections = []
            all_tables = soup.find_all("table")

            if self.debug_mode:
                print(f"  [DEBUG] Processing {len(all_tables)} tables")

            for table_idx, table in enumerate(all_tables):
                if self.is_section_table(table):
                    section_number = None
                    section_number_tag = table.find("a", href=lambda href: href and "consSelectedSection" in href)
                    if section_number_tag:
                        section_number = section_number_tag.text.strip()

                    # Debug: Log ALL section numbers we find
                    if section_number and self.debug_mode:
                        print(f"  [DEBUG] Table {table_idx}: Found section_number: '{section_number}' (isdigit: {section_number.isdigit()})")

                    # Skip if already processed
                    if section_number and section_number in processed_sections:
                        # Debug logging for sections 13-20
                        if section_number.isdigit() and 13 <= int(section_number) <= 20:
                            if self.debug_mode:
                                print(f"  [DEBUG] Section {section_number} SKIPPED - already in processed_sections")
                        continue

                    section = self.process_section_table(table)

                    # Debug logging for sections 13-20
                    if section_number and section_number.isdigit() and 13 <= int(section_number) <= 20:
                        if self.debug_mode:
                            print(f"  [DEBUG] Section {section_number} returned from process_section_table:")
                            print(f"    Type: {type(section)}")
                            print(f"    Value: {section}")
                            print(f"    Bool: {bool(section)}")
                            if section:
                                if isinstance(section, dict):
                                    print(f"    Dict keys: {section.keys()}")
                                    print(f"    Section number in dict: {section.get('number')}")

                    if section:
                        if isinstance(section, list):
                            for s in section:
                                if s.get("number"):
                                    processed_sections.add(s.get("number"))
                                    # Debug logging for sections 13-20
                                    if s.get("number").isdigit() and 13 <= int(s.get("number")) <= 20:
                                        if self.debug_mode:
                                            print(f"  [DEBUG] Section {s.get('number')} ADDED to all_sections")
                            all_sections.extend(section)
                        else:
                            if section.get("number"):
                                processed_sections.add(section.get("number"))
                                # Debug logging for sections 13-20
                                if section.get("number").isdigit() and 13 <= int(section.get("number")) <= 20:
                                    if self.debug_mode:
                                        print(f"  [DEBUG] Section {section.get('number')} ADDED to all_sections")
                            all_sections.append(section)
                            
            if all_sections:
                parts.append({"number": "MAIN PART", "title": None,
                            "section_groups": [{"title": None, "sections": all_sections}]})
        
        return parts

    def process_main_part(self, soup, first_part_header_table=None, processed_sections=None):
        """
        Build the MAIN PART (everything before the first PART header table) with duplicate prevention.
        """
        import re
        
        if processed_sections is None:
            processed_sections = set()

        current_part = {"number": "MAIN PART", "title": None, "section_groups": []}

        # Find bounds for MAIN PART slice
        all_tables = soup.find_all("table")
        end_index = len(all_tables)
        if first_part_header_table:
            for i, tb in enumerate(all_tables):
                if tb == first_part_header_table:
                    end_index = i
                    break

        if self.debug_mode:
            print("=== STARTING MAIN PART ===")
            print(f"  Tables available before first PART header: {end_index}")

        # Collect items (headers + section tables) in slice
        items = []
        section_table_count = 0
        sections_seen_before_header = 0  # Track sections before any header

        for tb in all_tables[:end_index]:
            # First check if this is a section table
            is_section = self.is_section_table(tb)
            if is_section:
                section_table_count += 1
                sections_seen_before_header += 1
                items.append(("section", tb, None))

            # Then check for headers
            # IMPORTANT: Only recognize headers from proper heading tables (cellspacing="2mm")
            # AND only after we've seen at least one section table (to avoid navigation/TOC)
            for h in tb.find_all("font", class_=("sectiontitle", "sectionsubtitle", "sectionparttitle")):
                # Check if this is a proper heading table, not a navigation table
                # Proper heading tables have cellspacing="2mm" or similar
                cellspacing = tb.get("cellspacing", "")
                # Accept tables with cellspacing of 2mm, 3mm, 4mm, etc. (heading tables)
                # Reject tables with no cellspacing or large widths (navigation tables)
                is_proper_heading_table = cellspacing and ("mm" in cellspacing or cellspacing in ["2", "3", "4"])

                # CRITICAL: Also require that we've seen at least one section before this header
                # This prevents navigation/TOC chapter headings from being recognized
                has_sections_before = sections_seen_before_header > 0

                if self.debug_mode:
                    header_text = h.get_text()[:50]
                    if 'CHAPTER' in header_text:
                        print(f"  [DEBUG] Chapter header: '{header_text}' - cellspacing='{cellspacing}' - proper_table={is_proper_heading_table} - sections_before={sections_seen_before_header} - will_add={is_proper_heading_table and has_sections_before}")

                if is_proper_heading_table and has_sections_before:
                    items.append(("header", tb, h))
                    # Reset counter after adding header
                    sections_seen_before_header = 0

        if self.debug_mode:
            print(f"  Total section tables in MAIN PART: {section_table_count}")

        # Walk items and build groups
        sections_before_first_title = []
        subtitle_sections = []
        subtitle_groups = []
        previous_section = None

        current_num, current_title = None, None

        i, n = 0, len(items)
        sections_processed = 0

        while i < n:
            kind, tb, tag = items[i]

            if kind == "header":
                # coalesce consecutive header items
                run_tags = [tag]
                j = i + 1
                while j < n and items[j][0] == "header":
                    run_tags.append(items[j][2])
                    j += 1

                new_num, new_title = self._resolve_header_run(run_tags)

                # close previous header-run group if it has sections
                if (current_num is not None or current_title is not None) and subtitle_sections:
                    subtitle_groups.append({
                        "number": current_num,
                        "title": current_title,
                        "sections": subtitle_sections
                    })
                    subtitle_sections = []
                    previous_section = None

                # if this is the first header, flush pre-title bucket
                if current_num is None and current_title is None and sections_before_first_title:
                    current_part["section_groups"].append({
                        "number": None, "title": None, "sections": sections_before_first_title
                    })
                    sections_before_first_title = []

                current_num, current_title = new_num, new_title

                if self.debug_mode and (new_num or new_title):
                    print(f"  MAIN PART header: {new_num} - {new_title}")

                i = j
                continue

            # kind == "section"
            # Extract section number first to check for duplicates
            section_number_tag = tb.find("a", href=lambda href: href and "consSelectedSection" in href)
            section_number = section_number_tag.text.strip() if section_number_tag else None

            # Debug for sections 13-20
            if section_number and section_number.isdigit() and 13 <= int(section_number) <= 20:
                if self.debug_mode:
                    print(f"  [DEBUG] MAIN PART processing section {section_number}, in processed_sections: {section_number in processed_sections}")

            # Skip if already processed
            if section_number and section_number in processed_sections:
                if self.debug_mode:
                    print(f"  Skipping duplicate section {section_number} in MAIN PART")
                i += 1
                continue
                
            result = self.process_section_table(tb, previous_section)
            sections_processed += 1

            # Debug for sections 13-20
            if section_number and section_number.isdigit() and 13 <= int(section_number) <= 20:
                if self.debug_mode:
                    print(f"  [DEBUG] MAIN PART section {section_number} result: type={type(result)}, bool={bool(result)}")
                    if result:
                        print(f"    result keys: {result.keys() if isinstance(result, dict) else 'not a dict'}")

            if result:
                if isinstance(result, list):
                    for section in result:
                        if section.get("number"):
                            if section.get("number") in processed_sections:
                                if self.debug_mode:
                                    print(f"  Skipping duplicate section {section.get('number')} in MAIN PART")
                                continue
                            processed_sections.add(section.get("number"))
                        
                        if current_num or current_title:
                            subtitle_sections.append(section)
                        else:
                            sections_before_first_title.append(section)
                        previous_section = section
                else:
                    if result.get("number"):
                        if result.get("number") in processed_sections:
                            if self.debug_mode:
                                print(f"  Skipping duplicate section {result.get('number')} in MAIN PART")
                            i += 1
                            continue
                        processed_sections.add(result.get("number"))

                    # Debug for sections 13-20
                    if result.get("number") and result.get("number").isdigit() and 13 <= int(result.get("number")) <= 20:
                        if self.debug_mode:
                            print(f"  [DEBUG] Section {result.get('number')} - current_num={current_num}, current_title={current_title}")
                            if current_num or current_title:
                                print(f"    -> Adding to subtitle_sections")
                            else:
                                print(f"    -> Adding to sections_before_first_title")

                    if current_num or current_title:
                        subtitle_sections.append(result)
                    else:
                        sections_before_first_title.append(result)
                    previous_section = result

            i += 1

        # flush remaining buckets
        if sections_before_first_title:
            current_part["section_groups"].append({"number": None, "title": None, "sections": sections_before_first_title})

        if (current_num is not None or current_title is not None) and subtitle_sections:
            subtitle_groups.append({"number": current_num, "title": current_title, "sections": subtitle_sections})

        current_part["section_groups"].extend(subtitle_groups)

        # FILTER: MAIN PART should only contain CHAPTER I sections (typically sections 1-8)
        # Remove any section_groups that have sections > 8, as they belong to other chapters
        # EXCEPTION: Always keep Section 1 (Short title) in MAIN PART
        # CRITICAL FIX: Only apply this filter if there are actual PART/CHAPTER structures.
        # If first_part_header_table is None, that means there are NO parts and ALL sections
        # belong in MAIN PART (e.g., legislation_B_71).
        import re
        filtered_groups = []

        # Check if filtering should be applied
        should_filter = first_part_header_table is not None
        if self.debug_mode and not should_filter:
            print(f"  [DEBUG] No PART structures found - keeping ALL sections in MAIN PART (no filtering)")
        for group in current_part.get("section_groups", []):
            # Filter direct sections
            filtered_sections = []
            for section in group.get("sections", []):
                sec_num_str = section.get("number", "")

                # If no PART structures exist, keep ALL sections
                if not should_filter:
                    filtered_sections.append(section)
                    continue

                # Otherwise, apply the normal filter for MAIN PART (sections <= 8)
                m = re.match(r'^(\d+)', str(sec_num_str))
                if m:
                    sec_num = int(m.group(1))
                    # ALWAYS keep Section 1 (Short title) in MAIN PART
                    if sec_num == 1:
                        filtered_sections.append(section)
                    # CHAPTER I typically ends at section 5 or 8
                    # Keep only sections <= 8 in MAIN PART
                    elif sec_num <= 8:
                        filtered_sections.append(section)
                else:
                    # Keep non-numeric sections
                    filtered_sections.append(section)

            # Filter SubChapter sections
            filtered_subchapters = []
            for subchapter in group.get("SubChapter", []):
                filtered_subchapter_groups = []
                for sg in subchapter.get("section_groups", []):
                    filtered_sg_sections = []
                    for section in sg.get("sections", []):
                        sec_num_str = section.get("number", "")

                        # If no PART structures exist, keep ALL sections
                        if not should_filter:
                            filtered_sg_sections.append(section)
                            continue

                        # Otherwise, apply the normal filter
                        m = re.match(r'^(\d+)', str(sec_num_str))
                        if m:
                            sec_num = int(m.group(1))
                            # ALWAYS keep Section 1 (Short title) in MAIN PART
                            if sec_num == 1:
                                filtered_sg_sections.append(section)
                            elif sec_num <= 8:
                                filtered_sg_sections.append(section)
                        else:
                            filtered_sg_sections.append(section)

                    # Only keep section_group if it has sections
                    if filtered_sg_sections:
                        sg["sections"] = filtered_sg_sections
                        filtered_subchapter_groups.append(sg)

                # Only keep SubChapter if it has section_groups with sections
                if filtered_subchapter_groups:
                    subchapter["section_groups"] = filtered_subchapter_groups
                    filtered_subchapters.append(subchapter)

            # Update group with filtered data
            group["sections"] = filtered_sections
            if filtered_subchapters:
                group["SubChapter"] = filtered_subchapters

            # Only keep group if it has sections (direct or in SubChapters) after filtering
            has_content = len(filtered_sections) > 0 or len(filtered_subchapters) > 0
            if has_content:
                filtered_groups.append(group)
            elif self.debug_mode:
                print(f"  Removed empty group '{group.get('title', '')}' from MAIN PART")

        current_part["section_groups"] = filtered_groups

        if self.debug_mode:
            total_sections = sum(len(g.get("sections", [])) for g in current_part.get("section_groups", []))
            print("=== MAIN PART COMPLETE ===")
            print(f"  Tables processed: {sections_processed}")
            print(f"  Total sections in MAIN PART (after filtering): {total_sections}")

        return current_part if current_part.get("section_groups") else None


    def process_part(self, part_header, next_part_start, soup, processed_sections=None):
        """Process a single PART slice with duplicate prevention."""
        import re
        
        if processed_sections is None:
            processed_sections = set()

        # Normalize PART number/title
        raw_header = part_header.get_text(" ", strip=True) if hasattr(part_header, "get_text") else str(part_header).strip()
        
        UNI_ROMAN = {
            "Ⅰ":"I","Ⅱ":"II","Ⅲ":"III","Ⅳ":"IV","Ⅴ":"V","Ⅵ":"VI","Ⅶ":"VII","Ⅷ":"VIII","Ⅸ":"IX","Ⅹ":"X","Ⅺ":"XI","Ⅻ":"XII",
            "Ⅼ":"L","Ⅽ":"C","Ⅾ":"D","Ⅿ":"M",
            "ⅰ":"I","ⅱ":"II","ⅲ":"III","ⅳ":"IV","ⅴ":"V","ⅵ":"VI","ⅶ":"VII","ⅷ":"VIII","ⅸ":"IX","ⅹ":"X","ⅺ":"XI","ⅻ":"XII",
            "ⅼ":"L","ⅽ":"C","ⅾ":"D","ⅿ":"M",
        }
        def _norm_roman(tok: str) -> str:
            return re.sub(r"\s+", "", "".join(UNI_ROMAN.get(ch, ch) for ch in (tok or ""))).upper()

        m = re.search(r'(?i)\bPART\s+((?:[IVXLCDM]+(?:\s+[IVXLCDM]+)*)|[ⅰ-ⅿⅠ-Ⅿ0-9]+)', raw_header)
        part_number = f"PART {_norm_roman(m.group(1))}" if m else re.sub(r"\s+", " ", raw_header).strip()
        part_title_tag = part_header.find_next("font", class_="sectionparttitle")
        part_title = part_title_tag.text.strip() if part_title_tag else None
        current_part = {"number": part_number, "title": part_title, "section_groups": []}

        if self.debug_mode:
            print(f"  Processing {part_number} (title: {part_title})")

        # Bounds
        def _next_section_table(after_node):
            cur = after_node.find_next("table") if hasattr(after_node, "find_next") else None
            while cur and not self.is_section_table(cur):
                cur = cur.find_next("table")
            return cur
        
        part_start_tag = part_header.find_parent("table") or _next_section_table(part_header)

        all_tables = soup.find_all("table")
        start_index = -1
        if part_start_tag:
            for i, table in enumerate(all_tables):
                if table == part_start_tag:
                    start_index = i
                    break
        end_index = len(all_tables)
        if next_part_start:
            for i, table in enumerate(all_tables):
                if table == next_part_start:
                    end_index = i
                    break

        scan_count = (end_index - start_index) if start_index >= 0 else 0
        if self.debug_mode:
            print(f"    Scanning {scan_count} tables in range (indices {start_index} to {end_index})")

        # Build ordered items for this PART slice
        items = []
        section_table_count = 0
        
        if start_index >= 0:
            rng = all_tables[start_index:end_index]
            for tb in rng:
                hdrs = tb.find_all("font", class_=("sectiontitle", "sectionsubtitle", "sectionparttitle"))
                for h in hdrs:
                    items.append(("header", tb, h))
                    
                if self.is_section_table(tb):
                    section_table_count += 1
                    items.append(("section", tb, None))
        else:
            cur = part_start_tag
            while cur and cur != next_part_start:
                hdrs = cur.find_all("font", class_=("sectiontitle", "sectionsubtitle", "sectionparttitle"))
                for h in hdrs:
                    items.append(("header", cur, h))
                    
                if self.is_section_table(cur):
                    section_table_count += 1
                    items.append(("section", cur, None))
                cur = cur.find_next("table")

        if self.debug_mode:
            print(f"    Total section tables in {part_number}: {section_table_count}")

        sections_before_first_title = []
        current_num, current_title = None, None
        subtitle_sections = []
        subtitle_groups = []
        previous_section = None

        i, n = 0, len(items)
        sections_processed = 0
        
        while i < n:
            kind, tb, tag = items[i]

            if kind == "header":
                # coalesce consecutive header items into one
                run_tags = [tag]
                j = i + 1
                while j < n and items[j][0] == "header":
                    run_tags.append(items[j][2])
                    j += 1

                new_num, new_title = self._resolve_header_run(run_tags)

                # close previous group (only if it has sections)
                if (current_num is not None or current_title is not None) and subtitle_sections:
                    subtitle_groups.append({
                        "number": current_num,
                        "title": current_title,
                        "sections": subtitle_sections
                    })
                    subtitle_sections = []
                    previous_section = None

                # if this is the first subtitle, flush pre-title bucket
                if current_num is None and current_title is None and sections_before_first_title:
                    current_part["section_groups"].append({"number": None, "title": None, "sections": sections_before_first_title})
                    sections_before_first_title = []

                current_num, current_title = new_num, new_title
                
                if self.debug_mode and new_title:
                    print(f"    Found header in {part_number}: {new_num} - {new_title}")
                    
                i = j
                continue

            # section
            # Extract section number first to check for duplicates
            section_number_tag = tb.find("a", href=lambda href: href and "consSelectedSection" in href)
            section_number = section_number_tag.text.strip() if section_number_tag else None
            
            # Skip if already processed
            if section_number and section_number in processed_sections:
                if self.debug_mode:
                    print(f"    Skipping duplicate section {section_number} in {part_number}")
                i += 1
                continue
                
            result = self.process_section_table(tb, previous_section)
            sections_processed += 1
            
            if result:
                if isinstance(result, list):
                    for section in result:
                        if section.get("number"):
                            if section.get("number") in processed_sections:
                                if self.debug_mode:
                                    print(f"    Skipping duplicate section {section.get('number')} in {part_number}")
                                continue
                            processed_sections.add(section.get("number"))
                        
                        if current_num or current_title:
                            subtitle_sections.append(section)
                        else:
                            sections_before_first_title.append(section)
                        previous_section = section
                else:
                    if result.get("number"):
                        if result.get("number") in processed_sections:
                            if self.debug_mode:
                                print(f"    Skipping duplicate section {result.get('number')} in {part_number}")
                            i += 1
                            continue
                        processed_sections.add(result.get("number"))
                    
                    if current_num or current_title:
                        subtitle_sections.append(result)
                    else:
                        sections_before_first_title.append(result)
                    previous_section = result
            
            i += 1

        # Flush remaining buckets
        if sections_before_first_title:
            current_part["section_groups"].append({"number": None, "title": None, "sections": sections_before_first_title})
        if (current_num is not None or current_title is not None) and subtitle_sections:
            subtitle_groups.append({"number": current_num, "title": current_title, "sections": subtitle_sections})

        current_part["section_groups"].extend(subtitle_groups)

        if self.debug_mode:
            total_sections = sum(len(g.get("sections", [])) for g in current_part.get("section_groups", []))
            print(f"    {part_number} processing complete:")
            print(f"      Tables processed: {sections_processed}")
            print(f"      Total sections: {total_sections}")

        return current_part

    def _extract_chapter_title(self, chapter_name, soup):
        """Extract the title for a chapter from the soup."""
        # Special case for CHAPTER VIII
        if "VIII" in chapter_name:
            return "OF THE ISSUE AND SERVICE OF SUMMONS"
        
        # Look for the title that follows the chapter header
        chapter_pattern = re.escape(chapter_name)
        
        # Search in the soup for chapter title patterns
        for element in soup.find_all(string=re.compile(chapter_pattern, re.I)):
            parent = element.parent
            if parent:
                # Look for title in next siblings or nearby elements
                next_sibling = parent.find_next(string=True)
                if next_sibling:
                    text = next_sibling.strip()
                    # Common title patterns after chapter headers
                    if re.match(r'^(OF|FOR|RELATING|CONCERNING|REGARDING)', text, re.I):
                        return self.clean_text(text)
                    # Or look for text in nearby font elements
                    next_font = parent.find_next("font")
                    if next_font:
                        font_text = next_font.get_text(strip=True)
                        if len(font_text) > 5 and not re.match(r'^\d+\.', font_text):
                            return self.clean_text(font_text)
        
        return None
    def _resolve_header_run(self, header_tags):
        """
        Given a list of <font> header tags, return (chapter_number, chapter_title).
        IMPROVED: Now recognizes both explicit CHAPTER declarations AND section group headers.
        """
        import re
        UNI_ROMAN = {
            "Ⅰ":"I","Ⅱ":"II","Ⅲ":"III","Ⅳ":"IV","Ⅴ":"V","Ⅵ":"VI","Ⅶ":"VII","Ⅷ":"VIII","Ⅸ":"IX","Ⅹ":"X","Ⅺ":"XI","Ⅻ":"XII",
            "Ⅼ":"L","Ⅽ":"C","Ⅾ":"D","Ⅿ":"M",
            "ⅰ":"I","ⅱ":"II","ⅲ":"III","ⅳ":"IV","ⅴ":"V","ⅵ":"VI","ⅶ":"VII","ⅷ":"VIII","ⅹ":"X","ⅺ":"XI","ⅻ":"XII",
            "ⅼ":"L","ⅽ":"C","ⅾ":"D","ⅿ":"M",
        }
        def _norm_roman(tok: str) -> str:
            return re.sub(r"\s+", "", "".join(UNI_ROMAN.get(ch, ch) for ch in (tok or ""))).upper()
        def _clean(s: str) -> str:
            return self.clean_text((s or "").replace("\xa0", " "))

        chapter_num = None
        titles = []
        
        # Process all tags to find chapters and titles
        for tag in header_tags:
            txt = _clean(tag.get_text(" ", strip=True))
            if not txt:
                continue
                
            # Check for explicit CHAPTER declaration
            m = re.match(r'^\s*CHAPTER\s+([IVXLCDM]+|[ⅰ-ⅿⅠ-Ⅿ]+|\d+)\s*$', txt, flags=re.I)
            if m:
                chapter_num = f"CHAPTER {_norm_roman(m.group(1))}"
                continue
            
            # Check for CHAPTER with inline title
            m = re.match(r'^\s*CHAPTER\s+([IVXLCDM]+|[ⅰ-ⅿⅠ-Ⅿ]+|\d+)\s*[-–—:]\s*(.+)$', txt, flags=re.I)
            if m:
                chapter_num = f"CHAPTER {_norm_roman(m.group(1))}"
                titles.append(m.group(2).strip())
                continue
                
            # Skip if it's another structural element
            if re.match(r'^\s*(PART|SCHEDULE|APPENDIX)\b', txt, flags=re.I):
                continue
            
            # Check if this could be a chapter title
            # More flexible: accept multi-word titles starting with "OF", or substantive phrases
            if (re.match(r'^OF\s+', txt, flags=re.I) or 
                (len(txt.split()) >= 2 and not txt.isupper()) or
                (len(txt) > 20 and not re.match(r'^\d+\.', txt))):
                titles.append(txt)
            # Also accept all-caps multi-word titles as potential chapter titles
            elif txt.isupper() and len(txt.split()) >= 2 and len(txt) > 10:
                titles.append(txt)

        # Prefer the first substantial title
        chapter_title = titles[0] if titles else None
        
        # If we have a title but no chapter number, check if the title contains a chapter reference
        if not chapter_num and chapter_title:
            m = re.search(r'\bCHAPTER\s+([IVXLCDM]+)', chapter_title, flags=re.I)
            if m:
                chapter_num = f"CHAPTER {_norm_roman(m.group(1))}"
        
        return (chapter_num, chapter_title)


    def is_section_table(self, element):
        """Flexible detection of a section table with debugging for missing sections."""
        if element is None or element.name != "table":
            return False

        # Quick check for sections 59-71
        table_text = element.get_text(strip=True)
        contains_service_sections = any(f"{i}." in table_text for i in range(59, 72))

        if self.debug_mode and contains_service_sections:
            print(f"\n*** CHECKING TABLE WITH SERVICE SECTIONS ***")
            print(f"Table attributes: {element.attrs}")
            print(f"Table text preview: {table_text[:200]}...")

        # CRITICAL CHECK FIRST: Exclude wrapper tables that contain multiple section links
        # These are page-level containers, not individual section tables
        section_links = element.find_all("a", href=lambda href: href and "consSelectedSection" in href)
        if len(section_links) > 1:
            # Multiple section links - this is a wrapper table, not a section table
            if self.debug_mode and contains_service_sections:
                print(f"*** TABLE HAS {len(section_links)} SECTION LINKS - WRAPPER TABLE, NOT A SECTION TABLE ***")
            return False

        # Standard attributes used on most pages
        if (element.get("cellpadding") == "0" and
            element.get("cellspacing") == "4mm" and
            element.get("align") == "center" and
            element.get("border") == "0"):

            # IMPORTANT: Tables with these attributes could be:
            # 1. Actual section tables (have section link or section number)
            # 2. Continuation tables for interpretation sections (only have amendments)
            # Only treat as section table if it has a section link OR starts with a section number

            # Check if table has a section link
            if len(section_links) == 1:
                if self.debug_mode and contains_service_sections:
                    print(f"*** SERVICE TABLE MATCHED STANDARD ATTRIBUTES + SECTION LINK ***")
                return True

            # Check if table starts with a section number pattern (e.g., "89.", "12A.", etc.)
            # This is for sections that might not have section links but are clearly new sections
            if re.search(r'^\s*\d+[A-Za-z\-]*\s*\.', table_text):
                if self.debug_mode and contains_service_sections:
                    print(f"*** SERVICE TABLE MATCHED STANDARD ATTRIBUTES + SECTION NUMBER ***")
                return True

            # If it has standard attributes but no section link and no section number,
            # it's likely a continuation table (e.g., definitions in interpretation sections)
            if self.debug_mode and contains_service_sections:
                print(f"*** TABLE HAS STANDARD ATTRIBUTES BUT NO SECTION LINK/NUMBER - LIKELY CONTINUATION TABLE ***")
            return False

        # Repealed blocks with marginal notes often lack exact attrs
        if "Repealed" in table_text and element.find_all("tr", class_="morginalnotes"):
            if self.debug_mode and contains_service_sections:
                print(f"*** SERVICE TABLE MATCHED REPEALED PATTERN ***")
            return True

        # A link to a specific section is a strong signal
        if len(section_links) == 1:
            # Only one section link - this is a single-section table
            if self.debug_mode and contains_service_sections:
                print(f"*** SERVICE TABLE HAS SECTION LINK: {section_links[0].get('href')} ***")
            return True

        # Alternative shape: "Title. 731A. Content"
        if re.search(r'^[^.]+\.\s*\d{1,4}[A-Za-z\-]*\.\s*\S', table_text):
            if self.debug_mode and contains_service_sections:
                print(f"*** SERVICE TABLE MATCHED TITLE-NUMBER PATTERN ***")
            return True

        # Generic heuristic: starts with "N." and has section-ish fonts
        if re.search(r'^\s*\d+[A-Za-z\-]*\s*\.', table_text):
            section_fonts = element.find("font", class_=["sectioncontent", "sectionshorttitle"])
            if section_fonts:
                if self.debug_mode and contains_service_sections:
                    print(f"*** SERVICE TABLE MATCHED GENERIC PATTERN WITH FONTS ***")
                return True
            if len(table_text) > 20 and not element.find("font", class_="sectionpart"):
                if self.debug_mode and contains_service_sections:
                    print(f"*** SERVICE TABLE MATCHED GENERIC PATTERN WITHOUT PART FONT ***")
                return True

        # Special check for SERVICE sections - be more lenient
        if contains_service_sections:
            print(f"*** TABLE WITH SERVICE SECTIONS FAILED ALL CHECKS - INVESTIGATING ***")
            print(f"Full table HTML: {str(element)[:500]}...")
            
            # Check if it's a nested table or has different structure
            parent_table = element.find_parent("table")
            if parent_table and parent_table != element:
                print(f"*** SERVICE TABLE IS NESTED - Parent table: {parent_table.attrs} ***")
            
            # Check for any section-like content
            has_section_number = bool(re.search(r'\b(59|60|61|62|63|64|65|66|67|68|69|70|71)\b', table_text))
            has_section_content = len(table_text) > 50
            
            if has_section_number and has_section_content:
                print(f"*** FORCING SERVICE TABLE TO BE RECOGNIZED AS SECTION TABLE ***")
                return True

        if self.debug_mode and contains_service_sections:
            print(f"*** SERVICE TABLE NOT RECOGNIZED AS SECTION TABLE ***")
        
        return False

    def extract_schedules(self, soup):
        """Extract schedule links from the legislation HTML."""
        base_url = "https://www.lawlanka.com/lal_v2/"
        
        schedules = []
        
        # Method 1: Look for schedule links with LegislativeConsFiles
        schedule_links = soup.find_all('a', href=lambda href: href and 'LegislativeConsFiles' in href)
        
        for link in schedule_links:
            text = link.get_text().strip() if link.get_text() else "Schedule"
            href = link.get('href')
            
            if href:
                # Handle relative URLs
                if not href.startswith('http'):
                    full_url = base_url + href
                else:
                    full_url = href
                    
                schedules.append({
                    "title": text,
                    "url": full_url
                })
        
        # Method 2: Look for javascript:openScheduleWindow calls (already handled by extract_schedule_parts)
        # but we can merge results
        schedule_parts = self.extract_schedule_parts(soup)
        
        # Add schedule parts that aren't already in schedules
        existing_urls = {s['url'] for s in schedules}
        for sp in schedule_parts:
            if sp['url'] not in existing_urls:
                schedules.append(sp)
        
        # Method 3: Look for any links with "schedule" in the text
        if not schedules:
            all_links = soup.find_all('a', href=True)
            for link in all_links:
                link_text = link.get_text().strip().lower()
                if 'schedule' in link_text:
                    href = link.get('href')
                    if href and not href.startswith('#'):  # Skip internal anchors
                        if not href.startswith('http'):
                            full_url = base_url + href
                        else:
                            full_url = href
                        
                        schedules.append({
                            "title": link.get_text().strip(),
                            "url": full_url
                        })
        
        # Remove duplicates
        seen = set()
        unique_schedules = []
        for s in schedules:
            if s['url'] not in seen:
                seen.add(s['url'])
                unique_schedules.append(s)
        
        return unique_schedules

    def extract_schedule_info(self, href_content, schedule_text):
        """Extracts folder name from JavaScript function call in href"""
        # Method 1: Handle both regular quotes and URL-encoded quotes
        regex = r'openScheduleWindow\((?:["\']|%22)(.*?)(?:["\']|%22)\s*,\s*(?:["\']|%22)(.*?)(?:["\']|%22)\)'
        match = re.search(regex, href_content)
        
        if not match or len(match.groups()) < 2:
            # Method 2: Extract everything between parentheses and split
            regex_alt = r'openScheduleWindow\((.*?)\)'
            match_alt = re.search(regex_alt, href_content)
            
            if match_alt:
                params_str = match_alt.group(1)
                params = params_str.split(',')
                
                if len(params) >= 2:
                    folder_name = params[0].strip().replace('"', '').replace("'", '').replace('%22', '')
                    return folder_name
            
            return None
        
        folder_name = match.group(1)
        
        if '%' in folder_name:
            folder_name = urllib.parse.unquote(folder_name)
        
        return folder_name

    def extract_schedule_parts(self, soup):
        """Extract schedule information from the HTML soup."""
        schedule_parts = []
        base_url = "https://www.lawlanka.com/lal_v2/pages/popUp/schedulePopUp.jsp?folderName="
        
        # Find all schedule links in the document
        schedule_links = soup.select('.ordinance a[href^="javascript:openScheduleWindow"]')
        
        for link in schedule_links:
            schedule_text = link.text.strip()
            href_content = link.get('href', '')
            folder_name = self.extract_schedule_info(href_content, schedule_text)
            
            if folder_name:
                full_url = f"{base_url}{folder_name}"
                schedule_parts.append({
                    "title": schedule_text,
                    "url": full_url
                })
        
        return schedule_parts    

    def extract_connected_pages_links(self, soup):
        """Extract connected pages links from the HTML."""
        base_link = "https://www.lawlanka.com/lal_v2/"
        tr_tag = soup.find("tr", attrs={"height": "21px", "valign": "middle"})
        
        if not tr_tag:
            return []
        
        table = tr_tag.find("table", class_="sectionorordinancecontent")
        if not table:
            return []
        
        links = []
        for a_tag in table.find_all("a"):
            full_link = base_link + a_tag.get("href")
            links.append({"index": a_tag.get_text(strip=True), "url": full_link})
        
        return links

    def get_document_statistics(self):
        """Return statistics about the sections found"""
        if not self.sections_found:
            return "No sections found"
        
        sorted_sections = sorted(self.sections_found)
        
        # Find gaps in section numbering
        gaps = []
        for i in range(len(sorted_sections) - 1):
            if sorted_sections[i+1] - sorted_sections[i] > 1:
                gap_start = sorted_sections[i] + 1
                gap_end = sorted_sections[i+1] - 1
                if gap_start == gap_end:
                    gaps.append(str(gap_start))
                else:
                    gaps.append(f"{gap_start}-{gap_end}")
        
        stats = {
            "total_sections": len(self.sections_found),
            "range": f"{min(sorted_sections)} to {max(sorted_sections)}",
            "gaps": gaps if gaps else "None",
            "coverage": f"{len(self.sections_found) / (max(sorted_sections) - min(sorted_sections) + 1) * 100:.1f}%"
        }
        
        return stats

    def process_html_files(self):
        """Process HTML files with enhanced section extraction"""
        if not self.html_folder or not self.data_folder:
            print("Error: Paths not set. Please set paths before processing.")
            return
        
        os.makedirs(self.data_folder, exist_ok=True)
        
        if not os.path.exists(self.html_folder):
            print(f"Error: HTML folder {self.html_folder} does not exist.")
            return
        
        subfolders = [f for f in os.listdir(self.html_folder) 
                     if os.path.isdir(os.path.join(self.html_folder, f))]
        
        if not subfolders:
            print(f"No subfolders found in {self.html_folder}")
            return
        
        print(f"Found {len(subfolders)} subfolders to process in {self.html_folder}")
        
        for subfolder in subfolders:
            # Reset counters for each file
            self.section_count = 0
            self.last_section_number = 0
            self.sections_found = set()
            self.section_range = {"min": float('inf'), "max": 0}
            
            subfolder_path = os.path.join(self.html_folder, subfolder)
            html_file = f"{subfolder}.html"
            html_path = os.path.join(subfolder_path, html_file)
            
            if not os.path.exists(html_path):
                print(f"Warning: Expected HTML file {html_file} not found in {subfolder_path}")
                continue
            
            print(f"\nProcessing {subfolder}/{html_file}...")
            
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            # Create JSON object
            json_data = self.construct_json_data(html_content, subfolder)
            
            # Save JSON file
            output_file = os.path.join(self.data_folder, f"{subfolder}.json")
            with open(output_file, "w", encoding="utf-8") as out_f:
                json.dump(json_data, out_f, indent=4, ensure_ascii=False)
            
            # Print statistics
            stats = self.get_document_statistics()
            print(f"Finished processing {subfolder}/{html_file}")
            print(f"Statistics: {json.dumps(stats, indent=2)}")
            print(f"Data saved to {output_file}")
        
        print(f"\nAll HTML files in {self.html_folder} have been processed.")     
        # --- NEW: detect PART/CHAPTER containers and their numeric ranges
    def extract_textual_parts_and_groups(self, raw_text: str):
        """
        Enhanced extraction with deduplication.
        Prevents duplicate chapters from being added to the structure.
        """
        import re
        
        if not raw_text:
            if self.debug_mode:
                print("  extract_textual_parts_and_groups: No raw text provided")
            return []

        # Normalize text
        text = raw_text.replace("\r\n", "\n").replace("\xa0", " ")
        text = re.sub(r"[\u2000-\u200b\u2028\u2029\u00ad]", " ", text)
        text = text.replace("–", "-").replace("—", "-")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Find all sections first
        # IMPORTANT: Limit to 1-3 digit numbers to avoid matching years like "2023."
        # Section numbers in legislation are typically < 1000
        # Simple pattern - outliers will be filtered later in range calculation
        sec_rx = re.compile(r'(?m)^\s*(\d{1,3})[A-Za-z\-]*\s*\.?\s*(?=[A-Z("])')
        all_sections = []
        for m in sec_rx.finditer(text):
            try:
                num = int(m.group(1))
                all_sections.append((m.start(), num))
            except:
                pass
        all_sections.sort(key=lambda x: x[0])
        
        found_items = []
        seen_positions = set()  # Track positions to avoid duplicates at same location
        
        # Find PARTS
        part_patterns = [
            re.compile(r'(?m)^\s*["\']?\s*PART\s+([IVXLCDM]+[A-Z]?)(?:\s|$|\.)', re.I),
            re.compile(r'(?m)^\s*["\']?\s*PART\s+(\d+[A-Z]?)(?:\s|$|\.)', re.I),
        ]
        
        for pattern in part_patterns:
            for m in pattern.finditer(text):
                # Skip if we've already found something at this position
                if m.start() in seen_positions:
                    continue
                seen_positions.add(m.start())
                
                identifier = m.group(1) if m.lastindex >= 1 else ""
                title = self._extract_title_from_context(text, m.start(), m.end())
                
                found_items.append({
                    'type': 'PART',
                    'identifier': identifier.upper(),
                    'number': f"PART {identifier.upper()}",
                    'title': title,
                    'position': m.start(),
                    'is_repealed': 'Repealed' in text[m.start():m.end()+200]
                })
        
        # Find CHAPTERS - with better deduplication
        chapter_patterns = [
            re.compile(r'(?m)^\s*CHAPTER\s+([IVXLCDM]+(?:\s+(?:AND|&)\s+[IVXLCDM]+)?)\b', re.I),
            re.compile(r'(?m)^\s*CHAPTER\s+(\d+)\b', re.I),
        ]
        
        for pattern in chapter_patterns:
            for m in pattern.finditer(text):
                # Skip if too close to an existing position (within 50 chars)
                skip = False
                for pos in seen_positions:
                    if abs(m.start() - pos) < 50:
                        skip = True
                        break
                
                if skip:
                    continue
                
                seen_positions.add(m.start())
                
                identifier = m.group(1) if m.lastindex >= 1 else ""
                title = self._extract_title_from_context(text, m.start(), m.end())
                
                # Skip if title is too long (likely captured wrong text)
                if title and len(title) > 200:
                    title = None
                
                found_items.append({
                    'type': 'CHAPTER',
                    'identifier': identifier.upper(),
                    'number': f"CHAPTER {identifier.upper()}",
                    'title': title,
                    'position': m.start(),
                    'is_repealed': self._check_if_repealed(text, m.start(), m.end())
                })
        
        # Sort by position
        found_items.sort(key=lambda x: x['position'])
        
        # Calculate section ranges for chapters and parts
        for i, item in enumerate(found_items):
            start_pos = item['position']
            next_pos = found_items[i + 1]['position'] if i + 1 < len(found_items) else len(text)

            sections_in_range = [num for pos, num in all_sections if start_pos <= pos < next_pos]

            # IMPORTANT: Handle Section 1 (Short title) carefully
            # Section 1 should be excluded from numbered PARTS (PART II, PART VI, etc.)
            # but CAN be included in the first CHAPTER if:
            # 1. It's a CHAPTER (not a PART), AND
            # 2. It's the first CHAPTER in the document (i==0 or previous item is not a CHAPTER), AND
            # 3. Section 1 appears in the text after this chapter's position
            #
            # This handles two cases:
            # - legislation_A_5: sections 1-4 before CHAPTER I → excluded (correct)
            # - legislation_A_2: section 1 after CHAPTER 80 → included (correct)
            should_exclude_section_1 = False

            if 1 in sections_in_range:
                # Always exclude section 1 from numbered PARTS
                if item['type'] == 'PART' and item.get('identifier', '') != 'MAIN':
                    should_exclude_section_1 = True
                    if self.debug_mode:
                        print(f"  [SECTION 1] Excluding from PART {item.get('identifier')}")
                # For chapters, exclude section 1 unless it's the first UNIQUE chapter
                elif item['type'] == 'CHAPTER':
                    # Check if this is the first occurrence of ANY chapter (no previous CHAPTER items with different identifiers)
                    current_identifier = item.get('identifier', '')
                    previous_chapter_identifiers = set()
                    for prev_item in found_items[:i]:
                        if prev_item['type'] == 'CHAPTER':
                            prev_id = prev_item.get('identifier', '')
                            if prev_id and prev_id != current_identifier:
                                previous_chapter_identifiers.add(prev_id)

                    is_first_unique_chapter = len(previous_chapter_identifiers) == 0
                    if self.debug_mode:
                        print(f"  [SECTION 1] {item.get('number')}: is_first_unique_chapter={is_first_unique_chapter}, previous_chapters={previous_chapter_identifiers}")

                    if not is_first_unique_chapter:
                        # Not the first unique chapter, exclude section 1
                        should_exclude_section_1 = True
                    # else: is first unique chapter, keep section 1 if it appears after the chapter heading

            if should_exclude_section_1:
                sections_in_range = [n for n in sections_in_range if n != 1]
                if self.debug_mode:
                    print(f"  [SECTION 1] Excluded, new range: {sections_in_range}")

            if sections_in_range:
                # Filter out outliers: if there's a big gap, keep only the main cluster
                # This prevents pagination numbers or stray numbers from affecting the range
                sections_sorted = sorted(sections_in_range)
                if len(sections_sorted) >= 3:
                    # Find the most common cluster (consecutive or near-consecutive sections)
                    gaps = [sections_sorted[j+1] - sections_sorted[j] for j in range(len(sections_sorted)-1)]
                    # If there's a gap > 20, it's likely an outlier
                    max_gap_idx = max(range(len(gaps)), key=lambda j: gaps[j]) if gaps else -1
                    if max_gap_idx >= 0 and gaps[max_gap_idx] > 20:
                        # Split at the large gap and keep the larger cluster
                        before_gap = sections_sorted[:max_gap_idx+1]
                        after_gap = sections_sorted[max_gap_idx+1:]
                        sections_in_range = before_gap if len(before_gap) >= len(after_gap) else after_gap

                item['min'] = min(sections_in_range)
                item['max'] = max(sections_in_range)
        
        # Extract subchapters for each chapter
        for i, item in enumerate(found_items):
            if item['type'] == 'CHAPTER':
                chapter_start = item['position']
                next_item_pos = found_items[i + 1]['position'] if i + 1 < len(found_items) else len(text)
                chapter_text = text[chapter_start:next_item_pos]
                
                chapter_min = item.get('min')
                chapter_max = item.get('max')
                
                # Find SUBCHAPTERS within this chapter
                groups = self._extract_subchapter_groups(
                    chapter_text, 
                    all_sections, 
                    chapter_start,
                    chapter_min,
                    chapter_max
                )
                
                item['groups'] = groups
        
        # Build and return deduplicated structure
        return self._organize_structure(found_items)
    def _organize_nested_structure_with_subchapters(self, items):
        """
        Organize items into Parts -> Chapters -> SubChapters hierarchy.
        Validates that SubChapter titles don't match parent Chapter title/number.
        """
        structure = []
        current_part = None
        current_chapter = None
        orphaned_chapters = []
        orphaned_subchapters = []
        
        for item in items:
            if item['type'] == 'PART':
                # Save any orphaned chapters to previous part
                if orphaned_chapters and current_part:
                    current_part['chapters'].extend(orphaned_chapters)
                    orphaned_chapters = []
                
                # Create new part
                current_part = {
                    'number': item['number'],
                    'title': item['title'],
                    'min': item.get('min'),
                    'max': item.get('max'),
                    'chapters': []
                }
                structure.append(current_part)
                current_chapter = None
                
            elif item['type'] == 'CHAPTER':
                # Save any orphaned subchapters to previous chapter
                if orphaned_subchapters and current_chapter:
                    # Filter out subchapters that match chapter title
                    valid_subchapters = []
                    for subch in orphaned_subchapters:
                        subch_title = (subch.get('title') or '').strip().upper()
                        ch_title = (current_chapter.get('title') or '').strip().upper()
                        ch_number = (current_chapter.get('number') or '').strip().upper()
                        
                        # Skip if subchapter title matches chapter title or number
                        if subch_title and (subch_title == ch_title or subch_title == ch_number):
                            continue
                        
                        # Also skip if subchapter title contains the chapter number
                        if ch_number and ch_number in subch_title:
                            continue
                            
                        valid_subchapters.append(subch)
                    
                    current_chapter['subchapters'].extend(valid_subchapters)
                    orphaned_subchapters = []
                
                # Create new chapter
                current_chapter = {
                    'number': item['number'],
                    'title': item['title'],
                    'min': item.get('min'),
                    'max': item.get('max'),
                    'subchapters': []
                }
                
                if current_part:
                    current_part['chapters'].append(current_chapter)
                else:
                    orphaned_chapters.append(current_chapter)
                    
            elif item['type'] == 'SUBCHAPTER':
                # Validate subchapter doesn't match current chapter
                if current_chapter:
                    subch_title = (item.get('title') or '').strip().upper()
                    ch_title = (current_chapter.get('title') or '').strip().upper()
                    ch_number = (current_chapter.get('number') or '').strip().upper()
                    
                    # Skip if subchapter title matches chapter title or number
                    if subch_title and (subch_title == ch_title or subch_title == ch_number):
                        if self.debug_mode:
                            print(f"  Skipping subchapter '{subch_title}' - matches chapter")
                        continue
                    
                    # Skip if it's just the chapter header repeated
                    if ch_number and subch_title.startswith(ch_number):
                        if self.debug_mode:
                            print(f"  Skipping subchapter '{subch_title}' - contains chapter number")
                        continue
                
                # Create subchapter
                subchapter = {
                    'number': item.get('number'),  # Can be None
                    'title': item['title'],
                    'min': item.get('min'),
                    'max': item.get('max')
                }
                
                if current_chapter:
                    current_chapter['subchapters'].append(subchapter)
                else:
                    orphaned_subchapters.append(subchapter)
        
        # Handle remaining orphaned items
        if orphaned_chapters or orphaned_subchapters:
            main_part = None
            for part in structure:
                if part['number'] == 'MAIN PART':
                    main_part = part
                    break
            
            if not main_part:
                min_sec = float('inf')
                max_sec = 0
                
                for ch in orphaned_chapters:
                    if ch.get('min') and ch['min'] < min_sec:
                        min_sec = ch['min']
                    if ch.get('max') and ch['max'] > max_sec:
                        max_sec = ch['max']
                
                for subch in orphaned_subchapters:
                    if subch.get('min') and subch['min'] < min_sec:
                        min_sec = subch['min']
                    if subch.get('max') and subch['max'] > max_sec:
                        max_sec = subch['max']
                
                # IMPORTANT: Don't use 1000 as fallback for MAIN PART max
                # If max_sec is 0, it means MAIN PART has no sections (or only section 1 which is excluded)
                # In this case, set max to 1 (just section 1) to avoid capturing all sections
                main_part = {
                    'number': 'MAIN PART',
                    'title': None,
                    'min': min_sec if min_sec != float('inf') else 1,
                    'max': max_sec if max_sec != 0 else 1,
                    'chapters': []
                }
                structure.insert(0, main_part)
            
            main_part['chapters'].extend(orphaned_chapters)
            
            if orphaned_subchapters:
                # Don't create subchapters without a proper parent chapter
                default_chapter = {
                    'number': None,
                    'title': 'GENERAL',
                    'min': min(sc.get('min', 1) for sc in orphaned_subchapters),
                    'max': max(sc.get('max', 1000) for sc in orphaned_subchapters),
                    'subchapters': orphaned_subchapters
                }
                main_part['chapters'].append(default_chapter)
        
        # Final validation: remove any duplicate subchapter titles within same chapter
        for part in structure:
            for chapter in part.get('chapters', []):
                if chapter.get('subchapters'):
                    seen_titles = set()
                    unique_subchapters = []
                    
                    for subch in chapter['subchapters']:
                        title_key = (subch.get('title') or '').strip().upper()
                        if title_key and title_key not in seen_titles:
                            seen_titles.add(title_key)
                            unique_subchapters.append(subch)
                        elif not title_key:
                            # Keep subchapters without titles
                            unique_subchapters.append(subch)
                    
                    chapter['subchapters'] = unique_subchapters
        
        return structure
    def _organize_nested_structure(self, found_items):
        """
        Organize flat list of parts and chapters into nested structure.
        Chapters are placed under their parent parts based on section ranges.
        """
        if not found_items:
            return []
        
        # Separate parts and chapters
        parts = []
        chapters = []
        
        for item in found_items:
            if item['kind'] == 'PART':
                parts.append(item)
            elif item['kind'] == 'CHAPTER':
                chapters.append(item)
        
        # Build nested structure
        nested_structure = []
        
        if not parts:
            # No parts found - create a single MAIN PART to hold all chapters
            # IMPORTANT: Use None for max when no parts exist, or calculate from chapters
            # Don't use 1000 as it causes routing issues
            main_part = {
                "number": "MAIN PART",
                "title": None,
                "min": 1,
                "max": None,  # Will be calculated from chapters if needed
                "chapters": []
            }
            
            # Add all chapters to MAIN PART
            for ch in chapters:
                main_part["chapters"].append({
                    "number": ch['number'],
                    "title": ch.get('title'),
                    "min": ch.get('min'),
                    "max": ch.get('max')
                })
            
            nested_structure.append(main_part)
        else:
            # Process each part and assign chapters
            for part in parts:
                part_obj = {
                    "number": part['number'],
                    "title": part.get('title'),
                    "min": part.get('min'),
                    "max": part.get('max'),
                    "chapters": []
                }
                
                # Find chapters that belong to this part
                part_min = part.get('min', 0)
                part_max = part.get('max', 1000)
                
                for ch in chapters:
                    ch_min = ch.get('min', 0)
                    ch_max = ch.get('max', 0)
                    
                    # Check if chapter falls within part's range
                    # A chapter belongs to a part if its range overlaps significantly
                    if ch_min >= part_min and ch_max <= part_max:
                        # Full containment
                        part_obj["chapters"].append({
                            "number": ch['number'],
                            "title": ch.get('title'),
                            "min": ch_min,
                            "max": ch_max
                        })
                    elif ch_min <= part_max and ch_max >= part_min:
                        # Partial overlap - calculate overlap percentage
                        overlap_start = max(ch_min, part_min)
                        overlap_end = min(ch_max, part_max)
                        overlap_size = max(0, overlap_end - overlap_start + 1)
                        ch_size = ch_max - ch_min + 1
                        
                        # If more than 50% of chapter is in this part, assign it here
                        if ch_size > 0 and overlap_size / ch_size > 0.5:
                            part_obj["chapters"].append({
                                "number": ch['number'],
                                "title": ch.get('title'),
                                "min": ch_min,
                                "max": ch_max
                            })
                
                nested_structure.append(part_obj)
            
            # Check for orphaned chapters (not assigned to any part)
            assigned_chapters = set()
            for part_obj in nested_structure:
                for ch in part_obj.get("chapters", []):
                    assigned_chapters.add(ch["number"])
            
            orphaned = [ch for ch in chapters if ch['number'] not in assigned_chapters]
            
            if orphaned:
                # Create a MAIN PART for orphaned chapters
                main_part = {
                    "number": "MAIN PART",
                    "title": None,
                    "min": min(ch.get('min', 1) for ch in orphaned),
                    "max": max(ch.get('max', 100) for ch in orphaned),
                    "chapters": []
                }
                
                for ch in orphaned:
                    main_part["chapters"].append({
                        "number": ch['number'],
                        "title": ch.get('title'),
                        "min": ch.get('min'),
                        "max": ch.get('max')
                    })
                
                # Insert MAIN PART at the beginning
                nested_structure.insert(0, main_part)
        
        # Sort chapters within each part
        for part_obj in nested_structure:
            part_obj["chapters"].sort(key=lambda ch: (ch.get("min", 999), ch["number"]))
        
        # Sort parts: MAIN PART first, then others by minimum section number
        nested_structure.sort(key=lambda p: (
            0 if p["number"] == "MAIN PART" else 1,
            p.get("min", 999),
            p["number"]
        ))
        
        if self.debug_mode:
            print(f"\n=== NESTED STRUCTURE CREATED ===")
            for part in nested_structure:
                print(f"  {part['number']}: sections {part.get('min', '?')}-{part.get('max', '?')}")
                for ch in part.get("chapters", []):
                    print(f"    └── {ch['number']}: sections {ch.get('min', '?')}-{ch.get('max', '?')}")
        
        return nested_structure

    def _extract_dynamic_title(self, text, match_start, match_end):
        """
        Dynamically extract title from text around a structural element.
        """
        import re
        
        # Look ahead for title on same line or next line
        context = text[match_end:match_end + 300]
        lines = context.split('\n')
        
        for line in lines[:3]:
            line = line.strip()
            if not line or re.match(r'^\d+\.', line):
                continue
            
            # Check if line looks like a title
            if (len(line) > 5 and 
                not line.isdigit() and
                not re.match(r'^\[\s*§', line)):
                
                # Clean and return
                title = self.clean_text(line)
                if 'Repealed' in title:
                    return 'Repealed'
                return title if len(title) < 150 else title[:150]
        
        return None
    def _check_if_repealed(self, text, start, end):
        """
        Check if a chapter/part is marked as repealed.
        """
        import re
        # Check immediate context
        context = text[start:min(end + 200, len(text))]
        return bool(re.search(r'\bRepealed\b', context, re.I))

    def _calculate_subchapter_confidence(self, header_text, full_text, position):
        """
        Calculate confidence that a header is a subchapter.
        """
        import re
        
        confidence = 0.3  # Base
        
        # Check if all caps
        if header_text.isupper():
            confidence += 0.2
        
        # Check if followed by sections
        after_text = full_text[position:position + 500]
        if re.search(r'\n\s*\d+\.', after_text):
            confidence += 0.3
        
        # Check for legal/administrative keywords
        legal_terms = [
            'GENERAL', 'SPECIAL', 'PROCEDURE', 'PROVISIONS', 'ENFORCEMENT',
            'PENALTIES', 'APPEALS', 'JURISDICTION', 'POWERS', 'DUTIES',
            'RIGHTS', 'APPLICATION', 'SERVICE', 'ADMINISTRATION', 'REGISTRATION',
            'DEFINITIONS', 'INTERPRETATION', 'MISCELLANEOUS', 'OFFENCES'
        ]
        
        header_upper = header_text.upper()
        if any(term in header_upper for term in legal_terms):
            confidence += 0.2
        
        # Length check
        word_count = len(header_text.split())
        if 1 <= word_count <= 10:
            confidence += 0.1
        
        return min(confidence, 1.0)
    def extract_all_chapters_from_html(self, soup, selected_blob_html=""):
        """
        Extract ALL chapters dynamically without hardcoded expectations.
        """
        import re
        from bs4 import BeautifulSoup
        
        all_chapters = {}
        
        # Get all text sources
        visible_text = soup.get_text()
        
        blob_text = ""
        if selected_blob_html:
            try:
                import html as html_module
                unescaped = html_module.unescape(selected_blob_html)
                blob_soup = BeautifulSoup(unescaped, "html.parser")
                blob_text = blob_soup.get_text()
            except:
                blob_text = selected_blob_html
        
        # Combine all text
        full_text = visible_text + "\n" + blob_text
        
        # Use dynamic patterns
        patterns = [
            r'CHAPTER\s+([IVXLCDM]+(?:\s+AND\s+[IVXLCDM]+)?)',
            r'CHAPTER\s+(\d+)',
            r'PART\s+([IVXLCDM]+)',
            r'PART\s+(\d+)',
            r'TITLE\s+([IVXLCDM]+|\d+)',
            r'BOOK\s+([IVXLCDM]+|\d+)',
        ]
        
        for pattern in patterns:
            for m in re.finditer(pattern, full_text, re.I | re.M):
                match_text = m.group(0).upper()
                
                # Determine type
                if 'CHAPTER' in match_text:
                    prefix = 'CHAPTER'
                elif 'PART' in match_text:
                    prefix = 'PART'
                elif 'TITLE' in match_text:
                    prefix = 'TITLE'
                elif 'BOOK' in match_text:
                    prefix = 'BOOK'
                else:
                    continue
                
                identifier = m.group(1).upper() if m.lastindex >= 1 else ""
                chapter_num = f"{prefix} {identifier}"
                
                if chapter_num not in all_chapters:
                    # Check if repealed
                    context = full_text[m.start():m.end() + 200]
                    is_repealed = 'Repealed' in context
                    
                    all_chapters[chapter_num] = {
                        "number": chapter_num,
                        "is_repealed": is_repealed,
                        "source": "dynamic_detection"
                    }
        
        return all_chapters
    
    def strip_leading_section_number(self, text: str, section_number: str) -> str:
            if not text:
                return text
            if section_number:
                pattern = r'^\s*' + re.escape(section_number) + r'\s*\.?\s+'
                new_text = re.sub(pattern, '', text, count=1, flags=re.IGNORECASE)
                if new_text != text:
                    return new_text
            # generic fallback (when number not matched or not provided)
            return re.sub(r'^\s*\d+[A-Za-z\-]*\s*\.?\s+', '', text, count=1)
    
    def _extract_all_textual_containers(self, soup, raw_blob):
        """
        Extract PART containers with their chapter groups. 
        Only returns PART-level containers, with chapters as nested groups within.
        """
        def _norm(items):
            out = []
            for c in items or []:
                try:
                    if isinstance(c.get("min"), int) and isinstance(c.get("max"), int):
                        out.append({
                            "number": c.get("number"),
                            "title": c.get("title"),
                            "min": int(c["min"]),
                            "max": int(c["max"]),
                            "groups": c.get("groups") or []
                        })
                except Exception:
                    pass
            return out

        vis = self.extract_textual_parts_and_groups(soup.get_text("\n")) or []

        try:
            blob_text = BeautifulSoup(raw_blob or "", "html.parser").get_text("\n")
        except Exception:
            blob_text = ""
        hid = self.extract_textual_parts_and_groups(blob_text) or []

        vis, hid = _norm(vis), _norm(hid)

        # Separate parts and chapters
        parts_dict = {}
        chapters_list = []
        
        for src in (vis + hid):
            if src.get("number", "").startswith("PART"):
                key = src.get("number")
                if key not in parts_dict:
                    parts_dict[key] = src
                else:
                    # Merge if we have duplicates - prefer the one with more info
                    cur = parts_dict[key]
                    cur_w = cur["max"] - cur["min"]
                    new_w = src["max"] - src["min"]
                    if new_w < cur_w or (new_w == cur_w and src.get("title") and not cur.get("title")):
                        parts_dict[key] = src
            elif src.get("number", "").startswith("CHAPTER"):
                chapters_list.append(src)

        # FIX PART GAPS BEFORE ASSIGNING CHAPTERS
        # Sort parts by min section number
        if parts_dict:
            if self.debug_mode:
                print(f"  [TEXTUAL] Found {len(parts_dict)} parts before gap fixing:")
                for pk, pv in parts_dict.items():
                    print(f"    {pk}: min={pv.get('min')}, max={pv.get('max')}")

            sorted_parts = sorted(parts_dict.values(), key=lambda p: p.get("min") if p.get("min") is not None else 999999)

            # Extend part ranges to cover gaps between parts
            for i in range(len(sorted_parts) - 1):
                current_part = sorted_parts[i]
                next_part = sorted_parts[i + 1]

                current_max = current_part.get("max")
                next_min = next_part.get("min")

                # Skip if either value is None
                if current_max is None or next_min is None:
                    continue

                if current_max < next_min - 1:
                    # There's a gap - extend current part's max to cover it
                    gap_size = next_min - current_max - 1
                    new_max = next_min - 1

                    if self.debug_mode:
                        print(f"  [TEXTUAL] Fixing gap: Extending {current_part['number']} max from {current_max} to {new_max} (covers {gap_size} sections)")

                    # Extend current part to cover the gap
                    # This ensures chapters falling in the gap get assigned to the correct part
                    current_part["max"] = new_max

        # Now assign chapters to parts based on their ranges
        for chapter in chapters_list:
            ch_min = chapter["min"]
            ch_max = chapter["max"]
            best_part = None
            best_overlap = 0
            
            for part_key, part in parts_dict.items():
                if part["min"] <= ch_min <= part["max"] and part["min"] <= ch_max <= part["max"]:
                    # Full containment
                    overlap = ch_max - ch_min + 1
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_part = part_key
            
            if best_part:
                # Add chapter to part's groups
                parts_dict[best_part]["groups"].append({
                    "number": chapter["number"],
                    "title": chapter.get("title"),
                    "min": chapter["min"],
                    "max": chapter["max"]
                })
        
        # Return only PART containers (chapters are nested within as groups)
        return list(parts_dict.values())

    def _extract_textual_parts_and_groups(self, full_text: str):
        """
        Parse the raw statute text and return a flat list of textual containers
        (PART / CHAPTER) with their titles and (min,max) section ranges.
        Each container item has:
        {
            "kind": "PART" | "CHAPTER",
            "number": "PART IV" | "CHAPTER XX",
            "title": "<title or None>",
            "start": int,   # char index in full_text
            "end": int,     # char index in full_text (exclusive), best-effort
            "min": int|None,
            "max": int|None,
        }
        """
        import re

        text = full_text or ""
        n = len(text)

        # ---- Heading patterns (safer: identifier must end before space/punct/boundary) ----
        pat_defs = [
            # PARTS
            (re.compile(r'(?m)^\s*PART\s+([IVXLCDM]+)(?=[\s\.\-:;]|$)', re.I), "PART", "roman"),
            (re.compile(r'(?m)^\s*PART\s+(\d+)(?=[\s\.\-:;]|$)', re.I), "PART", "arabic"),

            # CHAPTERS
            (re.compile(r'(?m)^\s*CHAPTER\s+([IVXLCDM]+(?:\s+(?:AND|&)\s+[IVXLCDM]+)?)'
                        r'(?=[\s\.\-:;]|$)', re.I), "CHAPTER", "roman"),
            (re.compile(r'(?m)^\s*CHAPTER\s+([ⅰ-ⅿⅠ-Ⅿ]+)(?=[\s\.\-:;]|$)', re.I), "CHAPTER", "roman_unicode"),
            (re.compile(r'(?m)^\s*CHAPTER\s+(\d+)(?=[\s\.\-:;]|$)', re.I), "CHAPTER", "arabic"),
        ]

        # Find all headings with positions
        hits = []
        for rx, kind, _t in pat_defs:
            for m in rx.finditer(text):
                start = m.start()
                endline = text.find("\n", start)
                if endline == -1:
                    endline = n
                raw_line = text[start:endline]

                ident = m.group(1).strip()
                header_number = f"{kind} {ident}".strip()

                # Try to extract title on same line after a delimiter, or on next line
                title = None
                # same line delimiters like " — ", " - ", ":"
                same_line = re.split(r'\s(?:—|-|:)\s', raw_line, maxsplit=1)
                if len(same_line) == 2 and same_line[1].strip():
                    title = same_line[1].strip()
                else:
                    # look one line down if it looks like a title (not another heading/blank)
                    next_nl = endline + 1
                    next_end = text.find("\n", next_nl)
                    if next_end == -1:
                        next_end = n
                    next_line = text[next_nl:next_end].strip()
                    if next_line and not next_line.upper().startswith(("CHAPTER", "PART", "BOOK", "TITLE", "DIVISION")):
                        title = next_line

                hits.append({
                    "kind": kind,
                    "number": header_number,
                    "title": title,
                    "start": start,
                    "line_end": endline,  # end of the header line
                })

        if not hits:
            return []

        # Sort by start position and assign end bounds
        hits.sort(key=lambda h: h["start"])
        for i, h in enumerate(hits):
            h["end"] = hits[i + 1]["start"] if i + 1 < len(hits) else n

        # Precompute a simple section-number finder to derive min/max in ranges
        # IMPORTANT: Limit to 1-3 digits to avoid matching years like "2023."
        # Period is optional to catch interpretation sections
        # Limit whitespace before capital letter to avoid pagination numbers
        sec_rx = re.compile(r'(?m)^\s*(\d{1,3})[A-Za-z\-]*\s*(?:[\.、)]|-\s|(?=\s{0,10}[A-Z("]))', re.U)

        containers = []
        for h in hits:
            frag = text[h["line_end"]:h["end"]]  # body after the heading line
            sec_nums = [int(m.group(1)) for m in sec_rx.finditer(frag)]

            # IMPORTANT: Exclude Section 1 from PART/CHAPTER ranges
            # Section 1 (Short title) should ONLY belong to MAIN PART, never to textual containers
            # This prevents section 1 from being incorrectly assigned to PART VI or other parts
            sec_nums = [n for n in sec_nums if n != 1]

            cmin = min(sec_nums) if sec_nums else None
            cmax = max(sec_nums) if sec_nums else None

            containers.append({
                "kind": h["kind"],
                "number": h["number"],
                "title": h.get("title"),
                "start": h["start"],
                "end": h["end"],
                "min": cmin,
                "max": cmax,
            })

        return containers
   


        
    def _merge_amendments(self, a, b):
        key = lambda x: (x.get("text"), x.get("link"))
        seen, out = set(), []
        for src in (a or []) + (b or []):
            k = key(src or {})
            if k in seen: 
                continue
            seen.add(k); out.append(src)
        return out or None
    def postprocess_section_payload(self, section):
        """
        Final cleanup for a section dict:
        - ensure amendment is list[{"text","link"}] (convert strings -> objects)
        - remove duplicate amendments & subsection identifiers
        - prevent "Short title" from absorbing quoted definition lines
        """
        import re
        
        if not section:
            return section

        def _fix_amendments(am):
            if not am:
                return None
            fixed = []
            base_link = "https://www.lawlanka.com/lal_v2/pages/popUp/actPopUp.jsp?actId="
            for it in am:
                if isinstance(it, str):
                    fixed.append({"text": self.clean_text(it), "link": None})
                    continue
                if isinstance(it, dict):
                    txt = self.clean_text(it.get("text", ""))
                    href = it.get("link")
                    if href and href.startswith("javascript:openSectionOrdinanceWindow("):
                        m = re.search(r"openSectionOrdinanceWindow\('([^']+)','[^']*'\)", href)
                        if m:
                            href = f"{base_link}{m.group(1).strip()}"
                    fixed.append({"text": txt, "link": href})
            # dedupe
            seen = set()
            out = []
            for x in fixed:
                key = (x.get("text"), x.get("link"))
                if key in seen:
                    continue
                seen.add(key)
                out.append(x)
            return out or None

        # Fix amendments
        section["amendment"] = _fix_amendments(section.get("amendment"))

        # Check if this is a Short title section - if so, it's already been handled
        # by _sanitize_short_title_section, don't modify content
        title = (section.get("title") or "").strip().lower()
        is_short_title = bool(re.search(r'\bshort\s*title\b', title))
        
        # Note: SubChapter headings in content will be handled later by SubChapter injection
        # They need to stay in the content initially so they're in the full_text for detection

        # If NOT a short title section, check for definition lines that shouldn't be in content
        if not is_short_title:
            # Check if content has definition lines that should be separated
            defs = []
            keep = []
            for line in section.get("content") or []:
                # Lines that look like: "action" means ... ; "Attorney-General" includes ...
                if re.match(r'^\s*"[A-Za-z0-9 \-–—\.\'/]+"s*(means|includes|is)\b', line, flags=re.I):
                    defs.append(line)
                else:
                    keep.append(line)

            # Only modify if we found definitions
            if defs:
                section["content"] = keep
                section["definitions"] = defs  # optional, for your later use

        # De-dup subsection identifiers within the same level
        seen_ids = set()
        uniq = []
        for s in section.get("subsections") or []:
            ident = (s.get("identifier") or "").strip().lower()
            if ident and ident in seen_ids:
                continue
            seen_ids.add(ident)
            uniq.append(s)
        section["subsections"] = uniq

        # DO NOT modify or duplicate content array - it's already correct from extraction
        # The duplication issue happens when content is modified here after being set correctly
        
        return section
    def _merge_subsections(self, A, B):
        """Merge by identifier; recurse into children."""
        if not A: return B or []
        if not B: return A or []
        by_id = { (s.get("identifier") or ""): s for s in A }
        for s in B:
            sid = s.get("identifier") or ""
            if sid not in by_id:
                by_id[sid] = s
            else:
                t = by_id[sid]
                # prefer richer content
                t["content"] = max(t.get("content",""), s.get("content",""), key=len)
                t["subsections"] = self._merge_subsections(t.get("subsections", []), s.get("subsections", []))
        # keep original-ish order: items from A first, then any new from B
        ids_in_A = [s.get("identifier") or "" for s in A]
        tail = [by_id[k] for k in by_id.keys() if k not in ids_in_A]
        return A + tail

    def _dedupe_sections_in_all_parts(self, parts):
        def _merge_explanations(A, B):
            if not A: return B
            if not B: return A
            title = (A.get("title") or B.get("title") or "Explanation")
            content = self._uniq_order((A.get("content") or []) + (B.get("content") or []))
            return {"title": title, "content": content, "subsections": []}

        def _merge_illustrations(A, B):
            if not A: return B
            if not B: return A
            title = (A.get("title") or B.get("title") or "Illustrations")
            content = self._uniq_order((A.get("content") or []) + (B.get("content") or []))
            seen, subs = set(), []
            for s in (A.get("subsections") or []) + (B.get("subsections") or []):
                key = ((s.get("identifier") or ""), (s.get("content") or ""))
                if key in seen: 
                    continue
                seen.add(key); subs.append(s)
            return {"title": title, "content": content, "subsections": subs}

        for part in parts:
            for grp in part.get("section_groups", []) or []:
                src = grp.get("sections", []) or []
                by_num, order = {}, []
                for s in src:
                    n = s.get("number")
                    if n is None:
                        if s.get("content") or s.get("subsections") or s.get("amendment"):
                            order.append(None)
                            by_num.setdefault(None, []).append(s)
                        continue

                    if n not in by_num:
                        by_num[n] = s
                        order.append(n)
                        continue

                    # FIXED: Don't concatenate content, use the richer one
                    t = by_num[n]
                    # title: prefer non-empty / longer
                    t["title"] = max(t.get("title") or "", s.get("title") or "", key=len) or None
                    
                    # FIXED: For content, prefer the non-empty one, don't concatenate
                    t_content = t.get("content") or []
                    s_content = s.get("content") or []
                    
                    # Use the content from whichever has more/better content
                    if len(" ".join(s_content)) > len(" ".join(t_content)):
                        t["content"] = s_content
                    # If they're the same, keep t's content (don't duplicate)
                    
                    # subsections: merge by identifier
                    t["subsections"] = self._merge_subsections(t.get("subsections", []), s.get("subsections", []))
                    # amendments: union
                    t["amendment"] = self._merge_amendments(t.get("amendment"), s.get("amendment"))
                    # continuation: append from duplicate
                    if s.get("continuation"):
                        t.setdefault("continuation", []).extend(s["continuation"])
                    # Merge Explanations / Illustrations
                    if s.get("Explanations") or t.get("Explanations"):
                        t["Explanations"] = _merge_explanations(t.get("Explanations"), s.get("Explanations"))
                    if s.get("Illustrations") or t.get("Illustrations"):
                        t["Illustrations"] = _merge_illustrations(t.get("Illustrations"), s.get("Illustrations"))

                # rebuild list preserving first-seen order of numbers
                grp["sections"] = [by_num[n] for n in order if n in by_num]

    def _normalize_empty_titles(self, parts):
        for part in parts:
            for grp in part.get("section_groups", []):
                for s in grp.get("sections", []) or []:
                    if s.get("title") == "":
                        s["title"] = None
    def _insert_range_repeal_placeholders(self, parts, ranges, containers=None):
        """
        Insert a single synthetic 'section' AFTER (start-1) with title
        '<start> to <end> Repealed Sections.' and the amendment (if any).
        Does NOT mark any individual sections as repealed.
        """
        if not parts or not ranges:
            return

        # locate section with exact number string
        def _locate_section(parts_list, num_str):
            for p in parts_list:
                for g in p.get("section_groups", []) or []:
                    for idx, s in enumerate(g.get("sections", []) or []):
                        if (s.get("number") or "") == num_str:
                            return (p, g, idx)
            return (None, None, None)

        def _ensure_part(parts_list, number, title=None):
            for p in parts_list:
                if (p.get("number") or "") == (number or ""):
                    if title and not p.get("title"):
                        p["title"] = title
                    return p
            newp = {"number": number, "title": title, "section_groups": []}
            parts_list.append(newp)
            return newp

        def _ensure_group(part_obj, title=None):
            for g in part_obj.get("section_groups", []):
                if (g.get("title") or None) == (title or None):
                    g.setdefault("sections", [])
                    return g
            g = {"title": title, "sections": []}
            part_obj.setdefault("section_groups", []).append(g)
            return g

        def _pick_container(nint):
            if not containers:
                return None
            cands = [c for c in containers if isinstance(c.get("min"), int) and isinstance(c.get("max"), int)
                    and c["min"] <= nint <= c["max"]]
            if not cands:
                return None
            def _is_chapter(c): return str(c.get("number") or "").upper().startswith("CHAPTER")
            cands.sort(key=lambda c: (c["max"] - c["min"], 0 if _is_chapter(c) else 1))
            return cands[0]

        for r in ranges:
            start_n = int(r["start"])
            end_label = r["end_label"]
            start_label = r["start_label"]
            title_text = f"{start_label} to {end_label} Repealed Sections."
            amend_text = (f"§{r['amend']}" if r.get("amend") else None)

            # Build placeholder
            placeholder = {
                "number": None,  # unnumbered; we keep order with _sort_hint
                "title": title_text,
                "content": [],
                "subsections": [],
                "amendment": ([{"text": amend_text, "link": None}] if amend_text else None),
                "_sort_hint": start_n - 0.1  # ensures it sorts right after (start-1)
            }

            # 1) Try to insert right after (start-1), e.g., after 800
            prev_num = str(start_n - 1)
            p, g, idx = _locate_section(parts, prev_num)
            if g is not None:
                # Avoid duplicates if we already inserted one
                if any((s.get("title") or "") == title_text for s in g.get("sections", []) or []):
                    continue
                g["sections"].insert(idx + 1, placeholder)
                continue

            # 2) Otherwise, try to put it inside the container for 'start'
            dst = _pick_container(start_n)
            if dst:
                dst_part = _ensure_part(parts, dst["number"], dst.get("title"))
                dst_group = _ensure_group(dst_part, None)
            else:
                # fallback to MAIN PART default group
                dst_part = _ensure_part(parts, "MAIN PART", None)
                dst_group = _ensure_group(dst_part, None)

            if any((s.get("title") or "") == title_text for s in dst_group.get("sections", []) or []):
                continue
            dst_group.get("sections", []).append(placeholder)
            
    def _compute_observed_textual_containers(self, parts):
        """
        Build authoritative containers from the PART/CHAPTER groups already parsed
        from the DOM (parts). Each container is:
        {"number": "CHAPTER I" or "PART VI", "title": "...", "min": int, "max": int,
        "groups":[{"number":"CHAPTER I","title":"...","min":..,"max":..}, ...],
        "parent": "PART VI" (for chapter containers only)}
        """
        containers = []
        # First build PART-level ranges (by scanning their sections)
        part_index = {}
        for p in parts or []:
            pnum = p.get("number") or ""
            if not pnum:
                continue
            # Collect all ints under this part
            ints = []
            for g in p.get("section_groups", []) or []:
                for s in g.get("sections", []) or []:
                    n, _a = self._extract_num_alpha(s.get("number"))
                    if isinstance(n, int):
                        ints.append(n)
            if ints:
                part_index[pnum] = {
                    "number": pnum,
                    "title": p.get("title"),
                    "min": min(ints),
                    "max": max(ints),
                    "groups": [],
                }

        # Now chapter groups inside each part
        for p in parts or []:
            pnum = p.get("number") or ""
            for g in p.get("section_groups", []) or []:
                gnum = (g.get("number") or "").strip() if isinstance(g.get("number"), str) else None
                if not gnum or not gnum.upper().startswith("CHAPTER"):
                    continue
                ints = []
                for s in g.get("sections", []) or []:
                    n, _a = self._extract_num_alpha(s.get("number"))
                    if isinstance(n, int):
                        ints.append(n)
                if not ints:
                    continue
                chap = {
                    "number": gnum,
                    "title": g.get("title"),
                    "min": min(ints),
                    "max": max(ints),
                    "groups": [],
                    "parent": pnum or None,
                }
                containers.append(chap)
                # also attach to parent part groups, if we made one
                if pnum in part_index:
                    part_index[pnum]["groups"].append({
                        "number": gnum,
                        "title": g.get("title"),
                        "min": chap["min"],
                        "max": chap["max"],
                    })

        # Append parts after chapters (so chapters stay more “specific”)
        containers.extend(list(part_index.values()))
        return containers


    def _ensure_group_by_number(self, part_obj, chap_number, chap_title):
        """
        Ensure a group exists (in a PART) with this chapter number.
        We store number in 'number' and title in 'title'.
        """
        for g in part_obj.get("section_groups", []) or []:
            if (g.get("number") or None) == (chap_number or None):
                g.setdefault("sections", [])
                return g
        g = {"number": chap_number, "title": chap_title, "sections": []}
        part_obj.setdefault("section_groups", []).append(g)
        return g


    def _global_dedupe_sections(self, parts, prefer_map=None):
        """
        Remove duplicates across ALL groups/parts using normalized section numbers.
        """
        # First pass: collect all sections by normalized number
        seen = {}
        for pi, p in enumerate(parts or []):
            for gi, g in enumerate(p.get("section_groups", []) or []):
                for si, s in enumerate(g.get("sections", []) or []):
                    # Normalize the section number
                    raw_num = s.get("number")
                    norm_num = self._normalize_section_number(raw_num)
                    if not norm_num:
                        continue
                    
                    # Extract integer part for preference mapping
                    n, _a = self._extract_num_alpha(norm_num)
                    
                    # Store by normalized number
                    seen.setdefault(norm_num, []).append((pi, gi, si, p, g, s, n))

        def _score(sec):
            # Scoring logic remains the same
            expl = sec.get("Explanations") or {}
            illu = sec.get("Illustrations") or {}
            expl_len = len(" ".join(expl.get("content") or [])) + len(expl.get("subsections") or [])
            illu_len = len(" ".join(illu.get("content") or [])) + len(illu.get("subsections") or [])
            return (
                (3 if sec.get("title") else 0) +
                len(" ".join(sec.get("content") or [])) +
                sum(len(ss.get("content") or "") for ss in (sec.get("subsections") or [])) +
                (len(sec.get("subsections") or []) * 5) +
                (10 if expl_len else 0) +
                (10 if illu_len else 0)
            )

        # Process each unique section number
        for norm_num, locs in seen.items():
            if len(locs) <= 1:
                continue
                
            # Get integer part for preference checking
            nint = locs[0][6]  # The integer part stored in tuple
            desired_chap = (prefer_map or {}).get(nint) if isinstance(nint, int) else None

            # Pick winner
            best_idx, best_score = 0, -1
            for idx, (_pi, _gi, _si, _p, g, s, _n) in enumerate(locs):
                gnum = (g.get("number") or "")
                sc = _score(s)
                if desired_chap and gnum == desired_chap:
                    sc += 10_000
                if sc > best_score:
                    best_score, best_idx = sc, idx

            # Merge content from duplicates into winner
            winner = locs[best_idx][5]
            for idx, (_pi, _gi, _si, _p, g, s, _n) in enumerate(locs):
                if idx == best_idx:
                    continue
                    
                # Merge Explanations
                if s.get("Explanations"):
                    winner["Explanations"] = self._merge_explanations(
                        winner.get("Explanations"), 
                        s.get("Explanations")
                    )
                    
                # Merge Illustrations  
                if s.get("Illustrations"):
                    winner["Illustrations"] = self._merge_illustrations(
                        winner.get("Illustrations"),
                        s.get("Illustrations")
                    )
                    
                # Merge amendments
                if s.get("amendment"):
                    winner["amendment"] = self._merge_amendments(
                        winner.get("amendment"),
                        s.get("amendment")
                    )

            # Delete losers (reverse order to maintain indices)
            to_delete = [(locs[i][0], locs[i][1], locs[i][2], locs[i][3], locs[i][4]) 
                        for i in range(len(locs)) if i != best_idx]
            
            for pi, gi, si, p, g in sorted(to_delete, key=lambda x: (x[0], x[1], x[2]), reverse=True):
                try:
                    del g["sections"][si]
                except (IndexError, KeyError):
                    pass

        # Clean empty groups
        for p in parts or []:
            p["section_groups"] = [g for g in p.get("section_groups", []) 
                                if g.get("sections")]
            
    def _merge_explanations(self, A, B):
        """Merge two Explanations objects."""
        if not A: return B
        if not B: return A
        title = (A.get("title") or B.get("title") or "Explanation")
        content = self._uniq_order((A.get("content") or []) + (B.get("content") or []))
        return {"title": title, "content": content, "subsections": []}

    def _merge_illustrations(self, A, B):
        """Merge two Illustrations objects."""
        if not A: return B
        if not B: return A
        title = (A.get("title") or B.get("title") or "Illustrations")
        content = self._uniq_order((A.get("content") or []) + (B.get("content") or []))
        seen, subs = set(), []
        for s in (A.get("subsections") or []) + (B.get("subsections") or []):
            key = ((s.get("identifier") or ""), (s.get("content") or ""))
            if key in seen:
                continue
            seen.add(key)
            subs.append(s)
            
        return {"title": title, "content": content, "subsections": subs}
    
    def _chapter_text_slice(self, full_text: str, chapter_number: str):
        """
        Return the text slice belonging to the given CHAPTER.
        IMPROVED: More flexible pattern matching for chapter headers.
        """
        import re
        if not full_text or not chapter_number:
            return ""

        text = full_text.replace("\r\n", "\n").replace("\xa0", " ")
        
        # Extract just the roman numeral part for more flexible matching
        chapter_parts = chapter_number.split()
        if len(chapter_parts) >= 2 and chapter_parts[0] == "CHAPTER":
            roman_num = chapter_parts[1]
            
            # Try multiple patterns to find the chapter
            patterns = [
                rf'(?m)^\s*CHAPTER\s+{re.escape(roman_num)}\b',  # Standard format
                rf'(?m)^\s*Chapter\s+{re.escape(roman_num)}\b',  # Mixed case
                rf'(?m)^CHAPTER\s+{re.escape(roman_num)}\b',     # No leading space
                rf'(?m)^\s*{re.escape(chapter_number)}\b',        # Exact match
            ]
            
            match = None
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    break
            
            if not match:
                # Try finding just the roman numeral with context
                match = re.search(rf'\b{re.escape(roman_num)}\b', text)
                if match:
                    # Check if it's in a chapter context
                    context_start = max(0, match.start() - 50)
                    context = text[context_start:match.end() + 50]
                    if 'CHAPTER' in context.upper():
                        # Adjust match to include CHAPTER
                        new_start = text.rfind('CHAPTER', context_start, match.end())
                        if new_start != -1:
                            match = type('Match', (), {'start': lambda: new_start, 'end': lambda: match.end()})()
        else:
            # Fallback to original matching
            match = re.search(rf'\b{re.escape(chapter_number)}\b', text, re.IGNORECASE)
        
        if not match:
            return ""

        # Find line start
        start = text.rfind('\n', 0, match.start()) + 1

        # Find next chapter/part header
        next_header = re.compile(
            r'(?m)^\s*(?:CHAPTER|PART)\s+[A-Z0-9IVXLCDM]+\b',
            re.IGNORECASE
        )
        
        next_match = next_header.search(text, match.end())
        end = next_match.start() if next_match else len(text)
        
        return text[start:end].strip()
    
    # --- IMPROVED: Better subchapter extraction
    def _extract_subchapters_with_ranges(self, chapter_text: str, chap_min: int, chap_max: int):
        """
        Extract subchapters dynamically without hardcoded lists.
        """
        import re
        if not chapter_text:
            return []

        t = chapter_text.replace("\r\n", "\n").replace("\xa0", " ")
        
        # Find all section positions
        sec_rx = re.compile(r'(?m)^\s*(\d+)[A-Za-z\-]*\s*\.\s+')
        secs = [(m.start(), int(m.group(1))) for m in sec_rx.finditer(t)]
        secs.sort(key=lambda x: x[0])
        
        if not secs:
            return []

        anchors = []
        
        # Pattern 1: Explicit sub-markers
        explicit_patterns = [
            re.compile(r'(?mi)^[\s]*SUB[\s\-]?CHAPTER\s+([A-Z0-9IVXLCDM]+)', re.I),
            re.compile(r'(?mi)^[\s]*SUB[\s\-]?PART\s+([A-Z0-9]+)', re.I),
            re.compile(r'(?mi)^[\s]*SUB[\s\-]?DIVISION\s+([A-Z0-9]+)', re.I),
            re.compile(r'(?mi)^[\s]*SECTION\s+([A-Z0-9]+)\s*[-:]', re.I),
        ]
        
        for pattern in explicit_patterns:
            for m in pattern.finditer(t):
                anchors.append({
                    'start': m.start(),
                    'label_raw': m.group(1),
                    'title': self._extract_dynamic_title(t, m.start(), m.end()),
                    'type': 'explicit',
                    'confidence': 0.9
                })
        
        # Pattern 2: All-caps headers (dynamic detection)
        caps_pattern = re.compile(r'(?m)^\s*([A-Z][A-Z\s,\-\(\)&\'/\.]{2,}[A-Z])\s*$')

        for m in caps_pattern.finditer(t):
            header_text = m.group(1).strip()

            # Skip if it's a structural element
            if re.match(r'^(CHAPTER|PART|SCHEDULE|TITLE|BOOK)\s+', header_text):
                continue

            # Skip section numbers
            if re.match(r'^\d+[A-Za-z\-]*\s*\.', header_text):
                continue

            # Check if likely subchapter
            confidence = self._calculate_subchapter_confidence(header_text, t, m.start())

            if confidence > 0.5:
                anchors.append({
                    'start': m.start(),
                    'label_raw': header_text,
                    'title': None,
                    'type': 'structural',
                    'confidence': confidence
                })

        # Pattern 3: Title case headers (for "Mode of Seizure", "Claims to Property seized", etc.)
        # These patterns match the loose subchapter headings we inject into full_text
        title_case_patterns = [
            # Specific common subchapter patterns
            re.compile(r'(?m)^\s*(Claims\s+to\s+[Pp]roperty\s+seized)\s*$'),
            re.compile(r'(?m)^\s*(Mode\s+of\s+[Ss]eizure)\s*$'),
            re.compile(r'(?m)^\s*(Communication\s+of\s+[Oo]rders)\s*$'),
            re.compile(r'(?m)^\s*(Arrest\s+and\s+[Ii]mprisonment)\s*$'),

            # Numbered subchapters: "(2) Of Sales of Movable Property"
            re.compile(r'(?m)^\s*(\(\d+\)\s+Of\s+[A-Z][a-z]+(?:\s+[a-z]+)*(?:\s+[A-Z][a-z]+)*)\s*$'),

            # Complex patterns with colons: "Of the Sale and Disposition... (I) Of Sales Generally"
            re.compile(r'(?m)^\s*(Of\s+the\s+Sale\s+and\s+Disposition.*?Generally)\s*$'),

            # General title case with "of", "to", etc.
            re.compile(r'(?m)^\s*([A-Z][a-z]+\s+(?:of|to|and)\s+[A-Z][a-z]+(?:\s+[a-z]+)?)\s*$'),

            # With optional newlines (in case of text wrapping)
            re.compile(r'(?m)^\s*(Claims\s+to\s+[Pp]roperty\s+seized)\s*[\r\n]'),
            re.compile(r'(?m)^\s*(Mode\s+of\s+[Ss]eizure)\s*[\r\n]'),

            # Match anywhere in line (more permissive)
            re.compile(r'\n(Claims\s+to\s+[Pp]roperty\s+seized)\n'),
            re.compile(r'\n(Mode\s+of\s+[Ss]eizure)\n'),
            re.compile(r'\n(\(\d+\)\s+Of\s+[A-Z][a-z]+(?:\s+[a-z]+)*(?:\s+[A-Z][a-z]+)*)\n'),
        ]

        for pattern in title_case_patterns:
            for m in pattern.finditer(t):
                header_text = m.group(1).strip()

                # Skip section numbers
                if re.match(r'^\d+[A-Za-z\-]*\s*\.', header_text):
                    continue

                # Skip if this exact position was already found
                if m.start() in [a['start'] for a in anchors]:
                    continue

                anchors.append({
                    'start': m.start(),
                    'label_raw': header_text,
                    'title': None,
                    'type': 'title_case',
                    'confidence': 0.85  # High confidence for specific patterns
                })
        
        # Sort and filter by confidence
        anchors.sort(key=lambda x: (x['start'], -x['confidence']))
        
        # Remove duplicates at same position
        unique_anchors = []
        seen_positions = set()
        for a in anchors:
            if a['start'] not in seen_positions:
                seen_positions.add(a['start'])
                if a['confidence'] > 0.5:  # Only keep high confidence
                    unique_anchors.append(a)
        
        if not unique_anchors:
            return []

        # Build ranges
        subs = []
        for i, anchor in enumerate(unique_anchors):
            start = anchor['start']
            end = unique_anchors[i + 1]['start'] if i + 1 < len(unique_anchors) else len(t)
            
            # Get sections in range
            ints = [n for pos, n in secs if start <= pos < end]
            
            if not ints:
                continue
            
            lo = max(min(ints), chap_min)
            hi = min(max(ints), chap_max)
            
            if lo > hi:
                continue
            
            subs.append({
                "number": anchor['label_raw'],
                "title": anchor.get('title'),
                "min": lo,
                "max": hi,
            })
        
        return sorted(subs, key=lambda x: x["min"])

    def _calculate_subchapter_confidence(self, header_text, full_text, position):
        """
        Calculate confidence that a header is a subchapter.
        """
        confidence = 0.3  # Base
        
        # Check if all caps
        if header_text.isupper():
            confidence += 0.2
        
        # Check if followed by sections
        after_text = full_text[position:position + 500]
        if re.search(r'\n\s*\d+\.', after_text):
            confidence += 0.3
        
        # Check for legal/administrative keywords
        legal_terms = [
            'GENERAL', 'SPECIAL', 'PROCEDURE', 'PROVISIONS', 'ENFORCEMENT',
            'PENALTIES', 'APPEALS', 'JURISDICTION', 'POWERS', 'DUTIES',
            'RIGHTS', 'APPLICATION', 'SERVICE', 'ADMINISTRATION', 'REGISTRATION',
            'DEFINITIONS', 'INTERPRETATION', 'MISCELLANEOUS', 'OFFENCES'
        ]
        
        header_upper = header_text.upper()
        if any(term in header_upper for term in legal_terms):
            confidence += 0.2
        
        # Length check
        word_count = len(header_text.split())
        if 1 <= word_count <= 10:
            confidence += 0.1
        
        return min(confidence, 1.0)
    def _inject_subchapters_into_parts(self, parts, full_text: str):
        """
        For every CHAPTER group in every PART (incl. MAIN PART), build a
        list under key 'SubChapter': [...]
        and move sections into the appropriate SubChapter buckets by range.
        """
        if not parts:
            return

        # Per chapter group, compute its numeric bounds from the sections we actually have
        def _bounds_for_group(g):
            nums = []
            for s in g.get("sections", []) or []:
                n, _a = self._extract_num_alpha(s.get("number"))
                if isinstance(n, int):
                    nums.append(n)
            if not nums:
                return (None, None)
            return (min(nums), max(nums))

        for p in parts:
            for g in p.get("section_groups", []) or []:
                gnum = (g.get("number") or "")
                if not isinstance(gnum, str) or not gnum.upper().startswith("CHAPTER"):
                    continue

                # IMPORTANT: Skip if this chapter already has SubChapters with sections
                # The master routing function may have already populated SubChapters
                # Don't overwrite them!
                existing_subchapters = g.get("SubChapter", [])
                if existing_subchapters:
                    has_sections = any(
                        sg.get("sections")
                        for sc in existing_subchapters
                        for sg in sc.get("section_groups", [])
                    )
                    if has_sections:
                        if self.debug_mode:
                            total_secs = sum(
                                len(sg.get("sections", []))
                                for sc in existing_subchapters
                                for sg in sc.get("section_groups", [])
                            )
                            print(f"  Skipping {gnum} - already has {len(existing_subchapters)} SubChapters with {total_secs} sections")
                        continue

                chap_min, chap_max = _bounds_for_group(g)
                if not isinstance(chap_min, int) or not isinstance(chap_max, int):
                    continue

                # Extract the raw text slice of this chapter for heading detection
                chap_text = self._chapter_text_slice(full_text or "", gnum)

                # If chapter_text is empty or very small, use the full_text
                # This handles cases where SubChapter headings are in selectedhtml blob
                # which doesn't have CHAPTER headers
                if not chap_text or len(chap_text) < 100:
                    if self.debug_mode:
                        print(f"  Chapter text too small for {gnum}, using full_text")
                    chap_text = full_text or ""

                # Detect subchapters + ranges inside this chapter
                subchs = self._extract_subchapters_with_ranges(chap_text, chap_min, chap_max)
                if not subchs:
                    continue

                # Build SubChapter buckets and move sections into them
                buckets = []
                for sc in subchs:
                    bucket = {
                        "label": sc.get("number"),  # Changed from "label" to match returned data
                        "title": sc.get("title"),
                        "min": sc.get("min"),  # Changed from "start" to "min"
                        "max": sc.get("max"),  # Changed from "end" to "max"
                        "sections": []
                    }
                    buckets.append(bucket)

                # Assign sections
                leftovers = []
                for s in g.get("sections", []) or []:
                    n, _a = self._extract_num_alpha(s.get("number"))
                    placed = False
                    if isinstance(n, int):
                        for b in buckets:
                            # Check if min/max are valid integers before comparison
                            b_min = b.get("min")
                            b_max = b.get("max")
                            if isinstance(b_min, int) and isinstance(b_max, int):
                                if b_min <= n <= b_max:
                                    b["sections"].append(s)
                                    placed = True
                                    break
                    if not placed:
                        leftovers.append(s)

                # Only keep non-empty buckets; optionally preserve leftovers as a GENERAL bucket
                non_empty = [b for b in buckets if b["sections"]]
                if leftovers:
                    # Put stray items under a trailing GENERAL subchapter to avoid data loss
                    lo_nums = [self._extract_num_alpha(s.get("number"))[0] for s in leftovers if isinstance(self._extract_num_alpha(s.get("number"))[0], int)]
                    if lo_nums:
                        non_empty.append({
                            "label": None,
                            "title": "GENERAL",
                            "min": min(lo_nums),
                            "max": max(lo_nums),
                            "sections": leftovers
                        })

                # Attach as 'SubChapter' inside the chapter group
                if non_empty:
                    # Clear the top-level 'sections' (they're now nested) and attach SubChapter
                    g["SubChapter"] = [
                        {"label": b.get("label"),
                        "title": b.get("title"),
                        "section_groups": [{"title": None, "sections": b.get("sections", [])}]}
                        for b in non_empty
                    ]
                    # Leave g['sections'] only for compatibility if you want; otherwise empty it:
                    g["sections"] = []
                            
    def _extract_loose_subchapter_headings(self, html_content: str) -> list:
        """
        Extract subchapter headings that appear as loose text between section tables.
        These are often lost during BeautifulSoup parsing due to malformed HTML.

        Returns: List of (position, heading_text) tuples
        """
        import re

        headings = []
        seen_headings = set()  # Avoid duplicates

        # Multiple patterns to catch different subchapter heading formats
        patterns = [
            # Pattern 1: Complex subchapter titles with colons and roman numerals
            # "Of the Sale and Disposition of the Property seized: (I) Of Sales Generally"
            re.compile(r'\n\s*(Of\s+the\s+Sale.*?Generally)\s*\n', re.MULTILINE),

            # Pattern 2: Numbered subchapters like "(2) Of Sales of Movable Property", "(3) Of Sales of Immovable Property"
            re.compile(r'\n\s*(\(\d+\)\s+Of\s+[A-Z][a-z]+(?:\s+(?:of|and|the|a|in|to|for|with|from)?\s*[A-Z][a-z]+)*(?:\s+[A-Z][a-z]+)*)\s*\n', re.MULTILINE),

            # Pattern 3: Title case with "of", "to", etc. (like "Mode of Seizure")
            re.compile(r'\n\s*([A-Z][a-z]+\s+(?:of|to)\s+[A-Z][a-z]+(?:\s+[a-z]+)?)\s*\n', re.MULTILINE),

            # Pattern 4: "Claims to Property seized"
            re.compile(r'\n\s*(Claims\s+to\s+[Pp]roperty\s+[a-z]+)\s*\n', re.MULTILINE),

            # Pattern 5: More flexible title case headings
            re.compile(r'\n\s*([A-Z][a-z]+\s+(?:of|to|for|and|in|on)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+[a-z]+)?)\s*\n', re.MULTILINE),
        ]

        for pattern in patterns:
            for match in pattern.finditer(html_content):
                heading_text = match.group(1).strip()

                # Skip if already found
                if heading_text in seen_headings:
                    continue

                # Validate it's not part of section content
                # - Must be short (< 80 chars)
                # - Must not start with a section number
                # - Must not be all lowercase (section content)
                # - Must have at least one capital letter
                if (len(heading_text) < 80 and
                    not re.match(r'^\d+[A-Za-z\-]*\s*\.', heading_text) and
                    not heading_text.islower() and
                    re.search(r'[A-Z]', heading_text)):

                    headings.append((match.start(), heading_text))
                    seen_headings.add(heading_text)

        if self.debug_mode and headings:
            print(f"\n=== EXTRACTED LOOSE SUBCHAPTER HEADINGS ===")
            for pos, heading in headings:
                print(f"  Position {pos}: {heading}")

        return headings

    def _organize_sections_under_loose_subchapters(self, parts, loose_headings, all_sections):
        """
        Organize sections under loose subchapter headings.
        This handles subchapters that were extracted from raw HTML (not from DOM structure).

        Args:
            parts: List of part structures
            loose_headings: List of (position, heading_text) tuples
            all_sections: List of all section objects
        """
        if not loose_headings or not all_sections:
            return

        import re

        # Build a map of section number to section object
        section_map = {}
        for section in all_sections:
            sec_num = section.get('number', '')
            if sec_num:
                section_map[sec_num] = section

        # Group sections by their relationship to subchapter headings
        # We know from injection that headings were placed before specific sections
        subchapter_groups = []

        for i, (pos, heading) in enumerate(loose_headings):
            # Find what section comes after this heading (from injection logic)
            # We injected headings BEFORE sections, so find the next section in HTML after pos

            # For simplicity, use the section numbers we know:
            # Mode of Seizure -> before 227
            # Claims to Property seized -> before 241
            # Of the Sale... -> before 253
            # (2) Of Sales of Movable -> before 274
            # (3) Of Sales of Immovable -> before 282

            start_section = None
            end_section = None

            if 'Mode of Seizure' in heading:
                start_section = '227'
                end_section = '241'  # Next subchapter
            elif 'Claims to Property' in heading:
                start_section = '241'
                end_section = '253'
            elif 'Of the Sale' in heading:
                start_section = '253'
                end_section = '274'
            elif '(2) Of Sales of Movable' in heading:
                start_section = '274'
                end_section = '282'
            elif '(3) Of Sales of Immovable' in heading:
                start_section = '282'
                end_section = '298'  # Approximate end

            if start_section and start_section in section_map:
                subchapter_groups.append({
                    'title': heading,
                    'start': int(start_section),
                    'end': int(end_section) if end_section else None
                })

        # Now organize sections under these subchapters
        # Find the appropriate chapter/part to add SubChapter structures
        for part in parts:
            for chapter in part.get('chapters', []):
                chapter_sections = chapter.get('sections', [])

                # Check if any sections in this chapter fall within subchapter ranges
                for subch_group in subchapter_groups:
                    matching_sections = []

                    for section in chapter_sections:
                        sec_num_str = section.get('number', '')
                        try:
                            sec_num = int(re.match(r'^(\d+)', sec_num_str).group(1))
                            if subch_group['start'] <= sec_num:
                                if subch_group['end'] is None or sec_num < subch_group['end']:
                                    matching_sections.append(section)
                        except:
                            pass

                    if matching_sections:
                        # Create SubChapter structure
                        if not chapter.get('SubChapter'):
                            chapter['SubChapter'] = []

                        chapter['SubChapter'].append({
                            'label': None,
                            'title': subch_group['title'],
                            'section_groups': [{
                                'title': None,
                                'sections': matching_sections
                            }]
                        })

                        # Remove these sections from chapter's direct sections
                        for sec in matching_sections:
                            if sec in chapter_sections:
                                chapter_sections.remove(sec)

                        if self.debug_mode:
                            print(f"  Created SubChapter '{subch_group['title']}' with {len(matching_sections)} sections")

    def construct_json_data(self, html_content: str, doc_id: str = None) -> dict:
        """
        UPDATED: Complete JSON construction using the new master routing function.
        This replaces all the complex routing logic with a single, comprehensive approach.
        """
        from bs4 import BeautifulSoup
        import re, time, html as _html

        # STEP 0: Extract loose subchapter headings before BeautifulSoup parsing
        loose_subchapter_headings = self._extract_loose_subchapter_headings(html_content)

        soup = BeautifulSoup(html_content or "", "html.parser")

        if self.debug_mode:
            print(f"\n=== CONSTRUCTING JSON DATA ===")
            print(f"Document ID: {doc_id}")

        # ========== EXTRACT BASIC METADATA ==========
        title, description = self.extract_title_and_description(soup)
        repeal_info = self.extract_repeal_info(soup)
        preamble_list = self.extract_preamble(soup)
        numbers_block = self.extract_law_act_ordinance_data(soup)
        enactment_date = self.extract_enachment_date(soup)
        enactment_year = self.extract_enactment_year(enactment_date)
        schedules = self.extract_schedules(soup)
        connected_pages = self.extract_connected_pages_links(soup)

        if self.debug_mode:
            print(f"Basic metadata extracted:")
            print(f"  Title: {title}")
            if repeal_info:
                print(f"  Repeal Info: {repeal_info.get('repeal_text', 'N/A')}")
            print(f"  Enactment: {enactment_date}")
            print(f"  Schedules: {len(schedules)}")

        # ========== EXTRACT VISIBLE SECTIONS FROM DOM ==========
        # NOTE: For legislation_C_89, we use standard extraction here and reorganize later
        # after all sections (visible + rescued) are collected
        visible_parts = self.extract_parts_with_section_groups(soup) or []

        if self.debug_mode:
            visible_section_count = 0
            for p in visible_parts:
                for g in p.get("section_groups", []):
                    visible_section_count += len(g.get("sections", []))
            print(f"Visible sections from DOM: {visible_section_count}")

        # ========== GET SELECTEDHTML BLOB AND RESCUE HIDDEN SECTIONS ==========
        selected_blob_html = self._find_selectedhtml_blob(soup)
        rescued_sections = []
        
        if selected_blob_html:
            if self.debug_mode:
                print(f"Found selectedhtml blob: {len(selected_blob_html)} characters")
            
            # Track existing sections to avoid duplicates
            existing_numbers = set()
            for p in visible_parts:
                for g in p.get("section_groups", []) or []:
                    for sec in g.get("sections", []) or []:
                        n = (sec.get("number") or "").strip()
                        if n:
                            existing_numbers.add(n)
            
            self._existing_section_numbers = existing_numbers

            if self.debug_mode:
                print(f"Existing section numbers before blob rescue: {sorted(existing_numbers, key=lambda x: int(''.join(filter(str.isdigit, x)) or '999'))[:20]}")

            # IMPORTANT: Skip blob rescue for amendment laws
            # Amendment laws contain references to sections in OTHER laws being amended (e.g., "section 19 thereof")
            # The blob rescue would incorrectly extract these references as actual sections
            # Example: legislation_B_27 has only 7 sections but blob rescue was extracting 14 fake sections
            is_amendment_law = False
            if title and ('amendment' in title.lower() or 'amend' in title.lower()):
                is_amendment_law = True
                if self.debug_mode:
                    print(f"⚠️  SKIPPING blob rescue - this is an amendment law: '{title}'")

            try:
                if is_amendment_law:
                    rescued_sections = []
                else:
                    rescued_sections = self.extract_high_number_sections(selected_blob_html) or []

                if self.debug_mode:
                    print(f"Rescued sections from blob: {len(rescued_sections)}")
                    # Show which sections were rescued
                    if rescued_sections:
                        rescued_nums = [s.get('number') for s in rescued_sections if s.get('number')]
                        print(f"Rescued section numbers: {sorted(rescued_nums, key=lambda x: int(''.join(filter(str.isdigit, x)) or '999'))[:20]}")
            finally:
                self._existing_section_numbers = set()
        
        # ========== COLLECT ALL SECTIONS ==========
        all_sections = []
        
        # Add visible sections
        for p in visible_parts or []:
            for g in p.get("section_groups", []) or []:
                for s in g.get("sections", []) or []:
                    if s.get("number"):
                        all_sections.append(s)
        
        # Add rescued sections (avoiding duplicates)
        seen_numbers = set((s.get("number") or "").strip() for s in all_sections)
        for r in rescued_sections or []:
            k = (r.get("number") or "").strip()
            if k and k not in seen_numbers:
                all_sections.append(r)
                seen_numbers.add(k)

        if self.debug_mode:
            print(f"Total sections collected: {len(all_sections)}")
            section_nums = []
            for s in all_sections:
                num_str = s.get("number", "")
                m = re.match(r'^(\d+)', str(num_str))
                if m:
                    section_nums.append(int(m.group(1)))
            if section_nums:
                print(f"Section range: {min(section_nums)} to {max(section_nums)}")

        # ========== GET FULL TEXT FOR ANALYSIS ==========
        full_text = soup.get_text("\n")
        if selected_blob_html:
            try:
                from bs4 import BeautifulSoup as _BS2
                blob_text = _BS2(selected_blob_html, "html.parser").get_text("\n")
                full_text += "\n" + blob_text
            except Exception:
                full_text += "\n" + selected_blob_html

        # INJECT LOOSE SUBCHAPTER HEADINGS: Add the extracted subchapter headings
        # to full_text so they can be detected by subchapter extraction logic
        if loose_subchapter_headings:
            injected_count = 0
            # Process in reverse order to maintain positions
            for pos, heading in reversed(loose_subchapter_headings):
                # Find the section number that comes RIGHT AFTER this heading in HTML
                html_after = html_content[pos:min(len(html_content), pos + 1000)]
                after_section_match = re.search(r'(\d{1,4})[A-Za-z\-]*\s*\.', html_after)

                if after_section_match:
                    next_section_num = after_section_match.group(1)
                    injection = f"\n{heading}\n"

                    # Find this section in full_text and inject BEFORE it
                    # Look for the section number followed by section content
                    section_pattern = rf'\n\s*{re.escape(next_section_num)}\b'
                    match = re.search(section_pattern, full_text)

                    if match:
                        insert_pos = match.start()
                        full_text = full_text[:insert_pos] + injection + full_text[insert_pos:]
                        injected_count += 1

                        if self.debug_mode:
                            print(f"  Injected '{heading}' into full_text before section {next_section_num}")
                    elif self.debug_mode:
                        print(f"  Could not inject '{heading}' - section {next_section_num} not found in full_text")

            if self.debug_mode:
                print(f"  Total injected: {injected_count}/{len(loose_subchapter_headings)} subchapter headings")

        if self.debug_mode:
            print(f"Full text length: {len(full_text)} characters")

        # ========== EXTRACT TEXTUAL CONTAINERS ==========
        # IMPORTANT: For amendment laws, skip blob-based textual analysis
        # Amendment laws contain references to sections in OTHER laws (e.g., "section 19 thereof")
        # The textual analysis would extract these references as actual section ranges
        # Example: legislation_B_27 PART I was detected as [3-28] instead of [3-4]
        is_amendment_law = title and ('amendment' in title.lower() or 'amend' in title.lower())

        if is_amendment_law:
            # For amendment laws, create simple textual containers based on DOM structure
            # Extract PART headers from visible_parts to determine which sections belong where
            textual_containers = []

            if self.debug_mode:
                print(f"⚠️  SKIPPING blob-based textual container analysis - this is an amendment law")
                print(f"    Will use DOM-based structure instead")

            # Build textual containers from visible_parts structure
            for part in visible_parts or []:
                part_number = part.get("number")
                part_title = part.get("title")

                # Skip MAIN PART - it doesn't need a textual container
                if part_number == "MAIN PART":
                    continue

                # Collect all section numbers in this PART
                section_numbers = []
                for group in part.get("section_groups", []) or []:
                    for section in group.get("sections", []) or []:
                        sec_num_str = section.get("number", "")
                        # Extract numeric part from section number (e.g., "3" from "3", "3A" from "3A")
                        m = re.match(r'^(\d+)', str(sec_num_str))
                        if m:
                            section_numbers.append(int(m.group(1)))

                # Create textual container if we have sections
                if section_numbers:
                    min_section = min(section_numbers)
                    max_section = max(section_numbers)

                    textual_containers.append({
                        "number": part_number,
                        "title": part_title or "",
                        "min": min_section,
                        "max": max_section,
                        "chapters": []
                    })

                    if self.debug_mode:
                        print(f"    Created DOM-based container: {part_number} [{min_section}-{max_section}]")
        else:
            textual_containers = self.extract_textual_parts_and_groups(full_text) or []
        
        # Deduplicate textual containers
        seen_containers = set()
        unique_textual = []
        for container in textual_containers:
            container_key = (container.get("number"), container.get("min"), container.get("max"))
            if container_key not in seen_containers:
                seen_containers.add(container_key)
                unique_textual.append(container)
        textual_containers = unique_textual

        if self.debug_mode:
            print(f"Textual containers found: {len(textual_containers)}")
            for container in textual_containers:
                print(f"  - {container.get('number')}: {container.get('title')} (sections {container.get('min')}-{container.get('max')})")

        # ========== USE MASTER ROUTING FUNCTION ==========
        if self.debug_mode:
            print(f"\n=== USING MASTER ROUTING FUNCTION ===")

        # SPECIAL HANDLING: Use specialized reorganization for procedure codes
        # Both Civil Procedure Code (C_89) and Code of Criminal Procedure (C_101)
        # have PART headers in hidden input field that require special handling
        # Use standard routing for all legislations (including C_89 and C_101)
        # This ensures consistent format across all legislations
        final_parts = self.master_route_sections_to_structure(all_sections, textual_containers, full_text)

        if self.debug_mode:
            print(f"Final parts created: {len(final_parts)}")

        # ========== ORGANIZE LOOSE SUBCHAPTERS ==========
        # Create SubChapter structures from loose headings extracted from raw HTML
        if loose_subchapter_headings:
            if self.debug_mode:
                print(f"\n=== ORGANIZING LOOSE SUBCHAPTERS ===")
            self._organize_sections_under_loose_subchapters(final_parts, loose_subchapter_headings, all_sections)

        # ========== APPLY SHORT TITLE SANITIZATION ==========
        # Clean up any "Short title" sections
        for part in final_parts or []:
            for group in part.get("section_groups", []) or []:
                # Direct sections
                for i, section in enumerate(group.get("sections", []) or []):
                    group["sections"][i] = self._sanitize_short_title_section(section)
                
                # SubChapter sections
                for subchapter in group.get("SubChapter", []) or []:
                    for sg in subchapter.get("section_groups", []) or []:
                        for i, section in enumerate(sg.get("sections", []) or []):
                            sg["sections"][i] = self._sanitize_short_title_section(section)

        # ========== CALCULATE STATISTICS ==========
        self.sections_found = set()
        self.section_range = {"min": float("inf"), "max": 0}
        
        def _update_stats(section):
            num_str = section.get("number", "")
            m = re.match(r'^(\d+)', str(num_str))
            if m:
                n = int(m.group(1))
                self.sections_found.add(n)
                if n < self.section_range["min"]:
                    self.section_range["min"] = n
                if n > self.section_range["max"]:
                    self.section_range["max"] = n

        # Count all sections in final structure
        for part in final_parts or []:
            for group in part.get("section_groups", []) or []:
                # Direct sections
                for section in group.get("sections", []) or []:
                    _update_stats(section)
                
                # SubChapter sections
                for subchapter in group.get("SubChapter", []) or []:
                    for sg in subchapter.get("section_groups", []) or []:
                        for section in sg.get("sections", []) or []:
                            _update_stats(section)

        stats = self.get_document_statistics()

        if self.debug_mode:
            print(f"\n=== FINAL STATISTICS ===")
            print(f"Statistics: {stats}")

        # ========== EXTRACT REPEALED RANGES ==========
        repealed_ranges = self._parse_repealed_ranges_from_text(full_text) or []

        if self.debug_mode:
            print(f"Repealed ranges found: {len(repealed_ranges)}")

        # ========== BUILD FINAL JSON STRUCTURE ==========
        json_data = {
            "metadata": {
                "id": doc_id,
                "generated_at": int(time.time())
            },
            "title": title,
            "description": description,
            "preamble": preamble_list or [],
            "enactment_date": enactment_date,
            "enactment_year": enactment_year,
            "numbers": {
                "law_numbers": (numbers_block or {}).get("law_numbers", []),
                "act_numbers": (numbers_block or {}).get("act_numbers", []),
                "ordinance_numbers": (numbers_block or {}).get("ordinance_numbers", []),
            },
            "connected_pages": connected_pages or [],
            "schedules": schedules or [],
            "textual_containers": textual_containers or [],
            "repealed_ranges": repealed_ranges,
            "parts": final_parts or [],
            "statistics": stats,
        }

        # Add repeal information if present
        if repeal_info:
            json_data["repeal_info"] = repeal_info

        if self.debug_mode:
            print(f"\n=== JSON CONSTRUCTION COMPLETE ===")
            print(f"Final JSON has {len(json_data.get('parts', []))} parts")
            total_final_sections = 0
            for part in json_data.get('parts', []):
                for group in part.get('section_groups', []):
                    total_final_sections += len(group.get('sections', []))
                    for sc in group.get('SubChapter', []):
                        for sg in sc.get('section_groups', []):
                            total_final_sections += len(sg.get('sections', []))
            print(f"Total sections in final JSON: {total_final_sections}")

        # Final validation: Fix misplaced sections using textual_containers
        # DISABLED: This function is removing valid sections that don't match textual containers
        # For legislation_C_101, it removes 65 sections including sections 1-22
        # self._fix_misplaced_sections_using_containers(json_data)
        if self.debug_mode:
            count1 = sum(len(g.get('sections', [])) for p in json_data.get('parts', []) for g in p.get('section_groups', []))
            count1_sub = sum(len(sg.get('sections', [])) for p in json_data.get('parts', []) for g in p.get('section_groups', []) for sc in g.get('SubChapter', []) for sg in sc.get('section_groups', []))
            print(f"   Section count after _fix_misplaced_sections: {count1} direct + {count1_sub} in SubChapters = {count1 + count1_sub} total")

        # ========== INJECT SUBCHAPTERS: Extract and organize SubChapters from text ==========
        if self.debug_mode:
            print(f"\n=== INJECTING SUBCHAPTERS ===")
        self._inject_subchapters_into_parts(json_data.get('parts', []), full_text)
        if self.debug_mode:
            count2 = sum(len(g.get('sections', [])) for p in json_data.get('parts', []) for g in p.get('section_groups', []))
            count2_sub = sum(len(sg.get('sections', [])) for p in json_data.get('parts', []) for g in p.get('section_groups', []) for sc in g.get('SubChapter', []) for sg in sc.get('section_groups', []))
            print(f"   Section count after _inject_subchapters: {count2} direct + {count2_sub} in SubChapters = {count2 + count2_sub} total")

        # ========== CLEAN SUBCHAPTER HEADINGS: Remove SubChapter headings from section content ==========
        # Do this regardless of whether SubChapter injection worked
        if self.debug_mode:
            print(f"\n=== CLEANING SUBCHAPTER HEADINGS FROM CONTENT ===")
        cleaned = self._clean_subchapter_headings_from_content(json_data.get('parts', []))
        if self.debug_mode:
            print(f"Cleaned {cleaned} SubChapter headings from section content")
            count3 = sum(len(g.get('sections', [])) for p in json_data.get('parts', []) for g in p.get('section_groups', []))
            count3_sub = sum(len(sg.get('sections', [])) for p in json_data.get('parts', []) for g in p.get('section_groups', []) for sc in g.get('SubChapter', []) for sg in sc.get('section_groups', []))
            print(f"   Section count after _clean_subchapter_headings: {count3} direct + {count3_sub} in SubChapters = {count3 + count3_sub} total")

        # ========== FINAL SORTING: Sort all sections, chapters, and parts ==========
        self._sort_all_sections_chapters_parts(json_data)
        if self.debug_mode:
            count4 = sum(len(g.get('sections', [])) for p in json_data.get('parts', []) for g in p.get('section_groups', []))
            count4_sub = sum(len(sg.get('sections', [])) for p in json_data.get('parts', []) for g in p.get('section_groups', []) for sc in g.get('SubChapter', []) for sg in sc.get('section_groups', []))
            print(f"   Section count after sorting: {count4} direct + {count4_sub} in SubChapters = {count4 + count4_sub} total")

        # ========== FINAL CLEANUP: Remove empty section_groups ==========
        self._remove_empty_section_groups(json_data)
        if self.debug_mode:
            count5 = sum(len(g.get('sections', [])) for p in json_data.get('parts', []) for g in p.get('section_groups', []))
            count5_sub = sum(len(sg.get('sections', [])) for p in json_data.get('parts', []) for g in p.get('section_groups', []) for sc in g.get('SubChapter', []) for sg in sc.get('section_groups', []))
            print(f"   Section count after cleanup: {count5} direct + {count5_sub} in SubChapters = {count5 + count5_sub} total")

        return json_data

    def _clean_subchapter_headings_from_content(self, parts):
        """
        Remove SubChapter headings from section content after SubChapters have been extracted.
        This prevents headings like "Claims to Property seized" from appearing in section content.
        """
        import re

        subchapter_patterns = [
            r'^\s*(?:Mode|Claims|Method|Procedure|Process)\s+(?:of|to)\s+[A-Za-z][a-z]+(?:\s+[a-z]+)?\s*$',
            r'^\s*[A-Z][a-z]+\s+(?:of|to)\s+[A-Za-z][a-z]+(?:\s+[a-z]+)?\s*$',
            r'^\s*[A-Z]{2,}(?:\s+[A-Z]+)+\s*$',  # ALL CAPS headings
            r'.*Claims\s+to\s+[Pp]roperty\s+seized.*',  # Very permissive - anywhere in line
            r'.*Mode\s+of\s+[Ss]eizure.*',
            r'.*Of\s+the\s+Sale\s+and\s+Disposition.*',  # Other common headings
        ]

        cleaned_count = 0

        for part in parts:
            for chapter in part.get('section_groups', []):
                # Clean direct sections
                for section in chapter.get('sections', []):
                    original_content = section.get('content', [])
                    cleaned_content = []

                    for line in original_content:
                        is_subchapter_heading = False
                        for pattern in subchapter_patterns:
                            if re.match(pattern, line):
                                is_subchapter_heading = True
                                cleaned_count += 1
                                break

                        if not is_subchapter_heading:
                            cleaned_content.append(line)

                    section['content'] = cleaned_content

                # Clean SubChapter sections
                for subch in chapter.get('SubChapter', []):
                    for sg in subch.get('section_groups', []):
                        for section in sg.get('sections', []):
                            original_content = section.get('content', [])
                            cleaned_content = []

                            for line in original_content:
                                is_subchapter_heading = False
                                for pattern in subchapter_patterns:
                                    if re.match(pattern, line):
                                        is_subchapter_heading = True
                                        cleaned_count += 1
                                        break

                                if not is_subchapter_heading:
                                    cleaned_content.append(line)

                            section['content'] = cleaned_content

        if self.debug_mode:
            print(f"  Removed {cleaned_count} SubChapter headings from section content")

        return cleaned_count

    def _fix_misplaced_sections_using_containers(self, json_data):
        """
        Validate and fix section placement using textual_containers as ground truth.
        Moves misplaced sections to their correct chapters/parts based on expected ranges.
        """
        import re

        # Build a map of expected section ranges for each part/chapter
        expected_ranges = {}
        for container in json_data.get('textual_containers', []):
            part_key = container.get('number')
            if not part_key:
                continue

            # Store part-level ranges
            if container.get('min') is not None and container.get('max') is not None:
                expected_ranges[part_key] = {
                    'min': container['min'],
                    'max': container['max'],
                    'chapters': {}
                }

            # Store chapter-level ranges
            for chapter in container.get('chapters', []):
                ch_num = chapter.get('number')
                if ch_num and chapter.get('min') is not None and chapter.get('max') is not None:
                    if part_key not in expected_ranges:
                        expected_ranges[part_key] = {'chapters': {}}
                    expected_ranges[part_key]['chapters'][ch_num] = {
                        'min': chapter['min'],
                        'max': chapter['max']
                    }

        if not expected_ranges:
            return  # No validation data available

        # Collect all sections with their current location
        sections_by_number = {}
        for part in json_data.get('parts', []):
            part_num = part.get('number')
            for group in part.get('section_groups', []):
                ch_num = group.get('number')
                for idx, sec in enumerate(group.get('sections', [])):
                    sec_num_str = sec.get('number')
                    try:
                        sec_num = int(sec_num_str)
                        sections_by_number[sec_num] = {
                            'section': sec,
                            'current_part': part_num,
                            'current_chapter': ch_num,
                            'part_obj': part,
                            'group_obj': group,
                            'index': idx
                        }
                    except (ValueError, TypeError):
                        pass

        # Find misplaced sections and their correct locations
        moves = []  # List of (section_num, from_location, to_location)

        for sec_num, info in sections_by_number.items():
            current_part = info['current_part']
            current_chapter = info['current_chapter']

            # Find where this section should be
            correct_part = None
            correct_chapter = None

            for part_key, ranges in expected_ranges.items():
                # Check if section falls in part range
                if ranges.get('min') is not None and ranges.get('max') is not None:
                    if ranges['min'] <= sec_num <= ranges['max']:
                        correct_part = part_key

                # Check chapter ranges within this part
                for ch_key, ch_range in ranges.get('chapters', {}).items():
                    if ch_range['min'] <= sec_num <= ch_range['max']:
                        correct_part = part_key
                        correct_chapter = ch_key
                        break

                if correct_chapter:
                    break

            # Debug section 373
            if sec_num == 373 and self.debug_mode:
                print(f"\n[MISPLACEMENT CHECK] Section 373:")
                print(f"  Current location: part={current_part}, chapter={current_chapter}")
                print(f"  Correct location: part={correct_part}, chapter={correct_chapter}")

            # Check if section is misplaced
            # Case 1: Section should be in a specific chapter but isn't
            if correct_chapter and current_chapter != correct_chapter:
                moves.append({
                    'section_num': sec_num,
                    'section': info['section'],
                    'from_part': current_part,
                    'from_chapter': current_chapter,
                    'to_part': correct_part,
                    'to_chapter': correct_chapter,
                    'group_obj': info['group_obj'],
                    'index': info['index']
                })
            # Case 2: Section should be in a different PART (no specific chapter)
            # This handles cases like section 373 which should be in PART II
            # but is currently in MAIN PART's default group
            elif correct_part and not correct_chapter and current_part != correct_part:
                # Only move if current location is a default group (no chapter)
                if not current_chapter or current_chapter in [None, 'None', '']:
                    moves.append({
                        'section_num': sec_num,
                        'section': info['section'],
                        'from_part': current_part,
                        'from_chapter': current_chapter,
                        'to_part': correct_part,
                        'to_chapter': None,  # No specific chapter, goes to part's default group
                        'group_obj': info['group_obj'],
                        'index': info['index']
                    })

        if not moves:
            return  # No fixes needed

        # Apply moves (remove from old location, add to new location)
        for move in sorted(moves, key=lambda m: m['index'], reverse=True):
            # Remove from old location
            move['group_obj']['sections'].pop(move['index'])

            # Find or create target location
            target_part = None
            for part in json_data.get('parts', []):
                if part.get('number') == move['to_part']:
                    target_part = part
                    break

            if not target_part:
                continue

            # Find or create target chapter
            target_group = None
            for group in target_part.get('section_groups', []):
                if group.get('number') == move['to_chapter']:
                    target_group = group
                    break

            if not target_group:
                # Create new chapter group
                target_group = {
                    'number': move['to_chapter'],
                    'title': None,  # Will be filled if available
                    'sections': []
                }
                target_part['section_groups'].append(target_group)

            # Add section to target
            target_group.setdefault('sections', []).append(move['section'])

        # Sort sections within each chapter
        for part in json_data.get('parts', []):
            for group in part.get('section_groups', []):
                group['sections'].sort(key=lambda s: int(s.get('number', 999)) if str(s.get('number', '')).isdigit() else 999)

        # Sort chapters within each part by roman numeral
        for part in json_data.get('parts', []):
            part['section_groups'].sort(key=lambda g: self._roman_to_int(
                re.search(r'([IVXLCDM]+)', g.get('number', ''), re.I).group(1)
            ) if g.get('number') and re.search(r'([IVXLCDM]+)', g.get('number', ''), re.I) else 999999)

    def _sort_parts_and_chapters_by_sections(self, parts):
        """
        Sort Parts and Chapters in increasing order of their section numbers.
        Parts are sorted by their minimum section number.
        Chapters within each part are also sorted by their minimum section number.
        """
        import re
        
        def extract_section_num(section):
            """Extract numeric part from section number."""
            if not section or not section.get('number'):
                return float('inf')
            num_str = str(section.get('number', ''))
            m = re.match(r'^(\d+)', num_str)
            return int(m.group(1)) if m else float('inf')
        
        def get_min_section_in_group(group):
            """Get minimum section number in a section group."""
            min_num = float('inf')
            
            # Check direct sections
            for section in group.get('sections', []):
                num = extract_section_num(section)
                if num < min_num:
                    min_num = num
            
            # Check SubChapter sections if present
            if 'SubChapter' in group:
                for subch in group['SubChapter']:
                    for sg in subch.get('section_groups', []):
                        for section in sg.get('sections', []):
                            num = extract_section_num(section)
                            if num < min_num:
                                min_num = num
            
            return min_num
        
        def get_min_section_in_part(part):
            """Get minimum section number in entire part."""
            min_num = float('inf')
            
            # Check all section groups (chapters)
            for group in part.get('section_groups', []):
                group_min = get_min_section_in_group(group)
                if group_min < min_num:
                    min_num = group_min
            
            return min_num
        
        # Sort chapters within each part
        for part in parts:
            if 'section_groups' in part and part['section_groups']:
                # FIX: Handle None values in sorting
                part['section_groups'].sort(key=lambda g: (
                    get_min_section_in_group(g), 
                    g.get('number') or '',  # Use empty string if None
                    g.get('title') or ''    # Also use title as fallback
                ))
                
                if self.debug_mode:
                    print(f"\nSorting chapters in {part.get('number', 'UNKNOWN PART')}:")
                    for group in part['section_groups']:
                        min_sec = get_min_section_in_group(group)
                        chapter_name = group.get('number') or group.get('title', 'Untitled')
                        print(f"  {chapter_name}: min section = {min_sec if min_sec != float('inf') else 'N/A'}")
        
        # Sort parts by their minimum section number
        # FIX: Handle None values in part sorting
        parts.sort(key=lambda p: (
            get_min_section_in_part(p), 
            p.get('number') or '',  # Use empty string if None
            p.get('title') or ''    # Also use title as fallback
        ))
        
        if self.debug_mode:
            print("\n=== PARTS SORTED BY SECTION NUMBERS ===")
            for part in parts:
                min_sec = get_min_section_in_part(part)
                print(f"{part.get('number', 'UNKNOWN')}: min section = {min_sec if min_sec != float('inf') else 'N/A'}")
        
        return parts

    def _extract_misplaced_subchapters_to_chapters(self, parts):
        """
        Extract SubChapters that contain low-numbered sections (like 6, 7, 8)
        and should actually be separate chapters, not subchapters.

        This handles cases like CHAPTER II GENERAL PROVISIONS being extracted
        as a SubChapter under CHAPTER XXII instead of as a separate chapter.
        """
        import re

        def extract_section_num_int(section_num_str):
            """Extract integer section number."""
            if not section_num_str:
                return None
            m = re.match(r'^(\d+)', str(section_num_str))
            return int(m.group(1)) if m else None

        if self.debug_mode:
            print("\n=== EXTRACTING MISPLACED SUBCHAPTERS ===")

        chapters_to_add = []

        for part_idx, part in enumerate(parts):
            part_num = part.get('number', 'UNKNOWN')

            for chapter_idx, chapter in enumerate(part.get('section_groups', [])):
                chapter_num = chapter.get('number', 'UNKNOWN')

                # Get the minimum section number in the chapter's direct sections
                chapter_min_section = float('inf')
                for s in chapter.get('sections', []):
                    num = extract_section_num_int(s.get('number'))
                    if num and num < chapter_min_section:
                        chapter_min_section = num

                # Check SubChapters
                subchapters_to_remove = []
                for subch_idx, subch in enumerate(chapter.get('SubChapter', [])):
                    subch_title = subch.get('title', '')

                    # Get minimum section in this subchapter
                    subch_min_section = float('inf')
                    for sg in subch.get('section_groups', []):
                        for s in sg.get('sections', []):
                            num = extract_section_num_int(s.get('number'))
                            if num and num < subch_min_section:
                                subch_min_section = num

                    # If this subchapter has very low section numbers compared to the chapter,
                    # it's likely a misplaced chapter
                    if subch_min_section < 20 and (chapter_min_section == float('inf') or subch_min_section < chapter_min_section - 100):
                        # This subchapter should be a separate chapter
                        if self.debug_mode:
                            print(f"  Found misplaced SubChapter '{subch_title}' with section {subch_min_section} in {part_num} > {chapter_num}")

                        # Infer chapter number from section range
                        # For very early sections (1-10), use roman numerals
                        chapter_number = None
                        if subch_min_section <= 3:
                            chapter_number = "CHAPTER I"
                        elif subch_min_section <= 8:
                            chapter_number = "CHAPTER II"
                        elif subch_min_section <= 15:
                            chapter_number = "CHAPTER III"

                        # Create a new chapter from this subchapter
                        new_chapter = {
                            'number': chapter_number,
                            'title': subch_title,
                            'sections': []
                        }

                        # Extract all sections from this subchapter
                        for sg in subch.get('section_groups', []):
                            new_chapter['sections'].extend(sg.get('sections', []))

                        # Mark for addition to the part
                        chapters_to_add.append({
                            'part_idx': part_idx,
                            'chapter': new_chapter,
                            'subch_idx': subch_idx
                        })

                        subchapters_to_remove.append(subch)

                # Remove the subchapters that were promoted to chapters
                if subchapters_to_remove:
                    chapter['SubChapter'] = [sc for sc in chapter.get('SubChapter', []) if sc not in subchapters_to_remove]
                    if not chapter['SubChapter']:
                        del chapter['SubChapter']

        # Add the extracted chapters to their parts
        for addition in chapters_to_add:
            part = parts[addition['part_idx']]
            part['section_groups'].append(addition['chapter'])
            if self.debug_mode:
                print(f"  Created new chapter '{addition['chapter']['title']}' in {part.get('number')}")

        if self.debug_mode:
            print(f"Total chapters extracted from SubChapters: {len(chapters_to_add)}\n")

    def _fix_chapter_part_assignments(self, parts, textual_containers):
        """
        Fix chapters that are in the wrong PART.
        Uses textual_containers to determine which PART each chapter should belong to
        based on the chapter's section range.
        """
        import re

        def extract_section_num_int(section_num_str):
            """Extract integer section number."""
            if not section_num_str:
                return None
            m = re.match(r'^(\d+)', str(section_num_str))
            return int(m.group(1)) if m else None

        def get_min_section_in_chapter(chapter):
            """Get minimum section number in a chapter (includes SubChapters)."""
            min_sec = float('inf')

            # Check direct sections
            for s in chapter.get('sections', []):
                num = extract_section_num_int(s.get('number'))
                if num and num < min_sec:
                    min_sec = num

            # Check SubChapter sections
            for subchapter in chapter.get('SubChapter', []):
                for sg in subchapter.get('section_groups', []):
                    for s in sg.get('sections', []):
                        num = extract_section_num_int(s.get('number'))
                        if num and num < min_sec:
                            min_sec = num

            return min_sec if min_sec != float('inf') else None

        def find_correct_part_for_section(section_num, containers):
            """Find which PART this section should belong to based on PART boundaries."""
            # IMPORTANT: Check if section falls within PART's min-max range (not just min to next min)
            # Previous logic assumed parts were contiguous, but they have gaps!
            # Example: PART III is sections 19-22, but sections 23-79 should NOT be in PART III

            # Build list of PART containers with both min and max
            part_containers = []
            for container in containers:
                if container.get('number', '').startswith('PART'):
                    min_sec = container.get('min')
                    max_sec = container.get('max')
                    if min_sec and max_sec:
                        part_containers.append((min_sec, max_sec, container.get('number')))

            if not part_containers:
                return None

            # Check if section falls within any PART's explicit range
            for part_min, part_max, part_num in part_containers:
                if part_min <= section_num <= part_max:
                    return part_num

            return None

        if self.debug_mode:
            print("\n=== FIXING CHAPTER-TO-PART ASSIGNMENTS ===")

        # Find MAIN PART and other parts
        main_part = None
        other_parts_map = {}

        for part in parts:
            part_num = part.get('number', '')
            if part_num == 'MAIN PART':
                main_part = part
            else:
                other_parts_map[part_num] = part

        if not main_part:
            if self.debug_mode:
                print("  No MAIN PART found")
            return

        # Check each chapter in MAIN PART
        chapters_to_move = []

        for chapter in main_part.get('section_groups', []):
            # IMPORTANT: Skip the default/preliminary section group (sections without a chapter)
            # These are typically sections 1-2 (Short title, etc.) that should stay in MAIN PART
            # The default group has no chapter number (number is None or "")
            chapter_num = chapter.get('number')
            if not chapter_num or chapter_num in ['None', '[Default]', '']:
                if self.debug_mode:
                    section_nums = [s.get('number') for s in chapter.get('sections', [])]
                    print(f"  Skipping default group (no chapter number) with sections: {section_nums}")
                continue

            min_sec = get_min_section_in_chapter(chapter)
            if min_sec is None:
                continue

            # Find if this chapter should be in a different PART
            correct_part_num = find_correct_part_for_section(min_sec, textual_containers)

            if correct_part_num and correct_part_num != 'MAIN PART':
                # This chapter should be in a different PART
                chapters_to_move.append({
                    'chapter': chapter,
                    'target_part': correct_part_num,
                    'min_section': min_sec
                })

        # Move chapters to their correct parts
        for move_info in chapters_to_move:
            chapter = move_info['chapter']
            target_part_num = move_info['target_part']

            # Find or create target part
            if target_part_num in other_parts_map:
                target_part = other_parts_map[target_part_num]
            else:
                # Create the part if it doesn't exist
                target_part = {
                    'number': target_part_num,
                    'title': None,
                    'section_groups': []
                }
                parts.append(target_part)
                other_parts_map[target_part_num] = target_part

            # Move chapter
            main_part['section_groups'].remove(chapter)
            target_part['section_groups'].append(chapter)

            if self.debug_mode:
                print(f"  Moved {chapter.get('number', 'chapter')} ({chapter.get('title', '')}) with min section {move_info['min_section']} from MAIN PART to {target_part_num}")

        if self.debug_mode:
            print(f"Total chapters moved: {len(chapters_to_move)}\n")

    def _clean_up_chapter_sections(self, parts):
        """
        Clean up chapters where sections have large gaps indicating they don't belong together.
        For example, if a chapter has sections [6, 7, 8, 336, 337], the 336+ sections
        should be removed as they clearly belong to a different chapter.
        """
        import re

        def extract_section_num_int(section_num_str):
            """Extract integer section number."""
            if not section_num_str:
                return None
            m = re.match(r'^(\d+)', str(section_num_str))
            return int(m.group(1)) if m else None

        if self.debug_mode:
            print("\n=== CLEANING UP CHAPTER SECTIONS ===")

        for part in parts:
            for chapter in part.get('section_groups', []):
                sections = chapter.get('sections', [])
                if not sections:
                    continue

                # Get all section numbers
                section_nums = []
                for s in sections:
                    num = extract_section_num_int(s.get('number'))
                    if num is not None:
                        section_nums.append((num, s))

                if len(section_nums) < 2:
                    continue

                # Sort by section number
                section_nums.sort(key=lambda x: x[0])

                # Find gaps larger than 100
                cleaned_sections = []
                last_num = section_nums[0][0]
                cleaned_sections.append(section_nums[0][1])

                for num, section in section_nums[1:]:
                    gap = num - last_num
                    if gap > 100:
                        # Large gap - stop including sections
                        if self.debug_mode:
                            print(f"  Removing sections after gap in {chapter.get('title', 'chapter')}: kept up to {last_num}, skipping {num}+")
                        break
                    cleaned_sections.append(section)
                    last_num = num

                # Update chapter with cleaned sections
                if len(cleaned_sections) < len(sections):
                    chapter['sections'] = cleaned_sections
                    if self.debug_mode:
                        removed = len(sections) - len(cleaned_sections)
                        print(f"  Removed {removed} sections from {chapter.get('title', 'chapter')}")

        if self.debug_mode:
            print("=== CLEANUP COMPLETE ===\n")

    def _relocate_misplaced_sections_by_containers(self, parts, textual_containers):
        """
        Move sections that are in wrong chapters/parts to their correct location
        based on textual_containers (which have accurate section ranges).
        """
        import re

        def extract_section_num_int(section_num_str):
            """Extract integer section number."""
            if not section_num_str:
                return None
            m = re.match(r'^(\d+)', str(section_num_str))
            return int(m.group(1)) if m else None

        def find_correct_container(section_num, section_num_str, containers):
            """
            Find which container this section should belong to.
            When multiple containers match (e.g., PART IV: 39-42, PART IVA: 42-42):
            - Alphanumeric sections (42A, 42B) prefer narrow ranges (PART IVA: 42-42)
            - Plain numeric sections (42) prefer broader ranges (PART IV: 39-42)
            """
            # Find all matching containers
            matching_containers = []
            for container in containers:
                min_sec = container.get('min')
                max_sec = container.get('max')
                if min_sec and max_sec:
                    if min_sec <= section_num <= max_sec:
                        matching_containers.append(container)

            if not matching_containers:
                return None

            if len(matching_containers) == 1:
                return matching_containers[0]

            # Multiple matches - apply preference logic
            has_alpha_suffix = bool(re.match(r'^\d+[A-Za-z]+', str(section_num_str)))

            if has_alpha_suffix:
                # Prefer narrow range for alphanumeric sections
                # Sort by range size (ascending), so narrow ranges come first
                matching_containers.sort(key=lambda c: (c.get('max', 0) - c.get('min', 0)))
            else:
                # Prefer broader range for plain numeric sections
                # Sort by range size (descending), so broader ranges come first
                matching_containers.sort(key=lambda c: -(c.get('max', 0) - c.get('min', 0)))

            return matching_containers[0]

        def find_part_and_chapter(parts, container_number, container_title):
            """Find the part and chapter matching the container."""
            # Try to match by container number (e.g., "PART I", "CHAPTER II")
            container_number = container_number or ''
            container_title = container_title or ''

            for part in parts:
                # Check if this is a PART container
                if container_number and container_number.upper().startswith('PART'):
                    if part.get('number') == container_number:
                        # Return first chapter or find matching one
                        for chapter in part.get('section_groups', []):
                            if container_title:
                                chapter_title = (chapter.get('title') or '').upper()
                                if chapter_title == container_title.upper():
                                    return (part, chapter)
                        # Return first chapter if no title match
                        if part.get('section_groups'):
                            return (part, part['section_groups'][0])

                # Check if this is a CHAPTER container
                if container_number and container_number.upper().startswith('CHAPTER'):
                    for chapter in part.get('section_groups', []):
                        chapter_title = (chapter.get('title') or '').upper()
                        chapter_num = chapter.get('number') or ''
                        if chapter_num == container_number or chapter_title == container_title.upper():
                            return (part, chapter)

                # Check by title match
                if container_title:
                    for chapter in part.get('section_groups', []):
                        chapter_title = (chapter.get('title') or '').upper()
                        if chapter_title == container_title.upper():
                            return (part, chapter)

            return (None, None)

        if self.debug_mode:
            print("\n=== RELOCATING MISPLACED SECTIONS ===")

        # Collect all sections with their current locations
        sections_to_move = []

        for part_idx, part in enumerate(parts):
            for chapter_idx, chapter in enumerate(part.get('section_groups', [])):
                sections = chapter.get('sections', [])
                for section_idx, section in enumerate(sections[:]):  # Copy to avoid modification during iteration
                    section_num_str = section.get('number')
                    section_num_int = extract_section_num_int(section_num_str)
                    if section_num_int:
                        # Find which container this section should be in
                        correct_container = find_correct_container(section_num_int, section_num_str, textual_containers)

                        if correct_container:
                            # Check if section is already in correct container
                            current_chapter_title = (chapter.get('title') or '').upper()
                            current_part_num = part.get('number') or ''
                            container_num = correct_container.get('number') or ''
                            container_title = (correct_container.get('title') or '').upper()

                            # Determine if misplaced
                            is_misplaced = False

                            # If container specifies a PART, check if we're in the right part
                            if container_num and container_num.upper().startswith('PART'):
                                if current_part_num != container_num:
                                    is_misplaced = True
                            # If container specifies a CHAPTER, check title
                            elif container_title and container_title != current_chapter_title:
                                is_misplaced = True

                            if is_misplaced:
                                sections_to_move.append({
                                    'section': section,
                                    'from_part_idx': part_idx,
                                    'from_chapter_idx': chapter_idx,
                                    'from_section_idx': section_idx,
                                    'to_container': correct_container
                                })

        # Move sections
        moved_count = 0
        for move_info in sections_to_move:
            section = move_info['section']
            container = move_info['to_container']

            # Find destination part and chapter
            dest_part, dest_chapter = find_part_and_chapter(parts, container.get('number'), container.get('title'))

            if dest_part and dest_chapter:
                # Remove from current location
                from_part = parts[move_info['from_part_idx']]
                from_chapter = from_part['section_groups'][move_info['from_chapter_idx']]
                from_chapter['sections'].remove(section)

                # Add to destination
                dest_chapter.setdefault('sections', []).append(section)

                moved_count += 1

                if self.debug_mode:
                    print(f"  Moved Section {section.get('number')} from {from_part.get('number')} to {dest_part.get('number')} / {dest_chapter.get('title')}")

        if self.debug_mode:
            print(f"Total sections relocated: {moved_count}\n")

    def _sort_all_sections_chapters_parts(self, json_data):
        """
        Comprehensive sorting function that ensures:
        1. Sections are placed in the correct chapters/parts based on textual containers
        2. Sections within each group are sorted in ascending order
        3. Chapters within each part are sorted by their minimum section number
        4. Parts are sorted by their minimum section number

        This handles all levels: SubChapters, Chapters, Parts
        """
        import re

        def extract_section_num(section):
            """Extract numeric part from section number with alpha suffix handling."""
            if not section or not section.get('number'):
                return float('inf')
            num_str = str(section.get('number', ''))
            m = re.match(r'^(\d+)', num_str)
            if not m:
                return float('inf')
            num = int(m.group(1))
            # Handle alpha suffixes (6A, 6B, etc.)
            if len(num_str) > len(m.group(1)):
                suffix = num_str[len(m.group(1)):].strip().replace('-', '').replace('.', '')
                if suffix and suffix[0].isalpha():
                    # A=0.01, B=0.02, etc.
                    num += (ord(suffix[0].upper()) - ord('A') + 1) * 0.01
            return num

        def sort_sections(sections):
            """Sort a list of sections by their number."""
            return sorted(sections, key=extract_section_num)

        def get_min_section_in_sections(sections):
            """Get minimum section number from a list of sections."""
            if not sections:
                return float('inf')
            return min(extract_section_num(s) for s in sections)

        def get_min_section_in_group(group):
            """Get minimum section number in a section group (handles SubChapters)."""
            min_num = float('inf')

            # Check direct sections
            if group.get('sections'):
                min_num = min(min_num, get_min_section_in_sections(group['sections']))

            # Check SubChapter sections
            if group.get('SubChapter'):
                for subch in group['SubChapter']:
                    for sg in subch.get('section_groups', []):
                        if sg.get('sections'):
                            min_num = min(min_num, get_min_section_in_sections(sg['sections']))

            return min_num

        def get_min_section_in_part(part):
            """Get minimum section number in entire part."""
            min_num = float('inf')
            for group in part.get('section_groups', []):
                min_num = min(min_num, get_min_section_in_group(group))
            return min_num

        # Process the parts from json_data
        parts = json_data.get('parts', [])

        if self.debug_mode:
            print("\n=== FINAL COMPREHENSIVE SORTING ===")
            # Count total sections at start
            initial_section_count = sum(
                len(g.get('sections', [])) for p in parts
                for g in p.get('section_groups', [])
            )
            print(f"Initial section count: {initial_section_count}")

        # 0. Extract orphaned sections from SubChapters that should be chapters
        self._extract_misplaced_subchapters_to_chapters(parts)

        # 1. Fix chapter-to-part assignments based on textual_containers
        textual_containers = json_data.get('textual_containers', [])
        if textual_containers:
            self._fix_chapter_part_assignments(parts, textual_containers)

        # 2. Fix misplaced sections based on textual_containers
        if textual_containers:
            self._relocate_misplaced_sections_by_containers(parts, textual_containers)

        # 3. Clean up chapters - remove sections that don't belong based on proximity
        # DISABLED: This is too aggressive and removes valid sections with large gaps
        # For legislation_C_101, it removes sections 136-271 because there's a gap
        # self._clean_up_chapter_sections(parts)

        # 1. Sort sections within each group
        for part in parts:
            for group in part.get('section_groups', []):
                # Sort direct sections
                if group.get('sections'):
                    original = [s.get('number') for s in group['sections'][:3]]
                    group['sections'] = sort_sections(group['sections'])
                    if self.debug_mode and original != [s.get('number') for s in group['sections'][:3]]:
                        print(f"Sorted sections in {group.get('title', 'group')}")

                # Sort sections within SubChapters and sort SubChapters themselves
                if group.get('SubChapter'):
                    for subch in group['SubChapter']:
                        for sg in subch.get('section_groups', []):
                            if sg.get('sections'):
                                sg['sections'] = sort_sections(sg['sections'])

                    # Sort SubChapters by their minimum section number
                    def get_min_section_in_subchapter(subch):
                        min_num = float('inf')
                        for sg in subch.get('section_groups', []):
                            for s in sg.get('sections', []):
                                num = extract_section_num(s)
                                if num < min_num:
                                    min_num = num
                        return min_num

                    group['SubChapter'].sort(key=get_min_section_in_subchapter)

        # 2. Sort chapters within each part
        for part in parts:
            if part.get('section_groups'):
                original_order = [(g.get('number'), get_min_section_in_group(g)) for g in part['section_groups'][:3]]
                part['section_groups'].sort(key=lambda g: (
                    get_min_section_in_group(g),
                    g.get('number') or '',
                    g.get('title') or ''
                ))
                if self.debug_mode:
                    new_order = [(g.get('number'), get_min_section_in_group(g)) for g in part['section_groups'][:3]]
                    if original_order != new_order:
                        print(f"Reordered chapters in {part.get('number', 'part')}")

        # 3. Sort parts
        original_part_order = [(p.get('number'), get_min_section_in_part(p)) for p in parts[:3]]
        parts.sort(key=lambda p: (
            get_min_section_in_part(p),
            p.get('number') or '',
            p.get('title') or ''
        ))

        if self.debug_mode:
            new_part_order = [(p.get('number'), get_min_section_in_part(p)) for p in parts[:3]]
            if original_part_order != new_part_order:
                print(f"Reordered parts")
                print(f"  Before: {original_part_order}")
                print(f"  After: {new_part_order}")

        # Update the json_data with sorted parts
        # IMPORTANT: Count sections before and after to detect data loss
        original_section_count = sum(
            len(s) for p in json_data.get('parts', [])
            for g in p.get('section_groups', [])
            for s in [g.get('sections', [])]
        )
        new_section_count = sum(
            len(s) for p in parts
            for g in p.get('section_groups', [])
            for s in [g.get('sections', [])]
        )

        if self.debug_mode and original_section_count != new_section_count:
            print(f"⚠️  WARNING: Section count changed during sorting!")
            print(f"   Before: {original_section_count} sections")
            print(f"   After: {new_section_count} sections")
            print(f"   Lost: {original_section_count - new_section_count} sections")

        json_data['parts'] = parts

        if self.debug_mode:
            print("=== SORTING COMPLETE ===\n")

    def _remove_empty_section_groups(self, json_data):
        """
        Remove all section_groups that have empty sections arrays from all parts.
        Also applies MAIN PART filtering to ensure only CHAPTER I sections remain.
        This ensures the final JSON doesn't contain empty group structures.
        """
        import re
        parts = json_data.get('parts', [])
        total_removed = 0

        for part in parts:
            is_main_part = 'MAIN PART' in part.get('number', '')
            original_groups = part.get('section_groups', [])
            filtered_groups = []

            for group in original_groups:
                # DISABLED: Old MAIN PART filtering logic that was removing valid sections
                # This was filtering out sections > 8 from MAIN PART SubChapters, but many
                # legislations (like legislation_C_101) have MAIN PART chapters with sections 23-458
                # The master routing function now correctly assigns sections, so this filter is no longer needed
                # if is_main_part:
                #     filtered_subchapters = []
                #     ...
                # No longer applying MAIN PART specific filtering

                # Check if group has any sections (direct or in SubChapters)
                has_sections = False

                # Check direct sections
                if group.get('sections'):
                    has_sections = True

                # Check SubChapter sections
                if not has_sections and group.get('SubChapter'):
                    for subch in group['SubChapter']:
                        for sg in subch.get('section_groups', []):
                            if sg.get('sections'):
                                has_sections = True
                                break
                        if has_sections:
                            break

                # Only keep groups that have sections
                if has_sections:
                    filtered_groups.append(group)
                else:
                    total_removed += 1
                    if self.debug_mode:
                        part_name = part.get('number', 'UNKNOWN')
                        group_name = group.get('title', group.get('number', 'UNNAMED'))
                        # Debug: show what's actually in the group
                        direct_secs = group.get('sections', [])
                        print(f"  Removed empty group '{group_name}' from {part_name} (had {len(direct_secs)} direct sections, {len(group.get('SubChapter', []))} subchapters)")

            part['section_groups'] = filtered_groups

        if self.debug_mode and total_removed > 0:
            print(f"\n=== CLEANUP COMPLETE: Removed {total_removed} empty section_groups ===\n")

    def _ensure_proper_part_chapter_order(self, final_structure):
        """
        Ensure Parts and Chapters are in the correct order based on section numbers.
        Also handles special cases like MAIN PART and sections without chapters.
        """
        import re
        
        def get_section_num(section):
            """Extract section number as integer."""
            if not section or not section.get('number'):
                return None
            m = re.match(r'^(\d+)', str(section.get('number', '')))
            return int(m.group(1)) if m else None
        
        # First, ensure sections without chapters (1-4 typically) are in the right place
        sections_without_chapters = []
        main_part = None
        other_parts = []
        
        for part in final_structure:
            if part.get('number') == 'MAIN PART':
                main_part = part
            else:
                other_parts.append(part)
        
        # If no MAIN PART exists, create one for sections without chapters
        if not main_part:
            main_part = {
                'number': 'MAIN PART',
                'title': None,
                'section_groups': []
            }
        
        # Collect all sections and find which are without proper chapters (typically 1-10)
        all_sections_nums = []
        for part in final_structure:
            for group in part.get('section_groups', []):
                for section in group.get('sections', []):
                    num = get_section_num(section)
                    if num:
                        all_sections_nums.append(num)
                
                # Also check SubChapter sections
                if 'SubChapter' in group:
                    for subch in group['SubChapter']:
                        for sg in subch.get('section_groups', []):
                            for section in sg.get('sections', []):
                                num = get_section_num(section)
                                if num:
                                    all_sections_nums.append(num)
        
        # Determine sections without chapters range (sections before first chapter usually)
        no_chapter_max = 10  # Default assumption
        if all_sections_nums:
            all_sections_nums.sort()
            # Find first gap greater than 5
            for i in range(len(all_sections_nums) - 1):
                if all_sections_nums[i+1] - all_sections_nums[i] > 5:
                    no_chapter_max = all_sections_nums[i]
                    break
        
        # Move sections without chapters to MAIN PART if needed
        for part in other_parts[:]:
            groups_to_move = []
            remaining_groups = []
            
            for group in part.get('section_groups', []):
                has_no_chapter = False
                has_chapter = False
                
                for section in group.get('sections', []):
                    num = get_section_num(section)
                    if num:
                        if num <= no_chapter_max:
                            has_no_chapter = True
                        else:
                            has_chapter = True
                
                if has_no_chapter and not has_chapter:
                    groups_to_move.append(group)
                else:
                    remaining_groups.append(group)
            
            # Move groups without chapters to MAIN PART
            if groups_to_move:
                # Ensure MAIN PART has a group for sections without chapters
                no_chapter_group = None
                for g in main_part['section_groups']:
                    if g.get('title') is None and g.get('number') is None:  # CHANGED: Check for None instead of "PRELIMINARY"
                        no_chapter_group = g
                        break
                
                if not no_chapter_group:
                    no_chapter_group = {
                        'number': None,
                        'title': None,  # CHANGED: Set to None instead of "PRELIMINARY"
                        'sections': []
                    }
                    main_part['section_groups'].insert(0, no_chapter_group)
                
                # Add sections to the group
                for group in groups_to_move:
                    no_chapter_group['sections'].extend(group.get('sections', []))
            
            part['section_groups'] = remaining_groups
        
        # Build final ordered structure
        ordered_structure = []
        
        # MAIN PART always goes first if it has content
        if main_part and main_part.get('section_groups'):
            ordered_structure.append(main_part)
        
        # Add other parts sorted by their minimum section
        ordered_structure.extend(other_parts)
        
        # Apply sorting - Handle potential errors
        try:
            return self._sort_parts_and_chapters_by_sections(ordered_structure)
        except Exception as e:
            if self.debug_mode:
                print(f"Warning: Error during sorting: {e}")
                print("Returning unsorted structure")
            return ordered_structure
    def _determine_document_structure(self, textual_containers):
        """
        FIXED: Better structure determination that doesn't interfere with MAIN PART routing.
        """
        chapter_containers = []
        part_containers = []
        
        for container in textual_containers or []:
            number = container.get("number", "").upper()
            
            if number.startswith("CHAPTER"):
                chapter_containers.append(container)
            elif number.startswith("PART") and number != "MAIN PART":
                part_containers.append(container)
        
        # Always prefer parts structure if parts exist
        if part_containers:
            return 'parts', part_containers, []
        elif chapter_containers:
            return 'chapters', chapter_containers, []
        else:
            return 'simple', [], []
    def finalize_structure_order(self, final_structure):
        """
        UPDATED: Ensure MAIN PART stays first, then proper part ordering.
        """
        if not final_structure:
            return []
        
        main_parts = []
        other_parts = []
        
        for part in final_structure:
            if part.get('number') == 'MAIN PART':
                main_parts.append(part)
            else:
                other_parts.append(part)
        
        # Sort other parts
        other_parts.sort(key=lambda p: (
            self._get_part_sort_key(p.get('number', ''))
        ))
        
        # MAIN PART always first
        final_order = main_parts + other_parts
        
        if self.debug_mode:
            print("\n=== FINAL STRUCTURE ORDER ===")
            for part in final_order:
                section_count = 0
                for g in part.get('section_groups', []):
                    section_count += len(g.get('sections', []))
                print(f"{part.get('number', 'UNKNOWN')}: {section_count} sections")
        
        return final_order
    def _extract_subchapter_groups(self, chapter_text: str, all_sections: list, chapter_offset: int, 
                                chapter_min: int = None, chapter_max: int = None) -> list:
        """
        Extract SUBCHAPTER groups - focusing on ALL CAPS headers only.
        EXCLUDES PART and CHAPTER declarations which are main structural elements.
        Constrains section ranges to parent chapter's bounds.
        
        Args:
            chapter_text: The text content of the chapter
            all_sections: List of (position, section_number) tuples
            chapter_offset: Starting position of chapter in full text
            chapter_min: Minimum section number in parent chapter
            chapter_max: Maximum section number in parent chapter
        """
        import re
        
        groups = []
        
        if self.debug_mode:
            print(f"\n=== SEARCHING FOR ALL-CAPS SUBCHAPTERS (EXCLUDING PART/CHAPTER) ===")
            print(f"  Chapter text length: {len(chapter_text)}")
            print(f"  Chapter section range: {chapter_min} to {chapter_max}")
        
        # Primary patterns - ALL CAPS ONLY (but NOT PART/CHAPTER)
        all_caps_patterns = [
            # Explicit SUBCHAPTER declarations (these ARE subchapters)
            re.compile(r'(?m)^\s*(SUB[\s\-]*CHAPTER\s+[IVXLCDM]+(?:\s*[-–—:]?\s*[A-Z\s,\-\(\)&\'\/\.]+)?)\s*$'),
            re.compile(r'(?m)^\s*(SUBCHAPTER\s+[IVXLCDM]+(?:\s*[-–—:]?\s*[A-Z\s,\-\(\)&\'\/\.]+)?)\s*$'),

            # All-caps headers (2+ words) - will be validated to exclude PART/CHAPTER
            re.compile(r'(?m)^\s*([A-Z]{2,}(?:\s+[A-Z]+)+)\s*$'),

            # All-caps headers with special chars - will be validated
            re.compile(r'(?m)^\s*([A-Z][A-Z\s,\-\(\)&\'\/\.]{2,}[A-Z])\s*$'),

            # All-caps with leading markers (NOT including PART/CHAPTER)
            re.compile(r'(?m)^\s*\(([A-Z])\)\s+([A-Z][A-Z\s,\-\(\)&\'\/\.]+)$'),
            re.compile(r'(?m)^\s*([A-Z])\.\s+([A-Z][A-Z\s,\-\(\)&\'\/\.]+)$'),
            re.compile(r'(?m)^\s*([IVXLCDM]+)\.\s+([A-Z][A-Z\s,\-\(\)&\'\/\.]+)$'),

            # All-caps with "OF" pattern (but will validate it's not "OF CHAPTER" etc.)
            re.compile(r'(?m)^\s*(OF\s+[A-Z][A-Z\s,\-\(\)&\'\/\.]+)\s*$'),

            # Common legal all-caps headers (these are definitely subchapters)
            re.compile(r'(?m)^\s*((?:PRELIMINARY|GENERAL|SPECIAL|SUPPLEMENTARY|TRANSITIONAL|FINAL|MISCELLANEOUS)\s+PROVISIONS?)\s*$'),
            re.compile(r'(?m)^\s*(DEFINITIONS?\s*(?:AND\s+)?INTERPRETATIONS?)\s*$'),
            re.compile(r'(?m)^\s*(POWERS?\s+AND\s+DUTIES)\s*$'),
            re.compile(r'(?m)^\s*(RIGHTS?\s+AND\s+OBLIGATIONS?)\s*$'),
            re.compile(r'(?m)^\s*(PROCEDURES?\s+AND\s+PROCEEDINGS?)\s*$'),
            re.compile(r'(?m)^\s*(ENFORCEMENT\s+AND\s+PENALTIES)\s*$'),
            re.compile(r'(?m)^\s*(APPEALS?\s+AND\s+REVIEWS?)\s*$'),

            # NEW: Title Case patterns for subchapters that aren't in all caps
            # These appear between sections as standalone lines followed by section content
            # Made more flexible to handle mixed capitalization
            re.compile(r'(?m)^\s*([A-Z][a-z]+(?:\s+(?:of|to|and|for|in|on|with)\s+[A-Z][a-z]+(?:\s+[a-z]+)?)+)\s*$'),
            re.compile(r'(?m)^\s*([A-Z][a-z]+\s+(?:of|to)\s+[A-Za-z][a-z]+(?:\s+[a-z]+)?)\s*$'),
            re.compile(r'(?m)^\s*((?:Mode|Claims|Method|Procedure|Process)\s+(?:of|to)\s+[A-Za-z][a-z]+(?:\s+[a-z]+)?)\s*$', re.IGNORECASE),
            # Even more permissive patterns for common subchapter titles
            re.compile(r'(?m)^\s*(Claims\s+to\s+[Pp]roperty\s+seized)\s*$'),
            re.compile(r'(?m)^\s*(Mode\s+of\s+[Ss]eizure)\s*$'),
        ]
        
        # Track what we've already found to avoid duplicates
        found_positions = set()
        
        for pattern in all_caps_patterns:
            for m in pattern.finditer(chapter_text):
                # Get the full match
                if m.lastindex and m.lastindex > 1:
                    # Pattern with groups - combine them
                    parts = []
                    for i in range(1, m.lastindex + 1):
                        if m.group(i):
                            parts.append(m.group(i))
                    full_text = ' '.join(parts).strip()
                else:
                    full_text = m.group(1) if m.lastindex else m.group(0)
                    full_text = full_text.strip()
                
                # Skip if already found at this position
                if m.start() in found_positions:
                    continue
                
                # CRITICAL: Validate it's NOT a PART or CHAPTER
                if not self._validate_all_caps_subchapter(full_text):
                    if self.debug_mode:
                        print(f"  Skipped (PART/CHAPTER or invalid): {full_text}")
                    continue
                
                found_positions.add(m.start())
                
                # Determine if it's an explicit SUBCHAPTER or just a header
                is_explicit = 'SUBCHAPTER' in full_text or 'SUB-CHAPTER' in full_text or 'SUB CHAPTER' in full_text
                
                # Extract number if present
                number = None
                title = full_text
                
                if is_explicit:
                    # Extract SUBCHAPTER number
                    subch_match = re.search(r'SUB[\s\-]*CHAPTER\s+([IVXLCDM]+)', full_text, re.I)
                    if subch_match:
                        number = f"SUBCHAPTER {subch_match.group(1)}"
                        # Title is what comes after
                        title_part = full_text[subch_match.end():].strip()
                        title = title_part.strip('-–—: ') if title_part else f"SUBCHAPTER {subch_match.group(1)}"
                else:
                    # Check for letter/number prefix
                    prefix_match = re.match(r'^\(([A-Z])\)\s+(.+)$', full_text)
                    if prefix_match:
                        number = f"({prefix_match.group(1)})"
                        title = prefix_match.group(2)
                    else:
                        prefix_match = re.match(r'^([A-Z])\.\s+(.+)$', full_text)
                        if prefix_match:
                            number = f"{prefix_match.group(1)}."
                            title = prefix_match.group(2)
                        else:
                            prefix_match = re.match(r'^([IVXLCDM]+)\.\s+(.+)$', full_text)
                            if prefix_match:
                                number = f"{prefix_match.group(1)}."
                                title = prefix_match.group(2)
                
                if self.debug_mode:
                    print(f"  Found SubChapter: {number or 'N/A'} - {title}")
                
                groups.append({
                    'number': number,
                    'title': title,
                    'start_pos': chapter_offset + m.start(),
                    'end_pos': chapter_offset + m.end(),
                    'confidence': 1.0 if is_explicit else 0.8,
                    'type': 'all_caps'
                })
        
        # Sort by position
        groups.sort(key=lambda x: x['start_pos'])
        
        # Remove close duplicates
        filtered = []
        last_pos = -1
        
        for group in groups:
            if group['start_pos'] - last_pos > 10:  # Not too close to previous
                filtered.append(group)
                last_pos = group['start_pos']
        
        groups = filtered
        
        # Set proper end positions
        for i, group in enumerate(groups):
            if i + 1 < len(groups):
                group['end_pos'] = groups[i + 1]['start_pos']
            else:
                group['end_pos'] = chapter_offset + len(chapter_text)
        
        # Calculate section ranges - CONSTRAINED to parent chapter bounds
        for group in groups:
            # Find sections within this subchapter's text position
            group_sections = [num for pos, num in all_sections 
                            if group['start_pos'] <= pos < group['end_pos']]
            
            # CRITICAL FIX: Constrain to parent chapter's section range
            if chapter_min is not None and chapter_max is not None:
                group_sections = [num for num in group_sections 
                                if chapter_min <= num <= chapter_max]
            
            if group_sections:
                group['min'] = min(group_sections)
                group['max'] = max(group_sections)
            else:
                # No sections found - might be a header without sections
                group['min'] = None
                group['max'] = None
        
        if self.debug_mode:
            print(f"  Total subchapters found (excluding PART/CHAPTER): {len(groups)}")
            for g in groups:
                min_str = str(g.get('min')) if g.get('min') is not None else 'N/A'
                max_str = str(g.get('max')) if g.get('max') is not None else 'N/A'
                print(f"    - {g.get('number', '[No Number]')}: {g['title']} (sections {min_str}-{max_str})")
        
        return groups

    def _validate_all_caps_subchapter(self, text: str) -> bool:
        """
        Validate that text is a valid subchapter header.
        EXCLUDES PART and CHAPTER declarations.
        Now supports both ALL CAPS and Title Case headings.
        """
        import re

        if not text or len(text.strip()) < 2:
            return False

        text = text.strip()

        # Check if it's title case (for specific patterns like "Mode of Seizure")
        is_title_case = bool(re.match(r'^[A-Z][a-z]+(?:\s+(?:of|to|and|for|in|on|with|from|the|a)\s+[A-Z][a-z]+|\s+[A-Z][a-z]+)+', text))

        # Check for specific known SubChapter patterns (including numbered ones)
        is_known_subchapter = bool(re.search(
            r'(?:Claims\s+to\s+[Pp]roperty\s+seized|'
            r'Mode\s+of\s+[Ss]eizure|'
            r'Communication\s+of\s+Orders|'
            r'Arrest\s+and\s+Imprisonment|'
            r'\(\d+\)\s+Of\s+[A-Z][a-z]+|'  # "(2) Of Sales of Movable Property"
            r'Of\s+the\s+Sale\s+and\s+Disposition)',
            text
        ))

        # Must be mostly uppercase OR valid title case OR known subchapter
        if not text.isupper() and not is_title_case and not is_known_subchapter:
            return False
        
        # CRITICAL: Exclude ALL forms of PART and CHAPTER declarations
        # These are main structural elements, NOT subchapters
        exclude_patterns = [
            r'^PART\s+[IVXLCDM]+',           # PART I, PART II, etc.
            r'^PART\s+\d+',                   # PART 1, PART 2, etc.
            r'^PART\s+[A-Z]',                 # PART A, PART B, etc.
            r'^CHAPTER\s+[IVXLCDM]+',         # CHAPTER I, CHAPTER II, etc.
            r'^CHAPTER\s+\d+',                # CHAPTER 1, CHAPTER 2, etc.
            r'^CHAPTER\s+[A-Z]',              # CHAPTER A, CHAPTER B, etc.
            r'^SCHEDULE\s+[IVXLCDM]+',        # SCHEDULE I, etc.
            r'^SCHEDULE\s+\d+',               # SCHEDULE 1, etc.
            r'^TITLE\s+[IVXLCDM]+',           # TITLE I, etc.
            r'^APPENDIX\s+[A-Z0-9]+',         # APPENDIX A, etc.
            r'^ANNEX\s+[A-Z0-9]+',            # ANNEX 1, etc.
            r'^BOOK\s+[IVXLCDM]+',            # BOOK I, etc.
            r'^DIVISION\s+[A-Z0-9]+',         # DIVISION 1, etc. (unless SUB-DIVISION)
            r'^ARTICLE\s+[A-Z0-9]+',          # ARTICLE I, etc.
        ]
        
        for pattern in exclude_patterns:
            if re.match(pattern, text, re.I):
                return False
        
        # Also exclude if it contains these words at the start
        if re.match(r'^(PART|CHAPTER|SCHEDULE|TITLE|APPENDIX|ANNEX|BOOK|ARTICLE)\b', text, re.I):
            return False
        
        # Exclude pure section numbers
        if re.match(r'^\s*\d+[A-Za-z]*\s*\.?\s*$', text):
            return False
        
        # Exclude single letters/numbers
        if re.match(r'^[A-Z]$|^\d+$|^[IVXLCDM]+$', text):
            return False
        
        # Must have at least some letters (either consecutive uppercase OR title case pattern)
        has_caps = bool(re.search(r'[A-Z]{2,}', text))  # Consecutive uppercase
        has_title_pattern = bool(re.search(r'[A-Z][a-z]+', text))  # Title case word
        if not has_caps and not has_title_pattern:
            return False
        
        # Length constraints
        if len(text) > 100:
            return False
        
        # Common false positives to exclude
        false_positives = [
            'THE', 'A', 'AN', 'AND', 'OR', 'BUT', 'IF', 'THEN',
            'YES', 'NO', 'NOTE', 'SEE', 'CF', 'ID', 'IBID', 'ETC',
            'REPEALED', 'DELETED', 'RESERVED', 'OMITTED'
        ]
        
        if text in false_positives:
            return False
        
        # Accept if it contains legal keywords (but NOT if it's a PART/CHAPTER)
        legal_keywords = [
            'PROVISION', 'PROCEDURE', 'GENERAL', 'SPECIAL', 'PRELIMINARY',
            'JURISDICTION', 'POWER', 'DUTY', 'RIGHT', 'APPEAL', 'ENFORCEMENT',
            'PENALTY', 'OFFENCE', 'ADMINISTRATION', 'REGISTRATION', 'SERVICE',
            'DEFINITION', 'INTERPRETATION', 'APPLICATION', 'SCOPE', 'EVIDENCE',
            'WITNESS', 'DOCUMENT', 'ORDER', 'NOTICE', 'FORM', 'PROCESS',
            'COURT', 'JUDGE', 'MAGISTRATE', 'TRIBUNAL', 'AUTHORITY',
            'SUPPLEMENTARY', 'TRANSITIONAL', 'MISCELLANEOUS', 'FINAL'
        ]
        
        for keyword in legal_keywords:
            if keyword in text:
                return True
        
        # Accept if it starts with common patterns (but NOT PART/CHAPTER)
        if text.startswith(('OF ', 'FOR ', 'TO ', 'IN ', 'ON ', 'BY ', 'WITH ')):
            return True
        
        # Accept if it's explicitly a SUBCHAPTER
        if re.match(r'^SUB[\s\-]*CHAPTER\b', text, re.I):
            return True
        
        # Accept if it has multiple words (likely a title)
        word_count = len(text.split())
        if 2 <= word_count <= 10:
            return True
        
        return False
    def _detect_section_clusters(self, text: str, all_sections: list, offset: int) -> list:
        """
        Detect subchapters by analyzing section number clustering.
        Looks for gaps or patterns in section numbering that indicate groupings.
        """
        import re
        
        clusters = []
        
        # Find sections in this text range
        local_sections = []
        for pos, num in all_sections:
            if offset <= pos < offset + len(text):
                # Find the actual position in the local text
                local_pos = pos - offset
                local_sections.append((local_pos, num))
        
        if len(local_sections) < 3:
            return clusters
        
        # Look for gaps in section numbering
        gaps = []
        for i in range(len(local_sections) - 1):
            curr_num = local_sections[i][1]
            next_num = local_sections[i + 1][1]
            
            # Significant gap might indicate a new group
            if next_num - curr_num > 5:
                gap_pos = (local_sections[i][0] + local_sections[i + 1][0]) // 2
                gaps.append({
                    'position': gap_pos,
                    'before': curr_num,
                    'after': next_num
                })
        
        # Look for title-like text near gaps
        for gap in gaps:
            # Search for potential title around the gap position
            search_start = max(0, gap['position'] - 500)
            search_end = min(len(text), gap['position'] + 500)
            search_text = text[search_start:search_end]
            
            # Look for all-caps lines near the gap
            title_pattern = re.compile(r'(?m)^\s*([A-Z][A-Z\s,\-\(\)&\'\/\.]{3,}[A-Z])\s*$')
            
            for m in title_pattern.finditer(search_text):
                title = m.group(1).strip()
                actual_pos = search_start + m.start()
                
                # Check if this title is close to the gap
                if abs(actual_pos - gap['position']) < 300:
                    if self._is_valid_subchapter_title(title):
                        clusters.append({
                            'number': None,
                            'title': title,
                            'start_pos': offset + actual_pos,
                            'end_pos': offset + actual_pos + len(title),
                            'confidence': 0.7,
                            'type': 'cluster_gap'
                        })
        
        # Look for section number patterns (e.g., 100s, 200s, 300s)
        if local_sections:
            current_hundred = local_sections[0][1] // 100
            hundred_groups = []
            current_group = {'hundred': current_hundred, 'sections': [local_sections[0]]}
            
            for section in local_sections[1:]:
                section_hundred = section[1] // 100
                if section_hundred != current_hundred:
                    hundred_groups.append(current_group)
                    current_hundred = section_hundred
                    current_group = {'hundred': current_hundred, 'sections': [section]}
                else:
                    current_group['sections'].append(section)
            
            hundred_groups.append(current_group)
            
            # Create clusters for hundred groups if significant
            if len(hundred_groups) > 1:
                for group in hundred_groups:
                    if len(group['sections']) >= 2:
                        start_pos = group['sections'][0][0]
                        
                        # Look for a title before the first section
                        before_text = text[max(0, start_pos-300):start_pos]
                        title_match = re.search(r'([A-Z][A-Z\s,\-\(\)&\'\/\.]{3,}[A-Z])\s*$', before_text)
                        
                        if title_match:
                            title = title_match.group(1).strip()
                        else:
                            title = f"Sections {group['hundred']*100}-{group['hundred']*100+99}"
                        
                        clusters.append({
                            'number': None,
                            'title': title,
                            'start_pos': offset + start_pos,
                            'end_pos': offset + start_pos,
                            'confidence': 0.6,
                            'type': 'hundred_group'
                        })
        
        return clusters

    def _find_structural_markers(self, text: str, offset: int) -> list:
        """
        Find structural markers that indicate subchapter boundaries.
        Looks for patterns like repeated structures, similar formatting, etc.
        """
        import re
        
        markers = []
        
        # Look for repeated patterns that might indicate groupings
        patterns_to_check = [
            # Dash or dot leaders
            (re.compile(r'(?m)^[\s\-\.=_]{10,}$'), 'separator'),
            
            # Centered text (approximated by leading spaces)
            (re.compile(r'(?m)^\s{10,}([A-Z][A-Z\s]{2,}[A-Z])\s*$'), 'centered'),
            
            # Bold/emphasized patterns (might be marked with special chars)
            (re.compile(r'(?m)^\*\*([A-Z][A-Z\s,\-]{2,}[A-Z])\*\*$'), 'bold'),
            
            # Numbered groupings like "GROUP 1", "DIVISION A"
            (re.compile(r'(?m)^(GROUP|DIVISION|SECTION|AREA|ZONE)\s+([A-Z0-9]+)\s*[-–—:]?\s*(.*)$', re.I), 'named_group'),
        ]
        
        for pattern, marker_type in patterns_to_check:
            for m in pattern.finditer(text):
                if marker_type == 'separator':
                    # Separators indicate the END of a group, look for title before it
                    before_text = text[max(0, m.start()-200):m.start()].strip()
                    lines = before_text.split('\n')
                    if lines:
                        last_line = lines[-1].strip()
                        if last_line and last_line.isupper() and len(last_line) > 3:
                            markers.append({
                                'number': None,
                                'title': last_line,
                                'start_pos': offset + m.start() - len(last_line),
                                'end_pos': offset + m.start(),
                                'confidence': 0.5,
                                'type': 'separator_marker'
                            })
                
                elif marker_type in ['centered', 'bold']:
                    title = m.group(1).strip()
                    if self._is_valid_subchapter_title(title):
                        markers.append({
                            'number': None,
                            'title': title,
                            'start_pos': offset + m.start(),
                            'end_pos': offset + m.end(),
                            'confidence': 0.6,
                            'type': marker_type
                        })
                
                elif marker_type == 'named_group':
                    group_type = m.group(1)
                    identifier = m.group(2)
                    title = m.group(3).strip() if m.lastindex >= 3 else ""
                    
                    markers.append({
                        'number': f"{group_type.upper()} {identifier}",
                        'title': title or f"{group_type} {identifier}",
                        'start_pos': offset + m.start(),
                        'end_pos': offset + m.end(),
                        'confidence': 0.9,
                        'type': 'named_group'
                    })
        
        return markers
    def master_route_sections_to_structure(self, all_sections, textual_containers, full_text=""):
        """
        ENHANCED FLEXIBLE STRUCTURE HANDLER: Now properly handles mixed structures where:
        1. Some parts have nested chapters (MAIN PART -> CHAPTER I, II, etc.)
        2. Some parts have direct sections (PART I -> sections directly)
        3. Mixed combinations of the above
        """
        import re
        
        def _extract_section_num(section):
            if not section or not section.get("number"):
                return None
            m = re.match(r'^(\d+)', str(section.get("number", "")))
            return int(m.group(1)) if m else None

        if self.debug_mode:
            print(f"\n=== ENHANCED FLEXIBLE STRUCTURE HANDLER ===")
            print(f"Total sections: {len(all_sections)}")
            print(f"Textual containers: {len(textual_containers)}")

            # DEBUG: Track sections 23-79 specifically
            sections_23_79 = [s for s in all_sections if s.get('number') and _extract_section_num(s) is not None and 23 <= _extract_section_num(s) <= 79]
            print(f"DEBUG: Found {len(sections_23_79)} sections in range 23-79 at start of routing")
        
        def _sort_sections(sections):
            return sorted(sections, key=lambda s: (_extract_section_num(s) or 10000, s.get("number", "")))
        
        # ========== STEP 1: ANALYZE CONTAINER STRUCTURE ==========
        
        structure_analysis = {
            "parts_with_chapters": [],     # Parts that have nested chapters
            "parts_without_chapters": [],  # Parts that should have direct sections
            "standalone_chapters": [],     # Top-level chapters not in parts
            "has_mixed_structure": False
        }
        
        if self.debug_mode:
            print(f"\n=== ANALYZING CONTAINER STRUCTURE ===")
        
        for container in textual_containers or []:
            number = container.get("number", "").strip()
            
            if self.debug_mode:
                print(f"Analyzing container: {number}")
                if container.get("chapters"):
                    print(f"  Has {len(container.get('chapters', []))} nested chapters")
            
            # Check for Parts with nested chapters
            if number.upper().startswith("PART") or number.upper() == "MAIN PART":
                nested_chapters = container.get("chapters", [])
                
                if nested_chapters:
                    # This part HAS nested chapters
                    structure_analysis["parts_with_chapters"].append({
                        "number": number,
                        "title": container.get("title"),
                        "min": container.get("min"),
                        "max": container.get("max"),
                        "chapters": nested_chapters
                    })
                    
                    if self.debug_mode:
                        print(f"  → PART with nested chapters: {len(nested_chapters)} chapters")
                else:
                    # This part has NO nested chapters (should have direct sections)
                    structure_analysis["parts_without_chapters"].append({
                        "number": number,
                        "title": container.get("title"),
                        "min": container.get("min"),
                        "max": container.get("max")
                    })
                    
                    if self.debug_mode:
                        print(f"  → PART without chapters (direct sections)")
            
            # Check for standalone chapters
            elif number.upper().startswith("CHAPTER"):
                structure_analysis["standalone_chapters"].append({
                    "number": number,
                    "title": container.get("title"),
                    "min": container.get("min"),
                    "max": container.get("max"),
                    "groups": container.get("groups", [])
                })
                
                if self.debug_mode:
                    print(f"  → Standalone CHAPTER")
        
        # Determine if we have a mixed structure
        structure_analysis["has_mixed_structure"] = (
            len(structure_analysis["parts_with_chapters"]) > 0 and 
            len(structure_analysis["parts_without_chapters"]) > 0
        )
        
        if self.debug_mode:
            print(f"\n=== STRUCTURE ANALYSIS RESULT ===")
            print(f"Parts with chapters: {len(structure_analysis['parts_with_chapters'])}")
            print(f"Parts without chapters: {len(structure_analysis['parts_without_chapters'])}")
            print(f"Standalone chapters: {len(structure_analysis['standalone_chapters'])}")
            print(f"Mixed structure: {structure_analysis['has_mixed_structure']}")
        
        # ========== STEP 2: BUILD ROUTING TARGETS ==========
        
        routing_targets = []
        
        # Fix gaps between chapters and assign ranges to chapters with None values
        # Strategy:
        # 1. Fix small gaps (1-2 sections) between consecutive chapters
        # 2. Assign ranges to chapters that have None values based on surrounding chapters
        for part_data in structure_analysis["parts_with_chapters"]:
            chapters = part_data.get("chapters", [])
            if not chapters:
                continue

            # First pass: Sort all chapters by identifier (to get proper sequence)
            # Extract chapter numbers for sorting
            def extract_chapter_num(ch):
                import re
                num_str = ch.get('identifier', '')
                # Convert roman numerals to int for sorting
                roman_to_int = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
                if num_str and num_str[0] in roman_to_int:
                    # Roman numeral
                    result = 0
                    prev_value = 0
                    for char in reversed(num_str):
                        value = roman_to_int.get(char, 0)
                        if value < prev_value:
                            result -= value
                        else:
                            result += value
                        prev_value = value
                    return result
                return 0

            # Sort chapters by their identifier (roman numeral order)
            sorted_all_chapters = sorted(chapters, key=extract_chapter_num)

            # DISABLED: Auto-extension of first chapter to include section 1
            # This was causing issues where sections appearing BEFORE a chapter heading in the DOM
            # were incorrectly included in that chapter (e.g., legislation_A_5 where sections 1-4
            # appear before CHAPTER I heading, but were being included in CHAPTER I)
            #
            # The proper behavior is: sections should only be in a chapter if they appear AFTER
            # the chapter heading in the DOM, not based on numerical order.
            #
            # Keeping this code commented for reference:
            # if sorted_all_chapters:
            #     first_ch = sorted_all_chapters[0]
            #     first_min = first_ch.get('min')
            #     if isinstance(first_min, int) and first_min > 1:
            #         section_1_exists = any(
            #             str(s.get('number', '')).strip() == '1'
            #             for s in all_sections
            #         )
            #         if section_1_exists:
            #             if self.debug_mode:
            #                 print(f"  Extending first chapter {first_ch['number']} min from {first_min} to 1 (section 1 exists)")
            #             first_ch['min'] = 1
            #         elif self.debug_mode:
            #             print(f"  NOT extending first chapter {first_ch['number']} - section 1 does not exist (legislation starts at section {first_min})")

            if self.debug_mode and sorted_all_chapters:
                first_ch = sorted_all_chapters[0]
                first_min = first_ch.get('min')
                print(f"  First chapter {first_ch.get('number')} min={first_min} (NOT auto-extending to include earlier sections)")

            # Second pass: Fix gaps and assign None values
            for i in range(len(sorted_all_chapters) - 1):
                current_ch = sorted_all_chapters[i]
                next_ch = sorted_all_chapters[i + 1]

                current_min = current_ch.get('min')
                current_max = current_ch.get('max')
                next_min = next_ch.get('min')
                next_max = next_ch.get('max')

                # Case 1: Current chapter has valid range, next has None - assign the gap
                if isinstance(current_max, int) and next_min is None and next_max is None:
                    # Look ahead to find the next chapter with a valid min
                    gap_end = None
                    next_chapter_after_none = None
                    for j in range(i + 2, len(sorted_all_chapters)):
                        future_min = sorted_all_chapters[j].get('min')
                        if isinstance(future_min, int):
                            gap_end = future_min
                            next_chapter_after_none = sorted_all_chapters[j]
                            break

                    if gap_end is not None and gap_end > current_max + 1:
                        gap_size = gap_end - current_max - 1

                        # If there's another chapter with None after this one, split the gap
                        # Otherwise, assign the full gap to this chapter
                        if next_chapter_after_none and j == i + 2:
                            # There's exactly one chapter with None between current and next valid chapter
                            # Assign only the first section(s) to this chapter
                            # Leave the rest for the next chapter
                            next_ch['min'] = current_max + 1
                            next_ch['max'] = current_max + 1  # Only assign one section for now
                            if self.debug_mode:
                                print(f"  Assigning range to {next_ch['number']}: [{next_ch['min']}-{next_ch['max']}] (partial gap, next chapter starts at {gap_end})")
                        else:
                            # Assign the full gap
                            next_ch['min'] = current_max + 1
                            next_ch['max'] = gap_end - 1
                            if self.debug_mode:
                                print(f"  Assigning range to {next_ch['number']}: [{next_ch['min']}-{next_ch['max']}]")

                # Case 2: Both have valid ranges - check for small gaps
                elif isinstance(current_max, int) and isinstance(next_min, int):
                    gap_size = next_min - current_max - 1
                    if 1 <= gap_size <= 3:
                        # Small gap - extend next chapter to include it
                        if self.debug_mode:
                            print(f"  Fixing chapter gap: Sections {current_max + 1}-{next_min - 1} between {current_ch['number']} (max={current_max}) and {next_ch['number']} (min={next_min})")
                            print(f"    -> Extending {next_ch['number']} min from {next_min} to {current_max + 1}")
                        next_ch['min'] = current_max + 1

        # Add parts with chapters - create chapter-level targets
        # IMPORTANT: Include repealed chapters too, because sections still exist in repealed chapters
        # and need to be routed correctly (e.g., legislation_B_72 CHAPTER 184)
        for part_data in structure_analysis["parts_with_chapters"]:
            for chapter_data in part_data["chapters"]:
                # Always create routing target, even if chapter is marked as repealed
                # Sections in repealed chapters still need proper placement
                routing_targets.append({
                    "type": "chapter_in_part",
                    "part_number": part_data["number"],
                    "part_title": part_data.get("title"),
                    "chapter_number": chapter_data["number"],
                    "chapter_title": chapter_data.get("title"),
                    "min": chapter_data.get("min"),
                    "max": chapter_data.get("max"),
                    "groups": chapter_data.get("groups", []),
                    "is_repealed": chapter_data.get("is_repealed", False)  # Track but don't skip
                })
        
        # Add parts without chapters - create part-level targets
        # IMPORTANT: Make the LAST PART (by section number) have max=None to capture remaining sections
        # This handles cases where sections are extracted from hidden content after
        # textual container analysis (e.g., legislation_A_15 sections 92-101)
        parts_without_chapters = structure_analysis["parts_without_chapters"]

        # Fix small gaps between consecutive parts (up to 3 sections)
        # Strategy similar to chapter gap fixing:
        # 1. If current part has valid range and next has valid range, check for small gaps
        # 2. Extend the first part to include the gap sections
        # Example: PART II [8-9], PART III [12-21] -> PART II extends to [8-10], PART III extends to [11-21]
        if parts_without_chapters:
            # Sort by min value
            sorted_parts = sorted([p for p in parts_without_chapters if isinstance(p.get('min'), int) and isinstance(p.get('max'), int)],
                                key=lambda p: p.get('min', 0))

            for i in range(len(sorted_parts) - 1):
                current_part = sorted_parts[i]
                next_part = sorted_parts[i + 1]

                current_max = current_part.get('max')
                next_min = next_part.get('min')

                # Check for gaps between parts
                if isinstance(current_max, int) and isinstance(next_min, int):
                    gap_size = next_min - current_max - 1
                    if gap_size >= 1:
                        # Handle gaps of any size
                        # Strategy:
                        # - Small gaps (1-3): split between parts
                        # - Medium gaps (4-10): assign to current part
                        # - Large gaps (11+): assign to current part (likely textual analysis underestimated the range)

                        if gap_size == 1:
                            # Extend next part to include the single gap section
                            gap_section = current_max + 1
                            if self.debug_mode:
                                print(f"  Fixing part gap: Section {gap_section} between {current_part['number']} (max={current_max}) and {next_part['number']} (min={next_min})")
                                print(f"    -> Extending {next_part['number']} min from {next_min} to {gap_section}")
                            next_part['min'] = gap_section
                        elif gap_size <= 3:
                            # For gaps of 2-3 sections, split them between the two parts
                            # Extend current part by ceiling(gap_size/2)
                            # Extend next part by floor(gap_size/2)
                            extend_current = (gap_size + 1) // 2
                            extend_next = gap_size - extend_current

                            new_current_max = current_max + extend_current
                            new_next_min = next_min - extend_next

                            if self.debug_mode:
                                print(f"  Fixing part gap: Sections {current_max + 1}-{next_min - 1} between {current_part['number']} (max={current_max}) and {next_part['number']} (min={next_min})")
                                print(f"    -> Extending {current_part['number']} max from {current_max} to {new_current_max}")
                                print(f"    -> Extending {next_part['number']} min from {next_min} to {new_next_min}")

                            current_part['max'] = new_current_max
                            next_part['min'] = new_next_min
                        else:
                            # For larger gaps (4+), assign all to the current part
                            # This handles cases where textual analysis failed to detect the full range
                            # e.g., legislation_A_125 PART XII should be [95-130] but was detected as [95-100]
                            new_current_max = next_min - 1

                            if self.debug_mode:
                                print(f"  Fixing large part gap: Sections {current_max + 1}-{next_min - 1} ({gap_size} sections) between {current_part['number']} (max={current_max}) and {next_part['number']} (min={next_min})")
                                print(f"    -> Extending {current_part['number']} max from {current_max} to {new_current_max}")

                            current_part['max'] = new_current_max

        # Find the part with the highest min value - that's the "last" part by section order
        last_part_by_sections = None
        if parts_without_chapters:
            # Filter parts that have valid min values
            parts_with_min = [p for p in parts_without_chapters if isinstance(p.get('min'), int)]
            if parts_with_min:
                last_part_by_sections = max(parts_with_min, key=lambda p: p.get('min', 0))

        if self.debug_mode and parts_without_chapters:
            print(f"\n=== PARTS WITHOUT CHAPTERS (setting last part by section # to have max=None) ===")
            for i, p in enumerate(parts_without_chapters):
                is_last = (last_part_by_sections and p['number'] == last_part_by_sections['number'])
                marker = " <- LAST BY SECTION #" if is_last else ""
                print(f"  {i}: {p['number']} min={p.get('min')}, max={p.get('max')}{marker}")

        for part_data in parts_without_chapters:
            # Check if this is the last part by section number
            is_last_part = (last_part_by_sections and part_data['number'] == last_part_by_sections['number'])

            final_max = None if is_last_part else part_data.get("max")

            if self.debug_mode and is_last_part:
                print(f"  >>> LAST PART BY SECTION #: {part_data['number']} - Setting max={final_max} (was {part_data.get('max')})")

            routing_targets.append({
                "type": "part_direct",
                "part_number": part_data["number"],
                "part_title": part_data.get("title"),
                "min": part_data.get("min"),
                "max": final_max  # Last part by section # has flexible max
            })
        
        # Add standalone chapters
        for chapter_data in structure_analysis["standalone_chapters"]:
            routing_targets.append({
                "type": "standalone_chapter",
                "chapter_number": chapter_data["number"],
                "chapter_title": chapter_data.get("title"),
                "min": chapter_data.get("min"),
                "max": chapter_data.get("max"),
                "groups": chapter_data.get("groups", [])
            })
        
        if self.debug_mode:
            print(f"\n=== ROUTING TARGETS ===")
            for target in routing_targets:
                print(f"  {target['type']}: {target.get('part_number', '')} {target.get('chapter_number', '')} [{target.get('min')}-{target.get('max')}]")
        
        # ========== STEP 3: ASSIGN SECTIONS TO TARGETS ==========
        
        section_assignments = {}
        unassigned_sections = []
        
        for section in all_sections:
            sec_num = _extract_section_num(section)
            section_number_str = section.get("number", "")

            # Debug section 373
            if section_number_str == "373" and self.debug_mode:
                print(f"  DEBUG: Processing section 373 assignment, sec_num={sec_num}")

            if not sec_num:
                unassigned_sections.append(section)
                continue

            # SPECIAL CASE: Section 1 (Short title) - handle carefully
            # In legislations with multiple PARTS (e.g., PART I, PART II), section 1 should go to MAIN PART
            # But in legislations with only CHAPTERS (e.g., CHAPTER 6), section 1 should be assigned to that chapter
            if sec_num == 1:
                section_title = (section.get("title") or "").lower()
                if "short title" in section_title or section_title.strip() in ["", "short title.", "short title"]:
                    # Check if there's a chapter/part that explicitly includes section 1 in its range
                    # If yes, let it be assigned normally. If no, force to MAIN PART.
                    has_explicit_target = False
                    for target in routing_targets:
                        target_min = target.get("min")
                        target_max = target.get("max")
                        # Check if this target explicitly starts from section 1
                        if isinstance(target_min, int) and target_min == 1:
                            has_explicit_target = True
                            break

                    if not has_explicit_target:
                        # No target explicitly includes section 1, force to MAIN PART
                        unassigned_sections.append(section)
                        continue
                    # If has_explicit_target is True, fall through to normal assignment logic

            # Find best matching target
            best_target = None
            best_score = -1

            for target in routing_targets:
                target_min = target.get("min")
                target_max = target.get("max")

                # IMPORTANT: Skip repealed chapters/parts for section routing
                # Repealed containers should not receive sections unless the section itself is marked repealed
                # This prevents misrouting sections to repealed chapters that happen to have overlapping ranges
                # (e.g., legislation_A_5 where sections 2-4 were being routed to repealed CHAPTER V AND VI)
                if target.get("is_repealed", False):
                    continue

                # Handle max=None as infinity (for last part to capture remaining sections)
                if isinstance(target_min, int):
                    if target_max is None:
                        # This target has flexible max (typically last part)
                        # It matches any section >= min
                        if sec_num >= target_min:
                            # Give it a score of 0 - lower priority than exact ranges (which score 1 to 1000)
                            # but still higher than the initial best_score of -1
                            score = 0

                            if score > best_score:
                                best_score = score
                                best_target = target
                    elif isinstance(target_max, int):
                        if target_min <= sec_num <= target_max:
                            # Section fits in range - score by specificity (smaller range = better)
                            range_size = target_max - target_min
                            score = 1000 - range_size  # Smaller range = higher score

                            # SPECIAL HANDLING FOR ALPHANUMERIC SECTIONS (e.g., 42A, 42B)
                            # When parts overlap (e.g., PART IV: 39-42, PART IVA: 42-42):
                            # - Plain numeric sections (42) should prefer broader range (PART IV)
                            # - Alphanumeric sections (42A, 42B) should prefer narrow range (PART IVA)
                            has_alpha_suffix = bool(re.match(r'^\d+[A-Za-z]+', section_number_str))
                            is_narrow_range = (target_min == target_max)

                            if is_narrow_range and has_alpha_suffix:
                                # Alphanumeric section + narrow range = boost score
                                score += 100  # Prefer narrow range for 42A, 42B
                                if self.debug_mode and section_number_str in ['42A', '42B']:
                                    print(f"  DEBUG: Section {section_number_str} (alphanumeric) matching narrow range [{target_min}-{target_max}], boosting score to {score}")
                            elif is_narrow_range and not has_alpha_suffix:
                                # Plain numeric section + narrow range = reduce score
                                score -= 100  # Prefer broader range for plain 42
                                if self.debug_mode and section_number_str in ['42', '42A', '42B']:
                                    print(f"  DEBUG: Section {section_number_str} (plain numeric) matching narrow range [{target_min}-{target_max}], reducing score to {score}")

                            if score > best_score:
                                best_score = score
                                best_target = target

            if best_target:
                section_assignments[section_number_str] = best_target
                if self.debug_mode and ("12A" in section_number_str or "12a" in section_number_str.lower()):
                    print(f"  DEBUG: Section {section_number_str} assigned to {best_target.get('type')} {best_target.get('chapter_number', '')} [{best_target.get('min')}-{best_target.get('max')}]")
                # Debug sections 92-101, 39, 18, and 373
                if self.debug_mode and sec_num and (92 <= sec_num <= 101 or sec_num == 39 or sec_num == 18 or sec_num == 373):
                    target_desc = f"{best_target.get('part_number', '')}"
                    if best_target.get('chapter_number'):
                        target_desc += f" / {best_target.get('chapter_number')}"
                    print(f"  DEBUG: Section {section_number_str} (num={sec_num}) assigned to {target_desc} [{best_target.get('min')}-{best_target.get('max')}], score={best_score}")
            else:
                unassigned_sections.append(section)
                # Debug unassigned sections 92-101, 39, 18, and 373
                if self.debug_mode and sec_num and (92 <= sec_num <= 101 or sec_num == 39 or sec_num == 18 or sec_num == 373):
                    print(f"  DEBUG: Section {section_number_str} (num={sec_num}) UNASSIGNED - no matching target")
                if self.debug_mode and ("12A" in section_number_str or "12a" in section_number_str.lower()):
                    print(f"  DEBUG: Section {section_number_str} is UNASSIGNED (sec_num={sec_num}, no matching target)")

        
        if self.debug_mode:
            print(f"\n=== SECTION ASSIGNMENTS ===")
            print(f"  Assigned: {len(section_assignments)}")
            print(f"  Unassigned: {len(unassigned_sections)}")

            # DEBUG: Check sections 23-79 assignments
            assigned_23_79 = [num for num, target in section_assignments.items() if _extract_section_num({'number': num}) is not None and 23 <= _extract_section_num({'number': num}) <= 79]
            unassigned_23_79 = [s for s in unassigned_sections if _extract_section_num(s) is not None and 23 <= _extract_section_num(s) <= 79]
            print(f"DEBUG: Sections 23-79 assigned: {len(assigned_23_79)}, unassigned: {len(unassigned_23_79)}")
            if assigned_23_79:
                print(f"DEBUG: Assigned sections 23-79: {sorted([int(re.match(r'^(\d+)', num).group(1)) for num in assigned_23_79 if re.match(r'^(\d+)', num)])[:10]}...")
            if unassigned_23_79:
                print(f"DEBUG: Unassigned sections 23-79: {sorted([_extract_section_num(s) for s in unassigned_23_79])[:10]}...")
        
        # ========== STEP 4: BUILD FINAL STRUCTURE ==========
        
        final_parts = []
        parts_dict = {}
        
        # Group sections by their targets
        target_sections = {}
        
        for section in all_sections:
            section_key = section.get("number", "")
            target = section_assignments.get(section_key)

            # Debug section 373
            if section_key == "373" and self.debug_mode:
                print(f"  DEBUG: Building structure for section 373")
                print(f"    target: {target}")
                if target:
                    print(f"    part_number: {target.get('part_number')}")
                    print(f"    chapter_number: {target.get('chapter_number')}")
                    print(f"    type: {target.get('type')}")

            if target:
                target_key = (
                    target.get("part_number", "MAIN PART"),
                    target.get("chapter_number"),
                    target["type"]
                )
            else:
                target_key = ("MAIN PART", None, "unassigned")

            if target_key not in target_sections:
                target_sections[target_key] = {"target": target, "sections": []}

            target_sections[target_key]["sections"].append(section)

            # Debug section 373
            if section_key == "373" and self.debug_mode:
                print(f"    target_key: {target_key}")
        
        # DEBUG: Check which target_keys contain sections 23-79
        if self.debug_mode:
            print(f"\n=== DEBUG: Checking target_keys for sections 23-79 ===")
            for target_key, data in target_sections.items():
                part_num, ch_num, t_type = target_key
                sec_23_79 = [_extract_section_num(s) for s in data["sections"] if _extract_section_num(s) and 23 <= _extract_section_num(s) <= 79]
                if sec_23_79:
                    print(f"  {part_num} / {ch_num} / {t_type}: {len(sec_23_79)} sections in range 23-79 (sample: {sorted(sec_23_79)[:10]})")

        # Build parts structure
        for target_key, data in target_sections.items():
            part_number, chapter_number, target_type = target_key
            target = data["target"]
            sections = _sort_sections(data["sections"])
            
            # Ensure part exists
            if part_number not in parts_dict:
                # Find part title
                part_title = None
                if target:
                    part_title = target.get("part_title")
                
                parts_dict[part_number] = {
                    "number": part_number,
                    "title": part_title,
                    "section_groups": []
                }
            
            part_obj = parts_dict[part_number]
            
            if target_type == "chapter_in_part":
                # Add to chapter within part
                chapter_title = target.get("chapter_title") if target else None
                
                # Find or create chapter group
                chapter_group = None
                for group in part_obj["section_groups"]:
                    if group.get("number") == chapter_number:
                        chapter_group = group
                        break
                
                if not chapter_group:
                    chapter_group = {
                        "number": chapter_number,
                        "title": chapter_title,
                        "sections": []
                    }
                    part_obj["section_groups"].append(chapter_group)
                
                # Check for subchapters
                if target and target.get("groups"):
                    for group in target["groups"]:
                        group_min = group.get("min")
                        group_max = group.get("max")
                        
                        if isinstance(group_min, int) and isinstance(group_max, int):
                            group_sections = [s for s in sections 
                                            if group_min <= (_extract_section_num(s) or 0) <= group_max]
                            
                            if group_sections:
                                # Skip if SubChapter title matches Chapter title
                                subch_title = (group.get("title") or "").strip().upper()
                                ch_title = (chapter_group.get("title") or "").strip().upper()
                                ch_number = (chapter_group.get("number") or "").strip().upper()

                                # Skip duplicate titles
                                if subch_title and (subch_title == ch_title or subch_title == ch_number):
                                    if self.debug_mode:
                                        print(f"  Skipping SubChapter '{subch_title}' - matches Chapter title/number")
                                    # Add sections directly to chapter instead
                                    chapter_group.setdefault("sections", []).extend(group_sections)
                                else:
                                    if "SubChapter" not in chapter_group:
                                        chapter_group["SubChapter"] = []

                                    chapter_group["SubChapter"].append({
                                        "title": group.get("title"),
                                        "section_groups": [{"title": None, "sections": group_sections}]
                                    })
                                
                                # Remove these sections from main chapter sections
                                for gs in group_sections:
                                    if gs in sections:
                                        sections.remove(gs)
                
                # Add remaining sections to chapter
                chapter_group["sections"].extend(sections)
            
            elif target_type == "part_direct":
                # Add directly to part (no chapter)
                default_group = None
                for group in part_obj["section_groups"]:
                    if group.get("number") is None:
                        default_group = group
                        break

                if not default_group:
                    default_group = {
                        "number": None,
                        "title": None,
                        "sections": []
                    }
                    part_obj["section_groups"].append(default_group)

                # DEBUG: Check if sections 23-79 are being added
                if self.debug_mode and part_number == "PART III":
                    sec_nums = [_extract_section_num(s) for s in sections]
                    sec_23_79 = [n for n in sec_nums if n and 23 <= n <= 79]
                    print(f"DEBUG: Adding {len(sections)} sections to PART III, including {len(sec_23_79)} in range 23-79")

                # DEBUG: Check if section 373 is being added
                if self.debug_mode:
                    sec_nums = [_extract_section_num(s) for s in sections]
                    if 373 in sec_nums:
                        print(f"[PART_DIRECT] Adding section 373 to part '{part_number}' default group")
                        print(f"  Total sections being added: {len(sections)}")
                        print(f"  Section numbers: {sec_nums}")

                default_group["sections"].extend(sections)
            
            elif target_type == "standalone_chapter":
                # Add as chapter in MAIN PART
                chapter_title = target.get("chapter_title") if target else None
                
                chapter_group = {
                    "number": chapter_number,
                    "title": chapter_title,
                    "sections": sections
                }
                part_obj["section_groups"].append(chapter_group)
            
            else:  # unassigned
                # Add to default group
                default_group = None
                for group in part_obj["section_groups"]:
                    if group.get("number") is None and group.get("title") is None:
                        default_group = group
                        break
                
                if not default_group:
                    default_group = {
                        "number": None,
                        "title": None,
                        "sections": []
                    }
                    part_obj["section_groups"].append(default_group)
                
                default_group["sections"].extend(sections)
        
        # Convert to final list and clean up
        final_parts = list(parts_dict.values())
        
        # Remove empty groups
        for part in final_parts:
            valid_groups = []
            for group in part["section_groups"]:
                has_direct_sections = bool(group.get("sections"))
                has_subchapter_sections = any(
                    sg.get("sections") for sc in group.get("SubChapter", [])
                    for sg in sc.get("section_groups", [])
                )
                
                if has_direct_sections or has_subchapter_sections:
                    # Sort sections within group
                    if group.get("sections"):
                        group["sections"] = _sort_sections(group["sections"])
                    
                    # Sort sections within subchapters
                    if group.get("SubChapter"):
                        for sc in group["SubChapter"]:
                            for sg in sc.get("section_groups", []):
                                if sg.get("sections"):
                                    sg["sections"] = _sort_sections(sg["sections"])
                    
                    valid_groups.append(group)
            
            part["section_groups"] = valid_groups
        
        # Remove empty parts
        final_parts = [part for part in final_parts if part["section_groups"]]
        
        # Sort parts: MAIN PART first, then others by minimum section number
        def _part_sort_key(part):
            # MAIN PART always comes first
            if part["number"] == "MAIN PART":
                return (0, 0, "")

            # Get the minimum section number from all sections in this part (recursively)
            def get_min_section_recursive(groups):
                min_sec = float('inf')
                for group in groups:
                    # Check sections at this level
                    for section in group.get("sections", []):
                        try:
                            section_num = int(section.get("number", 999))
                            min_sec = min(min_sec, section_num)
                        except (ValueError, TypeError):
                            pass

                    # Check SubChapter sections recursively
                    for subchapter in group.get("SubChapter", []):
                        sub_min = get_min_section_recursive(subchapter.get("section_groups", []))
                        min_sec = min(min_sec, sub_min)

                return min_sec

            min_section = get_min_section_recursive(part.get("section_groups", []))

            # Sort other parts by their minimum section number
            if min_section == float('inf'):
                # No sections found, try to extract roman numeral from part name
                m = re.search(r'PART\s+([IVXLCDM]+)', part["number"], re.I)
                if m:
                    return (1, 1000, self._roman_to_int(m.group(1)))
                return (1, 2000, part["number"])

            return (1, min_section, part["number"])
        
        def _chapter_sort_key(group):
            number = group.get("number")
            if number is None:
                return (0, 0)  # Default groups first

            number_str = str(number)
            # Match roman numerals after CHAPTER/PART keywords, or standalone
            roman_match = re.search(r'(?:CHAPTER|PART)\s+([IVXLCDM]+)|^([IVXLCDM]+)$', number_str, re.I)
            if roman_match:
                roman_num = roman_match.group(1) or roman_match.group(2)
                return (1, self._roman_to_int(roman_num))
            else:
                return (2, 999)
        
        # Fix: Move misplaced early sections to MAIN PART
        # Sections that appear before the first PART should be in MAIN PART
        def fix_misplaced_sections(parts):
            # Collect all sections from all parts with their part index
            all_sections_with_parts = []
            main_part_idx = None

            for idx, part in enumerate(parts):
                if part["number"] == "MAIN PART":
                    main_part_idx = idx
                    continue

                for group in part.get("section_groups", []):
                    for sec in group.get("sections", []):
                        try:
                            num = int(sec.get("number", 999))
                            all_sections_with_parts.append((num, idx, sec))
                        except:
                            pass

            if not all_sections_with_parts:
                return

            # Sort by section number
            all_sections_with_parts.sort(key=lambda x: x[0])

            # Find the second minimum section number and its part
            # (first minimum is section 1, second tells us where PART I starts)
            if len(all_sections_with_parts) < 2:
                return

            second_min_section = all_sections_with_parts[1][0]

            # Move any sections less than second_min_section to MAIN PART
            sections_to_move = []
            for idx, part in enumerate(parts):
                if part["number"] == "MAIN PART":
                    continue

                for group in part.get("section_groups", []):
                    sections_to_remove = []
                    for sec_idx, sec in enumerate(group.get("sections", [])):
                        try:
                            num = int(sec.get("number", 999))
                            # Move sections before the second part starts
                            if num < second_min_section:
                                sections_to_move.append(sec)
                                sections_to_remove.append(sec_idx)
                        except:
                            pass

                    # Remove sections in reverse order to maintain indices
                    for sec_idx in reversed(sections_to_remove):
                        group["sections"].pop(sec_idx)

            # Add moved sections to MAIN PART
            if sections_to_move:
                # Ensure MAIN PART exists
                if main_part_idx is None:
                    parts.insert(0, {
                        "number": "MAIN PART",
                        "title": None,
                        "section_groups": [{"number": None, "title": None, "sections": []}]
                    })
                    main_part_idx = 0

                # Ensure MAIN PART has at least one section_group
                if not parts[main_part_idx].get("section_groups"):
                    parts[main_part_idx]["section_groups"] = [{"number": None, "title": None, "sections": []}]

                # Add sections to the first group in MAIN PART
                parts[main_part_idx]["section_groups"][0].setdefault("sections", []).extend(sections_to_move)

                # Sort sections in MAIN PART
                parts[main_part_idx]["section_groups"][0]["sections"].sort(
                    key=lambda s: int(s.get("number", 999)) if s.get("number", "").isdigit() else 999
                )

        fix_misplaced_sections(final_parts)

        final_parts.sort(key=_part_sort_key)

        for part in final_parts:
            part["section_groups"].sort(key=_chapter_sort_key)
        
        if self.debug_mode:
            print(f"\n=== FINAL STRUCTURE (ENHANCED) ===")
            for part in final_parts:
                total_sections = 0
                print(f"\n{part['number']}:")
                for group in part["section_groups"]:
                    group_sections = len(group.get("sections", []))
                    subchapter_sections = sum(
                        len(sg.get("sections", []))
                        for sc in group.get("SubChapter", [])
                        for sg in sc.get("section_groups", [])
                    )
                    total_sections += group_sections + subchapter_sections
                    
                    group_name = group.get("number") or "[Default]"
                    print(f"  └─ {group_name}: {group_sections} direct sections")
                    
                    if group.get("SubChapter"):
                        for sc in group["SubChapter"]:
                            sc_sections = sum(len(sg.get("sections", [])) for sg in sc.get("section_groups", []))
                            print(f"     └─ '{sc.get('title')}': {sc_sections} sections")
                
                print(f"  Total: {total_sections} sections")

            # DEBUG: Check sections 23-79 in final parts
            final_sections_23_79 = []
            for part in final_parts:
                for group in part.get("section_groups", []):
                    for sec in group.get("sections", []):
                        sec_num = _extract_section_num(sec)
                        if sec_num is not None and 23 <= sec_num <= 79:
                            final_sections_23_79.append(sec_num)
            print(f"DEBUG: Sections 23-79 in final_parts: {len(final_sections_23_79)} (sample: {sorted(final_sections_23_79)[:10]}...)")

        return final_parts
    def _extract_subchapters_for_chapter(self, chapter_number, full_text, chapter_min, chapter_max):
        """
        Extract SubChapters within a specific chapter's text range.
        Returns list of subchapter definitions with titles and section ranges.
        """
        import re
        
        if not full_text or not chapter_number:
            return []
        
        # Find chapter text slice
        chapter_text = self._extract_chapter_text_slice(full_text, chapter_number)
        if not chapter_text:
            return []
        
        # Find SubChapter headers using multiple patterns
        subchapter_patterns = [
            # Explicit SubChapter declarations
            re.compile(r'(?m)^\s*(SUB[\s\-]*CHAPTER\s+[IVXLCDM]+(?:\s*[-–—:]?\s*[A-Z\s,\-\(\)&\'\/\.]+)?)\s*$', re.I),
            
            # All-caps legal headings (2+ words, no PART/CHAPTER)
            re.compile(r'(?m)^\s*([A-Z][A-Z\s,\-\(\)&\'\/\.]{10,}[A-Z])\s*$'),
            
            # Common legal section headers
            re.compile(r'(?m)^\s*((?:PRELIMINARY|GENERAL|SPECIAL|SUPPLEMENTARY|TRANSITIONAL|FINAL|MISCELLANEOUS)\s+PROVISIONS?)\s*$', re.I),
            re.compile(r'(?m)^\s*(DEFINITIONS?\s*(?:AND\s+)?INTERPRETATIONS?)\s*$', re.I),
            re.compile(r'(?m)^\s*(POWERS?\s+AND\s+DUTIES)\s*$', re.I),
            re.compile(r'(?m)^\s*(ENFORCEMENT\s+AND\s+PENALTIES)\s*$', re.I),
        ]
        
        subchapter_matches = []
        
        for pattern in subchapter_patterns:
            for match in pattern.finditer(chapter_text):
                title = match.group(1).strip()
                
                # Validate it's not a PART or CHAPTER
                if re.match(r'^(PART|CHAPTER)\s+', title, re.I):
                    continue
                
                # Must be substantial content
                if len(title) < 5 or len(title.split()) < 2:
                    continue
                
                subchapter_matches.append({
                    "title": self.clean_text(title),
                    "position": match.start(),
                    "match_text": title
                })
        
        if not subchapter_matches:
            return []
        
        # Sort by position and assign ranges
        subchapter_matches.sort(key=lambda x: x["position"])
        
        # Find section numbers in chapter text
        section_pattern = re.compile(r'(?m)^\s*(\d+)[A-Za-z]*\s*\.')
        all_sections_in_chapter = []
        
        for match in section_pattern.finditer(chapter_text):
            sec_num = int(match.group(1))
            if chapter_min <= sec_num <= chapter_max:
                all_sections_in_chapter.append((match.start(), sec_num))
        
        # Assign section ranges to subchapters
        subchapters = []
        
        for i, subchapter in enumerate(subchapter_matches):
            start_pos = subchapter["position"]
            end_pos = subchapter_matches[i + 1]["position"] if i + 1 < len(subchapter_matches) else len(chapter_text)
            
            # Find sections in this subchapter's range
            sections_in_range = [
                sec_num for pos, sec_num in all_sections_in_chapter
                if start_pos <= pos < end_pos
            ]
            
            if sections_in_range:
                subchapters.append({
                    "title": subchapter["title"],
                    "min": min(sections_in_range),
                    "max": max(sections_in_range)
                })
        
        return subchapters

    def _extract_chapter_text_slice(self, full_text, chapter_number):
        """Extract the text belonging to a specific chapter."""
        import re
        
        # Find chapter start
        chapter_patterns = [
            rf'(?m)^\s*{re.escape(chapter_number)}\b',
            rf'(?m)^\s*CHAPTER\s+{re.escape(chapter_number.split()[-1])}\b',
        ]
        
        chapter_start = None
        for pattern in chapter_patterns:
            match = re.search(pattern, full_text, re.I)
            if match:
                chapter_start = match.start()
                break
        
        if chapter_start is None:
            return ""
        
        # Find next chapter/part to determine end
        next_header = re.search(
            r'(?m)^\s*(?:CHAPTER|PART)\s+[A-Z0-9IVXLCDM]+\b',
            full_text[chapter_start + 100:],  # Skip current chapter
            re.I
        )
        
        chapter_end = chapter_start + 100 + next_header.start() if next_header else len(full_text)
        
        return full_text[chapter_start:chapter_end]

    def _get_chapter_sort_value(self, chapter_num):
        """Get sort value for chapter ordering."""
        if not chapter_num:
            return 0
        
        import re
        m = re.search(r'CHAPTER\s+([IVXLCDM]+)', str(chapter_num), re.I)
        if m:
            return self._roman_to_int(m.group(1))
        
        m = re.search(r'CHAPTER\s+(\d+)', str(chapter_num), re.I)
        if m:
            return int(m.group(1))
        
        return 999
    
    def _get_chapter_sort_value(self, chapter_num):
        """Get sort value for chapter ordering."""
        if not chapter_num:
            return 0
        
        import re
        m = re.search(r'CHAPTER\s+([IVXLCDM]+)', str(chapter_num), re.I)
        if m:
            return self._roman_to_int(m.group(1))
        
        m = re.search(r'CHAPTER\s+(\d+)', str(chapter_num), re.I)
        if m:
            return int(m.group(1))
        
        return 999
    def _calculate_subchapter_confidence(self, header_text: str, full_text: str, position: int) -> float:
        """
        Calculate confidence that a header is a subchapter using multiple factors.
        """
        import re
        
        confidence = 0.0
        
        # Factor 1: Text characteristics
        if header_text.isupper():
            confidence += 0.15
        
        word_count = len(header_text.split())
        if 2 <= word_count <= 8:
            confidence += 0.15
        elif 8 < word_count <= 15:
            confidence += 0.05
        
        # Factor 2: Legal terminology
        legal_terms = [
            'GENERAL', 'SPECIAL', 'PROCEDURE', 'PROVISIONS', 'ENFORCEMENT',
            'PENALTIES', 'APPEALS', 'JURISDICTION', 'POWERS', 'DUTIES',
            'RIGHTS', 'APPLICATION', 'SERVICE', 'ADMINISTRATION', 'REGISTRATION',
            'DEFINITIONS', 'INTERPRETATION', 'MISCELLANEOUS', 'OFFENCES',
            'PRELIMINARY', 'SUPPLEMENTARY', 'TRANSITIONAL', 'FINAL', 'PROCEEDINGS',
            'EVIDENCE', 'WITNESSES', 'DOCUMENTS', 'ORDERS', 'NOTICES', 'FORMS'
        ]
        
        header_upper = header_text.upper()
        term_count = sum(1 for term in legal_terms if term in header_upper)
        confidence += min(0.25, term_count * 0.15)
        
        # Factor 3: Starts with "OF" or contains "AND", "OR"
        if header_upper.startswith('OF '):
            confidence += 0.15
        if ' AND ' in header_upper or ' OR ' in header_upper:
            confidence += 0.1
        
        # Factor 4: Followed by sections
        after_text = full_text[position:position + 500] if position < len(full_text) - 500 else full_text[position:]
        if re.search(r'\n\s*\d+[A-Za-z]*\s*\.', after_text):
            confidence += 0.2
        
        # Factor 5: Not a section number or other structural element
        if re.match(r'^\s*\d+[A-Za-z]*\s*\.', header_text):
            confidence -= 0.5
        if re.match(r'^(CHAPTER|PART|SCHEDULE|APPENDIX)\s+', header_text, re.I):
            confidence -= 0.5
        
        # Factor 6: Pattern repetition (similar headers nearby)
        before_text = full_text[max(0, position-1000):position]
        similar_pattern = re.compile(r'(?m)^\s*[A-Z][A-Z\s,\-\(\)&\'\/\.]{3,}[A-Z]\s*$')
        similar_count = len(similar_pattern.findall(before_text))
        if similar_count > 0:
            confidence += min(0.15, similar_count * 0.05)
        
        return max(0.0, min(1.0, confidence))


    def _deduplicate_and_filter_groups(self, groups: list) -> list:
        """
        Remove duplicate groups and filter out low-confidence ones.
        Prefers higher confidence when duplicates exist.
        """
        if not groups:
            return []
        
        # Sort by position and confidence
        groups.sort(key=lambda x: (x['start_pos'], -x.get('confidence', 0)))
        
        filtered = []
        last_pos = -1
        
        for group in groups:
            # Skip if too close to last group (likely duplicate)
            if group['start_pos'] - last_pos < 10:
                # Keep the one with higher confidence
                if filtered and filtered[-1]['start_pos'] == group['start_pos']:
                    if group.get('confidence', 0) > filtered[-1].get('confidence', 0):
                        filtered[-1] = group
                continue
            
            # Skip low confidence unless it's an explicit subchapter
            if group.get('confidence', 0) < 0.5 and group.get('type') != 'explicit':
                continue
            
            # Skip if title is too generic or invalid
            if group.get('title'):
                title = group['title'].strip()
                if len(title) < 3 or title in ['THE', 'A', 'AN', 'AND', 'OR', 'OF']:
                    continue
            
            filtered.append(group)
            last_pos = group['start_pos']
        
        return filtered


    def _is_valid_subchapter_title(self, title: str) -> bool:
        """
        Enhanced validation for subchapter titles using multiple criteria.
        """
        import re
        
        if not title or len(title.strip()) < 3:
            return False
        
        title = title.strip()
        
        # Exclude main structural elements
        if re.match(r'^(PART|CHAPTER|SCHEDULE|TITLE|APPENDIX|ANNEX|BOOK)\s+', title, re.I):
            return False
        
        # Exclude section numbers
        if re.match(r'^\s*\d+[A-Za-z]*\s*\.', title):
            return False
        
        # Exclude single words unless they're significant legal terms
        if len(title.split()) == 1:
            single_word_valid = [
                'PRELIMINARY', 'GENERAL', 'DEFINITIONS', 'INTERPRETATION',
                'ADMINISTRATION', 'ENFORCEMENT', 'PENALTIES', 'OFFENCES',
                'MISCELLANEOUS', 'TRANSITIONAL', 'SUPPLEMENTARY', 'SCHEDULES'
            ]
            if title.upper() not in single_word_valid:
                return False
        
        # Must be mostly letters (not numbers or symbols)
        letter_count = sum(1 for c in title if c.isalpha())
        if letter_count < len(title) * 0.6:
            return False
        
        # Length constraints
        if len(title) > 100:
            return False
        
        # Positive indicators - FIX: Convert regex match to boolean
        positive_indicators = [
            title.isupper(),  # All caps
            title.startswith('OF '),  # Common legal pattern
            any(term in title.upper() for term in [
                'PROVISION', 'PROCEDURE', 'GENERAL', 'SPECIAL', 'APPLICATION',
                'JURISDICTION', 'POWER', 'DUTY', 'RIGHT', 'APPEAL', 'ENFORCEMENT',
                'PENALTY', 'OFFENCE', 'ADMINISTRATION', 'REGISTRATION', 'SERVICE'
            ]),
            ' AND ' in title or ' OR ' in title,  # Compound titles
            bool(re.match(r'^[A-Z][A-Z\s,\-\(\)&\'\/\.]+[A-Z]$', title))  # FIX: Convert to bool
        ]
        
        # Need at least 2 positive indicators
        if sum(positive_indicators) >= 2:
            return True
        
        # Special cases for known patterns
        known_patterns = [
            re.compile(r'^OF\s+[A-Z]', re.I),
            re.compile(r'PROCEEDINGS?\s*(?:IN|OF|FOR)?', re.I),
            re.compile(r'(?:CIVIL|CRIMINAL|SPECIAL)\s+(?:PROCEDURE|JURISDICTION)', re.I),
            re.compile(r'(?:ORIGINAL|APPELLATE|REVISIONAL)\s+JURISDICTION', re.I),
        ]
        
        for pattern in known_patterns:
            if pattern.search(title):
                return True
        
        return False


    def _extract_title_from_context(self, text: str, start: int, end: int) -> str:
        """
        Extract title from context around a match.
        """
        # Look at the rest of the line
        line_end = text.find('\n', end)
        if line_end == -1:
            line_end = len(text)
        
        line = text[start:line_end].strip()
        
        # Check for title after delimiter
        for delimiter in ['-', '–', '—', ':', '.-']:
            if delimiter in line:
                parts = line.split(delimiter, 1)
                if len(parts) == 2:
                    title = self.clean_text(parts[1].strip())
                    if title and len(title) > 2:
                        return title
        
        # Look at next few lines
        next_text = text[line_end+1:line_end+500]
        lines = next_text.split('\n')
        
        for line in lines[:5]:
            line = line.strip()
            
            # Skip empty or structural elements
            if not line or re.match(r'^(CHAPTER|PART|SCHEDULE|\d+\.)', line, re.I):
                break
            
            # This could be the title
            if len(line) > 2:
                return self.clean_text(line)
        
        return None


    def _organize_structure(self, items):
        """
        Organize items into proper hierarchy without duplicates.
        Deduplicates chapters and merges their information.
        """
        import re
        
        if self.debug_mode:
            print(f"\n=== ORGANIZING STRUCTURE ===")
            print(f"  Total items found: {len(items)}")
            chapter_counts = {}
            for item in items:
                if item['type'] == 'CHAPTER':
                    num = item['number']
                    chapter_counts[num] = chapter_counts.get(num, 0) + 1
            print(f"  Chapter duplicates: {chapter_counts}")
        
        # First, deduplicate chapters and parts
        parts_dict = {}
        chapters_dict = {}
        
        for item in items:
            if item['type'] == 'PART':
                part_num = item['number']
                if part_num not in parts_dict:
                    parts_dict[part_num] = item
                else:
                    # Merge - prefer the one with more information
                    existing = parts_dict[part_num]
                    existing = self._merge_items(existing, item)
                    parts_dict[part_num] = existing
                    
            elif item['type'] == 'CHAPTER':
                chapter_num = item['number']
                if chapter_num not in chapters_dict:
                    chapters_dict[chapter_num] = item
                else:
                    # Merge - prefer the one with more information
                    existing = chapters_dict[chapter_num]
                    existing = self._merge_items(existing, item)
                    chapters_dict[chapter_num] = existing
        
        if self.debug_mode:
            print(f"  After deduplication:")
            print(f"    Unique parts: {len(parts_dict)}")
            print(f"    Unique chapters: {len(chapters_dict)}")
        
        # Build structure
        structure = []
        
        # Create MAIN PART for orphaned chapters
        # IMPORTANT: Don't use 1000 as default max - it causes sections to be misrouted
        # Use 1 (just section 1/short title) or calculate from chapters
        main_part = {
            'number': 'MAIN PART',
            'title': None,
            'min': 1,
            'max': 1,  # Default to just section 1, will be updated if there are orphaned chapters
            'chapters': []
        }
        
        # Sort chapters by their minimum section number
        sorted_chapters = sorted(chapters_dict.values(), 
                                key=lambda ch: (ch.get('min', 999), 
                                            self._extract_chapter_order(ch['number'])))
        
        # If we have parts, assign chapters to them
        if parts_dict:
            for part_num, part in parts_dict.items():
                part_obj = {
                    'number': part_num,
                    'title': part.get('title'),
                    'min': part.get('min'),
                    'max': part.get('max'),
                    'chapters': []
                }
                structure.append(part_obj)

            # FIX PART GAPS BEFORE ASSIGNING CHAPTERS
            # Sort structure by min section number (handle None values)
            structure.sort(key=lambda p: p.get('min') if p.get('min') is not None else 999999)

            # Extend part ranges to cover gaps between parts
            for i in range(len(structure) - 1):
                current_part = structure[i]
                next_part = structure[i + 1]

                current_max = current_part.get('max')
                next_min = next_part.get('min')

                # Skip if either value is None
                if current_max is None or next_min is None:
                    continue

                if current_max < next_min - 1:
                    # There's a gap - extend current part's max to cover it
                    gap_size = next_min - current_max - 1
                    new_max = next_min - 1

                    if self.debug_mode:
                        print(f"  [ORGANIZE] Fixing gap: Extending {current_part['number']} max from {current_max} to {new_max} (covers {gap_size} sections)")

                    current_part['max'] = new_max

            # Assign chapters to parts based on section ranges
            for chapter in sorted_chapters:
                assigned = False
                ch_min = chapter.get('min')
                ch_max = chapter.get('max')
                
                if ch_min is not None and ch_max is not None:
                    # Find best matching part
                    best_part = None
                    best_overlap = 0
                    
                    for part_obj in structure:
                        part_min = part_obj.get('min')
                        part_max = part_obj.get('max')
                        
                        if part_min and part_max:
                            # Calculate overlap
                            overlap_start = max(ch_min, part_min)
                            overlap_end = min(ch_max, part_max)
                            overlap = max(0, overlap_end - overlap_start + 1)
                            
                            if overlap > best_overlap:
                                best_overlap = overlap
                                best_part = part_obj
                    
                    if best_part:
                        best_part['chapters'].append(chapter)
                        assigned = True
                
                if not assigned:
                    main_part['chapters'].append(chapter)
        else:
            # No parts, put all chapters in MAIN PART
            main_part['chapters'] = sorted_chapters
        
        # Add MAIN PART if it has chapters
        if main_part['chapters']:
            # Update MAIN PART range based on its chapters
            all_mins = [ch.get('min') for ch in main_part['chapters'] if ch.get('min')]
            all_maxs = [ch.get('max') for ch in main_part['chapters'] if ch.get('max')]
            
            if all_mins:
                main_part['min'] = min(all_mins)
            if all_maxs:
                main_part['max'] = max(all_maxs)
            
            structure.insert(0, main_part)
        
        # Sort chapters within each part
        for part in structure:
            part['chapters'].sort(key=lambda ch: (
                ch.get('min', 999),
                self._extract_chapter_order(ch['number'])
            ))
        
        if self.debug_mode:
            print(f"\n=== FINAL STRUCTURE ===")
            for part in structure:
                print(f"  {part['number']}:")
                for ch in part['chapters']:
                    ch_range = f"[{ch.get('min', '?')}-{ch.get('max', '?')}]"
                    groups_count = len(ch.get('groups', []))
                    print(f"    - {ch['number']} {ch_range} with {groups_count} subchapters")
        
        return structure
    
    def _merge_items(self, existing, new):
        """
        Merge two items (chapters or parts), preferring the one with more information.
        """
        # Score items by completeness
        def score_item(item):
            score = 0
            if item.get('title'):
                score += 10
            if item.get('min') is not None:
                score += 5
            if item.get('max') is not None:
                score += 5
            if item.get('groups'):
                score += len(item['groups']) * 2
            return score
        
        existing_score = score_item(existing)
        new_score = score_item(new)
        
        # Use the better one as base
        if new_score > existing_score:
            result = new.copy()
            fallback = existing
        else:
            result = existing.copy()
            fallback = new
        
        # Fill in missing fields from the other
        if not result.get('title') and fallback.get('title'):
            result['title'] = fallback['title']
        
        if result.get('min') is None and fallback.get('min') is not None:
            result['min'] = fallback['min']
        
        if result.get('max') is None and fallback.get('max') is not None:
            result['max'] = fallback['max']
        
        # Merge groups (subchapters)
        if fallback.get('groups'):
            if not result.get('groups'):
                result['groups'] = fallback['groups']
            else:
                # Merge groups by title
                existing_titles = {g['title'] for g in result['groups']}
                for group in fallback['groups']:
                    if group['title'] not in existing_titles:
                        result['groups'].append(group)
        
        return result
    def _extract_chapter_order(self, chapter_num):
        """
        Extract ordering value from chapter number.
        Handles: CHAPTER I, CHAPTER II, CHAPTER V AND VI, etc.
        """
        import re
        
        if not chapter_num:
            return 999
        
        # Handle "CHAPTER V AND VI" - use the first number
        if ' AND ' in chapter_num:
            chapter_num = chapter_num.split(' AND ')[0]
        
        # Extract roman numeral
        m = re.search(r'CHAPTER\s+([IVXLCDM]+)', chapter_num, re.I)
        if m:
            return self._roman_to_int(m.group(1))
        
        # Extract arabic numeral
        m = re.search(r'CHAPTER\s+(\d+)', chapter_num, re.I)
        if m:
            return int(m.group(1))
        
        return 999
    def _validate_section_assignment(self, section_num, container):
        """
        FIXED: Stricter validation to prevent incorrect routing.
        """
        if not isinstance(section_num, int):
            return False
        
        min_sec = container.get("min")
        max_sec = container.get("max")
        
        # Must have valid integer ranges
        if not (isinstance(min_sec, int) and isinstance(max_sec, int)):
            return False
        
        # Section must be strictly within the range (inclusive)
        is_valid = min_sec <= section_num <= max_sec
        
        if self.debug_mode and not is_valid:
            print(f"    Section {section_num} NOT in range [{min_sec}, {max_sec}] for {container.get('number')}")
        
        return is_valid
    def _extract_preliminary_sections_first(self, all_sections, textual_containers):
        """
        FIXED: More conservative approach - only extract truly preliminary sections.
        """
        import re
        
        # Get the minimum section number from all containers
        all_covered_sections = set()
        
        for container in textual_containers or []:
            min_sec = container.get("min")
            max_sec = container.get("max")
            
            if isinstance(min_sec, int) and isinstance(max_sec, int):
                all_covered_sections.update(range(min_sec, max_sec + 1))
        
        if not all_covered_sections:
            # No valid containers, treat everything as container sections
            return [], all_sections
        
        min_covered = min(all_covered_sections) if all_covered_sections else 1
        
        preliminary_sections = []
        container_sections = []
        
        for section in all_sections:
            sec_num_str = section.get("number", "")
            m = re.match(r'^(\d+)', sec_num_str)
            
            if m:
                sec_num = int(m.group(1))
                # Only consider sections BEFORE the first covered section as preliminary
                if sec_num < min_covered:
                    preliminary_sections.append(section)
                else:
                    container_sections.append(section)
            else:
                # Non-numeric sections are preliminary
                preliminary_sections.append(section)
        
        if self.debug_mode:
            print(f"\n=== PRELIMINARY EXTRACTION (FIXED) ===")
            print(f"  Min covered section: {min_covered}")
            print(f"  Preliminary sections: {[s.get('number') for s in preliminary_sections]}")
            print(f"  Container sections: {len(container_sections)}")
        
        return preliminary_sections, container_sections
    
   


       

    def _get_part_order_key(self, part):
        """Get ordering key for a part based on its first section number."""
        min_section = float('inf')
        
        for chapter in part.get("chapters", []):
            for section in chapter.get("sections", []):
                sec_num = self._extract_section_num(section)
                if sec_num is not None and sec_num < min_section:
                    min_section = sec_num
            
            # Fix: Check if subchapters is not None
            subchapters = chapter.get("subchapters")
            if subchapters:  # Only iterate if not None
                for subchapter in subchapters:
                    for section in subchapter.get("sections", []):
                        sec_num = self._extract_section_num(section)
                        if sec_num is not None and sec_num < min_section:
                            min_section = sec_num
        
        return (min_section, part.get("number", ""))
    def _extract_section_num(self, section):
        """Extract numeric part of section number."""
        import re
        if not section:
            return None
        num_str = section.get("number", "")
        m = re.match(r'^(\d+)', str(num_str))
        return int(m.group(1)) if m else None
    def _chapter_sort_key(self, chapter_num):
        """Convert chapter number to sortable value."""
        if not chapter_num:
            return 0
        
        import re
        
        # Handle roman numerals
        m = re.search(r'([IVXLCDM]+)', chapter_num)
        if m:
            return self._roman_to_int(m.group(1))
        
        # Handle arabic numerals
        m = re.search(r'(\d+)', chapter_num)
        if m:
            return int(m.group(1))
        
        return 999
    def _create_chapter_object(self, chapter_number, chapter_title, part_title, sections):
        """
        Helper to create a chapter object, ensuring title is null if it matches part title.
        """
        # Normalize and compare titles
        if (chapter_title and part_title and 
            chapter_title.strip().upper() == part_title.strip().upper()):
            chapter_title = None
        
        return {
            "number": chapter_number,
            "title": chapter_title,
            "sections": sections,
            "subchapters": []
        }
    def _extract_part_order(self, part_number):
        """Extract ordering value from part number for sorting."""
        import re
        
        if part_number == "MAIN PART":
            return -1
        
        # Handle roman numerals
        m = re.search(r'PART\s+([IVXLCDM]+)', part_number)
        if m:
            return self._roman_to_int(m.group(1))
        
        # Handle arabic numerals
        m = re.search(r'PART\s+(\d+)', part_number)
        if m:
            return int(m.group(1))
        
        return 999
    def _container_score(self, container):
        """Score a container for quality (prefer ones with more information)."""
        score = 0
        if container.get("title"):
            score += 10
        if isinstance(container.get("min"), int) and isinstance(container.get("max"), int):
            score += 5
            # Prefer narrower, more specific ranges
            range_size = container["max"] - container["min"]
            if range_size < 100:
                score += 5
        return score
    def _int_to_roman(self, num):
        """Convert integer to Roman numeral."""
        val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
        syms = ['M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I']
        roman_num = ''
        i = 0
        while num > 0:
            for _ in range(num // val[i]):
                roman_num += syms[i]
                num -= val[i]
            i += 1
        return roman_num
    def _part_sort_value(self, part_number):
        """Extract sort value from part number."""
        import re
        if "MAIN PART" in part_number:
            return (0, 0)
        m = re.search(r'PART\s+([IVXLCDM]+)', part_number)
        if m:
            return (1, self._roman_to_int(m.group(1)))
        return (2, 999)

    def _roman_to_int(self, s):
        """Convert Roman numeral to integer."""
        roman_values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
        total = 0
        prev_value = 0
        for char in reversed(s):
            value = roman_values.get(char, 0)
            if value < prev_value:
                total -= value
            else:
                total += value
            prev_value = value
        return total
    def assemble_chapters_into_parts_or_main(self, textual_containers, sections):
        """
        Build the final `parts` tree from textual_containers + a flat list of sections.

        Robust to messy inputs where some CHAPTER containers appear at top level
        instead of inside a PART. We:
        - Reattach stray CHAPTER containers to a PART using (a) range overlap,
            (b) otherwise the next PART that follows in the original list,
            (c) otherwise the last PART, (d) fallback MAIN PART.
        - Support Parts with no Chapters and Chapters with no SubChapters.
        - Keep MAIN PART and CHAPTER (UNCATEGORIZED) for spillover/early sections.
        """
        import re

        # ---------- helpers ----------
        def _roman_to_int(s):
            s = (s or "").upper().strip()
            if not s:
                return 0
            vals = {'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}
            total = 0; prev = 0
            for ch in reversed(s):
                v = vals.get(ch, 0)
                total = total - v if v < prev else total + v
                prev = v
            return total

        def _part_sort_value(part_number):
            if str(part_number).strip().upper() == "MAIN PART":
                return (-1, 0)
            m = re.search(r'\bPART\s+([IVXLCDM]+)\b', str(part_number or ""), re.I)
            return (0, _roman_to_int(m.group(1)) if m else 10**9)

        def _chapter_sort_value(ch_number):
            s = str(ch_number or "").strip().upper()
            if s == "CHAPTER (UNCATEGORIZED)":
                return (2, 10**9)
            m = re.search(r'\bCHAPTER\s+([IVXLCDM]+)\b', s, re.I)
            # push weird non-roman like "CHAPTER 105" after roman; UNCATEGORIZED last
            if not m:
                return (1, 10**9)
            return (0, _roman_to_int(m.group(1)))

        def _secnum_int(s):
            m = re.match(r'^\s*(\d+)', str(s or ""))
            return int(m.group(1)) if m else None

        def _ensure_chapter(part_obj, ch_number, ch_title=None, ch_min=None, ch_max=None):
            ch = {"number": ch_number, "title": ch_title, "subchapters": None, "sections": []}
            ch["_min"] = ch_min; ch["_max"] = ch_max
            part_obj["chapters"].append(ch)
            return ch

        def _ensure_subchapter(ch_obj, sc_number, sc_title=None, sc_min=None, sc_max=None):
            sc = {"number": sc_number, "title": sc_title, "sections": []}
            sc["_min"] = sc_min; sc["_max"] = sc_max
            ch_obj["subchapters"].append(sc)
            return sc

        def _overlap(a_min, a_max, b_min, b_max):
            if a_min is None or a_max is None or b_min is None or b_max is None:
                return 0
            lo = max(a_min, b_min); hi = min(a_max, b_max)
            return max(0, hi - lo)

        # ---------- normalize: separate PART vs CHAPTER containers ----------
        raw = textual_containers or []
        parts_seq = []      # keep order
        chapters_top = []   # stray top-level chapters
        main_part_stub = None

        for idx, tc in enumerate(raw):
            num = str(tc.get("number") or "").strip()
            entry = {
                "idx": idx,
                "number": num,
                "title": tc.get("title"),
                "min": tc.get("min"),
                "max": tc.get("max"),
                "groups": (tc.get("groups") or []),
            }
            if num.upper() == "MAIN PART":
                main_part_stub = entry
                parts_seq.append(entry)
            elif re.search(r'^\s*PART\b', num, re.I):
                parts_seq.append(entry)
            elif re.search(r'^\s*CHAPTER\b', num, re.I):
                chapters_top.append(entry)
            else:
                # Unknown container label -> treat as PART
                parts_seq.append(entry)

        # Ensure MAIN PART exists (for spillover)
        if not any(p["number"].strip().upper() == "MAIN PART" for p in parts_seq):
            # Try to infer rough min/max from early ranges
            all_ranges = [(tc.get("min"), tc.get("max")) for tc in raw]
            mins = [a for a, b in all_ranges if isinstance(a, int)]
            maxs = [b for a, b in all_ranges if isinstance(b, int)]
            mp = {
                "idx": -1,
                "number": "MAIN PART",
                "title": None,
                "min": (min(mins) if mins else None),
                "max": (min(maxs) if maxs else None),
                "groups": []
            }
            parts_seq.insert(0, mp)
            main_part_stub = mp

        # ---------- attach stray top-level CHAPTER containers to the best PART ----------
        # Strategy: (1) choose PART with maximum range overlap; tie -> earlier idx
        #           (2) if no positive overlap, choose next PART that follows in list order
        #           (3) else last PART
        #           (4) fallback MAIN PART
        assigned_groups = {p["number"]: [] for p in parts_seq}
        for ch in chapters_top:
            best = None
            best_ov = 0
            for p in parts_seq:
                ov = _overlap(ch["min"], ch["max"], p["min"], p["max"])
                if ov > best_ov:
                    best_ov = ov
                    best = p
            if best is None or best_ov == 0:
                # no overlap — use next PART in order
                next_parts = [p for p in parts_seq if p["idx"] >= ch["idx"]]
                best = next_parts[0] if next_parts else parts_seq[-1] if parts_seq else None
            if best is None:
                best = main_part_stub

            assigned_groups[best["number"]].append({
                "number": ch["number"],
                "title": ch.get("title"),
                "min": ch.get("min"),
                "max": ch.get("max"),
                "SubChapter": []  # no subchapters info available in top-level CHAPTER
            })

        # ---------- create concrete Parts/Chapters/SubChapters skeleton ----------
        parts_by_key = {}
        parts_list = []

        # Helper to iterate groups: own groups + assigned stray chapters
        def _iter_groups_for_part(p):
            own = p.get("groups") or []
            extra = assigned_groups.get(p["number"], [])
            return list(own) + list(extra)

        # MAIN PART should be present; ensure uncategorized chapter if MAIN PART has a range but no groups
        for p in parts_seq:
            p_key = p["number"]
            if p_key not in parts_by_key:
                po = {"number": p_key, "chapters": []}
                parts_by_key[p_key] = po
                parts_list.append(po)

            # If a PART has no groups, it's still ok (it may only collect via ranges later)
            groups = _iter_groups_for_part(p)
            if p_key.strip().upper() == "MAIN PART" and not groups and (p.get("min") is not None or p.get("max") is not None):
                groups = [{
                    "number": "CHAPTER (UNCATEGORIZED)",
                    "title": None,
                    "min": p.get("min"),
                    "max": p.get("max"),
                    "SubChapter": []
                }]

            # Create chapters and subchapters
            for g in groups:
                ch_obj = _ensure_chapter(
                    parts_by_key[p_key],
                    ch_number=g.get("number") or "CHAPTER (UNCATEGORIZED)",
                    ch_title=g.get("title"),
                    ch_min=g.get("min"),
                    ch_max=g.get("max"),
                )
                for sc in (g.get("SubChapter") or []):
                    _ensure_subchapter(
                        ch_obj,
                        sc_number=sc.get("number"),
                        sc_title=sc.get("title"),
                        sc_min=sc.get("min"),
                        sc_max=sc.get("max"),
                    )

        # Ensure MAIN PART exists in dicts for spillover
        if "MAIN PART" not in parts_by_key:
            parts_by_key["MAIN PART"] = {"number": "MAIN PART", "chapters": []}
            parts_list.insert(0, parts_by_key["MAIN PART"])

        # ---------- assign sections to (Part → Chapter → SubChapter) by numeric ranges ----------
        # Build index
        chapter_ranges = []
        for p in parts_list:
            for ch in p["chapters"]:
                chapter_ranges.append((p, ch))

        def _best_container_for_section(sec_n):
            best = None
            best_span = None
            for (p, ch) in chapter_ranges:
                cmin, cmax = ch.get("_min"), ch.get("_max")
                if isinstance(cmin, int) and isinstance(cmax, int) and cmin <= sec_n <= cmax:
                    span = cmax - cmin
                    # Prefer subchapter if tighter
                    best_sc = None; best_sc_span = None
                    for sc in ch.get("subchapters", []):
                        smin, smax = sc.get("_min"), sc.get("_max")
                        if isinstance(smin, int) and isinstance(smax, int) and smin <= sec_n <= smax:
                            sspan = smax - smin
                            if best_sc_span is None or sspan < best_sc_span:
                                best_sc_span = sspan; best_sc = sc
                    if best_sc is not None:
                        return (p, ch, best_sc)
                    if best_span is None or span < best_span:
                        best_span = span; best = (p, ch, None)
            return best

        def _ensure_uncategorized_chapter(part_obj, cmin=None, cmax=None):
            for ch in part_obj["chapters"]:
                if str(ch.get("number")).strip().upper() == "CHAPTER (UNCATEGORIZED)":
                    return ch
            return _ensure_chapter(part_obj, "CHAPTER (UNCATEGORIZED)", None, cmin, cmax)

        seen = set()
        for s in sections or []:
            key = (s.get("number") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            nval = _secnum_int(key)
            if nval is None:
                _ensure_uncategorized_chapter(parts_by_key["MAIN PART"]).setdefault("sections", []).append(s)
                continue
            dest = _best_container_for_section(nval)
            if dest:
                p, ch, sc = dest
                (sc or ch).setdefault("sections", []).append(s)
            else:
                _ensure_uncategorized_chapter(parts_by_key["MAIN PART"]).setdefault("sections", []).append(s)

        # ---------- sort + strip temp keys ----------
        def _sort_sections(lst):
            lst.sort(key=lambda ss: (_secnum_int(ss.get("number")) if _secnum_int(ss.get("number")) is not None else 10**9,
                                    str(ss.get("number") or "")))

        def _get_min_section(sections):
            """Get the minimum section number from a list of sections."""
            if not sections:
                return 10**9
            min_val = 10**9
            for s in sections:
                num = _secnum_int(s.get("number"))
                if num is not None and num < min_val:
                    min_val = num
            return min_val

        def _get_chapter_min_section(ch):
            """Get the minimum section number in a chapter (including subchapters)."""
            min_val = _get_min_section(ch.get("sections", []))
            for sc in ch.get("subchapters", []):
                sc_min = _get_min_section(sc.get("sections", []))
                if sc_min < min_val:
                    min_val = sc_min
            return min_val

        def _get_part_min_section(p):
            """Get the minimum section number in a part."""
            min_val = 10**9
            for ch in p.get("chapters", []):
                ch_min = _get_chapter_min_section(ch)
                if ch_min < min_val:
                    min_val = ch_min
            return min_val

        for p in parts_list:
            # Sort sections first
            for ch in p["chapters"]:
                for sc in ch.get("subchapters", []):
                    _sort_sections(sc.get("sections", []))
                    sc.pop("_min", None); sc.pop("_max", None)
                _sort_sections(ch.get("sections", []))
                ch.pop("_min", None); ch.pop("_max", None)
            # Sort chapters by their minimum section number
            p["chapters"].sort(key=lambda ch: _get_chapter_min_section(ch))

        # Sort parts by their minimum section number
        parts_list.sort(key=lambda po: _get_part_min_section(po))
        return parts_list
    def _chapter_sort_value(self, chapter_num):
        """Convert chapter number to sortable value."""
        if chapter_num == "PRELIMINARY":
            return 0
        
        m = re.search(r'CHAPTER\s+([IVXLCDM]+)', chapter_num, re.I)
        if m:
            roman = m.group(1)
            # Convert roman to number
            roman_values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
            value = 0
            i = 0
            while i < len(roman):
                if i + 1 < len(roman) and roman_values.get(roman[i], 0) < roman_values.get(roman[i+1], 0):
                    value += roman_values.get(roman[i+1], 0) - roman_values.get(roman[i], 0)
                    i += 2
                else:
                    value += roman_values.get(roman[i], 0)
                    i += 1
            return value
        return 999

    def _infer_chapter_section_range(self, chapter_num, full_text, all_chapters):
        """Infer the section range for a chapter based on its position in the text."""
        import re
        
        # Find where this chapter appears in the text
        chapter_pattern = re.compile(rf'\b{re.escape(chapter_num)}\b', re.I)
        chapter_match = chapter_pattern.search(full_text)
        
        if not chapter_match:
            return 1, 999  # Default range
        
        chapter_start = chapter_match.start()
        
        # Find the next chapter
        next_chapter_pos = len(full_text)
        chapter_list = sorted(all_chapters.keys(), key=lambda x: self._chapter_sort_value(x))
        current_idx = chapter_list.index(chapter_num) if chapter_num in chapter_list else -1
        
        if current_idx >= 0 and current_idx < len(chapter_list) - 1:
            next_chapter = chapter_list[current_idx + 1]
            next_match = re.search(rf'\b{re.escape(next_chapter)}\b', full_text[chapter_start + 100:], re.I)
            if next_match:
                next_chapter_pos = chapter_start + 100 + next_match.start()
        
        # Find all section numbers in this chapter's text range
        chapter_text = full_text[chapter_start:next_chapter_pos]
        section_pattern = re.compile(r'\b(\d+)[A-Za-z]*\.\s+')
        
        section_numbers = []
        for m in section_pattern.finditer(chapter_text):
            try:
                num = int(m.group(1))
                section_numbers.append(num)
            except:
                pass
        
        if section_numbers:
            return min(section_numbers), max(section_numbers)
        
        # If no sections found, use a default range based on chapter position
        if current_idx == 0:
            return 1, 100
        elif current_idx > 0:
            # Estimate based on position
            return (current_idx * 100) + 1, (current_idx + 1) * 100
        
        return 1, 999

    def _update_stats(self, num_str):
        """Update statistics for a section number."""
        import re
        m = re.match(r'^\s*(\d+)', str(num_str or ""))
        if m:
            n = int(m.group(1))
            self.sections_found.add(n)
            if n < self.section_range["min"]:
                self.section_range["min"] = n
            if n > self.section_range["max"]:
                self.section_range["max"] = n
           
    
    # 1. In _global_dedupe_and_route_sections method (around line 2516):
    
    
    def _get_section_sort_key(self, section):
        """Get sort key for a section."""
        import re
        sec_num_str = section.get("number", "")
        m = re.match(r'^(\d+)', sec_num_str)
        sec_num = int(m.group(1)) if m else 10**9
        return (sec_num, sec_num_str)

    def _get_chapter_sort_key(self, chapter_num):
        """Get sort key for a chapter."""
        import re
        if not chapter_num:
            return 10**9
        
        # Handle roman numerals in chapter numbers
        m = re.search(r'CHAPTER\s+([IVXLCDM]+)', chapter_num, re.I)
        if m:
            return self._roman_to_int(m.group(1))
        
        # Handle arabic numerals
        m = re.search(r'CHAPTER\s+(\d+)', chapter_num, re.I)
        if m:
            return int(m.group(1))
        
        return 10**9

    def _get_part_sort_key(self, part_num):
        """Get sort key for a part."""
        import re
        if part_num == "MAIN PART":
            return -1
        
        # Handle roman numerals in part numbers
        m = re.search(r'PART\s+([IVXLCDM]+)', part_num, re.I)
        if m:
            return self._roman_to_int(m.group(1))
        
        # Handle arabic numerals
        m = re.search(r'PART\s+(\d+)', part_num, re.I)
        if m:
            return int(m.group(1))
        
        return 10**9

    def _container_has_valid_range(self, container):
        """
        Check if a container has a valid, meaningful section range.
        """
        min_sec = container.get("min")
        max_sec = container.get("max")
        
        # Must have integer ranges
        if not (isinstance(min_sec, int) and isinstance(max_sec, int)):
            return False
        
        # Range must be valid (min <= max)
        if min_sec > max_sec:
            return False
        
        # Range must not be suspiciously large (likely invalid)
        if max_sec - min_sec > 1000:  # Adjust threshold as needed
            return False
        
        return True
    def _track_section_assignments(self, all_sections, assignments, operation_name):
        """
        Track which sections are assigned where for debugging.
        """
        if not self.debug_mode:
            return
        
        assigned_count = len([s for s in all_sections if id(s) in assignments])
        unassigned_count = len(all_sections) - assigned_count
        
        print(f"    {operation_name}:")
        print(f"      Total sections: {len(all_sections)}")
        print(f"      Assigned: {assigned_count}")
        print(f"      Unassigned: {unassigned_count}")
        
        if unassigned_count > 0:
            unassigned_numbers = []
            for s in all_sections:
                if id(s) not in assignments:
                    unassigned_numbers.append(s.get("number", "?"))
            print(f"      Unassigned section numbers: {unassigned_numbers[:10]}{'...' if len(unassigned_numbers) > 10 else ''}")

    
    # 5. Simple routing method (fallback)
    
    def _identify_preliminary_sections(self, all_sections, containers):
        """
        FIXED: Conservative identification - only sections clearly before all part ranges.
        """
        import re
        
        # Get the absolute minimum section from ALL containers
        all_mins = []
        for container in containers:
            min_sec = container.get("min")
            if isinstance(min_sec, int):
                all_mins.append(min_sec)
        
        if not all_mins:
            # No valid containers, everything goes to MAIN PART
            return all_sections, []
        
        absolute_min = min(all_mins)
        
        preliminary = []
        covered = []
        
        for section in all_sections:
            sec_num_str = section.get("number", "")
            m = re.match(r'^(\d+)', sec_num_str)
            if m:
                sec_num = int(m.group(1))
                if sec_num < absolute_min:
                    preliminary.append(section)
                else:
                    covered.append(section)
            else:
                preliminary.append(section)
        
        if self.debug_mode:
            print(f"\n=== PRELIMINARY IDENTIFICATION ===")
            print(f"  Absolute min from containers: {absolute_min}")
            print(f"  Preliminary: {[s.get('number') for s in preliminary]}")
            print(f"  Covered: {len(covered)}")
        
        return preliminary, covered

    def _is_subchapter_heading(self, raw: str) -> bool:
        """
        Heuristic: a “band” title like THE SUPREME COURT OF SRI LANKA.
        - Not CHAPTER/PART
        - No trailing section number
        - Mostly uppercase letters
        - At least two words with letters
        """
        import re
        if not raw:
            return False
        t = self.clean_text(raw)
        if re.match(r'^\s*(CHAPTER|PART)\b', t, flags=re.I):
            return False
        if re.search(r'\d+\.\s*$', t):  # looks like a section line
            return False
        if len(t) < 8:
            return False
        words = [w for w in t.split() if re.search(r'[A-Za-z]', w)]
        if len(words) < 2:
            return False
        letters = re.sub(r'[^A-Za-z]+', '', t)
        if not letters:
            return False
        caps_ratio = sum(1 for ch in letters if ch.isupper()) / max(1, len(letters))
        return caps_ratio >= 0.7

    def _ensure_subchapter(self, chapter_slot: dict, title: str) -> dict:
        """
        Ensure a SubChapter bucket exists under a CHAPTER group.
        Shape matches assemble_part_chapter_group_structure expectations.
        """
        chapter_slot.setdefault("SubChapter", [])
        for sc in chapter_slot["SubChapter"]:
            if (sc.get("title") or "").strip() == (title or "").strip():
                # ensure default section group exists
                if not sc.get("section_groups"):
                    sc["section_groups"] = [{"title": None, "sections": []}]
                return sc
        sc = {"title": self.clean_text(title), "section_groups": [{"title": None, "sections": []}]}
        chapter_slot["SubChapter"].append(sc)
        return sc
    def _norm_subsec_identifier(self, ident: str) -> str:
        """Canonicalize 'identifier' like '(1)', '(a).', 'i.' -> '(1)'/'(a)'/'(i)' for matching."""
        if not ident:
            return ""
        s = ident.strip()
        s = s.rstrip(".")
        if not (s.startswith("(") and s.endswith(")")):
            s = f"({s.strip('() ')})"
        return s.lower()

    def _strip_trailing_illustrations_token(self, text: str) -> str:
        """Remove a stray trailing 'Illustrations' token that sometimes sticks to content."""
        import re
        if not text:
            return text
        s = text.strip()
        s = re.sub(r'(?i)\billustrations?\s*[:\-–—]*\s*$', '', s).rstrip()
        return s

    def _split_off_illustrations_block(self, raw: str):
        """
        Split a text block into (main_without_illustrations, illustrations_raw_block or None).
        Recognizes headers: 'Illustration' / 'Illustrations:' (case-insensitive),
        and stops at the next header: Explanations/Explanation/CHAPTER/PART or end.
        """
        import re
        if not raw:
            return raw, None
        t = raw.replace("\r\n", "\n")
        # Find first Illustrations header
        m = re.search(r'(?im)^\s*Illustrations?\s*[:\-–—]?\s*$', t)
        if not m:
            # inline style like "Illustrations: (a) ...": allow same-line body
            m = re.search(r'(?im)^\s*Illustrations?\s*[:\-–—]\s*', t)
        if not m:
            return raw, None

        start = m.end()
        # Stop at the next header-ish line
        stop = re.search(
            r'(?im)^\s*(?:Explanations?\b|Illustrations?\b|CHAPTER\s+[A-Z0-9IVXLCDM]+|PART\s+[A-Z0-9IVXLCDM]+)\b',
            t[start:]
        )
        end = start + (stop.start() if stop else len(t))
        main = t[:m.start()].rstrip()
        illu = t[start:end].strip()
        return (main, illu if illu else None)

    def _normalize_section_number(self, num_str):
        """Normalize section number for consistent comparison."""
        if not num_str:
            return None
        # Remove trailing dots and normalize
        num_str = str(num_str).strip().rstrip('.')
        return num_str
    def _parse_illustrations_block(self, block: str):
        """
        Turn an illustrations raw block into {title, content, subsections}.
        Supports bullet markers like (a), (b), (1), (i) and plain paragraphs.
        """
        import re
        out = {"title": "Illustrations", "content": [], "subsections": []}
        if not block:
            return out
        norm = block.replace("\r\n", "\n").strip()

        # Try structured items first: (a) ..., (1) ..., (i) ...
        item_rx = re.compile(
            r'(?ms)^\s*\(\s*([a-z0-9ivxlcdm]+)\s*\)\s*(.*?)(?=^\s*\(\s*[a-z0-9ivxlcdm]+\s*\)\s*|\Z)',
            re.I
        )
        items = list(item_rx.finditer(norm))
        if items:
            for m in items:
                ident = m.group(1)
                body  = self.clean_text(m.group(2) or "")
                if body:
                    out["subsections"].append({
                        "identifier": f"({ident})",
                        "content": body,
                        "subsections": []
                    })
            return out

        # Fallback: treat as free text (single paragraph)
        s = self.clean_text(norm)
        if s:
            out["content"].append(s)
        return out

    def _merge_parsed_illustrations(self, parsed_list):
        """Merge multiple parsed illustration objects."""
        def uniq(seq):
            seen, out = set(), []
            for x in seq or []:
                if not x: 
                    continue
                if x in seen:
                    continue
                seen.add(x); out.append(x)
            return out

        merged = {"title": "Illustrations", "content": [], "subsections": []}
        seen_pairs = set()
        for il in parsed_list or []:
            for c in il.get("content") or []:
                if c and c not in merged["content"]:
                    merged["content"].append(c)
            for s in il.get("subsections") or []:
                key = (s.get("identifier") or "", s.get("content") or "")
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                merged["subsections"].append(s)
        return merged
    def _find_selectedhtml_blob(self, soup):
        """Extract the selectedhtml blob from the soup."""
        import re
        import html as _html
        
        node = (soup.find(attrs={"id": re.compile(r"selectedhtml", re.I)}) or
                soup.find(attrs={"name": re.compile(r"selectedhtml", re.I)}))
        val = ""
        if node:
            if node.name in ("input", "textarea"):
                val = node.get("value") or (node.string or "") or ""
            else:
                val = str(node)
        if not val:
            for sc in soup.find_all("script") or []:
                txt = sc.string or sc.get_text() or ""
                if "selectedhtml" in txt:
                    m = re.search(r'selectedhtml[^=]*=\s*(?P<q>["\'])(?P<body>.*?)(?P=q)', txt, re.S | re.I)
                    if m:
                        val = m.group("body")
                        break
        if val:
            try:
                val = _html.unescape(val)
            except Exception:
                pass
        return val or ""
    def _attach_illustrations_to_section(self, section_obj, illu_obj):
        """Attach/merge Illustrations into a section dict."""
        if not illu_obj:
            return
        cur = section_obj.get("Illustrations")
        if not cur:
            section_obj["Illustrations"] = illu_obj
            return
        # merge
        merged = self._merge_parsed_illustrations([cur, illu_obj])
        section_obj["Illustrations"] = merged
    def set_paths(self, html_folder, data_folder):
        """Set the paths for HTML input and JSON output folders."""
        self.html_folder = html_folder
        self.data_folder = data_folder

    # ============================================================================
    # SPECIALIZED FUNCTIONS FOR LEGISLATION_C_89 (CIVIL PROCEDURE CODE)
    # ============================================================================

    def extract_parts_from_hidden_input_c89(self, soup):
        """
        Extract PART headers and their section ranges from the hidden input field.
        Specific to legislation_C_89 (Civil Procedure Code).

        Returns: List of dicts with part_number, part_title, section_start, section_end
        """
        import re

        hidden_input = soup.find('input', attrs={'name': 'selectedhtml', 'type': 'hidden'})
        if not hidden_input:
            if self.debug_mode:
                print("  [C89] No hidden input found")
            return []

        hidden_text = hidden_input.get('value', '')
        if not hidden_text:
            if self.debug_mode:
                print("  [C89] Hidden input is empty")
            return []

        if self.debug_mode:
            print(f"  [C89] Hidden input length: {len(hidden_text)} characters")

        # Find all PART headers in the hidden text
        # Pattern: PART [ROMAN] followed by optional title
        part_pattern = re.compile(
            r'^\s*PART\s+([IVXLCDM]+)\s*$',
            re.MULTILINE
        )

        # Find all section numbers in the hidden text
        section_pattern = re.compile(
            r'^\s*(\d+[A-Z]?)\s*\.',
            re.MULTILINE
        )

        parts = []
        part_matches = list(part_pattern.finditer(hidden_text))

        if self.debug_mode:
            print(f"  [C89] Found {len(part_matches)} PART headers in hidden input")

        for i, part_match in enumerate(part_matches):
            part_roman = part_match.group(1)
            part_start_pos = part_match.start()

            # Find the next PART or end of text
            if i + 1 < len(part_matches):
                part_end_pos = part_matches[i + 1].start()
            else:
                part_end_pos = len(hidden_text)

            # Extract text for this PART
            part_text = hidden_text[part_start_pos:part_end_pos]

            # Find title (usually on the next line after PART)
            lines = part_text.split('\n')
            part_title = None
            for j, line in enumerate(lines[1:5], 1):  # Check next 4 lines
                line_stripped = line.strip()
                if line_stripped and not re.match(r'^\d+[A-Z]?\s*\.', line_stripped):
                    # This looks like a title (not a section number)
                    if 'CHAPTER' not in line_stripped:
                        part_title = line_stripped
                        break

            # Find all section numbers in this PART's text
            section_matches = list(section_pattern.finditer(part_text))

            section_numbers = []
            for sec_match in section_matches:
                sec_num = sec_match.group(1)
                section_numbers.append(sec_num)

            # Determine section range
            if section_numbers:
                # Convert section numbers to integers for comparison
                section_ints = []
                for sec in section_numbers:
                    match = re.match(r'(\d+)', sec)
                    if match:
                        section_ints.append(int(match.group(1)))

                if section_ints:
                    min_section = min(section_ints)
                    max_section = max(section_ints)
                else:
                    min_section = max_section = None
            else:
                min_section = max_section = None

            part_info = {
                'part_number': f'PART {part_roman}',
                'part_title': part_title,
                'section_start': min_section,
                'section_end': max_section,
                'section_count': len(section_numbers)
            }

            parts.append(part_info)

            if self.debug_mode:
                print(f"  [C89] {part_info['part_number']}: {part_info['part_title']}")
                print(f"        Sections: {min_section}-{max_section} ({len(section_numbers)} sections)")

        return parts

    def extract_parts_and_chapters_from_hidden_input(self, soup):
        """
        Extract complete PART and CHAPTER hierarchy from hidden input field.
        Returns: List of PARTs, each containing list of CHAPTERs with their sections.

        Structure:
        [
            {
                'part_number': 'PART I',
                'part_title': '...',
                'chapters': [
                    {
                        'chapter_number': 'CHAPTER I',
                        'chapter_title': '...',
                        'section_start': 1,
                        'section_end': 10
                    },
                    ...
                ]
            },
            ...
        ]
        """
        import re

        hidden_input = soup.find('input', attrs={'name': 'selectedhtml', 'type': 'hidden'})
        if not hidden_input:
            return []

        hidden_text = hidden_input.get('value', '')
        if not hidden_text:
            return []

        if self.debug_mode:
            print(f"  [C89+CHAPTERS] Hidden input length: {len(hidden_text)} characters")

        # Find all PART and CHAPTER headers
        part_pattern = re.compile(r'^\s*PART\s+([IVXLCDM]+)\s*$', re.MULTILINE)
        chapter_pattern = re.compile(r'^\s*CHAPTER\s+([IVXLCDM]+[A-Z]?)\s*$', re.MULTILINE)
        section_pattern = re.compile(r'^\s*(\d+[A-Z]?)\s*\.', re.MULTILINE)

        part_matches = list(part_pattern.finditer(hidden_text))
        chapter_matches = list(chapter_pattern.finditer(hidden_text))

        if self.debug_mode:
            print(f"  [C89+CHAPTERS] Found {len(part_matches)} PARTs and {len(chapter_matches)} CHAPTERs")

        parts = []

        # IMPORTANT: Check for content BEFORE the first PART (CHAPTER I - PRELIMINARY)
        # This contains sections 1, 4, 5 that come before PART I
        if part_matches:
            first_part_pos = part_matches[0].start()

            # Check if there are chapters before the first PART
            preliminary_chapters = []
            for ch_match in chapter_matches:
                if ch_match.start() < first_part_pos:
                    chapter_roman = ch_match.group(1)
                    chapter_start_pos = ch_match.start()

                    # Find the next CHAPTER or the first PART as end position
                    chapter_end_pos = first_part_pos
                    for next_ch in chapter_matches:
                        if next_ch.start() > chapter_start_pos and next_ch.start() < first_part_pos:
                            chapter_end_pos = next_ch.start()
                            break

                    # Extract text for this CHAPTER
                    chapter_text = hidden_text[chapter_start_pos:chapter_end_pos]

                    # Find CHAPTER title
                    lines = chapter_text.split('\n')
                    chapter_title = None
                    for j, line in enumerate(lines[1:5], 1):
                        line_stripped = line.strip()
                        if line_stripped and not re.match(r'^\d+[A-Z]?\s*\.', line_stripped) and 'CHAPTER' not in line_stripped:
                            chapter_title = line_stripped
                            break

                    # Find all sections in this CHAPTER
                    section_matches = list(section_pattern.finditer(chapter_text))
                    section_numbers = [sm.group(1) for sm in section_matches]

                    if section_numbers:
                        section_ints = []
                        for sec in section_numbers:
                            match = re.match(r'(\d+)', sec)
                            if match:
                                section_ints.append(int(match.group(1)))

                        if section_ints:
                            preliminary_chapters.append({
                                'chapter_number': f'CHAPTER {chapter_roman}',
                                'chapter_title': chapter_title,
                                'section_start': min(section_ints),
                                'section_end': max(section_ints),
                                'section_count': len(section_numbers)
                            })

            # Also check for standalone sections BEFORE the first PART (without chapter headers)
            # This handles cases like C_101 where section 1 appears before PART I
            if not preliminary_chapters:
                # Look for sections before the first PART
                text_before_first_part = hidden_text[:first_part_pos]
                section_matches_before = list(section_pattern.finditer(text_before_first_part))

                if section_matches_before:
                    section_numbers_before = [sm.group(1) for sm in section_matches_before]
                    section_ints_before = []

                    for sec in section_numbers_before:
                        match = re.match(r'(\d+)', sec)
                        if match:
                            section_ints_before.append(int(match.group(1)))

                    if section_ints_before:
                        # Create a pseudo-chapter for these standalone sections
                        preliminary_chapters.append({
                            'chapter_number': None,  # No chapter, just standalone sections
                            'chapter_title': 'PRELIMINARY',
                            'section_start': min(section_ints_before),
                            'section_end': max(section_ints_before),
                            'section_count': len(section_numbers_before)
                        })

                        if self.debug_mode:
                            print(f"  [C89+CHAPTERS] Found {len(section_numbers_before)} standalone sections before PART I: {min(section_ints_before)}-{max(section_ints_before)}")

            # If we found preliminary chapters or standalone sections, create a MAIN PART for them
            if preliminary_chapters:
                parts.append({
                    'part_number': 'MAIN PART',
                    'part_title': 'PRELIMINARY',
                    'chapters': preliminary_chapters
                })

                if self.debug_mode:
                    print(f"  [C89+CHAPTERS] MAIN PART (PRELIMINARY): {len(preliminary_chapters)} chapters")
                    for ch in preliminary_chapters:
                        ch_num = ch['chapter_number'] or 'Standalone sections'
                        print(f"    {ch_num}: {ch.get('chapter_title', 'N/A')} - sections {ch['section_start']}-{ch['section_end']}")

        for i, part_match in enumerate(part_matches):
            part_roman = part_match.group(1)
            part_start_pos = part_match.start()

            # Find the next PART or end of text
            if i + 1 < len(part_matches):
                part_end_pos = part_matches[i + 1].start()
            else:
                part_end_pos = len(hidden_text)

            # Extract text for this PART
            part_text = hidden_text[part_start_pos:part_end_pos]

            # Find PART title
            lines = part_text.split('\n')
            part_title = None
            for j, line in enumerate(lines[1:5], 1):
                line_stripped = line.strip()
                if line_stripped and not re.match(r'^\d+[A-Z]?\s*\.', line_stripped) and 'CHAPTER' not in line_stripped:
                    part_title = line_stripped
                    break

            # Find all CHAPTERs in this PART
            chapters = []
            for ch_match in chapter_matches:
                # Check if this chapter is within this PART's range
                if part_start_pos <= ch_match.start() < part_end_pos:
                    chapter_roman = ch_match.group(1)
                    chapter_start_pos = ch_match.start()

                    # Find the next CHAPTER or end of PART
                    chapter_end_pos = part_end_pos
                    for next_ch in chapter_matches:
                        if next_ch.start() > chapter_start_pos and next_ch.start() < part_end_pos:
                            chapter_end_pos = next_ch.start()
                            break

                    # Extract text for this CHAPTER
                    chapter_text = hidden_text[chapter_start_pos:chapter_end_pos]

                    # Find CHAPTER title (look in visible DOM for more accurate titles)
                    chapter_title = None

                    # Find all sections in this CHAPTER
                    section_matches = list(section_pattern.finditer(chapter_text))
                    section_numbers = [sm.group(1) for sm in section_matches]

                    if section_numbers:
                        section_ints = []
                        for sec in section_numbers:
                            match = re.match(r'(\d+)', sec)
                            if match:
                                section_ints.append(int(match.group(1)))

                        if section_ints:
                            chapters.append({
                                'chapter_number': f'CHAPTER {chapter_roman}',
                                'chapter_title': chapter_title,
                                'section_start': min(section_ints),
                                'section_end': max(section_ints),
                                'section_count': len(section_numbers)
                            })

            # If no chapters found, treat entire PART as one group
            if not chapters:
                # Find all sections in this PART
                section_matches = list(section_pattern.finditer(part_text))
                section_numbers = [sm.group(1) for sm in section_matches]

                if section_numbers:
                    section_ints = []
                    for sec in section_numbers:
                        match = re.match(r'(\d+)', sec)
                        if match:
                            section_ints.append(int(match.group(1)))

                    if section_ints:
                        # Create a default chapter for the whole PART
                        chapters.append({
                            'chapter_number': None,
                            'chapter_title': part_title,
                            'section_start': min(section_ints),
                            'section_end': max(section_ints),
                            'section_count': len(section_numbers)
                        })

            parts.append({
                'part_number': f'PART {part_roman}',
                'part_title': part_title,
                'chapters': chapters
            })

            if self.debug_mode:
                print(f"  [C89+CHAPTERS] {parts[-1]['part_number']}: {len(chapters)} chapters")
                for ch in chapters[:3]:
                    ch_num = ch['chapter_number'] or 'No Chapter'
                    print(f"    {ch_num}: sections {ch['section_start']}-{ch['section_end']}")

        return parts

    def map_section_to_part_c89(self, section_number, part_boundaries):
        """
        Determine which PART a section belongs to based on section number.

        Args:
            section_number: Section number (e.g., "5", "14A", "373")
            part_boundaries: List from extract_parts_from_hidden_input_c89()

        Returns: part_info dict or None
        """
        import re

        # Extract numeric part of section number
        match = re.match(r'(\d+)', str(section_number))
        if not match:
            return None

        sec_num_int = int(match.group(1))

        # Find the PART that contains this section
        for part_info in part_boundaries:
            start = part_info.get('section_start')
            end = part_info.get('section_end')

            if start is not None and end is not None:
                if start <= sec_num_int <= end:
                    return part_info

        return None

    def reorganize_sections_by_part_c89(self, parts, part_boundaries):
        """
        Reorganize sections into correct PARTs based on section numbers.

        Args:
            parts: List of part dicts from extract_parts_with_section_groups()
            part_boundaries: List from extract_parts_from_hidden_input_c89()

        Returns: Reorganized list of parts
        """
        if self.debug_mode:
            print("\n=== REORGANIZING SECTIONS FOR CIVIL PROCEDURE CODE ===")

        # Collect all sections from all parts
        all_sections = []
        for part in parts:
            for group in part.get('section_groups', []):
                for section in group.get('sections', []):
                    all_sections.append(section)

        if self.debug_mode:
            print(f"  Total sections collected: {len(all_sections)}")

        # Create new PART structure based on part_boundaries
        new_parts = []
        for part_info in part_boundaries:
            new_part = {
                'part_number': part_info['part_number'],
                'part_title': part_info['part_title'],
                'section_groups': [
                    {
                        'title': part_info['part_title'],
                        'sections': []
                    }
                ]
            }
            new_parts.append(new_part)

        # Add a MAIN PART for sections that don't fit anywhere
        main_part = {
            'part_number': 'MAIN PART',
            'part_title': None,
            'section_groups': [
                {
                    'title': 'Preliminary',
                    'sections': []
                }
            ]
        }

        # Assign each section to its correct PART
        unassigned_sections = []
        for section in all_sections:
            sec_num = section.get('number')
            part_info = self.map_section_to_part_c89(sec_num, part_boundaries)

            if part_info:
                # Find the corresponding new_part
                for new_part in new_parts:
                    if new_part['part_number'] == part_info['part_number']:
                        new_part['section_groups'][0]['sections'].append(section)
                        break
            else:
                # Assign to MAIN PART
                unassigned_sections.append(section)

        # Add unassigned sections to MAIN PART
        if unassigned_sections:
            main_part['section_groups'][0]['sections'] = unassigned_sections
            new_parts.insert(0, main_part)

            if self.debug_mode:
                print(f"  Unassigned sections: {len(unassigned_sections)}")

        # Sort sections within each PART
        for part in new_parts:
            for group in part.get('section_groups', []):
                sections = group.get('sections', [])
                if sections:
                    # Sort by section number
                    sections.sort(key=lambda s: self._extract_num_alpha(s.get('number', ''))[0] or 0)

        # Remove empty parts
        new_parts = [p for p in new_parts if any(
            len(g.get('sections', [])) > 0 for g in p.get('section_groups', [])
        )]

        if self.debug_mode:
            print(f"\n  Final PART structure:")
            for part in new_parts:
                total_sections = sum(len(g.get('sections', [])) for g in part.get('section_groups', []))
                if total_sections > 0:
                    all_sec_nums = []
                    for g in part.get('section_groups', []):
                        all_sec_nums.extend([s.get('number') for s in g.get('sections', [])])
                    print(f"    {part['part_number']}: {total_sections} sections")
                    if all_sec_nums:
                        print(f"      Range: {all_sec_nums[0]} to {all_sec_nums[-1]}")

        return new_parts

    def reorganize_sections_with_chapters(self, parts, parts_with_chapters):
        """
        Reorganize sections into PART > CHAPTER > Section hierarchy.

        Args:
            parts: List of part dicts from extract_parts_with_section_groups()
            parts_with_chapters: List from extract_parts_and_chapters_from_hidden_input()

        Returns: Reorganized list of parts with chapters
        """
        if self.debug_mode:
            print("\n=== REORGANIZING SECTIONS WITH CHAPTER HIERARCHY ===")

        # Collect all sections from all parts
        all_sections = []
        for part in parts:
            for group in part.get('section_groups', []):
                for section in group.get('sections', []):
                    all_sections.append(section)

        if self.debug_mode:
            print(f"  Total sections collected: {len(all_sections)}")

        # Create new structure with PARTs and CHAPTERs
        new_parts = []

        for part_info in parts_with_chapters:
            new_part = {
                'part_number': part_info['part_number'],
                'part_title': part_info['part_title'],
                'chapters': []
            }

            # Create chapters within this PART
            for chapter_info in part_info.get('chapters', []):
                new_chapter = {
                    'chapter_number': chapter_info['chapter_number'],
                    'chapter_title': chapter_info['chapter_title'],
                    'section_groups': [
                        {
                            'title': chapter_info['chapter_title'],
                            'sections': []
                        }
                    ]
                }

                # Assign sections to this chapter
                for section in all_sections:
                    sec_num = section.get('number')
                    # Extract numeric part
                    import re
                    match = re.match(r'(\d+)', str(sec_num))
                    if match:
                        sec_num_int = int(match.group(1))
                        ch_start = chapter_info['section_start']
                        ch_end = chapter_info['section_end']

                        if ch_start <= sec_num_int <= ch_end:
                            new_chapter['section_groups'][0]['sections'].append(section)

                # Only add chapter if it has sections
                if new_chapter['section_groups'][0]['sections']:
                    new_part['chapters'].append(new_chapter)

            # Only add part if it has chapters
            if new_part['chapters']:
                new_parts.append(new_part)

        # Sort sections within each chapter
        for part in new_parts:
            for chapter in part.get('chapters', []):
                for group in chapter.get('section_groups', []):
                    sections = group.get('sections', [])
                    if sections:
                        sections.sort(key=lambda s: self._extract_num_alpha(s.get('number', ''))[0] or 0)

        if self.debug_mode:
            print(f"\n  Final structure:")
            for part in new_parts:
                total_sections = sum(
                    len(g.get('sections', []))
                    for ch in part.get('chapters', [])
                    for g in ch.get('section_groups', [])
                )
                print(f"    {part['part_number']}: {len(part.get('chapters', []))} chapters, {total_sections} sections")
                for ch in part.get('chapters', [])[:2]:
                    ch_sections = []
                    for g in ch.get('section_groups', []):
                        ch_sections.extend([s.get('number') for s in g.get('sections', [])])
                    if ch_sections:
                        print(f"      {ch['chapter_number']}: {len(ch_sections)} sections ({ch_sections[0]}-{ch_sections[-1]})")

        return new_parts

    def process_legislation_c89(self, soup, legislation_id):
        """
        Main processing function for legislation_C_89 (Civil Procedure Code).
        Handles the unique structure with PART headers in hidden input field.

        Args:
            soup: BeautifulSoup object
            legislation_id: "legislation_C_89"

        Returns: Properly structured parts list
        """
        if self.debug_mode:
            print("\n" + "="*70)
            print("PROCESSING CIVIL PROCEDURE CODE (LEGISLATION_C_89)")
            print("Using specialized handler for hidden input PART structure")
            print("="*70 + "\n")

        # Step 1: Extract PART boundaries from hidden input
        part_boundaries = self.extract_parts_from_hidden_input_c89(soup)

        if not part_boundaries:
            if self.debug_mode:
                print("  [C89] No PART boundaries found, falling back to standard processing")
            return self.extract_parts_with_section_groups(soup)

        # Step 2: Extract sections using standard method
        parts = self.extract_parts_with_section_groups(soup)

        # Step 3: Reorganize sections into correct PARTs
        reorganized_parts = self.reorganize_sections_by_part_c89(parts, part_boundaries)

        return reorganized_parts


# Example usage
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # Process specific legislation file
        legislation_id = sys.argv[1]  # e.g., "legislation_A_15"

        # Determine folder based on ID
        folder_letter = legislation_id.split('_')[1]  # Extract "A" from "legislation_A_15"
        html_folder = f"data/html/legislation_{folder_letter}"
        data_folder = f"data/legislations/legislation_{folder_letter}"

        processor = MainHTMLProcessor(html_folder, data_folder)
        processor.debug_mode = True

        # Process the specific file
        html_path = os.path.join(html_folder, legislation_id, f"{legislation_id}.html")

        if not os.path.exists(html_path):
            print(f"Error: HTML file not found at {html_path}")
            sys.exit(1)

        print(f"Processing {legislation_id}...")

        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()

        # Create JSON object
        json_data = processor.construct_json_data(html_content, legislation_id)

        # Save JSON file
        output_file = os.path.join(data_folder, f"{legislation_id}.json")
        with open(output_file, "w", encoding="utf-8") as out_f:
            json.dump(json_data, out_f, indent=4, ensure_ascii=False)

        print(f"Saved to {output_file}")
    else:
        processor = MainHTMLProcessor()
        processor.set_paths("data/html/legislation_C", "data/json/legislation_C")
        processor.process_html_files()