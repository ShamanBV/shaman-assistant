"""
Feature Enrichment Script
==========================
Reads an Excel file with Shaman features and uses Claude to create
enriched descriptions optimized for embedding/search.

Usage:
    python enrich_features.py features.xlsx

Output:
    features_enriched.json - Ready to import into knowledge base
"""

import os
import sys
import json
import time
from pathlib import Path

# Try to import required libraries
try:
    import pandas as pd
except ImportError:
    print("Please install pandas: pip install pandas openpyxl")
    sys.exit(1)

try:
    from anthropic import Anthropic
except ImportError:
    print("Please install anthropic: pip install anthropic")
    sys.exit(1)

from dotenv import load_dotenv
load_dotenv()


def enrich_feature(client, row: dict) -> str:
    """Use Claude to create an enriched description for a feature."""

    # Build context from all columns
    feature_info = "\n".join([f"- {k}: {v}" for k, v in row.items() if pd.notna(v) and str(v).strip()])

    prompt = f"""You are helping create searchable documentation for Shaman, a pharma content authoring platform that integrates with Veeva.

Given this feature information, create an enriched description optimized for semantic search.

FEATURE DATA:
{feature_info}

TASK:
Create a comprehensive, searchable text block that includes:
1. Feature name with any acronym expansions
2. What it does in plain language
3. Who uses it (admins, reps, marketers, etc.)
4. Related Veeva/Shaman concepts
5. Common use cases or scenarios
6. Related search terms someone might use

FORMAT:
Write 3-5 sentences of natural, descriptive text. Include relevant keywords naturally.
Do NOT use bullet points or headers - just flowing text that reads well.

IMPORTANT:
- Expand acronyms (CLM = Closed Loop Marketing, AE = Approved Email, etc.)
- Mention if it's a paid feature or requires configuration
- Be specific about Veeva integration if applicable"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"   Error enriching: {e}")
        return None


def process_excel(input_file: str, output_file: str = None):
    """Process Excel file and create enriched JSON."""

    if not os.path.exists(input_file):
        print(f"Error: File not found: {input_file}")
        return

    # Initialize Anthropic client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set in environment")
        return

    client = Anthropic(api_key=api_key)

    # Read Excel file
    print(f"\n📖 Reading {input_file}...")

    try:
        df = pd.read_excel(input_file)
    except Exception as e:
        # Try CSV if Excel fails
        try:
            df = pd.read_csv(input_file)
        except:
            print(f"Error reading file: {e}")
            return

    print(f"   Found {len(df)} rows, {len(df.columns)} columns")
    print(f"   Columns: {', '.join(df.columns.tolist())}")

    # Process each row
    enriched_features = []

    print(f"\n🤖 Enriching features with AI...")

    for idx, row in df.iterrows():
        row_dict = row.to_dict()

        # Get feature name (try common column names)
        feature_name = None
        for col in ['name', 'feature', 'name feature', 'feature name', 'title']:
            if col in row_dict and pd.notna(row_dict.get(col)):
                feature_name = str(row_dict[col])
                break

        if not feature_name:
            feature_name = f"Feature {idx + 1}"

        print(f"   [{idx + 1}/{len(df)}] {feature_name[:50]}...")

        # Enrich with Claude
        enriched_description = enrich_feature(client, row_dict)

        if enriched_description:
            # Filter out unstable/internal columns from original_data
            exclude_columns = {'featureId', 'id', 'ID', 'feature_id', 'row_id', 'index'}
            filtered_data = {
                k: str(v) if pd.notna(v) else None
                for k, v in row_dict.items()
                if k.lower() not in {c.lower() for c in exclude_columns}
            }

            enriched_features.append({
                "id": f"feature_{idx}",
                "name": feature_name,
                "original_data": filtered_data,
                "enriched_description": enriched_description,
                "metadata": {
                    "source": "features",
                    "type": "feature",
                    "title": feature_name,
                    "row_number": idx + 1
                }
            })

        # Rate limiting - avoid hitting API limits
        time.sleep(0.5)

    # Save to JSON
    if output_file is None:
        output_file = Path(input_file).stem + "_enriched.json"

    print(f"\n💾 Saving to {output_file}...")

    with open(output_file, 'w') as f:
        json.dump(enriched_features, f, indent=2)

    print(f"\n✅ Done! Enriched {len(enriched_features)} features")
    print(f"   Output: {output_file}")

    # Show sample
    if enriched_features:
        print(f"\n📝 Sample enriched description:")
        print("-" * 50)
        print(enriched_features[0]["enriched_description"][:500])
        print("-" * 50)

    return output_file


def main():
    if len(sys.argv) < 2:
        print("Usage: python enrich_features.py <excel_file.xlsx>")
        print("\nExample:")
        print("  python enrich_features.py shaman_features.xlsx")
        return

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    process_excel(input_file, output_file)


if __name__ == "__main__":
    main()
