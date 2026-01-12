#!/usr/bin/env python3
"""
Shaman Assistant CLI
====================
AI-powered assistant for Shaman - pharma content authoring platform.

Usage:
    python main.py                      # Interactive Q&A mode
    python main.py --chat               # Agentic chat mode (with document generation)
    python main.py --ask "How do I..."  # Single question
    python main.py --sync all           # Sync all sources
    python main.py --sync confluence    # Sync specific source
    python main.py --stats              # Show statistics
"""
import argparse
import sys
from orchestrator import MagicAnswerOrchestrator, ShamanAssistant
from models import Intent


def main():
    parser = argparse.ArgumentParser(
        description="Shaman Assistant - AI-powered helper for pharma content authoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                           Interactive Q&A mode
  python main.py --chat                    Agentic chat mode (document generation)
  python main.py -a "How do I export?"     Ask a single question
  python main.py --sync confluence         Sync Confluence pages
  python main.py --stats                   Show KB statistics
        """
    )
    parser.add_argument(
        "--ask", "-a",
        type=str,
        help="Ask a single question"
    )
    parser.add_argument(
        "--search", "-s",
        type=str,
        help="Search without generating answer (debug mode)"
    )
    parser.add_argument(
        "--sync",
        type=str,
        choices=["all", "slack", "confluence", "intercom", "helpcenter", "video"],
        help="Sync a data source"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show knowledge base statistics"
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Skip cache for this query"
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Agentic chat mode with document generation"
    )
    parser.add_argument(
        "--resume",
        type=str,
        metavar="SESSION_ID",
        help="Resume a previous chat session (use with --chat)"
    )

    args = parser.parse_args()

    try:
        orchestrator = MagicAnswerOrchestrator()
    except Exception as e:
        print(f"Failed to initialize: {e}")
        print("Check your .env file and API keys.")
        sys.exit(1)

    if args.chat or args.resume:
        agentic_chat_mode(resume_session=args.resume)
    elif args.ask:
        answer = orchestrator.process(args.ask, skip_cache=args.no_cache)
        print_answer(answer)
    elif args.search:
        results = orchestrator.search(args.search)
        print_search_results(results)
    elif args.sync:
        run_sync(args.sync)
    elif args.stats:
        show_stats(orchestrator)
    else:
        interactive_mode(orchestrator)


def print_answer(answer):
    """Pretty print an answer."""
    print("\n" + "=" * 70)
    print(f"Intent: {answer.intent.value.upper()}", end="")
    if answer.cached:
        print(" (cached)", end="")
    print()
    if answer.optimized_query:
        print(f"Search query: \"{answer.optimized_query}\"")
    print("=" * 70)

    print(f"\n{answer.text}\n")

    if answer.sources:
        print("-" * 70)
        print(answer.format_sources())
    print()


def print_search_results(results):
    """Print raw search results."""
    print(f"\nFound {len(results)} results:\n")

    for i, r in enumerate(results, 1):
        print(f"[{i}] {r.source_label} (relevance: {r.relevance:.2f})")
        if r.title:
            print(f"    Title: {r.title}")
        if r.url:
            print(f"    URL: {r.url}")
        print(f"    {r.content[:200]}...")
        print()


def agentic_chat_mode(resume_session: str = None):
    """Agentic chat mode with document generation capabilities."""
    from services.memory import ConversationMemory, LearnedKnowledge

    # Initialize memory
    memory = ConversationMemory(session_id=resume_session)
    learned = LearnedKnowledge()

    print("\n" + "=" * 70)
    print("Shaman Assistant - Agentic Chat Mode")
    print("=" * 70)

    if resume_session and memory.history:
        print(f"Resumed session: {memory.session_id}")
        print(f"Loaded {len(memory.history)} previous messages")
    else:
        print(f"New session: {memory.session_id}")

    print("""
Capabilities:
  - Answer questions about Shaman platform
  - Search knowledge base (product docs, support tickets, etc.)
  - Generate documents (PPTX, DOCX, PDF)
  - Learn and remember information

Commands:
  /learn <text>     Add knowledge (e.g., /learn Customer X uses Veeva Vault)
  /learned          Show all learned knowledge
  /forget <id>      Remove learned knowledge by ID
  /sessions         List saved conversation sessions
  /save             Save current conversation
  /new              Start new conversation (saves current)
  /quit             Exit (auto-saves)

Type your request to get started.
""")
    print("=" * 70)

    try:
        assistant = ShamanAssistant()
    except Exception as e:
        print(f"Failed to initialize: {e}")
        print("Check your .env file and API keys.")
        return

    conversation_history = memory.get_history()

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nSaving conversation...")
            memory.history = conversation_history
            memory.save()
            print(f"Saved to session: {memory.session_id}")
            print("Goodbye!")
            break

        if not user_input:
            continue

        # Command: quit
        if user_input.lower() in ["/quit", "/exit", "/q"]:
            memory.history = conversation_history
            memory.save()
            print(f"Saved to session: {memory.session_id}")
            print("Goodbye!")
            break

        # Command: learn
        if user_input.lower().startswith("/learn "):
            content = user_input[7:].strip()
            if content:
                chunk = learned.add(content)
                print(f"Learned (ID: {chunk['id']}): {content}")
            else:
                print("Usage: /learn <knowledge to remember>")
            continue

        # Command: learned
        if user_input.lower() in ["/learned", "/knowledge"]:
            chunks = learned.list_all()
            if chunks:
                print(f"\nLearned Knowledge ({len(chunks)} items):")
                print("-" * 40)
                for chunk in chunks:
                    print(f"  [{chunk['id']}] {chunk['content'][:60]}...")
            else:
                print("No learned knowledge yet. Use /learn <text> to add.")
            continue

        # Command: forget
        if user_input.lower().startswith("/forget "):
            try:
                chunk_id = int(user_input[8:].strip())
                if learned.delete(chunk_id):
                    print(f"Removed knowledge ID: {chunk_id}")
                else:
                    print(f"Knowledge ID {chunk_id} not found")
            except ValueError:
                print("Usage: /forget <id>")
            continue

        # Command: sessions
        if user_input.lower() == "/sessions":
            sessions = ConversationMemory.list_sessions()
            if sessions:
                print(f"\nSaved Sessions ({len(sessions)}):")
                print("-" * 40)
                for s in sessions[:10]:  # Show last 10
                    print(f"  {s['session_id']} - {s['message_count']} messages")
            else:
                print("No saved sessions yet.")
            continue

        # Command: save
        if user_input.lower() == "/save":
            memory.history = conversation_history
            memory.save()
            print(f"Saved to session: {memory.session_id}")
            continue

        # Command: new
        if user_input.lower() == "/new":
            memory.history = conversation_history
            memory.save()
            print(f"Saved session: {memory.session_id}")
            memory = ConversationMemory()
            conversation_history = []
            print(f"Started new session: {memory.session_id}")
            continue

        # Regular message - process with assistant
        print("\nProcessing...")
        try:
            response, conversation_history = assistant.agentic_chat(
                user_input, conversation_history
            )
            print(f"\nAssistant: {response}")

            # Auto-save every few messages
            if len(conversation_history) % 6 == 0:
                memory.history = conversation_history
                memory.save()

        except Exception as e:
            print(f"\nError: {e}")
            import traceback
            traceback.print_exc()


def interactive_mode(orchestrator):
    """Interactive Q&A mode for testing."""
    print("\n" + "=" * 70)
    print("Shaman Assistant - Q&A Mode")
    print("=" * 70)
    print("""
Commands:
  /stats      Show knowledge base statistics
  /cache      Show cache statistics
  /clear      Clear cache
  /search X   Raw search (no answer generation)
  /sources    Toggle source display
  /chat       Switch to agentic chat mode
  /quit       Exit

Just type your question to get an answer.
""")
    print("=" * 70)
    
    show_sources = True

    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nGoodbye!")
            break

        if not question:
            continue

        # Commands
        if question.lower() in ["/quit", "/exit", "/q"]:
            print("Goodbye!")
            break

        if question.lower() == "/chat":
            agentic_chat_mode()
            break

        if question.lower() == "/stats":
            show_stats(orchestrator)
            continue

        if question.lower() == "/cache":
            stats = orchestrator.cache.stats()
            print(f"\nCache Statistics:")
            print(f"   Entries: {stats['entries']}")
            print(f"   TTL: {stats['ttl_seconds']}s")
            print(f"   Hits: {stats['hits']}")
            print(f"   Misses: {stats['misses']}")
            print(f"   Hit rate: {stats['hit_rate']}")
            continue

        if question.lower() == "/clear":
            cleared = orchestrator.cache.clear()
            print(f"Cleared {cleared} cache entries.")
            continue

        if question.lower() == "/sources":
            show_sources = not show_sources
            print(f"Source display: {'ON' if show_sources else 'OFF'}")
            continue

        if question.lower().startswith("/search "):
            query = question[8:].strip()
            results = orchestrator.search(query)
            print_search_results(results)
            continue

        # Process question
        print("\nProcessing...")

        try:
            answer = orchestrator.process(question)
            print_answer(answer) if show_sources else print(f"\n{answer.text}\n")
        except Exception as e:
            print(f"\nError: {e}")
            import traceback
            traceback.print_exc()


def show_stats(orchestrator):
    """Show knowledge base statistics."""
    stats = orchestrator.get_stats()

    print("\nKnowledge Base Statistics:")
    print("-" * 40)

    total = 0
    for source, count in stats["knowledge_base"].items():
        print(f"   {source:15} {count:>6} documents")
        total += count

    print("-" * 40)
    print(f"   {'Total':15} {total:>6} documents")

    cache = stats["cache"]
    print(f"\nCache: {cache['entries']} entries, {cache['hit_rate']} hit rate")


def run_sync(source: str):
    """Run sync for specified source."""
    from services.vector_store import VectorStore
    
    print(f"\nSyncing: {source}")

    vs = VectorStore()

    if source == "all":
        print("Syncing all sources...")
        run_sync("slack")
        run_sync("helpcenter")
        run_sync("intercom")
        run_sync("confluence")
        run_sync("video")

    elif source == "confluence":
        try:
            from ingest.confluence import ConfluenceIngestor
            ingestor = ConfluenceIngestor(vs)
            count = ingestor.sync()
            print(f"Synced {count} Confluence pages")
        except ImportError:
            print("Confluence ingestor not yet implemented")
        except Exception as e:
            print(f"Error: {e}")

    elif source == "video":
        try:
            from ingest.video_transcripts import VideoIngestor
            ingestor = VideoIngestor(vs)
            count = ingestor.sync()
            print(f"Synced {count} video transcripts")
        except ImportError:
            print("Video ingestor not yet implemented")
        except Exception as e:
            print(f"Error: {e}")

    elif source in ["slack", "helpcenter", "intercom"]:
        print(f"Use your existing multi_source_rag.py for {source}:")
        print(f"   python multi_source_rag.py --sync-{source}")

    else:
        print(f"Unknown source: {source}")


if __name__ == "__main__":
    main()
