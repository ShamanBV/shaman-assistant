"""
Confluence Ingestor
===================
Syncs Confluence pages to the knowledge base.

Setup:
1. Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens
2. Add to .env:
   CONFLUENCE_URL=https://yourcompany.atlassian.net/wiki
   CONFLUENCE_EMAIL=your@email.com
   CONFLUENCE_API_TOKEN=your_api_token

Usage:
    python main.py --sync confluence
"""
import requests
from requests.auth import HTTPBasicAuth
from typing import Generator
from bs4 import BeautifulSoup
import config
from .base import BaseIngestor, Document


class ConfluenceIngestor(BaseIngestor):
    """Ingestor for Confluence pages."""
    
    source_name = "confluence"
    
    def __init__(self, vector_store):
        super().__init__(vector_store)
        
        if not all([config.CONFLUENCE_URL, config.CONFLUENCE_EMAIL, config.CONFLUENCE_API_TOKEN]):
            raise ValueError(
                "Missing Confluence config. Set CONFLUENCE_URL, CONFLUENCE_EMAIL, "
                "and CONFLUENCE_API_TOKEN in .env"
            )
        
        self.base_url = config.CONFLUENCE_URL.rstrip('/')
        self.auth = HTTPBasicAuth(config.CONFLUENCE_EMAIL, config.CONFLUENCE_API_TOKEN)
        self.spaces = config.CONFLUENCE_SPACES
        self.max_pages = config.CONFLUENCE_MAX_PAGES
    
    def _request(self, endpoint: str, params: dict = None) -> dict:
        """Make authenticated request to Confluence API."""
        url = f"{self.base_url}/rest/api/{endpoint}"
        
        response = requests.get(
            url,
            auth=self.auth,
            params=params,
            headers={"Accept": "application/json"}
        )
        response.raise_for_status()
        return response.json()
    
    def _get_spaces(self) -> list[dict]:
        """Get list of spaces to index."""
        if self.spaces:
            # Fetch specific spaces
            spaces = []
            for space_key in self.spaces:
                try:
                    space = self._request(f"space/{space_key}")
                    spaces.append(space)
                except requests.HTTPError as e:
                    print(f"   ⚠ Could not access space {space_key}: {e}")
            return spaces
        else:
            # Fetch all spaces
            result = self._request("space", {"limit": 100})
            return result.get("results", [])
    
    def _get_pages_in_space(self, space_key: str) -> Generator[dict, None, None]:
        """Get all pages in a space."""
        start = 0
        limit = 50
        
        while True:
            result = self._request("content", {
                "spaceKey": space_key,
                "type": "page",
                "status": "current",
                "expand": "body.storage,version,ancestors",
                "start": start,
                "limit": limit
            })
            
            pages = result.get("results", [])
            if not pages:
                break
            
            for page in pages:
                yield page
            
            # Check if there are more
            if len(pages) < limit:
                break
            start += limit
    
    def _clean_html(self, html: str) -> str:
        """Convert HTML content to clean text."""
        if not html:
            return ""
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove script and style elements
        for element in soup(['script', 'style', 'nav', 'header', 'footer']):
            element.decompose()
        
        # Get text with reasonable spacing
        text = soup.get_text(separator='\n', strip=True)
        
        # Clean up excessive whitespace
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        return '\n'.join(lines)
    
    def _get_page_url(self, page: dict, space_key: str) -> str:
        """Construct URL for a page."""
        # Confluence Cloud URL format
        page_id = page.get("id")
        title = page.get("title", "").replace(" ", "+")
        return f"{self.base_url}/spaces/{space_key}/pages/{page_id}"
    
    def _get_breadcrumb(self, page: dict) -> str:
        """Get page path from ancestors."""
        ancestors = page.get("ancestors", [])
        if not ancestors:
            return ""
        
        path = " > ".join(a.get("title", "") for a in ancestors)
        return path
    
    def fetch_documents(self) -> Generator[Document, None, None]:
        """Fetch all Confluence pages."""
        spaces = self._get_spaces()
        print(f"   Found {len(spaces)} spaces to index")
        
        page_count = 0
        
        for space in spaces:
            space_key = space.get("key")
            space_name = space.get("name", space_key)
            print(f"\n   📁 Space: {space_name} ({space_key})")
            
            space_page_count = 0
            
            for page in self._get_pages_in_space(space_key):
                if page_count >= self.max_pages:
                    print(f"   ⚠ Reached max pages limit ({self.max_pages})")
                    return
                
                title = page.get("title", "Untitled")
                page_id = page.get("id")
                
                # Get content
                body = page.get("body", {}).get("storage", {}).get("value", "")
                content = self._clean_html(body)
                
                if not content or len(content) < 50:
                    continue
                
                # Build metadata
                url = self._get_page_url(page, space_key)
                breadcrumb = self._get_breadcrumb(page)
                version = page.get("version", {}).get("number", 1)
                
                # Chunk long pages
                chunks = self.chunk_text(content, chunk_size=1500, overlap=200)
                
                for i, chunk in enumerate(chunks):
                    chunk_title = title if len(chunks) == 1 else f"{title} (part {i+1})"
                    
                    # Create document with title prefix for better search
                    doc_content = f"# {title}\n\n"
                    if breadcrumb:
                        doc_content += f"Path: {breadcrumb}\n\n"
                    doc_content += chunk
                    
                    yield Document(
                        id=Document.create_id("confluence", f"{page_id}_{i}"),
                        content=doc_content,
                        metadata={
                            "source": "confluence",
                            "title": chunk_title,
                            "url": url,
                            "space": space_key,
                            "space_name": space_name,
                            "page_id": page_id,
                            "version": version,
                            "breadcrumb": breadcrumb,
                            "chunk": i,
                            "total_chunks": len(chunks)
                        }
                    )
                
                page_count += 1
                space_page_count += 1
            
            print(f"      Indexed {space_page_count} pages")
        
        print(f"\n   Total: {page_count} pages processed")
