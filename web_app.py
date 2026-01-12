#!/usr/bin/env python3
"""
Shaman Assistant Web UI
=======================
A clean web interface for the Shaman Assistant chat.

Usage:
    python web_app.py

Then open http://localhost:5000 in your browser.
"""
import json
import os
from pathlib import Path
from flask import Flask, render_template, request, jsonify, session
from datetime import datetime
import secrets

from orchestrator import ShamanAssistant
from services.memory import ConversationMemory, LearnedKnowledge

app = Flask(__name__)

# Use stable secret key (persisted to file so sessions survive restarts)
SECRET_KEY_FILE = Path("./.flask_secret_key")
if SECRET_KEY_FILE.exists():
    app.secret_key = SECRET_KEY_FILE.read_text().strip()
else:
    app.secret_key = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(app.secret_key)

# Server-side session storage (avoids cookie size limits)
SESSIONS_DIR = Path("./web_sessions")
SESSIONS_DIR.mkdir(exist_ok=True)


def serialize_history(history: list) -> list:
    """Convert conversation history to JSON-serializable format."""
    serialized = []
    for msg in history:
        if isinstance(msg.get("content"), list):
            content = []
            for block in msg["content"]:
                if hasattr(block, "type"):
                    # Anthropic API object (TextBlock, ToolUseBlock, ThinkingBlock, etc.)
                    if block.type == "thinking":
                        # Skip thinking blocks - they don't need to be persisted
                        continue
                    elif block.type == "text":
                        content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input
                        })
                    elif block.type == "tool_result":
                        content.append({
                            "type": "tool_result",
                            "tool_use_id": block.tool_use_id,
                            "content": block.content
                        })
                else:
                    content.append(block)
            if content:  # Only add if there's content after filtering
                serialized.append({"role": msg["role"], "content": content})
        else:
            serialized.append(msg)
    return serialized


def save_web_session(session_id: str, history: list):
    """Save session history to file (avoids cookie size limits)."""
    filepath = SESSIONS_DIR / f"{session_id}.json"
    filepath.write_text(json.dumps(history, indent=2, default=str), encoding="utf-8")


def load_web_session(session_id: str) -> list:
    """Load session history from file."""
    filepath = SESSIONS_DIR / f"{session_id}.json"
    if filepath.exists():
        try:
            return json.loads(filepath.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


# Global assistant instance (lazy loaded)
_assistant = None
_learned = None


def get_assistant():
    global _assistant
    if _assistant is None:
        _assistant = ShamanAssistant()
    return _assistant


def get_learned():
    global _learned
    if _learned is None:
        _learned = LearnedKnowledge()
    return _learned


@app.route("/")
def index():
    """Render the chat interface."""
    # Initialize session
    if "session_id" not in session:
        session["session_id"] = datetime.now().strftime("%Y%m%d_%H%M%S")
        session["history"] = []

    return render_template("chat.html", session_id=session["session_id"])


@app.route("/api/chat", methods=["POST"])
def chat():
    """Handle chat messages."""
    data = request.json
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    # Get session ID (stored in cookie, small)
    session_id = session.get("session_id")
    if not session_id:
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        session["session_id"] = session_id
        session.modified = True

    # Load conversation history from file (not cookie - avoids size limits)
    history = load_web_session(session_id)

    try:
        assistant = get_assistant()
        response, history = assistant.agentic_chat(user_message, history)

        # Save to file (serialize to avoid JSON serialization errors)
        save_web_session(session_id, serialize_history(history))

        return jsonify({
            "response": response,
            "session_id": session_id
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/learn", methods=["POST"])
def learn():
    """Add knowledge."""
    data = request.json
    content = data.get("content", "").strip()

    if not content:
        return jsonify({"error": "Empty content"}), 400

    learned = get_learned()
    chunk = learned.add(content)

    return jsonify({
        "success": True,
        "chunk": chunk
    })


@app.route("/api/learned", methods=["GET"])
def get_learned_knowledge():
    """Get all learned knowledge."""
    learned = get_learned()
    return jsonify({"chunks": learned.list_all()})


@app.route("/api/forget/<int:chunk_id>", methods=["DELETE"])
def forget(chunk_id):
    """Remove learned knowledge."""
    learned = get_learned()
    if learned.delete(chunk_id):
        return jsonify({"success": True})
    return jsonify({"error": "Not found"}), 404


@app.route("/api/sessions", methods=["GET"])
def list_sessions():
    """List saved sessions."""
    sessions = ConversationMemory.list_sessions()
    return jsonify({"sessions": sessions})


@app.route("/api/session/new", methods=["POST"])
def new_session():
    """Start a new session."""
    # Create new session
    new_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    session["session_id"] = new_id
    session.modified = True

    # Initialize empty session file
    save_web_session(new_id, [])

    return jsonify({
        "success": True,
        "session_id": new_id
    })


@app.route("/api/session/load/<session_id>", methods=["POST"])
def load_session(session_id):
    """Load a previous session."""
    session["session_id"] = session_id
    session.modified = True

    history = load_web_session(session_id)

    return jsonify({
        "success": True,
        "session_id": session_id,
        "message_count": len(history)
    })


# Output directory for generated files
OUTPUT_DIR = Path("./output")


@app.route("/api/files", methods=["GET"])
def list_files():
    """List all generated files in the output directory."""
    if not OUTPUT_DIR.exists():
        return jsonify({"files": []})

    files = []
    for f in sorted(OUTPUT_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.name != ".gitkeep":
            files.append({
                "name": f.name,
                "type": f.suffix.lstrip("."),
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()
            })
    return jsonify({"files": files})


@app.route("/view/<filename>")
def view_file(filename):
    """Render a file for viewing in the browser."""
    filepath = OUTPUT_DIR / filename

    if not filepath.exists() or not filepath.is_file():
        return "File not found", 404

    # Security: ensure file is within output directory
    try:
        filepath.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return "Access denied", 403

    suffix = filepath.suffix.lower()

    if suffix == ".md":
        # Render markdown with Shaman styling
        try:
            import markdown
            content = filepath.read_text(encoding="utf-8")
            html_content = markdown.markdown(
                content,
                extensions=['tables', 'fenced_code', 'codehilite', 'toc']
            )
        except ImportError:
            # Fallback: show raw markdown in pre tag
            content = filepath.read_text(encoding="utf-8")
            html_content = f"<pre>{content}</pre>"

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{filename} - Shaman Assistant</title>
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --green-25: #F6FCF9;
            --green-50: #E6F7EF;
            --green-100: #C2EBD8;
            --green-200: #9ADFBF;
            --green-300: #6FD3A5;
            --green-400: #4AC78D;
            --green-500: #1EBB74;
            --green-600: #00A66F;
            --green-700: #008F5F;
            --green-800: #00784F;
            --green-900: #00623F;
            --grey-0: #FFFFFF;
            --grey-25: #FAFAFA;
            --grey-50: #F5F5F5;
            --grey-100: #E8E8E8;
            --grey-200: #D4D4D4;
            --grey-300: #B3B3B3;
            --grey-400: #8F8F8F;
            --grey-500: #6B6B6B;
            --grey-600: #525252;
            --grey-700: #3D3D3D;
            --grey-800: #292929;
            --grey-900: #323232;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Noto Sans', sans-serif;
            background: var(--grey-25);
            color: var(--grey-900);
            line-height: 1.7;
            padding: 0;
        }}
        .header {{
            background: var(--green-600);
            color: white;
            padding: 16px 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .header h1 {{
            font-size: 18px;
            font-weight: 600;
        }}
        .header a {{
            color: var(--green-100);
            text-decoration: none;
            font-size: 14px;
        }}
        .header a:hover {{
            color: white;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
            padding: 40px 32px;
        }}
        .content {{
            background: white;
            border-radius: 12px;
            padding: 48px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        h1 {{ color: var(--green-800); font-size: 32px; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 2px solid var(--green-100); }}
        h2 {{ color: var(--green-700); font-size: 24px; margin-top: 32px; margin-bottom: 16px; }}
        h3 {{ color: var(--green-600); font-size: 20px; margin-top: 24px; margin-bottom: 12px; }}
        h4 {{ color: var(--grey-700); font-size: 16px; margin-top: 20px; margin-bottom: 8px; }}
        p {{ margin-bottom: 16px; }}
        ul, ol {{ margin-bottom: 16px; padding-left: 24px; }}
        li {{ margin-bottom: 8px; }}
        code {{
            background: var(--grey-50);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: 'SF Mono', Monaco, Consolas, monospace;
            font-size: 14px;
            color: var(--green-700);
        }}
        pre {{
            background: var(--grey-800);
            color: var(--grey-100);
            padding: 20px;
            border-radius: 8px;
            overflow-x: auto;
            margin-bottom: 16px;
        }}
        pre code {{
            background: none;
            padding: 0;
            color: inherit;
        }}
        blockquote {{
            border-left: 4px solid var(--green-400);
            padding-left: 20px;
            margin: 16px 0;
            color: var(--grey-600);
            font-style: italic;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 16px;
        }}
        th, td {{
            border: 1px solid var(--grey-200);
            padding: 12px 16px;
            text-align: left;
        }}
        th {{
            background: var(--green-50);
            color: var(--green-800);
            font-weight: 600;
        }}
        tr:nth-child(even) {{
            background: var(--grey-25);
        }}
        a {{
            color: var(--green-600);
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        hr {{
            border: none;
            border-top: 1px solid var(--grey-200);
            margin: 32px 0;
        }}
        .download-btn {{
            display: inline-block;
            background: var(--green-600);
            color: white;
            padding: 10px 20px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: 500;
            margin-top: 24px;
        }}
        .download-btn:hover {{
            background: var(--green-700);
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{filename}</h1>
        <a href="/">← Back to Chat</a>
    </div>
    <div class="container">
        <div class="content">
            {html_content}
        </div>
        <a href="/download/{filename}" class="download-btn">Download File</a>
    </div>
</body>
</html>'''

    elif suffix == ".json":
        # Pretty-print JSON
        content = filepath.read_text(encoding="utf-8")
        try:
            data = json.loads(content)
            formatted = json.dumps(data, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            formatted = content

        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{filename} - Shaman Assistant</title>
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Noto Sans', sans-serif; background: #FAFAFA; padding: 20px; }}
        .header {{ background: #00A66F; color: white; padding: 16px 24px; margin: -20px -20px 20px; }}
        .header a {{ color: #C2EBD8; text-decoration: none; }}
        pre {{ background: #292929; color: #E8E8E8; padding: 24px; border-radius: 8px; overflow-x: auto; }}
        .string {{ color: #9ADFBF; }}
        .number {{ color: #6FD3A5; }}
        .boolean {{ color: #4AC78D; }}
        .null {{ color: #8F8F8F; }}
        .key {{ color: #C2EBD8; }}
    </style>
</head>
<body>
    <div class="header">
        <a href="/">← Back</a> | <strong>{filename}</strong>
    </div>
    <pre>{formatted}</pre>
</body>
</html>'''

    else:
        # Other files: offer download
        return f'''<!DOCTYPE html>
<html>
<head><title>{filename}</title></head>
<body>
    <p>File type not supported for preview.</p>
    <a href="/download/{filename}">Download {filename}</a>
</body>
</html>'''


@app.route("/download/<filename>")
def download_file(filename):
    """Download a generated file."""
    from flask import send_from_directory
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("Shaman Assistant - Web UI")
    print("=" * 50)
    print("\nOpen http://localhost:5000 in your browser")
    print("Press Ctrl+C to stop\n")

    app.run(debug=True, port=5000)
