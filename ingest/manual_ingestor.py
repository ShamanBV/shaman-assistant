"""
Manual Content Ingestor
=======================
Add important documentation pages manually when scraping is blocked.

Supports: .txt, .md, .html, .htm files

Usage:
1. Save pages to the 'content_input/manual_docs/' folder
2. For .txt/.md files, add frontmatter:
   ---
   title: Page Title
   url: https://example.com/page
   section: approved_email
   ---

   Content goes here...

3. For .html files, just "Save Page As" from browser - metadata extracted automatically

4. Run: python multi_source_rag.py --sync-manual
"""

import os
import hashlib
import re
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

# Default folder for manual docs
MANUAL_DOCS_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "content_input", "manual_docs")


def html_table_to_markdown(table) -> str:
    """Convert an HTML table to markdown format."""
    rows = []

    # Get all rows
    for tr in table.find_all('tr'):
        cells = []
        for cell in tr.find_all(['th', 'td']):
            # Get text and clean it
            text = cell.get_text(strip=True).replace('|', '\\|').replace('\n', ' ')
            cells.append(text)
        if cells:
            rows.append(cells)

    if not rows:
        return ""

    # Build markdown table
    md_lines = []

    # Header row
    md_lines.append("| " + " | ".join(rows[0]) + " |")
    md_lines.append("| " + " | ".join(["---"] * len(rows[0])) + " |")

    # Data rows
    for row in rows[1:]:
        # Pad row to match header length
        while len(row) < len(rows[0]):
            row.append("")
        md_lines.append("| " + " | ".join(row[:len(rows[0])]) + " |")

    return "\n".join(md_lines)


def extract_html_content(html_content: str, filename: str) -> tuple:
    """
    Extract content and metadata from HTML.
    Returns (metadata_dict, body_text)
    """
    if BeautifulSoup is None:
        raise ImportError("BeautifulSoup is required for HTML parsing. Install with: pip install beautifulsoup4")

    soup = BeautifulSoup(html_content, 'html.parser')

    # Extract metadata
    metadata = {}

    # Title from <title> or <h1>
    title_tag = soup.find('title')
    if title_tag:
        metadata['title'] = title_tag.get_text(strip=True)
    else:
        h1 = soup.find('h1')
        if h1:
            metadata['title'] = h1.get_text(strip=True)
        else:
            metadata['title'] = filename.replace('.html', '').replace('.htm', '')

    # Clean up title
    for suffix in [' - Veeva CRM Help', ' | Veeva', ' - Veeva']:
        metadata['title'] = metadata['title'].replace(suffix, '')

    # Try to get URL from canonical link or base href
    canonical = soup.find('link', rel='canonical')
    if canonical and canonical.get('href'):
        metadata['url'] = canonical['href']
    else:
        base = soup.find('base')
        if base and base.get('href'):
            metadata['url'] = base['href']
        else:
            metadata['url'] = ''

    # Determine section from title/content
    title_lower = metadata['title'].lower()
    if 'approved email' in title_lower or 'token' in title_lower:
        metadata['section'] = 'approved_email'
    elif 'clm' in title_lower or 'closed loop' in title_lower:
        metadata['section'] = 'clm'
    elif 'promomats' in title_lower:
        metadata['section'] = 'promomats'
    elif 'mlr' in title_lower or 'review' in title_lower:
        metadata['section'] = 'mlr_review'
    else:
        metadata['section'] = 'manual'

    metadata['priority'] = 'high'  # Manual docs are high priority

    # Remove script and style elements only
    for tag in soup.find_all(['script', 'style']):
        tag.decompose()

    # Find main content area - try multiple selectors (MadCap Flare selectors first)
    main_content = None
    for selector in ['#mc-main-content', '#printArea', '.col-right',
                     'article', 'main', '.topic-content', '.article-body',
                     '.content', '.body-container', '#content', 'body']:
        if selector.startswith('.'):
            main_content = soup.find(class_=selector[1:])
        elif selector.startswith('#'):
            main_content = soup.find(id=selector[1:])
        else:
            main_content = soup.find(selector)
        if main_content:
            break

    if not main_content:
        main_content = soup.find('body') or soup

    # Remove navigation, sidebars, and other noise elements
    noise_selectors = [
        'nav', '.nav', '.navigation', '.sidebar', '.toc', '.breadcrumb',
        '.MCBreadcrumbsBox_0', '.nav-container', '.feedback', '.related-links',
        '.see-also', '.footer', 'header', 'footer', '.menu', '.search-box',
    ]
    for sel in noise_selectors:
        if sel.startswith('.'):
            for el in main_content.find_all(class_=sel[1:]):
                el.decompose()
        else:
            for el in main_content.find_all(sel):
                el.decompose()

    # Extract tables first and convert to markdown
    tables_md = []
    for table in main_content.find_all('table'):
        md_table = html_table_to_markdown(table)
        if md_table:
            tables_md.append(md_table)
        # Replace table with a placeholder
        table.replace_with(f"\n[TABLE_{len(tables_md)}]\n")

    # Get all text content
    body_text = main_content.get_text(separator='\n', strip=True)

    # Re-insert tables
    for i, table_md in enumerate(tables_md):
        body_text = body_text.replace(f"[TABLE_{i+1}]", f"\n\n{table_md}\n\n")

    # Clean up excessive whitespace
    body_text = re.sub(r'\n{3,}', '\n\n', body_text)
    body_text = re.sub(r' {2,}', ' ', body_text)

    # Remove common noise patterns from Veeva help pages
    noise_patterns = [
        r'Was this article helpful\?.*',
        r'Yes\s*No',
        r'Submit a request.*',
        r'©.*Veeva.*',
        r'Print\s*PDF',
        r'Feedback',
        r'Share this article',
    ]
    for pattern in noise_patterns:
        body_text = re.sub(pattern, '', body_text, flags=re.IGNORECASE)

    return metadata, body_text.strip()

# Chunk settings (same as PDF)
MAX_CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200


def parse_frontmatter(content: str) -> tuple:
    """Parse YAML-like frontmatter from content."""
    metadata = {}
    body = content

    # Check for frontmatter
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            body = parts[2].strip()

            # Parse simple key: value pairs
            for line in frontmatter.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    metadata[key.strip().lower()] = value.strip()

    return metadata, body


def chunk_text(text: str, chunk_size: int = MAX_CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list:
    """Split text into overlapping chunks."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        if end < len(text):
            # Look for paragraph break
            para_break = text.rfind("\n\n", start, end)
            if para_break > start + chunk_size // 2:
                end = para_break
            else:
                # Look for sentence break
                for sep in [". ", ".\n", "? ", "!\n"]:
                    sent_break = text.rfind(sep, start, end)
                    if sent_break > start + chunk_size // 2:
                        end = sent_break + 1
                        break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap
        if start < 0:
            start = 0

    return chunks


def ingest_manual_docs(folder: str = None) -> list:
    """
    Ingest all manual documents from a folder.

    Supports .txt and .md files with optional frontmatter.
    """
    if folder is None:
        folder = MANUAL_DOCS_FOLDER

    if not os.path.exists(folder):
        print(f"   Creating manual docs folder: {folder}")
        os.makedirs(folder)

        # Create example file
        example_path = os.path.join(folder, "EXAMPLE.md")
        with open(example_path, 'w') as f:
            f.write("""---
title: Example Document
url: https://example.com/doc
section: general
priority: high
---

# Example Document

This is an example of how to add manual documentation.

## Section 1

Add your content here...

## Section 2

More content...

Delete this file and add your own documents.
""")
        print(f"   Created example file: {example_path}")
        return []

    # Find all supported files
    doc_files = (
        list(Path(folder).glob("*.txt")) +
        list(Path(folder).glob("*.md")) +
        list(Path(folder).glob("*.html")) +
        list(Path(folder).glob("*.htm"))
    )

    # Exclude example file
    doc_files = [f for f in doc_files if f.name != "EXAMPLE.md"]

    if not doc_files:
        print(f"   No documents found in {folder}")
        return []

    print(f"   Found {len(doc_files)} manual documents")

    documents = []

    for doc_path in doc_files:
        filename = doc_path.name
        is_html = filename.lower().endswith(('.html', '.htm'))
        print(f"   {'🌐' if is_html else '📝'} Processing: {filename}")

        try:
            with open(doc_path, 'r', encoding='utf-8') as f:
                content = f.read()

            if not content or len(content) < 50:
                print(f"      ⚠ File too short: {filename}")
                continue

            # Handle HTML vs text/markdown differently
            if is_html:
                metadata, body = extract_html_content(content, filename)
            else:
                # Parse frontmatter for .txt/.md files
                metadata, body = parse_frontmatter(content)

            title = metadata.get('title', filename.replace('.md', '').replace('.txt', '').replace('.html', '').replace('.htm', ''))
            url = metadata.get('url', '')
            section = metadata.get('section', 'manual')
            priority = metadata.get('priority', 'normal' if not is_html else 'high')

            # Chunk the content
            chunks = chunk_text(body)
            print(f"      → {len(chunks)} chunks, section: {section}, priority: {priority}")

            for i, chunk in enumerate(chunks):
                doc_id = hashlib.md5(f"{filename}_{i}".encode()).hexdigest()

                # Add title context
                if i == 0:
                    chunk_content = f"# {title}\n\n{chunk}"
                else:
                    chunk_content = f"# {title} (part {i+1})\n\n{chunk}"

                documents.append({
                    "id": f"manual_{doc_id}",
                    "content": chunk_content,
                    "metadata": {
                        "source": "manual",
                        "title": title,
                        "url": url,
                        "section": section,
                        "priority": priority,
                        "filename": filename,
                        "chunk": i + 1,
                        "total_chunks": len(chunks),
                    }
                })

        except Exception as e:
            print(f"      ⚠ Error: {e}")
            continue

    print(f"   ✓ Processed {len(documents)} chunks from {len(doc_files)} documents")
    return documents


if __name__ == "__main__":
    docs = ingest_manual_docs()
    print(f"\nTotal documents: {len(docs)}")

    if docs:
        print("\nSample:")
        print(f"  Title: {docs[0]['metadata']['title']}")
        print(f"  Section: {docs[0]['metadata']['section']}")
