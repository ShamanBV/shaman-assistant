"""
Analyze indexed content to improve intent classifier patterns.

Extracts questions from:
1. Slack product-questions channel (internal team questions)
2. Slack qa-hero-cs-ops-boards (bug reports - less relevant for intent training)
3. Intercom conversations (end user questions, focus on unanswered/escalated)
"""

import os
import json
import chromadb
from dotenv import load_dotenv
from anthropic import Anthropic
from collections import defaultdict

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./knowledge_base")

def extract_questions():
    """Extract questions from indexed collections."""
    client = chromadb.PersistentClient(path=DB_PATH)

    results = {
        "slack_product_questions": [],
        "slack_qa_hero": [],
        "intercom_unanswered": []
    }

    # Get Slack messages
    try:
        slack_collection = client.get_collection("slack_messages")
        slack_data = slack_collection.get(include=["documents", "metadatas"])

        for doc, meta in zip(slack_data["documents"], slack_data["metadatas"]):
            channel = meta.get("channel", "")

            # Extract the question part (first message in thread usually)
            lines = doc.split("\n")
            for line in lines:
                # Look for lines that are questions
                if "?" in line and len(line) > 10:
                    clean_line = line.strip()
                    # Remove user prefixes like "[User Name]:"
                    if "]:" in clean_line:
                        clean_line = clean_line.split("]:", 1)[1].strip()

                    if "product-questions" in channel:
                        results["slack_product_questions"].append({
                            "question": clean_line,
                            "channel": channel,
                            "full_context": doc[:500]
                        })
                    elif "qa-hero" in channel:
                        results["slack_qa_hero"].append({
                            "question": clean_line,
                            "channel": channel,
                            "full_context": doc[:500]
                        })

        print(f"Found {len(results['slack_product_questions'])} questions from product-questions")
        print(f"Found {len(results['slack_qa_hero'])} questions from qa-hero")

    except Exception as e:
        print(f"Error reading Slack: {e}")

    # Get Intercom conversations
    try:
        intercom_collection = client.get_collection("intercom_conversations")
        intercom_data = intercom_collection.get(include=["documents", "metadatas"])

        for doc, meta in zip(intercom_data["documents"], intercom_data["metadatas"]):
            # Look for conversations that seem unanswered or escalated
            # These usually have keywords or no resolution in the thread
            is_potentially_escalated = (
                "let me check" in doc.lower() or
                "i'll get back" in doc.lower() or
                "need to ask" in doc.lower() or
                "escalat" in doc.lower() or
                "product team" in doc.lower() or
                "not sure" in doc.lower()
            )

            # Extract questions
            for line in doc.split("\n"):
                if "?" in line and len(line) > 10:
                    clean_line = line.strip()
                    if "]:" in clean_line:
                        clean_line = clean_line.split("]:", 1)[1].strip()

                    results["intercom_unanswered"].append({
                        "question": clean_line,
                        "is_escalated": is_potentially_escalated,
                        "full_context": doc[:500]
                    })

        print(f"Found {len(results['intercom_unanswered'])} questions from Intercom")

    except Exception as e:
        print(f"Error reading Intercom: {e}")

    return results


def analyze_with_claude(questions: dict) -> dict:
    """Use Claude to analyze question patterns and suggest intent improvements."""

    client = Anthropic()

    # Sample questions for analysis (limit to avoid token overload)
    sample = {
        "product_questions": questions["slack_product_questions"][:50],
        "qa_hero": questions["slack_qa_hero"][:30],
        "intercom": [q for q in questions["intercom_unanswered"] if q.get("is_escalated")][:50]
    }

    prompt = f"""Analyze these questions from a B2B SaaS support system (Shaman platform).

PRODUCT QUESTIONS (internal team asking about features/how-to):
{json.dumps([q["question"] for q in sample["product_questions"]], indent=2)}

QA/BUG REPORTS (internal team reporting bugs):
{json.dumps([q["question"] for q in sample["qa_hero"]], indent=2)}

INTERCOM (customer questions that were escalated/unanswered):
{json.dumps([q["question"] for q in sample["intercom"]], indent=2)}

Current intent categories:
- how_to: Questions about how to do something, feature inquiries
- bug_veeva: Bug related to Veeva integration
- bug_config: Bug related to configuration
- bug_product: Bug in product functionality
- feature_request: Suggesting new functionality
- escalation: Urgent issue needing human attention
- greeting: Just saying hi, thanks, etc.

ANALYZE AND PROVIDE:

1. COMMON PATTERNS - What patterns appear frequently in each category?
   Group similar phrasings together.

2. MISSING INTENTS - Are there question types that don't fit the current categories?
   Suggest new intent categories if needed.

3. HIGH-CONFIDENCE PATTERNS - Phrases that clearly indicate a specific intent (0.9+ confidence)
   Format: "pattern" → intent

4. AMBIGUOUS PATTERNS - Questions that are unclear and need clarification
   Format: "pattern" → why it's ambiguous

5. ENTITY PATTERNS - Common ways users mention:
   - Features (what naming conventions?)
   - Customers (how do they reference accounts?)
   - Errors (what error formats appear?)
   - Urgency indicators

6. SUGGESTED CLASSIFIER PROMPT IMPROVEMENTS - Specific text to add to the intent classifier prompt

Provide actionable, specific patterns from the actual questions shown."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


def main():
    print("=" * 60)
    print("ANALYZING INDEXED CONTENT FOR INTENT PATTERNS")
    print("=" * 60)

    # Extract questions
    print("\n📥 Extracting questions from knowledge base...")
    questions = extract_questions()

    # Save raw questions for reference
    with open("extracted_questions.json", "w") as f:
        json.dump(questions, f, indent=2)
    print("\n💾 Saved raw questions to extracted_questions.json")

    # Analyze with Claude
    print("\n🤖 Analyzing patterns with Claude...")
    analysis = analyze_with_claude(questions)

    # Save analysis
    with open("intent_analysis.md", "w") as f:
        f.write("# Intent Classifier Analysis\n\n")
        f.write(f"Based on {len(questions['slack_product_questions'])} product questions, ")
        f.write(f"{len(questions['slack_qa_hero'])} QA/bug reports, ")
        f.write(f"and {len(questions['intercom_unanswered'])} Intercom questions.\n\n")
        f.write(analysis)

    print("\n📊 Analysis saved to intent_analysis.md")
    print("\n" + "=" * 60)
    print(analysis)


if __name__ == "__main__":
    main()
