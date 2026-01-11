"""
Vector Store Service
====================
Wraps ChromaDB for vector search across all knowledge sources.
Builds on your existing multi_source_rag.py collections.
"""
import chromadb
from chromadb.utils import embedding_functions
from typing import Optional
import config
from models import SearchResult


class VectorStore:
    """
    Vector store service for MagicAnswer.
    Uses the same ChromaDB collections as your existing multi_source_rag.py.
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DB_PATH
        self.client = chromadb.PersistentClient(path=self.db_path)
        
        # Use same embedding function as existing setup
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=config.EMBEDDING_MODEL
        )
        
        # Get or create collections (matches your existing structure)
        self.collections = {
            "slack": self._get_or_create_collection("slack_messages"),
            "helpcenter": self._get_or_create_collection("helpcenter_articles"),
            "intercom": self._get_or_create_collection("intercom_conversations"),
            "confluence": self._get_or_create_collection("confluence_pages"),
            "video": self._get_or_create_collection("video_transcripts"),
        }
    
    def _get_or_create_collection(self, name: str):
        """Get or create a collection with the standard embedding function."""
        return self.client.get_or_create_collection(
            name=name,
            embedding_function=self.embedding_fn
        )
    
    def search(
        self,
        query: str,
        n_results: int = 10,
        sources: Optional[list[str]] = None
    ) -> list[SearchResult]:
        """
        Search across knowledge base collections.
        
        Args:
            query: Search query
            n_results: Total number of results to return
            sources: List of sources to search (default: all)
        
        Returns:
            List of SearchResult objects sorted by relevance
        """
        if sources is None:
            sources = list(self.collections.keys())
        
        all_results = []
        per_source = max(3, n_results // len(sources))
        
        for source in sources:
            if source not in self.collections:
                continue
                
            collection = self.collections[source]
            if collection.count() == 0:
                continue
            
            try:
                results = collection.query(
                    query_texts=[query],
                    n_results=per_source
                )
                
                docs = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                dists = results.get("distances", [[]])[0]
                
                for doc, meta, dist in zip(docs, metas, dists):
                    all_results.append(SearchResult(
                        content=doc,
                        source=source,
                        relevance=1 - dist,  # Convert distance to similarity
                        title=meta.get("title"),
                        url=meta.get("url"),
                        metadata=meta
                    ))
            except Exception as e:
                print(f"Warning: Error searching {source}: {e}")
                continue
        
        # Sort by relevance and return top n
        all_results.sort(key=lambda x: x.relevance, reverse=True)
        return all_results[:n_results]
    
    def add_documents(
        self,
        source: str,
        documents: list[str],
        metadatas: list[dict],
        ids: list[str]
    ) -> int:
        """
        Add documents to a collection.
        
        Args:
            source: Collection name (slack, helpcenter, etc.)
            documents: List of document texts
            metadatas: List of metadata dicts
            ids: List of unique document IDs
        
        Returns:
            Number of documents added
        """
        if source not in self.collections:
            raise ValueError(f"Unknown source: {source}")
        
        collection = self.collections[source]
        
        # Filter out existing IDs
        existing = collection.get(ids=ids)
        existing_ids = set(existing.get("ids", []))
        
        new_docs = []
        new_metas = []
        new_ids = []
        
        for doc, meta, doc_id in zip(documents, metadatas, ids):
            if doc_id not in existing_ids:
                new_docs.append(doc)
                new_metas.append(meta)
                new_ids.append(doc_id)
        
        if not new_docs:
            return 0
        
        # Add in batches
        batch_size = 100
        for i in range(0, len(new_docs), batch_size):
            collection.add(
                documents=new_docs[i:i + batch_size],
                metadatas=new_metas[i:i + batch_size],
                ids=new_ids[i:i + batch_size]
            )
        
        return len(new_docs)
    
    def get_stats(self) -> dict:
        """Get statistics for all collections."""
        return {
            name: collection.count()
            for name, collection in self.collections.items()
        }
    
    def clear_collection(self, source: str):
        """Clear all documents from a collection."""
        if source in self.collections:
            self.client.delete_collection(f"{source}_messages" if source == "slack" else source)
            self.collections[source] = self._get_or_create_collection(
                f"{source}_messages" if source == "slack" else source
            )
