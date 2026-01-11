"""
Documentation Gap Analysis

Analyzes questions from Slack and Intercom to identify:
1. Common question topics/themes
2. Existing documentation coverage
3. Gaps where documentation is missing or insufficient

Run: python analyze_doc_gaps.py
"""

import os
import json
import chromadb
from dotenv import load_dotenv
from anthropic import Anthropic
from collections import defaultdict

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./knowledge_base")


def extract_all_questions():
    """Extract questions from Slack and Intercom."""
    client = chromadb.PersistentClient(path=DB_PATH)

    questions = {
        "slack": [],
        "intercom": []
    }

    # Extract from Slack
    try:
        slack_col = client.get_collection("slack_messages")
        slack_data = slack_col.get(include=["documents", "metadatas"])

        for doc, meta in zip(slack_data["documents"], slack_data["metadatas"]):
            channel = meta.get("channel", "unknown")
            for line in doc.split("\n"):
                line = line.strip()
                # Questions or statements that indicate need for help
                if len(line) > 20 and ("?" in line or
                    line.lower().startswith(("how ", "why ", "what ", "can ", "is ", "does ", "where ", "when ", "could "))):
                    # Clean up user prefix
                    if "]:" in line:
                        line = line.split("]:", 1)[1].strip()
                    if line and len(line) > 15:
                        questions["slack"].append({
                            "question": line[:300],
                            "channel": channel,
                            "source": "slack"
                        })

        print(f"Extracted {len(questions['slack'])} questions from Slack")

    except Exception as e:
        print(f"Error reading Slack: {e}")

    # Extract from Intercom
    try:
        intercom_col = client.get_collection("intercom_conversations")
        intercom_data = intercom_col.get(include=["documents", "metadatas"])

        for doc, meta in zip(intercom_data["documents"], intercom_data["metadatas"]):
            for line in doc.split("\n"):
                line = line.strip()
                # Skip agent responses, focus on customer questions
                if "Agent (" in line:
                    continue
                if len(line) > 20 and ("?" in line or
                    line.lower().startswith(("how ", "why ", "what ", "can ", "is ", "does ", "where ", "when ", "could "))):
                    if "]:" in line:
                        line = line.split("]:", 1)[1].strip()
                    if line and len(line) > 15:
                        questions["intercom"].append({
                            "question": line[:300],
                            "source": "intercom"
                        })

        print(f"Extracted {len(questions['intercom'])} questions from Intercom")

    except Exception as e:
        print(f"Error reading Intercom: {e}")

    return questions


def get_existing_documentation():
    """Inventory what documentation exists in the knowledge base."""
    client = chromadb.PersistentClient(path=DB_PATH)

    doc_inventory = {}

    collections_to_check = [
        ("helpcenter", "Help Center Articles"),
        ("veeva", "Veeva Documentation"),
        ("confluence", "Confluence Pages"),
        ("pdf", "PDF Documents"),
        ("manual", "Manual Documents"),
        ("features", "Feature Registry"),
        ("video", "Video Transcripts")
    ]

    for col_name, label in collections_to_check:
        try:
            col = client.get_collection(f"{col_name}_docs" if col_name not in ["features"] else
                                        "feature_registry" if col_name == "features" else f"{col_name}_docs")
        except:
            try:
                # Try alternate naming
                names = {
                    "helpcenter": "helpcenter_articles",
                    "veeva": "veeva_docs",
                    "confluence": "confluence_pages",
                    "pdf": "pdf_documents",
                    "manual": "manual_documents",
                    "features": "feature_registry",
                    "video": "video_transcripts"
                }
                col = client.get_collection(names.get(col_name, col_name))
            except Exception as e:
                doc_inventory[label] = {"count": 0, "topics": [], "error": str(e)}
                continue

        try:
            data = col.get(include=["documents", "metadatas"])

            # Extract topics/titles from documents
            topics = []
            for doc, meta in zip(data["documents"][:50], data["metadatas"][:50]):
                title = meta.get("title", meta.get("name", ""))
                if title:
                    topics.append(title)
                elif doc:
                    # First line often is title
                    first_line = doc.split("\n")[0][:100]
                    topics.append(first_line)

            doc_inventory[label] = {
                "count": len(data["documents"]),
                "topics": topics[:30]
            }
        except Exception as e:
            doc_inventory[label] = {"count": 0, "topics": [], "error": str(e)}

    return doc_inventory


def analyze_with_claude(questions: dict, documentation: dict) -> str:
    """Use Claude to analyze gaps between questions and documentation."""

    client = Anthropic()

    # Sample questions (limit to manage tokens)
    slack_sample = questions["slack"][:100]
    intercom_sample = questions["intercom"][:100]

    prompt = f"""Analyze the documentation coverage for a B2B SaaS support team (Shaman platform - pharmaceutical content management).

## QUESTIONS FROM INTERNAL TEAM (Slack)
These are questions from internal support/CS team members:
{json.dumps([q["question"] for q in slack_sample], indent=2)}

## QUESTIONS FROM CUSTOMERS (Intercom)
These are questions from end customers:
{json.dumps([q["question"] for q in intercom_sample], indent=2)}

## EXISTING DOCUMENTATION
{json.dumps(documentation, indent=2)}

## ANALYSIS TASKS

### 1. TOPIC CLUSTERING
Group ALL the questions into logical topic clusters. For each cluster provide:
- Topic name
- Number of questions (approximate)
- Example questions (2-3)
- Urgency (how often this comes up)

### 2. DOCUMENTATION COVERAGE MATRIX
For each topic cluster, rate the documentation coverage:
| Topic | Coverage | Existing Sources | Gap Description |
|-------|----------|-----------------|-----------------|
| ... | None/Partial/Good | ... | ... |

Use:
- **None**: No documentation exists for this topic
- **Partial**: Some docs exist but incomplete or outdated
- **Good**: Well documented

### 3. PRIORITY GAPS
List the TOP 10 documentation gaps that should be addressed, ordered by:
- Frequency of questions
- Impact on support efficiency
- Customer-facing importance

Format:
1. **[Topic]** - [Why it's a gap] - Suggested doc: [what to create]

### 4. QUICK WINS
List 5 documentation improvements that would be easy to implement and have high impact.

### 5. RECOMMENDED DOCUMENTATION STRUCTURE
Suggest a documentation structure/hierarchy that would cover the identified gaps.

Be specific and actionable. Reference actual questions from the data."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


def main():
    print("=" * 70)
    print("DOCUMENTATION GAP ANALYSIS")
    print("=" * 70)

    # Step 1: Extract questions
    print("\n📥 Step 1: Extracting questions from knowledge base...")
    questions = extract_all_questions()

    total_questions = len(questions["slack"]) + len(questions["intercom"])
    print(f"   Total questions extracted: {total_questions}")

    # Save raw questions
    with open("all_questions.json", "w") as f:
        json.dump(questions, f, indent=2)
    print("   Saved to all_questions.json")

    # Step 2: Inventory existing docs
    print("\n📚 Step 2: Inventorying existing documentation...")
    documentation = get_existing_documentation()

    for source, info in documentation.items():
        count = info.get("count", 0)
        error = info.get("error", "")
        if error:
            print(f"   {source}: ⚠ {error}")
        else:
            print(f"   {source}: {count} documents")

    # Save inventory
    with open("doc_inventory.json", "w") as f:
        json.dump(documentation, f, indent=2)
    print("   Saved to doc_inventory.json")

    # Step 3: Analyze with Claude
    print("\n🤖 Step 3: Analyzing gaps with Claude...")
    analysis = analyze_with_claude(questions, documentation)

    # Save analysis
    report_file = "documentation_gap_analysis.md"
    with open(report_file, "w") as f:
        f.write("# Documentation Gap Analysis Report\n\n")
        f.write(f"**Generated from:** {len(questions['slack'])} Slack questions, {len(questions['intercom'])} Intercom questions\n\n")
        f.write("---\n\n")
        f.write(analysis)

    print(f"\n✅ Analysis complete! Report saved to {report_file}")
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Questions analyzed: {total_questions}")
    print(f"Documentation sources: {len(documentation)}")
    print(f"\nFull report: {report_file}")
    print("=" * 70)

    # Print the analysis
    print("\n" + analysis)


if __name__ == "__main__":
    main()
