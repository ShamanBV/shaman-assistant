"""
Check Vector Database Volume
"""

import chromadb
from chromadb.utils import embedding_functions

DB_PATH = "./knowledge_base"

client = chromadb.PersistentClient(path=DB_PATH)

print("=" * 50)
print("KNOWLEDGE BASE STATS")
print("=" * 50)

# List all collections
collections = client.list_collections()

total = 0
for coll in collections:
    count = coll.count()
    total += count
    print(f"\n📁 {coll.name}: {count} items")

    # Show sample metadata
    if count > 0:
        sample = coll.peek(limit=2)
        if sample.get("metadatas"):
            for meta in sample["metadatas"][:2]:
                print(f"   Sample: {meta}")

print(f"\n{'=' * 50}")
print(f"TOTAL: {total} items")
print("=" * 50)