"""
Veeva Help Center Scraper
=========================
Scrapes relevant sections from Veeva documentation for Shaman users.

Targets:
- Approved Email (tokens, templates)
- CLM (Closed Loop Marketing) media
- PromoMats document metadata
- MLR content reviews

Sources:
- crmhelp.veeva.com - Veeva CRM documentation
- commercial.veevavault.help - Vault PromoMats documentation

SETUP
-----
pip install requests beautifulsoup4
"""

import os
import re
import time
import json
import hashlib
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

# Multiple Veeva documentation sources
VEEVA_SOURCES = {
    "crm_help": {
        "base_url": "https://crmhelp.veeva.com/doc/Content/CRM_topics/",
        "start_paths": [
            "Multichannel/ApprovedEmail/",
            "Multichannel/CLM/",
            "Multichannel/Engage/",
        ],
        "allowed_domains": ["crmhelp.veeva.com"],
    },
    "vault_help": {
        "base_url": "https://commercial.veevavault.help/en/gr/",
        "start_paths": [
            "7479/",  # PromoMats Overview
            "",  # Root to discover structure
        ],
        "allowed_domains": ["commercial.veevavault.help"],
    },
}

# Keywords for relevant content (must contain at least one)
CONTENT_KEYWORDS = [
    # Approved Email
    "approved email", "email template", "email fragment", "token",
    "merge field", "dynamiccontentlink", "{{", "configuration token",

    # CLM
    "clm", "closed loop", "presentation", "key message", "slide",
    "media file", "clm content", "engage",

    # PromoMats metadata
    "promomats", "document field", "metadata", "rendition",
    "document type", "binder", "content module",

    # MLR Reviews
    "mlr", "review", "annotation", "workflow", "approval",
    "reviewer", "comment", "markup",

    # CRM Integration
    "veeva crm", "multichannel", "vault crm", "sync",
]

# Patterns to exclude
EXCLUDE_PATTERNS = [
    "/release-notes/",
    "/deprecated/",
    "/installation/",
    "/system-admin/",
    "/security/",
    "/login/",
    "/print-only/",
    ".pdf",
]

# Max pages to scrape per source
MAX_PAGES_PER_SOURCE = 300


class VeevaHelpScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.visited = set()
        self.articles = {}

    def _is_excluded(self, url: str) -> bool:
        """Check if URL should be excluded."""
        url_lower = url.lower()
        for pattern in EXCLUDE_PATTERNS:
            if pattern in url_lower:
                return True
        return False

    def _is_relevant_content(self, text: str) -> bool:
        """Check if content contains relevant keywords."""
        text_lower = text.lower()
        for keyword in CONTENT_KEYWORDS:
            if keyword in text_lower:
                return True
        return False

    def _determine_section(self, url: str, content: str) -> str:
        """Determine the section/category based on URL and content."""
        url_lower = url.lower()
        content_lower = content.lower()[:1000]  # Check first 1000 chars

        # Check URL patterns first (most reliable)
        if "/approvedemail/" in url_lower or "/approved-email/" in url_lower:
            return "approved_email"
        elif "/clm/" in url_lower:
            return "clm"
        elif "/engage/" in url_lower:
            return "engage"

        # Check content keywords
        if "approved email" in content_lower or "email template" in content_lower or "email fragment" in content_lower:
            return "approved_email"
        elif "clm " in content_lower or "closed loop" in content_lower or "key message" in content_lower:
            return "clm"
        elif "promomats" in content_lower or "promomats" in url_lower:
            return "promomats"
        elif "mlr" in content_lower or ("/review" in url_lower and "document" in content_lower):
            return "mlr_review"
        elif "engage " in content_lower:
            return "engage"
        else:
            return "general"

    def _extract_article(self, soup: BeautifulSoup, url: str) -> dict:
        """Extract article content from a page."""
        # Find title
        title = None
        for selector in ['h1', '.topic-title', 'title', '.article-title']:
            el = soup.select_one(selector)
            if el:
                title = el.get_text(strip=True)
                # Clean up title
                for suffix in [" | Veeva", " - Veeva", "Veeva CRM Help"]:
                    title = title.replace(suffix, "").strip()
                break

        if not title or len(title) < 5:
            return None

        # Find main content - try multiple selectors
        content = None
        for selector in [
            '.body-container',
            'article',
            '.topic-content',
            '.article-content',
            '.content-body',
            'main',
            '#content',
            '.MCBreadcrumbsBox_0 + *',  # Content after breadcrumbs
        ]:
            el = soup.select_one(selector)
            if el:
                # Remove navigation, sidebars, etc.
                for noise in el.select('nav, .sidebar, .toc, .breadcrumb, .feedback, script, style, .MCBreadcrumbsBox_0, .nav-container'):
                    noise.decompose()

                content = el.get_text(separator='\n', strip=True)
                if len(content) > 100:
                    break

        # Fallback: get body content
        if not content or len(content) < 100:
            body = soup.find('body')
            if body:
                for noise in body.select('nav, header, footer, script, style, .nav-container, .sidebar'):
                    noise.decompose()
                content = body.get_text(separator='\n', strip=True)

        if not content or len(content) < 100:
            return None

        # Check if content is relevant
        if not self._is_relevant_content(content):
            return None

        section = self._determine_section(url, content)

        return {
            "url": url,
            "title": title,
            "content": content[:15000],  # Limit content size
            "section": section,
            "source": "veeva_help"
        }

    def _find_links(self, soup: BeautifulSoup, base_url: str, allowed_domains: list) -> list:
        """Find all relevant links on a page."""
        links = []

        for a in soup.find_all('a', href=True):
            href = a['href']

            # Skip anchors and javascript
            if href.startswith('#') or href.startswith('javascript:'):
                continue

            full_url = urljoin(base_url, href)

            # Clean URL
            full_url = full_url.split('#')[0].rstrip('/')

            # Must be in allowed domain
            url_domain = urlparse(full_url).netloc
            if not any(domain in url_domain for domain in allowed_domains):
                continue

            # Skip excluded patterns
            if self._is_excluded(full_url):
                continue

            if full_url not in self.visited:
                links.append(full_url)

        return links

    def scrape_source(self, source_name: str, config: dict) -> list:
        """Scrape a single documentation source."""
        print(f"\n{'='*60}")
        print(f"📚 Scraping: {source_name}")
        print(f"{'='*60}")

        base_url = config["base_url"]
        allowed_domains = config["allowed_domains"]

        # Build start URLs
        to_visit = []
        for path in config["start_paths"]:
            to_visit.append(urljoin(base_url, path))

        source_articles = {}
        source_visited = 0

        while to_visit and source_visited < MAX_PAGES_PER_SOURCE:
            url = to_visit.pop(0)

            if url in self.visited:
                continue

            self.visited.add(url)
            source_visited += 1

            try:
                response = self.session.get(url, timeout=15)

                if response.status_code == 403:
                    print(f"   ⚠ 403 Forbidden: {url[:60]}...")
                    continue
                if response.status_code != 200:
                    continue

                soup = BeautifulSoup(response.text, 'html.parser')

                # Extract article if this is a content page
                article = self._extract_article(soup, url)
                if article:
                    article_id = hashlib.md5(url.encode()).hexdigest()
                    source_articles[article_id] = article
                    self.articles[article_id] = article
                    print(f"   ✓ [{article['section']}] {article['title'][:45]}...")

                # Find more links
                new_links = self._find_links(soup, url, allowed_domains)
                to_visit.extend(new_links)

                time.sleep(0.5)  # Be nice to the server

            except requests.exceptions.Timeout:
                print(f"   ⚠ Timeout: {url[:50]}...")
                continue
            except Exception as e:
                print(f"   ⚠ Error: {str(e)[:50]}")
                continue

            if source_visited % 25 == 0:
                print(f"   ... visited {source_visited} pages, found {len(source_articles)} articles")

        print(f"\n   📊 {source_name}: {len(source_articles)} articles from {source_visited} pages")
        return list(source_articles.values())

    def scrape_all(self) -> list:
        """Scrape all Veeva documentation sources."""
        print("=" * 60)
        print("VEEVA DOCUMENTATION SCRAPER")
        print("=" * 60)
        print("\nTargeting content about:")
        print("  • Approved Email (tokens, templates)")
        print("  • CLM media for Veeva CRM")
        print("  • PromoMats document metadata")
        print("  • MLR content reviews")

        for source_name, config in VEEVA_SOURCES.items():
            self.scrape_source(source_name, config)

        articles = list(self.articles.values())

        # Summary by section
        sections = {}
        for article in articles:
            sect = article.get("section", "other")
            sections[sect] = sections.get(sect, 0) + 1

        print(f"\n{'=' * 60}")
        print(f"TOTAL ARTICLES: {len(articles)}")
        print(f"{'=' * 60}")
        print("\nBy section:")
        for sect, count in sorted(sections.items(), key=lambda x: -x[1]):
            print(f"   {sect}: {count}")

        return articles


def main():
    scraper = VeevaHelpScraper()
    articles = scraper.scrape_all()

    # Save to JSON
    with open("veeva_helpcenter.json", "w") as f:
        json.dump(articles, f, indent=2)

    print(f"\n✓ Saved {len(articles)} articles to veeva_helpcenter.json")

    # Show samples
    if articles:
        print("\n📝 Sample articles:")
        for article in articles[:5]:
            print(f"   [{article['section']}] {article['title'][:50]}")


if __name__ == "__main__":
    main()
