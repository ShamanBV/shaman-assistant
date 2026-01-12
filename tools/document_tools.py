"""
Shaman Assistant Document Generation Tools
==========================================
Tools for creating PowerPoint, Word, and PDF documents.
"""
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path("./output")
OUTPUT_DIR.mkdir(exist_ok=True)


def create_pptx(title: str, filename: str, slides: list[dict]) -> str:
    """
    Create a PowerPoint presentation.

    Args:
        title: Presentation title
        filename: Output filename (without extension)
        slides: List of slide dicts with:
            - title: str
            - subtitle: str (optional)
            - content: list[str] (bullet points)
            - notes: str (speaker notes, optional)
            - layout: "title" | "section" | "content" | "two_column"

    Returns:
        Path to created file
    """
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width = Inches(13.333)  # 16:9
    prs.slide_height = Inches(7.5)

    LAYOUT_TITLE = 0
    LAYOUT_TITLE_CONTENT = 1
    LAYOUT_SECTION = 2
    LAYOUT_TWO_CONTENT = 3

    for i, slide_data in enumerate(slides):
        layout_name = slide_data.get("layout", "content")

        if layout_name == "title" or i == 0:
            layout = prs.slide_layouts[LAYOUT_TITLE]
        elif layout_name == "section":
            layout = prs.slide_layouts[LAYOUT_SECTION]
        elif layout_name == "two_column":
            layout = prs.slide_layouts[LAYOUT_TWO_CONTENT]
        else:
            layout = prs.slide_layouts[LAYOUT_TITLE_CONTENT]

        slide = prs.slides.add_slide(layout)

        if slide.shapes.title:
            slide.shapes.title.text = slide_data.get("title", "")

        if layout_name == "title" and slide_data.get("subtitle"):
            for shape in slide.placeholders:
                if shape.placeholder_format.idx == 1:
                    shape.text = slide_data["subtitle"]
                    break

        if content := slide_data.get("content"):
            for shape in slide.placeholders:
                if shape.placeholder_format.idx == 1 and layout_name != "title":
                    tf = shape.text_frame
                    tf.clear()
                    for j, item in enumerate(content):
                        p = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
                        p.text = item
                        p.level = 0
                    break

        if notes := slide_data.get("notes"):
            slide.notes_slide.notes_text_frame.text = notes

    filepath = OUTPUT_DIR / f"{filename}.pptx"
    prs.save(str(filepath))
    return str(filepath)


def create_docx(title: str, filename: str, sections: list[dict]) -> str:
    """
    Create a Word document.

    Args:
        title: Document title
        filename: Output filename (without extension)
        sections: List of section dicts with:
            - heading: str
            - level: int (1-3, default 1)
            - content: str (paragraphs, use \\n\\n for breaks)
            - bullets: list[str] (optional)

    Returns:
        Path to created file
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    title_para = doc.add_heading(title, level=0)
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d')}")
    doc.add_paragraph("")

    for section in sections:
        level = section.get("level", 1)
        doc.add_heading(section["heading"], level=level)

        if content := section.get("content"):
            for para_text in content.split("\n\n"):
                if para_text.strip():
                    doc.add_paragraph(para_text.strip())

        if bullets := section.get("bullets"):
            for bullet in bullets:
                doc.add_paragraph(bullet, style="List Bullet")

    filepath = OUTPUT_DIR / f"{filename}.docx"
    doc.save(str(filepath))
    return str(filepath)


def create_pdf(title: str, filename: str, content: str) -> str:
    """
    Create a PDF from markdown content.

    Args:
        title: Document title
        filename: Output filename (without extension)
        content: Markdown-formatted content

    Returns:
        Path to created file
    """
    try:
        import markdown
        from weasyprint import HTML
    except ImportError:
        # Fallback to markdown file
        filepath = OUTPUT_DIR / f"{filename}.md"
        filepath.write_text(f"# {title}\n\n{content}")
        return str(filepath)

    html_content = markdown.markdown(content, extensions=['tables', 'fenced_code'])

    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>{title}</title>
        <style>
            @page {{ size: A4; margin: 2.5cm; }}
            body {{ font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 11pt; line-height: 1.6; color: #333; }}
            h1 {{ color: #1a365d; border-bottom: 2px solid #1a365d; padding-bottom: 10px; }}
            h2 {{ color: #2c5282; margin-top: 1.5em; }}
            table {{ width: 100%; border-collapse: collapse; margin: 1em 0; }}
            th, td {{ border: 1px solid #e2e8f0; padding: 8px 12px; text-align: left; }}
            th {{ background-color: #edf2f7; }}
            code {{ background-color: #f7fafc; padding: 2px 6px; border-radius: 3px; }}
        </style>
    </head>
    <body>
        <h1>{title}</h1>
        <p style="color: #718096;">Generated: {datetime.now().strftime('%Y-%m-%d')}</p>
        {html_content}
    </body>
    </html>
    """

    filepath = OUTPUT_DIR / f"{filename}.pdf"
    HTML(string=full_html).write_pdf(str(filepath))
    return str(filepath)


def create_markdown(title: str, filename: str, content: str) -> str:
    """
    Create a Markdown file.

    Args:
        title: Document title (used as H1 heading)
        filename: Output filename (without extension)
        content: Markdown-formatted content

    Returns:
        Path to created file
    """
    full_content = f"# {title}\n\n{content}"
    filepath = OUTPUT_DIR / f"{filename}.md"
    filepath.write_text(full_content, encoding="utf-8")
    return str(filepath)


def create_json(filename: str, data: dict | list, schema_description: str = None) -> str:
    """
    Create a JSON file with structured data.

    Args:
        filename: Output filename (without extension)
        data: The JSON data structure (dict or list)
        schema_description: Optional description of the schema used

    Returns:
        Path to created file
    """
    import json

    output = {
        "_meta": {
            "generated": datetime.now().isoformat(),
            "generator": "Shaman Assistant"
        }
    }

    if schema_description:
        output["_meta"]["schema_description"] = schema_description

    # Merge data into output (or wrap if it's a list)
    if isinstance(data, dict):
        output["data"] = data
    else:
        output["data"] = data

    filepath = OUTPUT_DIR / f"{filename}.json"
    filepath.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(filepath)


# Tool definitions for Claude API
DOCUMENT_TOOLS = [
    {
        "name": "create_presentation",
        "description": "Create a PowerPoint presentation (.pptx). Use for customer onboarding decks, training materials, sales presentations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "filename": {"type": "string", "description": "Output filename without extension"},
                "slides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "subtitle": {"type": "string"},
                            "content": {"type": "array", "items": {"type": "string"}},
                            "notes": {"type": "string"},
                            "layout": {"type": "string", "enum": ["title", "section", "content", "two_column"]}
                        },
                        "required": ["title"]
                    }
                }
            },
            "required": ["title", "filename", "slides"]
        }
    },
    {
        "name": "create_document",
        "description": "Create a Word document (.docx). Use for SOPs, implementation guides, proposals, onboarding documentation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "filename": {"type": "string"},
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "level": {"type": "integer", "default": 1},
                            "content": {"type": "string"},
                            "bullets": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["heading"]
                    }
                }
            },
            "required": ["title", "filename", "sections"]
        }
    },
    {
        "name": "create_pdf_report",
        "description": "Create a PDF report. Use for compliance reports, branded deliverables, formal documentation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "filename": {"type": "string"},
                "content": {"type": "string", "description": "Markdown content"}
            },
            "required": ["title", "filename", "content"]
        }
    },
    {
        "name": "create_markdown",
        "description": "Create a Markdown file (.md). Use for documentation, README files, notes, specifications, or any text-based content with formatting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Document title (becomes H1 heading)"},
                "filename": {"type": "string", "description": "Output filename without extension"},
                "content": {"type": "string", "description": "Markdown-formatted content (supports headings, lists, code blocks, tables, etc.)"}
            },
            "required": ["title", "filename", "content"]
        }
    },
    {
        "name": "create_json_structure",
        "description": "Create a JSON file with structured data. Use when the user provides an example structure or schema and wants to generate data in that format. Great for configuration files, data exports, API payloads, or any structured data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Output filename without extension"},
                "data": {
                    "description": "The JSON data structure to create (can be object or array)",
                    "oneOf": [
                        {"type": "object"},
                        {"type": "array"}
                    ]
                },
                "schema_description": {"type": "string", "description": "Optional description of the schema/structure used"}
            },
            "required": ["filename", "data"]
        }
    }
]


def process_document_tool(tool_name: str, tool_input: dict) -> str:
    """
    Process a document generation tool call.

    Args:
        tool_name: Name of the tool to execute
        tool_input: Tool input parameters

    Returns:
        Path to created file or error message
    """
    try:
        if tool_name == "create_presentation":
            return create_pptx(**tool_input)
        elif tool_name == "create_document":
            return create_docx(**tool_input)
        elif tool_name == "create_pdf_report":
            return create_pdf(**tool_input)
        elif tool_name == "create_markdown":
            return create_markdown(**tool_input)
        elif tool_name == "create_json_structure":
            return create_json(**tool_input)
        else:
            return f"Unknown document tool: {tool_name}"
    except Exception as e:
        return f"Error creating document: {str(e)}"
