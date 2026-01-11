"""
Video Transcript Ingestor
=========================
Syncs video transcripts to the knowledge base.

Supports multiple sources:
1. Local transcript files (.txt, .vtt, .srt)
2. YouTube videos (auto-download transcripts)
3. Existing transcript JSON files

Setup for local files:
    Put transcript files in ./transcripts/ folder

Setup for YouTube:
    pip install youtube-transcript-api

Usage:
    python main.py --sync video
"""
import os
import re
import json
from pathlib import Path
from typing import Generator, Optional
import config
from .base import BaseIngestor, Document


class VideoIngestor(BaseIngestor):
    """Ingestor for video transcripts."""
    
    source_name = "video"
    
    def __init__(self, vector_store):
        super().__init__(vector_store)
        self.transcripts_path = Path(config.VIDEO_TRANSCRIPTS_PATH)
        
        # Create directory if needed
        self.transcripts_path.mkdir(parents=True, exist_ok=True)
    
    def fetch_documents(self) -> Generator[Document, None, None]:
        """Fetch all video transcripts."""
        # 1. Process local transcript files
        yield from self._process_local_files()
        
        # 2. Process YouTube URLs from config file if exists
        youtube_file = self.transcripts_path / "youtube_urls.txt"
        if youtube_file.exists():
            yield from self._process_youtube_file(youtube_file)
        
        # 3. Process pre-generated JSON transcripts
        yield from self._process_json_transcripts()
    
    def _process_local_files(self) -> Generator[Document, None, None]:
        """Process local .txt, .vtt, .srt files."""
        extensions = [".txt", ".vtt", ".srt"]
        
        files = [
            f for f in self.transcripts_path.iterdir()
            if f.is_file() and f.suffix.lower() in extensions
        ]
        
        if files:
            print(f"   Found {len(files)} local transcript files")
        
        for file_path in files:
            title = file_path.stem.replace("_", " ").replace("-", " ")
            
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            
            # Clean up VTT/SRT formatting
            if file_path.suffix.lower() in [".vtt", ".srt"]:
                content = self._clean_subtitle_format(content)
            
            if len(content) < 50:
                continue
            
            # Chunk the transcript
            chunks = self.chunk_text(content, chunk_size=1000, overlap=100)
            
            for i, chunk in enumerate(chunks):
                yield Document(
                    id=Document.create_id("video", f"{file_path.name}_{i}"),
                    content=f"# Video: {title}\n\n{chunk}",
                    metadata={
                        "source": "video",
                        "title": title,
                        "file": file_path.name,
                        "chunk": i,
                        "total_chunks": len(chunks),
                        "type": "local_file"
                    }
                )
    
    def _clean_subtitle_format(self, content: str) -> str:
        """Clean VTT/SRT subtitle formatting."""
        # Remove WEBVTT header
        content = re.sub(r'^WEBVTT\s*\n', '', content)
        
        # Remove timestamp lines (00:00:00.000 --> 00:00:05.000)
        content = re.sub(r'\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}', '', content)
        
        # Remove SRT sequence numbers
        content = re.sub(r'^\d+\s*$', '', content, flags=re.MULTILINE)
        
        # Remove positioning tags
        content = re.sub(r'<[^>]+>', '', content)
        
        # Clean up whitespace
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = '\n'.join(line.strip() for line in content.split('\n') if line.strip())
        
        return content
    
    def _process_youtube_file(self, file_path: Path) -> Generator[Document, None, None]:
        """Process YouTube URLs from a text file."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError:
            print("   ⚠ youtube-transcript-api not installed. Run: pip install youtube-transcript-api")
            return
        
        urls = file_path.read_text().strip().split('\n')
        urls = [url.strip() for url in urls if url.strip() and not url.startswith('#')]
        
        if urls:
            print(f"   Found {len(urls)} YouTube URLs to process")
        
        for url in urls:
            video_id = self._extract_youtube_id(url)
            if not video_id:
                print(f"   ⚠ Could not extract video ID from: {url}")
                continue
            
            try:
                transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
                
                # Combine transcript segments
                content = ' '.join(segment['text'] for segment in transcript_list)
                
                # Try to get video title (basic approach)
                title = f"YouTube Video {video_id}"
                
                chunks = self.chunk_text(content, chunk_size=1000, overlap=100)
                
                for i, chunk in enumerate(chunks):
                    yield Document(
                        id=Document.create_id("video", f"youtube_{video_id}_{i}"),
                        content=f"# Video: {title}\n\n{chunk}",
                        metadata={
                            "source": "video",
                            "title": title,
                            "url": f"https://www.youtube.com/watch?v={video_id}",
                            "video_id": video_id,
                            "chunk": i,
                            "total_chunks": len(chunks),
                            "type": "youtube"
                        }
                    )
                
                print(f"   ✓ Processed: {video_id}")
                
            except Exception as e:
                print(f"   ⚠ Failed to fetch transcript for {video_id}: {e}")
    
    def _extract_youtube_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from various URL formats."""
        patterns = [
            r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([a-zA-Z0-9_-]{11})',
            r'^([a-zA-Z0-9_-]{11})$'  # Just the ID
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    def _process_json_transcripts(self) -> Generator[Document, None, None]:
        """
        Process pre-generated JSON transcript files.
        
        Expected format:
        {
            "title": "Video Title",
            "url": "https://...",
            "transcript": "Full transcript text...",
            "duration": 3600,
            "metadata": {...}
        }
        """
        json_files = list(self.transcripts_path.glob("*.json"))
        
        if json_files:
            print(f"   Found {len(json_files)} JSON transcript files")
        
        for file_path in json_files:
            try:
                data = json.loads(file_path.read_text())
            except json.JSONDecodeError:
                print(f"   ⚠ Invalid JSON: {file_path.name}")
                continue
            
            title = data.get("title", file_path.stem)
            url = data.get("url", "")
            content = data.get("transcript", "")
            extra_meta = data.get("metadata", {})
            
            if not content or len(content) < 50:
                continue
            
            chunks = self.chunk_text(content, chunk_size=1000, overlap=100)
            
            for i, chunk in enumerate(chunks):
                metadata = {
                    "source": "video",
                    "title": title,
                    "url": url,
                    "file": file_path.name,
                    "chunk": i,
                    "total_chunks": len(chunks),
                    "type": "json",
                    **extra_meta
                }
                
                yield Document(
                    id=Document.create_id("video", f"json_{file_path.stem}_{i}"),
                    content=f"# Video: {title}\n\n{chunk}",
                    metadata=metadata
                )
    
    def add_youtube_video(self, url: str) -> bool:
        """
        Add a single YouTube video to be synced.
        
        Args:
            url: YouTube video URL
            
        Returns:
            True if added successfully
        """
        youtube_file = self.transcripts_path / "youtube_urls.txt"
        
        # Check if already exists
        existing = []
        if youtube_file.exists():
            existing = youtube_file.read_text().strip().split('\n')
        
        if url in existing:
            print(f"URL already in list: {url}")
            return False
        
        with open(youtube_file, 'a') as f:
            f.write(f"{url}\n")
        
        print(f"Added: {url}")
        return True
    
    def add_transcript_file(self, content: str, title: str, url: str = "") -> Path:
        """
        Add a transcript manually.
        
        Args:
            content: Transcript text
            title: Video title
            url: Optional video URL
            
        Returns:
            Path to created file
        """
        # Sanitize filename
        safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')
        file_path = self.transcripts_path / f"{safe_title}.json"
        
        data = {
            "title": title,
            "url": url,
            "transcript": content,
            "metadata": {
                "added_manually": True
            }
        }
        
        file_path.write_text(json.dumps(data, indent=2))
        print(f"Created: {file_path}")
        return file_path
