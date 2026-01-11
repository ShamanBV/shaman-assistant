"""
MagicAnswer Orchestrator
========================
The brain of the system. Handles:
- Intent classification (bug/enhancement/question)
- Query optimization
- RAG pipeline
- Response generation
"""
import config
from models import Intent, ClassificationResult, Answer, SearchResult
from services.vector_store import VectorStore
from services.llm_service import LLMService
from services.cache import Cache


class MagicAnswerOrchestrator:
    """
    Main orchestrator for MagicAnswer.
    
    Flow:
    1. Check cache
    2. Classify intent
    3. Route (bug → Jira, enhancement → ProductBoard, question → RAG)
    4. For questions: optimize query → search → generate answer
    5. Cache result
    """
    
    def __init__(self):
        self.vector_store = VectorStore()
        self.llm = LLMService()
        self.cache = Cache()
    
    def process(self, question: str, skip_cache: bool = False) -> Answer:
        """
        Process a user question end-to-end.
        
        Args:
            question: User's question
            skip_cache: If True, bypass cache lookup
            
        Returns:
            Answer object with response and metadata
        """
        # 1. Check cache
        if not skip_cache:
            cached = self.cache.get(question)
            if cached:
                cached.cached = True
                return cached
        
        # 2. Classify intent
        classification = self.classify_intent(question)
        
        # 3. Route based on intent
        if classification.intent == Intent.BUG:
            if classification.confidence >= config.CONFIDENCE_THRESHOLD:
                return self._bug_response(question, classification)
        
        if classification.intent == Intent.ENHANCEMENT:
            if classification.confidence >= config.CONFIDENCE_THRESHOLD:
                return self._enhancement_response(question, classification)
        
        # 4. For questions (or low-confidence bug/enhancement): RAG pipeline
        answer = self._answer_question(question, classification)
        
        # 5. Cache the result
        self.cache.set(question, answer)
        
        return answer
    
    def classify_intent(self, question: str) -> ClassificationResult:
        """Classify the question intent."""
        return self.llm.classify_intent(question)
    
    def search(self, query: str, n_results: int = 10, sources: list = None) -> list[SearchResult]:
        """Direct search without RAG (for debugging)."""
        return self.vector_store.search(query, n_results=n_results, sources=sources)
    
    def _bug_response(self, question: str, classification: ClassificationResult) -> Answer:
        """Generate response for bug reports."""
        return Answer(
            text=f"""🐛 **This sounds like a bug report!**

Please report it here: {config.BUG_REPORT_URL}

When reporting, include:
- **Steps to reproduce**: What were you doing when this happened?
- **Expected behavior**: What should have happened?
- **Actual behavior**: What happened instead?
- **Screenshots/recordings**: If possible
- **Browser/device**: Chrome, Safari, etc.

This helps our team fix the issue faster. Thanks for reporting!""",
            sources=[],
            intent=Intent.BUG,
            original_question=question
        )
    
    def _enhancement_response(self, question: str, classification: ClassificationResult) -> Answer:
        """Generate response for enhancement requests."""
        return Answer(
            text=f"""💡 **Great product idea!**

Please log this enhancement request: {config.ENHANCEMENT_URL}

When submitting, consider including:
- **Use case**: What problem does this solve?
- **Who benefits**: Which users/customers need this?
- **Priority**: How urgent is this for your workflow?

This helps us prioritize and track feature requests. Thanks for the suggestion!""",
            sources=[],
            intent=Intent.ENHANCEMENT,
            original_question=question
        )
    
    def _answer_question(self, question: str, classification: ClassificationResult) -> Answer:
        """
        Full RAG pipeline for answering questions.
        
        1. Optimize query for better vector search
        2. Search knowledge base
        3. Generate answer with sources
        """
        # 1. Optimize query
        optimized_query = self.llm.optimize_query(question)
        
        # 2. Search vector store
        results = self.vector_store.search(optimized_query, n_results=10)
        
        # 3. Generate answer
        answer_text = self.llm.generate_answer(question, results)
        
        return Answer(
            text=answer_text,
            sources=results,
            intent=classification.intent,
            original_question=question,
            optimized_query=optimized_query
        )
    
    def get_stats(self) -> dict:
        """Get statistics for the system."""
        return {
            "knowledge_base": self.vector_store.get_stats(),
            "cache": self.cache.stats()
        }


# Convenience function for quick testing
def ask(question: str) -> str:
    """Quick helper to ask a question and get just the answer text."""
    orchestrator = MagicAnswerOrchestrator()
    answer = orchestrator.process(question)
    return answer.text
