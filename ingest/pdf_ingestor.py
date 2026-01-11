"""
PDF Ingestor
============
Ingests PDF documents into the knowledge base.

Place PDFs in the 'content_input/pdfs/' folder and run:
    python multi_source_rag.py --sync-pdfs

For vision-enhanced processing (extracts context from images/screenshots):
    python multi_source_rag.py --sync-pdfs --vision

SETUP
-----
pip install pdfplumber pdf2image
apt-get install poppler-utils  # Linux - for pdf2image
brew install poppler           # macOS - for pdf2image
"""

import os
import hashlib
import base64
import json
from pathlib import Path
from io import BytesIO

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


# Default folder for PDFs
PDF_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "content_input", "pdfs")

# Chunk settings
MAX_CHUNK_SIZE = 2000  # chars per chunk
CHUNK_OVERLAP = 200    # overlap between chunks


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from a PDF file."""
    if pdfplumber is None:
        raise ImportError("pdfplumber is required. Install with: pip install pdfplumber")

    text_parts = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)

    return "\n\n".join(text_parts)


def chunk_text(text: str, chunk_size: int = MAX_CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list:
    """Split text into overlapping chunks."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # Try to break at a paragraph or sentence
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


def get_anthropic_client():
    """Get Anthropic client for vision processing."""
    if not ANTHROPIC_AVAILABLE:
        return None
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key)


def image_to_base64(image) -> str:
    """Convert PIL Image to base64 string."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")


def describe_page_with_vision(client, image, page_text: str, filename: str, page_num: int) -> str:
    """
    Use Claude Vision to describe visual elements on a PDF page.

    Specifically designed for Arcade-style tutorials with:
    - Step-by-step instructions
    - Screenshots with hotspots (highlighted areas showing where to click)
    - UI elements and buttons
    """
    if client is None:
        return ""

    try:
        image_data = image_to_base64(image)

        prompt = f"""Analyze this PDF page from "{filename}" (page {page_num}).

This appears to be a tutorial or how-to guide. Please describe:

1. **Visual Elements**: What UI elements, buttons, menus, or interface components are visible?
2. **Hotspots/Highlights**: Are there any highlighted areas, circles, arrows, or visual indicators pointing to specific elements? If so, describe what they're highlighting and why.
3. **Screenshots**: If there are screenshots, describe what application/interface is shown and what state it's in.
4. **Action Context**: Based on the visuals, what action is the user supposed to take?

The text on this page says:
{page_text[:1000] if page_text else "(No text extracted)"}

Provide a concise but informative description that would help someone understand what this step involves without seeing the image. Focus on actionable details like button names, menu locations, and visual cues."""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ],
                }
            ],
        )

        return response.content[0].text

    except Exception as e:
        print(f"      ⚠ Vision API error on page {page_num}: {e}")
        return ""


def extract_pages_with_vision(pdf_path: str, client) -> list:
    """
    Extract text and vision descriptions for each page.
    Only uses vision API for pages that contain images.

    Returns list of dicts: [{"page": int, "text": str, "vision": str}, ...]
    """
    if not PDF2IMAGE_AVAILABLE:
        print("      ⚠ pdf2image not installed, falling back to text-only extraction")
        return None

    pages_data = []
    filename = os.path.basename(pdf_path)
    vision_pages = 0
    text_only_pages = 0

    try:
        # First pass: identify which pages have images
        with pdfplumber.open(pdf_path) as pdf:
            pages_with_images = []
            for i, page in enumerate(pdf.pages):
                # Check if page has images (screenshots, diagrams, etc.)
                has_images = len(page.images) > 0 if hasattr(page, 'images') else False
                pages_with_images.append(has_images)

        # Convert only pages with images to image format
        if any(pages_with_images):
            images = convert_from_path(pdf_path, dpi=150)
        else:
            images = [None] * len(pages_with_images)

        # Second pass: extract text and optionally vision
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_num = i + 1
                page_text = page.extract_text() or ""
                has_images = pages_with_images[i]

                if has_images and images[i] is not None:
                    print(f"      🔍 Analyzing page {page_num}/{len(pdf.pages)} with vision (has images)...")
                    vision_desc = describe_page_with_vision(client, images[i], page_text, filename, page_num)
                    vision_pages += 1
                else:
                    print(f"      📝 Page {page_num}/{len(pdf.pages)} - text only (no images)")
                    vision_desc = ""
                    text_only_pages += 1

                pages_data.append({
                    "page": page_num,
                    "text": page_text,
                    "vision": vision_desc
                })

        print(f"      ✓ {vision_pages} pages with vision, {text_only_pages} text-only")
        return pages_data

    except Exception as e:
        print(f"      ⚠ Error in vision extraction: {e}")
        return None


def determine_section(filename: str, content: str) -> str:
    """Determine section based on filename and content."""
    filename_lower = filename.lower()
    content_lower = content.lower()[:2000]

    if "clm" in filename_lower or "clm" in content_lower:
        return "clm"
    elif "approved" in filename_lower and "email" in filename_lower:
        return "approved_email"
    elif "approved email" in content_lower:
        return "approved_email"
    elif "promomats" in filename_lower or "promomats" in content_lower:
        return "promomats"
    elif "mlr" in filename_lower or "mlr review" in content_lower:
        return "mlr_review"
    elif "veeva" in filename_lower:
        return "veeva_general"
    else:
        return "pdf_document"


def ingest_pdfs(pdf_folder: str = None, use_vision: bool = False) -> list:
    """
    Ingest all PDFs from a folder.

    Args:
        pdf_folder: Path to folder containing PDFs. Defaults to ./content_input/pdfs/
        use_vision: If True, use Claude Vision to extract descriptions from images/screenshots.
                   This creates richer embeddings for tutorial-style PDFs (like Arcade).

    Returns list of documents ready for indexing:
    [{"id": str, "content": str, "metadata": dict}, ...]
    """
    if pdf_folder is None:
        pdf_folder = PDF_FOLDER

    if not os.path.exists(pdf_folder):
        print(f"   Creating PDF folder: {pdf_folder}")
        os.makedirs(pdf_folder)
        return []

    pdf_files = list(Path(pdf_folder).glob("*.pdf"))

    if not pdf_files:
        print(f"   No PDFs found in {pdf_folder}")
        return []

    print(f"   Found {len(pdf_files)} PDF files")

    # Initialize vision client if needed
    vision_client = None
    if use_vision:
        if not PDF2IMAGE_AVAILABLE:
            print("   ⚠ pdf2image not installed. Install with: pip install pdf2image")
            print("   ⚠ Also install poppler: apt-get install poppler-utils (Linux) or brew install poppler (macOS)")
            print("   Falling back to text-only extraction...")
            use_vision = False
        elif not ANTHROPIC_AVAILABLE:
            print("   ⚠ anthropic not installed. Install with: pip install anthropic")
            print("   Falling back to text-only extraction...")
            use_vision = False
        else:
            vision_client = get_anthropic_client()
            if vision_client is None:
                print("   ⚠ ANTHROPIC_API_KEY not set. Falling back to text-only extraction...")
                use_vision = False
            else:
                print("   🔍 Vision mode enabled - will analyze images in PDFs")

    documents = []

    for pdf_path in pdf_files:
        filename = pdf_path.name
        print(f"   📄 Processing: {filename}")

        try:
            if use_vision and vision_client:
                # Vision-enhanced extraction
                pages_data = extract_pages_with_vision(str(pdf_path), vision_client)

                if pages_data:
                    # Combine text and vision descriptions per page
                    full_text_parts = []
                    for page_data in pages_data:
                        page_content = page_data["text"] or ""
                        if page_data["vision"]:
                            page_content += f"\n\n[Visual Context: {page_data['vision']}]"
                        full_text_parts.append(page_content)

                    full_text = "\n\n---\n\n".join(full_text_parts)

                    if not full_text or len(full_text) < 100:
                        print(f"      ⚠ No content extracted from {filename}")
                        continue

                    # Determine section
                    section = determine_section(filename, full_text)

                    # For vision-processed PDFs, chunk by page for better context
                    for i, page_data in enumerate(pages_data):
                        page_num = page_data["page"]
                        page_text = page_data["text"] or ""
                        vision_desc = page_data["vision"] or ""

                        # Combine text and vision for this page
                        if vision_desc:
                            page_content = f"{page_text}\n\n[Visual Context: {vision_desc}]"
                        else:
                            page_content = page_text

                        if not page_content.strip():
                            continue

                        doc_id = hashlib.md5(f"{filename}_page{page_num}".encode()).hexdigest()

                        chunk_content = f"# {filename} - Page {page_num}\n\n{page_content}"

                        documents.append({
                            "id": f"pdf_{doc_id}",
                            "content": chunk_content,
                            "metadata": {
                                "source": "pdf",
                                "filename": filename,
                                "section": section,
                                "page": page_num,
                                "total_pages": len(pages_data),
                                "vision_enhanced": True,
                            }
                        })

                    print(f"      → {len(pages_data)} pages with vision analysis, section: {section}")
                    continue

            # Standard text-only extraction (fallback or default)
            full_text = extract_text_from_pdf(str(pdf_path))

            if not full_text or len(full_text) < 100:
                print(f"      ⚠ No text extracted from {filename}")
                continue

            # Determine section
            section = determine_section(filename, full_text)

            # Chunk the text
            chunks = chunk_text(full_text)
            print(f"      → {len(chunks)} chunks, section: {section}")

            # Create documents for each chunk
            for i, chunk in enumerate(chunks):
                doc_id = hashlib.md5(f"{filename}_{i}".encode()).hexdigest()

                # Add context to chunk
                if i == 0:
                    chunk_content = f"# {filename}\n\n{chunk}"
                else:
                    chunk_content = f"# {filename} (part {i+1})\n\n{chunk}"

                documents.append({
                    "id": f"pdf_{doc_id}",
                    "content": chunk_content,
                    "metadata": {
                        "source": "pdf",
                        "filename": filename,
                        "section": section,
                        "chunk": i + 1,
                        "total_chunks": len(chunks),
                        "vision_enhanced": False,
                    }
                })

        except Exception as e:
            print(f"      ⚠ Error processing {filename}: {e}")
            continue

    print(f"   ✓ Extracted {len(documents)} chunks from {len(pdf_files)} PDFs")
    return documents


if __name__ == "__main__":
    # Test the ingestor
    docs = ingest_pdfs()
    print(f"\nTotal documents: {len(docs)}")

    if docs:
        print("\nSample document:")
        print(f"  ID: {docs[0]['id']}")
        print(f"  Metadata: {docs[0]['metadata']}")
        print(f"  Content preview: {docs[0]['content'][:200]}...")
