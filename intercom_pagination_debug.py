"""
Intercom Conversations Pagination Debug
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests

load_dotenv()

INTERCOM_ACCESS_TOKEN = os.getenv("INTERCOM_ACCESS_TOKEN", "")


def intercom_request(endpoint: str, params: dict = None) -> dict:
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


def debug_pagination():
    print("=" * 60)
    print("INTERCOM PAGINATION DEBUG")
    print("=" * 60)

    # Set cutoff to Jan 1, 2025
    cutoff = datetime(2025, 1, 1)
    cutoff_ts = int(cutoff.timestamp())

    print(f"\nCutoff date: {cutoff.strftime('%Y-%m-%d')}")
    print(f"Cutoff timestamp: {cutoff_ts}")

    all_conversations = []
    page = 1

    while True:
        print(f"\n--- Page {page} ---")
        data = intercom_request("conversations", {"per_page": 50, "page": page})

        if not data:
            print("No data returned!")
            break

        convos = data.get("conversations", [])
        print(f"Conversations on this page: {len(convos)}")

        if not convos:
            print("Empty page - stopping")
            break

        # Check dates on this page
        dates = []
        within_range = 0
        outside_range = 0

        for conv in convos:
            created_at = conv.get("created_at", 0)
            if created_at:
                dt = datetime.fromtimestamp(created_at)
                dates.append(dt)
                if created_at >= cutoff_ts:
                    within_range += 1
                    all_conversations.append(conv)
                else:
                    outside_range += 1

        if dates:
            print(f"Date range on page: {min(dates).strftime('%Y-%m-%d')} to {max(dates).strftime('%Y-%m-%d')}")
            print(f"Within range (>= Jan 1 2025): {within_range}")
            print(f"Outside range (< Jan 1 2025): {outside_range}")

        # Check pagination info
        pages = data.get("pages", {})
        print(f"Pagination info: {pages}")

        total_pages = pages.get("total_pages", 1)
        print(f"Total pages reported: {total_pages}")

        # If all conversations on this page are outside range, we can stop
        if outside_range == len(convos):
            print("All conversations on this page are before cutoff - stopping")
            break

        if page >= total_pages:
            print("Reached last page - stopping")
            break

        page += 1

        # Safety limit
        if page > 100:
            print("Safety limit reached (100 pages)")
            break

    print(f"\n{'=' * 60}")
    print(f"TOTAL CONVERSATIONS SINCE JAN 1 2025: {len(all_conversations)}")
    print(f"Pages fetched: {page}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    debug_pagination()