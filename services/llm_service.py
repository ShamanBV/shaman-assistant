"""
LLM Service
===========
Handles all Claude API interactions for classification, query optimization, and answer generation.
"""
import json
import anthropic
import config
from models import Intent, ClassificationResult, SearchResult


class LLMService:
    """Service for LLM operations using Claude."""
    
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.model = config.LLM_MODEL
    
    def classify_intent(self, question: str) -> ClassificationResult:
        """
        Classify question into bug/enhancement/question.
        
        Returns:
            ClassificationResult with intent, confidence, and reasoning
        """
        prompt = f"""Classify this user message into exactly one category.

Categories:
- BUG: User reports something broken, not working, error, crash, unexpected behavior
  Examples: "X is not working", "I get an error when...", "The button doesn't respond"
  
- ENHANCEMENT: User requests new feature, improvement, suggestion
  Examples: "Can you add...", "It would be nice if...", "Feature request:", "I wish..."
  
- QUESTION: User asks how to do something, needs information, troubleshooting steps
  Examples: "How do I...", "Where can I find...", "What does X mean?", "Why is..."

Message: "{question}"

Respond with valid JSON only, no markdown:
{{"intent": "BUG|ENHANCEMENT|QUESTION", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        
        try:
            # Clean potential markdown formatting
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            
            result = json.loads(text)
            return ClassificationResult(
                intent=Intent(result["intent"].lower()),
                confidence=float(result["confidence"]),
                reasoning=result["reasoning"]
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"Warning: Failed to parse classification: {e}")
            return ClassificationResult(
                intent=Intent.QUESTION,
                confidence=0.5,
                reasoning="Failed to parse response, defaulting to question"
            )
    
    def optimize_query(self, question: str) -> str:
        """
        Rewrite question as an optimal search query for vector search.
        
        Args:
            question: Original user question
            
        Returns:
            Optimized search query string
        """
        prompt = f"""Rewrite this question as an optimal search query for a knowledge base about a pharma content authoring platform.

Rules:
- Remove filler words and pleasantries ("Hi", "Please", "I was wondering")
- Keep key technical terms
- Expand relevant acronyms:
  - CLM = Closed Loop Marketing
  - MLR = Medical Legal Review
  - HCP = Healthcare Professional
  - Veeva = Veeva Vault/PromoMats
- Keep it concise (3-10 words ideal)
- Focus on the core topic

Original: "{question}"

Return only the optimized query, nothing else."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return response.content[0].text.strip().strip('"')
    
    def generate_answer(
        self,
        question: str,
        sources: list[SearchResult],
        include_sources: bool = True
    ) -> str:
        """
        Generate an answer using RAG with the provided sources.
        
        Args:
            question: Original user question
            sources: List of SearchResult objects from vector search
            include_sources: Whether to include source citations
            
        Returns:
            Generated answer text
        """
        if not sources:
            return (
                "I couldn't find relevant information in the knowledge base for this question.\n\n"
                "You could try:\n"
                "- Rephrasing your question\n"
                "- Asking in the #product-questions Slack channel\n"
                "- Checking the Help Center directly"
            )
        
        # Build context with source labels
        context_parts = []
        for i, source in enumerate(sources, 1):
            title_part = f" - {source.title}" if source.title else ""
            url_part = f"\nURL: {source.url}" if source.url else ""
            
            context_parts.append(
                f"[{i}] {source.source_emoji} {source.source_label}{title_part}{url_part}\n"
                f"{source.content[:1500]}"
            )
        
        context = "\n\n---\n\n".join(context_parts)
        
        prompt = f"""You are a helpful assistant answering questions about Shaman, a pharma content authoring platform.

Use the knowledge base sources below to answer the question. These sources include:
- Slack conversations (internal team discussions)
- Help Center articles (product documentation)
- Support tickets (customer conversations)
- Confluence pages (internal documentation)
- Video transcripts (training and demo content)

SOURCES:
{context}

QUESTION: {question}

Guidelines:
- Answer based on the sources provided
- Cite sources as [1], [2], etc. when referencing specific information
- If the sources partially answer the question, note what's missing
- If there's a Help Center article on the topic, mention it
- Be concise and actionable
- If you're not sure, say so rather than making things up"""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return response.content[0].text
    
    def summarize_for_slack(self, answer: str, max_length: int = 500) -> str:
        """
        Summarize a long answer for Slack (optional, for later use).
        
        Args:
            answer: Full answer text
            max_length: Maximum character length
            
        Returns:
            Summarized answer
        """
        if len(answer) <= max_length:
            return answer
        
        prompt = f"""Summarize this answer in under {max_length} characters while keeping the key information and any source citations:

{answer}

Return only the summary."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return response.content[0].text
