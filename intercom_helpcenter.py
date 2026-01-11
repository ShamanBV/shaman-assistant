"""
Intercom Help Center API Fetcher
================================
Fetches ALL articles via Intercom API, including:
- Draft/unpublished articles
- Articles in nested collections

SETUP
-----
Add to .env:
INTERCOM_ACCESS_TOKEN=your_token_here
"""

import os
import time
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

load_dotenv()

INTERCOM_ACCESS_TOKEN = os.getenv("INTERCOM_ACCESS_TOKEN", "")

# Set to True to include draft articles
INCLUDE_DRAFTS = False


class IntercomHelpCenter:
    def __init__(self, token: str = None):
        self.token = token or INTERCOM_ACCESS_TOKEN
        if not self.token:
            raise ValueError("INTERCOM_ACCESS_TOKEN not found in .env")

        self.base_url = "https://api.intercom.io"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Intercom-Version": "2.10"
        }

    def _request(self, endpoint: str, params: dict = None) -> dict:
        """Make authenticated request to Intercom API."""
        url = f"{self.base_url}/{endpoint}"

        response = requests.get(url, headers=self.headers, params=params or {})

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 10))
            print(f"   Rate limited. Waiting {retry_after}s...")
            time.sleep(retry_after)
            return self._request(endpoint, params)

        if response.status_code != 200:
            # Silently skip 404s (expected for some nested endpoints)
            if response.status_code != 404:
                print(f"   API error {response.status_code}: {response.text[:100]}")
            return {}

        return response.json()

    def _paginate_all(self, endpoint: str, data_key: str = "data") -> list:
        """Fetch ALL pages from a paginated endpoint using cursor-based pagination."""
        all_items = []
        page = 1
        starting_after = None

        while True:
            print(f"      Page {page}...")

            params = {"per_page": 50}
            if starting_after:
                params["starting_after"] = starting_after

            data = self._request(endpoint, params)

            if not data:
                break

            items = data.get(data_key, [])
            if not items:
                break

            all_items.extend(items)
            print(f"      Got {len(items)} items (total: {len(all_items)})")

            # Get cursor for next page
            pages = data.get("pages", {})
            next_page = pages.get("next")

            if next_page and isinstance(next_page, dict):
                starting_after = next_page.get("starting_after")
                if not starting_after:
                    break
            else:
                break

            page += 1
            time.sleep(0.1)

        return all_items

    def get_all_collections(self) -> list:
        """Fetch all Help Center collections (including nested ones)."""
        print("   Fetching collections...")
        collections = self._paginate_all("help_center/collections")
        print(f"   Found {len(collections)} collections")
        return collections

    def get_all_sections(self) -> list:
        """Fetch all sections."""
        print("   Fetching sections...")
        sections = self._paginate_all("help_center/sections")
        print(f"   Found {len(sections)} sections")
        return sections

    def get_all_articles_direct(self) -> list:
        """Fetch all articles directly via the articles endpoint."""
        print("   Fetching articles directly...")
        articles = self._paginate_all("articles")
        print(f"   Found {len(articles)} articles via direct fetch")
        return articles

    def get_articles_in_collection(self, collection_id: str) -> list:
        """Fetch articles in a specific collection."""
        data = self._request(f"help_center/collections/{collection_id}/articles")
        return data.get("data", []) if data else []

    def get_articles_in_section(self, section_id: str) -> list:
        """Fetch articles in a specific section."""
        data = self._request(f"help_center/sections/{section_id}/articles")
        return data.get("data", []) if data else []

    def fetch_all_articles_comprehensive(self) -> list:
        """
        Fetch ALL articles using multiple methods to ensure nothing is missed.
        """
        print("\n" + "=" * 60)
        print("📚 FETCHING ALL INTERCOM HELP CENTER ARTICLES")
        print("=" * 60)

        all_article_ids = set()
        all_articles = {}

        # Method 1: Direct articles endpoint
        print("\n📋 Method 1: Direct /articles endpoint")
        direct_articles = self.get_all_articles_direct()
        for article in direct_articles:
            aid = article.get("id")
            if aid:
                all_article_ids.add(aid)
                all_articles[aid] = article
        print(f"   → {len(all_article_ids)} unique articles so far")

        # Method 2: Traverse collections
        print("\n📁 Method 2: Traversing collections")
        collections = self.get_all_collections()

        for collection in collections:
            coll_id = collection.get("id")
            coll_name = collection.get("name", "Unnamed")

            # Get articles directly in this collection
            coll_articles = self.get_articles_in_collection(coll_id)
            new_count = 0
            for article in coll_articles:
                aid = article.get("id")
                if aid and aid not in all_article_ids:
                    all_article_ids.add(aid)
                    all_articles[aid] = article
                    new_count += 1

            if new_count > 0:
                print(f"   Collection '{coll_name[:30]}': +{new_count} new articles")

            time.sleep(0.05)

        print(f"   → {len(all_article_ids)} unique articles so far")

        # Method 3: Traverse sections
        print("\n📂 Method 3: Traversing sections")
        sections = self.get_all_sections()

        for section in sections:
            sect_id = section.get("id")
            sect_name = section.get("name", "Unnamed")

            sect_articles = self.get_articles_in_section(sect_id)
            new_count = 0
            for article in sect_articles:
                aid = article.get("id")
                if aid and aid not in all_article_ids:
                    all_article_ids.add(aid)
                    all_articles[aid] = article
                    new_count += 1

            if new_count > 0:
                print(f"   Section '{sect_name[:30]}': +{new_count} new articles")

            time.sleep(0.05)

        print(f"\n   → {len(all_article_ids)} total unique articles found")

        return list(all_articles.values())

    def get_article_detail(self, article_id: str) -> dict:
        """Fetch full article content."""
        return self._request(f"articles/{article_id}")

    def fetch_all_with_content(self, include_drafts: bool = True) -> list:
        """Fetch all articles with full content."""

        # Get all articles using comprehensive method
        articles_list = self.fetch_all_articles_comprehensive()

        # Filter by state
        if include_drafts:
            to_process = articles_list
            print(f"\n📝 Processing all {len(to_process)} articles (including drafts)")
        else:
            to_process = [a for a in articles_list if a.get("state") == "published"]
            print(f"\n📝 Processing {len(to_process)} published articles")

        # Fetch full content for each
        articles = []
        for i, article in enumerate(to_process):
            if (i + 1) % 20 == 0:
                print(f"   Fetching content {i + 1}/{len(to_process)}...")

            try:
                detail = self.get_article_detail(article["id"])

                if not detail:
                    continue

                # Clean HTML content
                body = detail.get("body", "")
                if body and "<" in body:
                    soup = BeautifulSoup(body, 'html.parser')
                    body = soup.get_text(separator='\n', strip=True)

                articles.append({
                    "id": article["id"],
                    "title": article.get("title", "Untitled"),
                    "description": article.get("description", ""),
                    "body": body,
                    "url": article.get("url", ""),
                    "state": article.get("state", "unknown"),
                    "author_id": article.get("author_id"),
                    "created_at": article.get("created_at"),
                    "updated_at": article.get("updated_at"),
                    "parent_id": article.get("parent_id"),
                    "parent_type": article.get("parent_type"),
                })

                time.sleep(0.05)  # Be nice to the API

            except Exception as e:
                print(f"   ⚠ Error fetching article {article.get('id')}: {e}")
                continue

        # Summary by state
        states = {}
        for a in articles:
            state = a.get("state", "unknown")
            states[state] = states.get(state, 0) + 1

        print(f"\n✓ Fetched {len(articles)} articles with content")
        print(f"   By state: {states}")

        return articles

    def get_structure(self) -> dict:
        """Get the full Help Center structure (for debugging)."""
        print("\n📂 HELP CENTER STRUCTURE")
        print("=" * 60)

        collections = self.get_all_collections()
        sections = self.get_all_sections()
        articles = self.get_all_articles_direct()

        # Build hierarchy
        # Collections can have parent_id pointing to other collections
        root_collections = [c for c in collections if not c.get("parent_id")]
        nested_collections = [c for c in collections if c.get("parent_id")]

        print(f"\nCollections ({len(collections)} total):")
        print(f"   Root level: {len(root_collections)}")
        print(f"   Nested: {len(nested_collections)}")

        def print_collection_tree(coll, indent=1):
            prefix = "   " * indent
            print(f"{prefix}📁 {coll.get('name', 'Unnamed')}")

            # Find child collections
            children = [c for c in nested_collections if c.get("parent_id") == coll.get("id")]
            for child in children:
                print_collection_tree(child, indent + 1)

            # Find sections in this collection
            coll_sections = [s for s in sections if s.get("parent_id") == coll.get("id")]
            for sect in coll_sections:
                print(f"{prefix}   📂 {sect.get('name', 'Unnamed')}")

        for coll in root_collections:
            print_collection_tree(coll)

        # Count articles by state
        published = len([a for a in articles if a.get("state") == "published"])
        draft = len([a for a in articles if a.get("state") == "draft"])
        other = len(articles) - published - draft

        print(f"\nArticles ({len(articles)} from direct fetch):")
        print(f"   Published: {published}")
        print(f"   Draft: {draft}")
        if other > 0:
            print(f"   Other: {other}")

        return {
            "collections": len(collections),
            "root_collections": len(root_collections),
            "nested_collections": len(nested_collections),
            "sections": len(sections),
            "articles": len(articles),
            "published": published,
            "drafts": draft
        }


def main():
    """Test the Intercom Help Center fetcher."""
    print("=" * 60)
    print("INTERCOM HELP CENTER FETCHER")
    print("=" * 60)

    try:
        hc = IntercomHelpCenter()
    except ValueError as e:
        print(f"Error: {e}")
        return

    # Show structure
    stats = hc.get_structure()

    # Fetch all articles with content
    articles = hc.fetch_all_with_content(include_drafts=INCLUDE_DRAFTS)

    # Show sample
    print("\n📝 Sample articles:")
    for article in articles[:10]:
        state = article.get('state', 'unknown')
        state_icon = "✓" if state == "published" else "○"
        print(f"   {state_icon} [{state}] {article['title'][:50]}")

    # Save to JSON
    import json
    with open("intercom_helpcenter.json", "w") as f:
        json.dump(articles, f, indent=2, default=str)
    print(f"\n✓ Saved {len(articles)} articles to intercom_helpcenter.json")


if __name__ == "__main__":
    main()