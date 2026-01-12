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
from flask import Flask, render_template, request, jsonify, session
from datetime import datetime
import secrets

from orchestrator import ShamanAssistant
from services.memory import ConversationMemory, LearnedKnowledge

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

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

    # Get or initialize conversation history
    history = session.get("history", [])

    try:
        assistant = get_assistant()
        response, history = assistant.agentic_chat(user_message, history)

        # Update session
        session["history"] = history
        session.modified = True

        return jsonify({
            "response": response,
            "session_id": session.get("session_id")
        })
    except Exception as e:
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
    # Save current session
    if session.get("history"):
        memory = ConversationMemory(session_id=session.get("session_id"))
        memory.history = session["history"]
        memory.save()

    # Create new session
    session["session_id"] = datetime.now().strftime("%Y%m%d_%H%M%S")
    session["history"] = []
    session.modified = True

    return jsonify({
        "success": True,
        "session_id": session["session_id"]
    })


@app.route("/api/session/load/<session_id>", methods=["POST"])
def load_session(session_id):
    """Load a previous session."""
    memory = ConversationMemory(session_id=session_id)

    session["session_id"] = session_id
    session["history"] = memory.get_history()
    session.modified = True

    return jsonify({
        "success": True,
        "session_id": session_id,
        "message_count": len(session["history"])
    })


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("Shaman Assistant - Web UI")
    print("=" * 50)
    print("\nOpen http://localhost:5000 in your browser")
    print("Press Ctrl+C to stop\n")

    app.run(debug=True, port=5000)
