"""
Extract error codes and messages from Slack/Intercom for documentation.
"""

import os
import re
import json
import chromadb
from dotenv import load_dotenv
from anthropic import Anthropic
from collections import defaultdict

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./knowledge_base")


def extract_errors():
    """Extract error patterns from indexed content."""
    client = chromadb.PersistentClient(path=DB_PATH)

    errors = []

    # Patterns to match
    patterns = [
        # HTTP status codes
        (r'\b(4\d{2}|5\d{2})\b', 'http_code'),
        # Error messages in quotes
        (r'"([^"]*(?:error|failed|invalid|cannot|unable|not found|denied|timeout)[^"]*)"', 'quoted_error'),
        (r"'([^']*(?:error|failed|invalid|cannot|unable|not found|denied|timeout)[^']*)'", 'quoted_error'),
        # Technical errors
        (r'(Error|Exception|Failed|Timeout|Invalid):\s*([^\n]{10,100})', 'technical'),
        # Specific patterns from the analysis
        (r'(Width must be a number)', 'specific'),
        (r'(Code \d+)', 'code_pattern'),
        (r'(SYNC_FAILED|EXPORT_FAILED|UPLOAD_FAILED)', 'sync_error'),
        # Vault/Veeva errors
        (r'(vault[^\n]{0,50}error[^\n]{0,50})', 'vault_error'),
        (r'(presentation[^\n]{0,30}not created[^\n]{0,50})', 'vault_error'),
        # Link/navigation errors
        (r'(links? (?:are |is )?not working[^\n]{0,50})', 'link_error'),
    ]

    for collection_name in ['slack_messages', 'intercom_conversations']:
        try:
            col = client.get_collection(collection_name)
            data = col.get(include=["documents", "metadatas"])

            for doc, meta in zip(data["documents"], data["metadatas"]):
                doc_lower = doc.lower()

                # Check for error-related content
                if any(kw in doc_lower for kw in ['error', 'failed', 'issue', 'problem', 'not working', 'broken', '401', '403', '404', '500']):
                    for pattern, error_type in patterns:
                        matches = re.findall(pattern, doc, re.IGNORECASE)
                        for match in matches:
                            if isinstance(match, tuple):
                                match = ' '.join(match)
                            match = match.strip()
                            if len(match) > 5 and len(match) < 200:
                                errors.append({
                                    "error": match,
                                    "type": error_type,
                                    "source": collection_name,
                                    "context": doc[:500]
                                })

        except Exception as e:
            print(f"Error reading {collection_name}: {e}")

    print(f"Extracted {len(errors)} raw error patterns")
    return errors


def analyze_errors_with_claude(errors: list) -> str:
    """Use Claude to categorize and document errors."""

    client = Anthropic()

    # Deduplicate and sample
    unique_errors = list(set(e["error"] for e in errors))[:150]
    contexts = [e["context"] for e in errors[:50]]

    prompt = f"""Analyze these error messages extracted from a B2B SaaS support system (Shaman - pharmaceutical content management).

## RAW ERROR PATTERNS FOUND
{json.dumps(unique_errors, indent=2)}

## SAMPLE CONTEXTS (showing how errors appear in conversations)
{json.dumps(contexts[:20], indent=2)}

## YOUR TASK

Create a comprehensive ERROR CODE REFERENCE DOCUMENT in Markdown format with:

### 1. HTTP Status Codes
For each HTTP code found (401, 403, 404, 500, etc.):
- Code and meaning
- Common causes in Shaman context
- Resolution steps

### 2. Veeva/Vault Errors
Errors related to Veeva integration:
- Error message
- What it means
- How to resolve

### 3. Upload/Export Errors
File upload and export issues:
- Error message
- Common causes
- Resolution steps

### 4. Sync Errors
Synchronization issues:
- Error message
- Meaning
- Resolution

### 5. Email/Template Errors
Email rendering and template issues:
- Error description
- Causes
- Fixes

### 6. Visual Builder Errors
Content creation errors:
- Error message
- Cause
- Solution

### 7. Permission/Access Errors
Access denied and permission issues:
- Error type
- What to check
- Resolution

Format each error as:
```
### Error: [Error Message or Code]
**Category:** [category]
**Meaning:** [what this error means]
**Common Causes:**
- Cause 1
- Cause 2
**Resolution:**
1. Step 1
2. Step 2
**Escalation:** [when to escalate to ConfigOps/Product]
```

Be practical and specific to the Shaman/Veeva context. Include actual error messages from the data."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


def main():
    print("=" * 60)
    print("EXTRACTING ERROR CODES")
    print("=" * 60)

    # Extract errors
    print("\n📥 Extracting error patterns...")
    errors = extract_errors()

    # Save raw errors
    with open("extracted_errors.json", "w") as f:
        json.dump(errors, f, indent=2)
    print(f"   Saved {len(errors)} patterns to extracted_errors.json")

    # Analyze with Claude
    print("\n🤖 Analyzing and documenting errors...")
    documentation = analyze_errors_with_claude(errors)

    # Create manual_docs directory if needed
    os.makedirs("manual_docs", exist_ok=True)

    # Write error codes doc
    with open("manual_docs/error_codes.md", "w") as f:
        f.write("# Shaman Error Code Reference\n\n")
        f.write("*Auto-generated from support conversations. Last updated: See git history.*\n\n")
        f.write("---\n\n")
        f.write(documentation)

    print("\n✅ Created manual_docs/error_codes.md")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
