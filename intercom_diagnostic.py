"""
Intercom Conversations Diagnostic
=================================
Check how many conversations are accessible via API vs UI.
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests

load_dotenv()

INTERCOM_ACCESS_TOKEN = os.getenv("INTERCOM_ACCESS_TOKEN", "")


def intercom_request(endpoint: str, params: dict = None) -> dict:
    """Make authenticated request to Intercom API."""
    headers = {
        "Authorization": f"Bearer {INTERCOM_ACCESS_TOKEN}",
        "Accept": "application/json",
        "Intercom-Version": "2.10"
    }

    url = f"https://api.intercom.io/{endpoint}"
    response = requests.get(url, headers=headers, params=params or {})

    if response.status_code != 200:
        print(f"API error {response.status_code}: {response.text[:200]}")
        return {}

    return response.json()


def count_all_conversations():
    """Count all conversations with pagination."""
    print("=" * 60)
    print("INTERCOM CONVERSATIONS DIAGNOSTIC")
    print("=" * 60)

    all_conversations = []
    page = 1

    print("\nFetching all conversations...")

    while True:
        print(f"   Page {page}...", end=" ")
        data = intercom_request("conversations", {"per_page": 50, "page": page})

        if not data:
            break

        convos = data.get("conversations", [])
        if not convos:
            print("(empty)")
            break

        all_conversations.extend(convos)
        print(f"got {len(convos)} (total: {len(all_conversations)})")

        # Check pagination
        pages = data.get("pages", {})
        total_pages = pages.get("total_pages", 1)

        if page >= total_pages:
            break

        page += 1

    print(f"\n{'=' * 60}")
    print(f"TOTAL CONVERSATIONS FOUND: {len(all_conversations)}")
    print(f"{'=' * 60}")

    # Breakdown by state
    states = {}
    for conv in all_conversations:
        state = conv.get("state", "unknown")
        states[state] = states.get(state, 0) + 1

    print("\nBy state:")
    for state, count in sorted(states.items()):
        print(f"   {state}: {count}")

    # Breakdown by time
    now = datetime.now()
    time_buckets = {
        "last_7_days": 0,
        "last_30_days": 0,
        "last_90_days": 0,
        "last_180_days": 0,
        "last_365_days": 0,
        "older": 0
    }

    for conv in all_conversations:
        created = conv.get("created_at", 0)
        if created:
            created_dt = datetime.fromtimestamp(created)
            age = (now - created_dt).days

            if age <= 7:
                time_buckets["last_7_days"] += 1
            elif age <= 30:
                time_buckets["last_30_days"] += 1
            elif age <= 90:
                time_buckets["last_90_days"] += 1
            elif age <= 180:
                time_buckets["last_180_days"] += 1
            elif age <= 365:
                time_buckets["last_365_days"] += 1
            else:
                time_buckets["older"] += 1

    print("\nBy age:")
    for bucket, count in time_buckets.items():
        print(f"   {bucket}: {count}")

    # Check date range
    if all_conversations:
        dates = [c.get("created_at", 0) for c in all_conversations if c.get("created_at")]
        if dates:
            oldest = datetime.fromtimestamp(min(dates))
            newest = datetime.fromtimestamp(max(dates))
            print(f"\nDate range:")
            print(f"   Oldest: {oldest.strftime('%Y-%m-%d')}")
            print(f"   Newest: {newest.strftime('%Y-%m-%d')}")

    print(f"\n{'=' * 60}")
    print("Compare this total with your Intercom UI:")
    print("Go to Inbox → All conversations → check the count")
    print(f"{'=' * 60}")

    return all_conversations


if __name__ == "__main__":
    if not INTERCOM_ACCESS_TOKEN:
        print("Error: INTERCOM_ACCESS_TOKEN not found in .env")
    else:
        count_all_conversations()