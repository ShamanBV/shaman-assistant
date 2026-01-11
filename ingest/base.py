"""
Base Ingestor
=============
Abstract base class for all data ingestors.
"""
from abc import ABC, abstractmethod
from typing import Generator
from dataclasses import dataclass
import hashlib


@dataclass
class Document:
    """A document to be ingested."""
    id: str
    content: str
    metadata: dict
    
    @classmethod
    def create_id(cls, source: str, unique_key: str) -> str:
        """Create a consistent document ID."""
        key = f"{source}_{unique_key}"
        return hashlib.md5(key.encode()).hexdigest()


class BaseIngestor(ABC):
    """
    Base class for all ingestors.
    
    Subclasses must implement:
    - source_name: The collection name (e.g., "confluence")
    - fetch_documents(): Generator that yields Documents
    """
    
    def __init__(self, vector_store):
        """
        Initialize ingestor with vector store.
        
        Args:
            vector_store: VectorStore instance for adding documents
        """
        self.vector_store = vector_store
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """Return the source name (collection name)."""
        pass
    
    @abstractmethod
    def fetch_documents(self) -> Generator[Document, None, None]:
        """
        Fetch documents from the source.
        
        Yields:
            Document objects to be indexed
        """
        pass
    
    def sync(self, batch_size: int = 100) -> int:
        """
        Sync documents from source to vector store.
        
        Args:
            batch_size: Number of documents to add at once
            
        Returns:
            Total number of documents added
        """
        print(f"\n{'=' * 60}")
        print(f"📥 SYNCING {self.source_name.upper()}")
        print('=' * 60)
        
        documents = []
        metadatas = []
        ids = []
        total_added = 0
        
        for doc in self.fetch_documents():
            documents.append(doc.content)
            metadatas.append(doc.metadata)
            ids.append(doc.id)
            
            # Process in batches
            if len(documents) >= batch_size:
                added = self._add_batch(documents, metadatas, ids)
                total_added += added
                documents, metadatas, ids = [], [], []
        
        # Add remaining
        if documents:
            added = self._add_batch(documents, metadatas, ids)
            total_added += added
        
        print(f"\n✅ Added {total_added} new documents to {self.source_name}")
        print(f"📊 Total in collection: {self.vector_store.collections[self.source_name].count()}")
        
        return total_added
    
    def _add_batch(self, documents: list, metadatas: list, ids: list) -> int:
        """Add a batch of documents to the vector store."""
        return self.vector_store.add_documents(
            source=self.source_name,
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )
    
    def chunk_text(
        self,
        text: str,
        chunk_size: int = 1000,
        overlap: int = 200
    ) -> list[str]:
        """
        Split text into overlapping chunks.
        
        Args:
            text: Text to chunk
            chunk_size: Target chunk size in characters
            overlap: Overlap between chunks
            
        Returns:
            List of text chunks
        """
        if len(text) <= chunk_size:
            return [text]
        
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + chunk_size
            
            # Try to break at sentence/paragraph boundary
            if end < len(text):
                # Look for paragraph break
                para_break = text.rfind('\n\n', start, end)
                if para_break > start + chunk_size // 2:
                    end = para_break
                else:
                    # Look for sentence break
                    for sep in ['. ', '! ', '? ', '\n']:
                        sent_break = text.rfind(sep, start, end)
                        if sent_break > start + chunk_size // 2:
                            end = sent_break + len(sep)
                            break
            
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            
            start = end - overlap
        
        return chunks
