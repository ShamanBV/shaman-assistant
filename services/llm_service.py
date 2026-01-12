"""
LLM Service
===========
Handles all Claude API interactions for classification, query optimization, and answer generation.
Includes agentic tool-use capabilities for document generation.
"""
import json
import anthropic
import config
from models import Intent, ClassificationResult, SearchResult
from tools import DOCUMENT_TOOLS, process_document_tool


# Expanded system prompt for Shaman Assistant
SHAMAN_SYSTEM_PROMPT = """You are the Shaman Assistant, an AI-powered helper for Shaman - a B2B SaaS content authoring platform for pharmaceutical companies.

## About Shaman
Shaman helps pharmaceutical marketers create compliant marketing materials including:
- Emails (multichannel campaigns)
- CLM presentations (Closed-Loop Marketing for sales reps)
- Promotional content for HCPs (Healthcare Professionals)

Key integrations:
- Veeva Vault (PromoMats, Medical) for MLR review and compliance
- Salesforce Marketing Cloud for email distribution

## Your Capabilities

### 1. Knowledge Retrieval
Use `search_knowledge` to find information about:
- Product documentation and features
- Customer onboarding playbooks
- Pharma compliance requirements (MLR workflows)
- Technical integration guides

**Always search first** when answering questions about Shaman or pharma marketing.

### 2. Document Generation
You can create professional deliverables:

- **Presentations** (`create_presentation`): Customer onboarding decks, training materials, sales enablement
- **Documents** (`create_document`): SOPs, implementation guides, proposals, checklists
- **PDF Reports** (`create_pdf_report`): Compliance reports, formal documentation

## Guidelines

When generating documents:
1. Use professional, pharma-appropriate language
2. Include compliance considerations where relevant
3. Structure content clearly
4. Add speaker notes for presentations
5. Keep content concise and actionable

## Response Style
- Be concise and direct
- Confirm what you're creating before generating files
- After creating files, provide the path and brief summary
- Ask specific questions if you need more information
"""


# Tool definitions including search and document generation
SHAMAN_TOOLS = [
    {
        "name": "search_knowledge",
        "description": "Search Shaman's knowledge base for product documentation, customer playbooks, pharma compliance guides, and marketing materials.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "n_results": {"type": "integer", "default": 5}
            },
            "required": ["query"]
        }
    },
    *DOCUMENT_TOOLS
]


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

    def agentic_chat(
        self,
        user_message: str,
        conversation_history: list,
        search_fn=None
    ) -> tuple[str, list]:
        """
        Agentic chat with tool use capabilities.

        Handles tool calls in a loop until the model produces a final response.

        Args:
            user_message: The user's message
            conversation_history: List of prior messages
            search_fn: Function to search knowledge base (query, n_results) -> list[dict]

        Returns:
            Tuple of (final_response_text, updated_conversation_history)
        """
        conversation_history.append({"role": "user", "content": user_message})

        response = self.client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=8192,
            system=SHAMAN_SYSTEM_PROMPT,
            tools=SHAMAN_TOOLS,
            messages=conversation_history
        )

        # Agentic loop - process tool calls until done
        while response.stop_reason == "tool_use":
            tool_results = []
            assistant_content = response.content

            for block in response.content:
                if block.type == "tool_use":
                    print(f"  -> Using tool: {block.name}")
                    result = self._process_tool_call(block.name, block.input, search_fn)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            conversation_history.append({"role": "assistant", "content": assistant_content})
            conversation_history.append({"role": "user", "content": tool_results})

            response = self.client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=8192,
                system=SHAMAN_SYSTEM_PROMPT,
                tools=SHAMAN_TOOLS,
                messages=conversation_history
            )

        # Extract final text
        final_response = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )
        conversation_history.append({"role": "assistant", "content": response.content})

        return final_response, conversation_history

    def _process_tool_call(self, tool_name: str, tool_input: dict, search_fn=None) -> str:
        """
        Process a tool call and return the result.

        Args:
            tool_name: Name of the tool to execute
            tool_input: Tool input parameters
            search_fn: Function for knowledge base search

        Returns:
            Tool result as string
        """
        if tool_name == "search_knowledge":
            if search_fn is None:
                return json.dumps({"error": "Search function not available"})
            results = search_fn(tool_input["query"], tool_input.get("n_results", 5))
            return json.dumps(results, indent=2, default=str)
        elif tool_name in ["create_presentation", "create_document", "create_pdf_report"]:
            return process_document_tool(tool_name, tool_input)
        else:
            return f"Unknown tool: {tool_name}"
