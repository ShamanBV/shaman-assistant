#!/usr/bin/env python3
"""
MagicAnswer CLI
===============
Test the system locally in PyCharm.

Usage:
    python main.py                      # Interactive mode
    python main.py --ask "How do I..."  # Single question
    python main.py --sync all           # Sync all sources
    python main.py --sync confluence    # Sync specific source
    python main.py --stats              # Show statistics
"""
import argparse
import sys
from orchestrator import MagicAnswerOrchestrator
from models import Intent


def main():
    parser = argparse.ArgumentParser(
        description="MagicAnswer - Internal Q&A System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                           Interactive mode
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
    
    args = parser.parse_args()
    
    try:
        orchestrator = MagicAnswerOrchestrator()
    except Exception as e:
        print(f"❌ Failed to initialize: {e}")
        print("Check your .env file and API keys.")
        sys.exit(1)
    
    if args.ask:
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
    intent_emoji = {
        Intent.BUG: "🐛",
        Intent.ENHANCEMENT: "💡",
        Intent.QUESTION: "❓",
        Intent.UNCLEAR: "🤔"
    }
    
    print("\n" + "=" * 70)
    print(f"{intent_emoji.get(answer.intent, '📝')} Intent: {answer.intent.value.upper()}", end="")
    if answer.cached:
        print(" (cached)", end="")
    print()
    if answer.optimized_query:
        print(f"🔍 Search query: \"{answer.optimized_query}\"")
    print("=" * 70)
    
    print(f"\n{answer.text}\n")
    
    if answer.sources:
        print("-" * 70)
        print(answer.format_sources())
    print()


def print_search_results(results):
    """Print raw search results."""
    print(f"\n📊 Found {len(results)} results:\n")
    
    for i, r in enumerate(results, 1):
        print(f"[{i}] {r.source_emoji} {r.source_label} (relevance: {r.relevance:.2f})")
        if r.title:
            print(f"    Title: {r.title}")
        if r.url:
            print(f"    URL: {r.url}")
        print(f"    {r.content[:200]}...")
        print()


def interactive_mode(orchestrator):
    """Interactive Q&A mode for testing."""
    print("\n" + "=" * 70)
    print("🔮 MagicAnswer - Interactive Mode")
    print("=" * 70)
    print("""
Commands:
  /stats      Show knowledge base statistics
  /cache      Show cache statistics  
  /clear      Clear cache
  /search X   Raw search (no answer generation)
  /sources    Toggle source display
  /quit       Exit

Just type your question to get an answer.
""")
    print("=" * 70)
    
    show_sources = True
    
    while True:
        try:
            question = input("\n❓ ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nGoodbye! 👋")
            break
        
        if not question:
            continue
        
        # Commands
        if question.lower() in ["/quit", "/exit", "/q"]:
            print("Goodbye! 👋")
            break
        
        if question.lower() == "/stats":
            show_stats(orchestrator)
            continue
        
        if question.lower() == "/cache":
            stats = orchestrator.cache.stats()
            print(f"\n📦 Cache Statistics:")
            print(f"   Entries: {stats['entries']}")
            print(f"   TTL: {stats['ttl_seconds']}s")
            print(f"   Hits: {stats['hits']}")
            print(f"   Misses: {stats['misses']}")
            print(f"   Hit rate: {stats['hit_rate']}")
            continue
        
        if question.lower() == "/clear":
            cleared = orchestrator.cache.clear()
            print(f"🗑️  Cleared {cleared} cache entries.")
            continue
        
        if question.lower() == "/sources":
            show_sources = not show_sources
            print(f"📚 Source display: {'ON' if show_sources else 'OFF'}")
            continue
        
        if question.lower().startswith("/search "):
            query = question[8:].strip()
            results = orchestrator.search(query)
            print_search_results(results)
            continue
        
        # Process question
        print("\n🤔 Processing...")
        
        try:
            answer = orchestrator.process(question)
            print_answer(answer) if show_sources else print(f"\n{answer.text}\n")
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()


def show_stats(orchestrator):
    """Show knowledge base statistics."""
    stats = orchestrator.get_stats()
    
    print("\n📊 Knowledge Base Statistics:")
    print("-" * 40)
    
    total = 0
    for source, count in stats["knowledge_base"].items():
        emoji = {
            "slack": "💬",
            "helpcenter": "📚",
            "intercom": "🎫",
            "confluence": "📄",
            "video": "🎥"
        }.get(source, "📎")
        print(f"   {emoji} {source:15} {count:>6} documents")
        total += count
    
    print("-" * 40)
    print(f"   {'Total':18} {total:>6} documents")
    
    cache = stats["cache"]
    print(f"\n📦 Cache: {cache['entries']} entries, {cache['hit_rate']} hit rate")


def run_sync(source: str):
    """Run sync for specified source."""
    from services.vector_store import VectorStore
    
    print(f"\n🔄 Syncing: {source}")
    
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
            print(f"✅ Synced {count} Confluence pages")
        except ImportError:
            print("❌ Confluence ingestor not yet implemented")
        except Exception as e:
            print(f"❌ Error: {e}")
            
    elif source == "video":
        try:
            from ingest.video_transcripts import VideoIngestor
            ingestor = VideoIngestor(vs)
            count = ingestor.sync()
            print(f"✅ Synced {count} video transcripts")
        except ImportError:
            print("❌ Video ingestor not yet implemented")
        except Exception as e:
            print(f"❌ Error: {e}")
            
    elif source in ["slack", "helpcenter", "intercom"]:
        print(f"💡 Use your existing multi_source_rag.py for {source}:")
        print(f"   python multi_source_rag.py --sync-{source}")
        
    else:
        print(f"❌ Unknown source: {source}")


if __name__ == "__main__":
    main()
