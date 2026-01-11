"""
MagicAnswer Data Models
=======================
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Intent(Enum):
    """Classification of user question intent."""
    BUG = "bug"
    ENHANCEMENT = "enhancement"
    QUESTION = "question"
    UNCLEAR = "unclear"


class Source(Enum):
    """Knowledge base sources."""
    SLACK = "slack"
    HELPCENTER = "helpcenter"
    INTERCOM = "intercom"
    CONFLUENCE = "confluence"
    VIDEO = "video"


@dataclass
class ClassificationResult:
    """Result of intent classification."""
    intent: Intent
    confidence: float
    reasoning: str


@dataclass
class SearchResult:
    """A single search result from the knowledge base."""
    content: str
    source: str
    relevance: float
    title: Optional[str] = None
    url: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    
    @property
    def source_emoji(self) -> str:
        """Get emoji for source type."""
        return {
            "slack": "💬",
            "helpcenter": "📚",
            "intercom": "🎫",
            "confluence": "📄",
            "video": "🎥"
        }.get(self.source, "📎")
    
    @property
    def source_label(self) -> str:
        """Get display label for source."""
        return {
            "slack": "Slack",
            "helpcenter": "Help Center",
            "intercom": "Support Ticket",
            "confluence": "Confluence",
            "video": "Video Transcript"
        }.get(self.source, self.source)


@dataclass
class Answer:
    """Complete answer with sources and metadata."""
    text: str
    sources: list[SearchResult]
    intent: Intent
    original_question: str = ""
    optimized_query: str = ""
    cached: bool = False
    
    def format_sources(self, max_sources: int = 5) -> str:
        """Format sources for display."""
        if not self.sources:
            return ""
        
        lines = ["Sources:"]
        for i, source in enumerate(self.sources[:max_sources], 1):
            lines.append(f"  [{i}] {source.source_emoji} {source.source_label}: {source.title or 'N/A'}")
            if source.url:
                lines.append(f"      {source.url}")
        return "\n".join(lines)
