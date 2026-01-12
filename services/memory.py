"""
Memory Service
==============
Handles conversation persistence and learned knowledge storage.
"""
import json
from pathlib import Path
from datetime import datetime
from typing import Optional


CONVERSATIONS_DIR = Path("./conversations")
CONVERSATIONS_DIR.mkdir(exist_ok=True)

LEARNED_KNOWLEDGE_FILE = Path("./learned_knowledge.json")


class ConversationMemory:
    """Manages conversation history persistence."""

    def __init__(self, session_id: Optional[str] = None):
        """
        Initialize conversation memory.

        Args:
            session_id: Optional session ID to resume. If None, creates new session.
        """
        if session_id:
            self.session_id = session_id
            self.filepath = CONVERSATIONS_DIR / f"{session_id}.json"
            self.history = self._load()
        else:
            self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.filepath = CONVERSATIONS_DIR / f"{self.session_id}.json"
            self.history = []

    def _load(self) -> list:
        """Load conversation history from file."""
        if self.filepath.exists():
            try:
                with open(self.filepath, "r") as f:
                    data = json.load(f)
                    return data.get("messages", [])
            except (json.JSONDecodeError, KeyError):
                return []
        return []

    def save(self):
        """Save current conversation history to file."""
        data = {
            "session_id": self.session_id,
            "updated_at": datetime.now().isoformat(),
            "messages": self._serialize_history()
        }
        with open(self.filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _serialize_history(self) -> list:
        """Convert history to JSON-serializable format."""
        serialized = []
        for msg in self.history:
            if isinstance(msg.get("content"), list):
                # Handle tool use blocks and thinking blocks
                content = []
                for block in msg["content"]:
                    if hasattr(block, "type"):
                        # Anthropic API object
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
                    else:
                        content.append(block)
                if content:  # Only add if there's content after filtering
                    serialized.append({"role": msg["role"], "content": content})
            else:
                serialized.append(msg)
        return serialized

    def add(self, role: str, content):
        """Add a message to history."""
        self.history.append({"role": role, "content": content})

    def get_history(self) -> list:
        """Get the conversation history."""
        return self.history

    def clear(self):
        """Clear conversation history."""
        self.history = []
        if self.filepath.exists():
            self.filepath.unlink()

    @staticmethod
    def list_sessions() -> list[dict]:
        """List all saved conversation sessions."""
        sessions = []
        for f in sorted(CONVERSATIONS_DIR.glob("*.json"), reverse=True):
            try:
                with open(f, "r") as file:
                    data = json.load(file)
                    sessions.append({
                        "session_id": data.get("session_id", f.stem),
                        "updated_at": data.get("updated_at", "unknown"),
                        "message_count": len(data.get("messages", []))
                    })
            except (json.JSONDecodeError, KeyError):
                continue
        return sessions


class LearnedKnowledge:
    """Manages user-provided knowledge chunks."""

    def __init__(self):
        self.filepath = LEARNED_KNOWLEDGE_FILE
        self.chunks = self._load()

    def _load(self) -> list[dict]:
        """Load learned knowledge from file."""
        if self.filepath.exists():
            try:
                with open(self.filepath, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return []
        return []

    def save(self):
        """Save learned knowledge to file."""
        with open(self.filepath, "w") as f:
            json.dump(self.chunks, f, indent=2)

    def add(self, content: str, category: str = "general") -> dict:
        """
        Add a knowledge chunk.

        Args:
            content: The knowledge content to store
            category: Category for the knowledge (e.g., "product", "process", "customer")

        Returns:
            The created chunk dict
        """
        chunk = {
            "id": len(self.chunks) + 1,
            "content": content,
            "category": category,
            "added_at": datetime.now().isoformat()
        }
        self.chunks.append(chunk)
        self.save()
        return chunk

    def list_all(self) -> list[dict]:
        """List all learned knowledge chunks."""
        return self.chunks

    def search(self, query: str) -> list[dict]:
        """Simple keyword search in learned knowledge."""
        query_lower = query.lower()
        return [
            chunk for chunk in self.chunks
            if query_lower in chunk["content"].lower()
        ]

    def delete(self, chunk_id: int) -> bool:
        """Delete a knowledge chunk by ID."""
        for i, chunk in enumerate(self.chunks):
            if chunk["id"] == chunk_id:
                self.chunks.pop(i)
                self.save()
                return True
        return False

    def get_context_string(self) -> str:
        """Get all learned knowledge as a context string for the LLM."""
        if not self.chunks:
            return ""

        parts = ["## Learned Knowledge (from user)"]
        for chunk in self.chunks:
            parts.append(f"- [{chunk['category']}] {chunk['content']}")

        return "\n".join(parts)
