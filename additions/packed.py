"""
Module for serving files from a PackedArchive.
Provides similar interface to cache.py but reads from packed .bin archives.

Supports:
- Serving files from vcsky/ and vcbr/ paths inside the archive
- Brotli compression passthrough when client supports it (Accept-Encoding: br)
- On-the-fly decompression when client doesn't support brotli
- Proper handling of .br files (stored without additional compression)
- Auto-download from URL if archive file is not present locally
"""

import os
import sys
from typing import Optional
from urllib.parse import urlparse

import httpx
import brotli
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

# Import PackedArchive from utils
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'utils'))
from utils.packer_brotli import PackedArchive

# Global archive instance (initialized by init_packed_archive)
_archive: Optional[PackedArchive] = None


def _is_url(path: str) -> bool:
    """Check if the path is a URL."""
    return path.startswith("http://") or path.startswith("https://")


def _get_filename_from_url(url: str) -> str:
    """Extract filename from URL."""
    parsed = urlparse(url)
    path = parsed.path
    filename = os.path.basename(path)
    if not filename:
        filename = "packed.bin"
    return filename


async def _download_file(url: str, dest_path: str) -> bool:
    """
    Download a file from URL to destination path.
    
    Args:
        url: URL to download from
        dest_path: Local path to save the file
        
    Returns:
        True if download succeeded, False otherwise
    """
    print(f"Downloading archive from {url}...")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), follow_redirects=True) as client:
            async with client.stream('GET', url) as response:
                response.raise_for_status()
                
                content_length = response.headers.get('content-length')
                total_size = int(content_length) if content_length else 0
                downloaded = 0
                
                with open(dest_path, 'wb') as f:
                    async for chunk in response.aiter_bytes(65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            print(f"\r  Downloaded: {downloaded / 1024 / 1024:.1f} MB ({percent:.1f}%)", end="", flush=True)
                        else:
                            print(f"\r  Downloaded: {downloaded / 1024 / 1024:.1f} MB", end="", flush=True)
                
                print()  # New line after download complete
                print(f"  Saved to: {dest_path}")
                return True
    except httpx.HTTPStatusError as e:
        print(f"Failed to download: HTTP {e.response.status_code}")
        return False
    except Exception as e:
        print(f"Error downloading file: {e}")
        return False


async def resolve_packed_source(source: str) -> Optional[str]:
    """
    Resolve packed archive source to local file path.
    
    If source is a URL:
    - Extract filename from URL
    - Check if file exists locally and has size > 0
    - If not, download it from URL
    - Return local file path
    
    If source is a local path:
    - Return it as-is
    
    Args:
        source: URL or local file path
        
    Returns:
        Local file path, or None if download failed
    """
    if not _is_url(source):
        # Local file path
        return source
    
    # It's a URL - extract filename and check if we have it locally
    filename = _get_filename_from_url(source)
    local_path = filename  # Save in current directory
    
    # Check if file exists and has size > 0
    if os.path.isfile(local_path) and os.path.getsize(local_path) > 0:
        print(f"Using existing archive: {local_path} ({os.path.getsize(local_path)} bytes)")
        return local_path
    
    # File doesn't exist or is empty - download it
    if await _download_file(source, local_path):
        return local_path
    
    return None


async def init_packed_archive(source: str) -> Optional[PackedArchive]:
    """
    Initialize the packed archive.
    Must be called before using get_packed_file().
    
    Supports both local file paths and URLs.
    If a URL is provided, the file will be downloaded if not present locally.
    
    Args:
        source: Path to the .bin archive file or URL to download from
        
    Returns:
        Initialized PackedArchive instance, or None if failed
    """
    global _archive
    
    # Resolve source to local path (download if needed)
    archive_path = await resolve_packed_source(source)
    if archive_path is None:
        print(f"Failed to resolve packed archive source: {source}")
        return None
    
    if not os.path.isfile(archive_path):
        print(f"Archive file not found: {archive_path}")
        return None
    
    _archive = PackedArchive(archive_path)
    await _archive.init()
    print(f"Loaded packed archive: {archive_path}")
    print(f"  Folders: {len(_archive.list_folders())}")
    print(f"  Files: {len(_archive.list_files())}")
    return _archive


def get_archive() -> Optional[PackedArchive]:
    """Get the global archive instance."""
    return _archive


def is_initialized() -> bool:
    """Check if the archive is initialized."""
    return _archive is not None and _archive._initialized


def _client_accepts_brotli(request: Request) -> bool:
    """Check if client accepts brotli encoding."""
    accept_encoding = request.headers.get("accept-encoding", "")
    return "br" in accept_encoding.lower()


def _get_response_headers(use_brotli: bool, media_type: str) -> dict:
    """Get response headers, optionally with brotli encoding."""
    headers = {
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Embedder-Policy": "require-corp",
        "Content-Type": media_type
    }
    
    if use_brotli:
        headers["Content-Encoding"] = "br"
    
    return headers


def _is_br_file(path: str) -> bool:
    """Check if the file is a .br (pre-compressed brotli) file."""
    return path.lower().endswith(".br")


def _get_media_type(path: str) -> str:
    """
    Get appropriate media type based on file extension.
    For .br files, returns the media type of the underlying content.
    """
    lower_path = path.lower()
    
    # Handle .br files - get media type of what's inside
    if lower_path.endswith(".wasm.br"):
        return "application/wasm"
    if lower_path.endswith(".js.br"):
        return "application/javascript"
    if lower_path.endswith(".json.br"):
        return "application/json"
    if lower_path.endswith(".html.br"):
        return "text/html"
    if lower_path.endswith(".css.br"):
        return "text/css"
    if lower_path.endswith(".br"):
        # Generic .br file - use octet-stream
        return "application/octet-stream"
    
    # Non-.br files
    if lower_path.endswith(".wasm"):
        return "application/wasm"
    if lower_path.endswith(".js"):
        return "application/javascript"
    if lower_path.endswith(".json"):
        return "application/json"
    if lower_path.endswith(".html"):
        return "text/html"
    if lower_path.endswith(".css"):
        return "text/css"
    if lower_path.endswith(".png"):
        return "image/png"
    if lower_path.endswith(".jpg") or lower_path.endswith(".jpeg"):
        return "image/jpeg"
    if lower_path.endswith(".gif"):
        return "image/gif"
    if lower_path.endswith(".svg"):
        return "image/svg+xml"
    if lower_path.endswith(".mp3"):
        return "audio/mpeg"
    if lower_path.endswith(".wav"):
        return "audio/wav"
    if lower_path.endswith(".ogg"):
        return "audio/ogg"
    
    return "application/octet-stream"


async def get_packed_file(path: str, request: Request) -> Optional[Response]:
    """
    Get a file from the packed archive.
    
    How .br files work:
    - .br files are stored in the archive WITHOUT additional brotli compression
    - archive.open(path) returns the raw .br file content (already brotli-compressed)
    - If client accepts br: send .br data with Content-Encoding: br
    - If client doesn't accept br: decompress .br data and send plain
    
    How regular files work:
    - Regular files are stored with brotli compression in the archive
    - If client accepts br: keep_brotli=True returns compressed data, send with Content-Encoding: br
    - If client doesn't accept br: keep_brotli=False decompresses, send plain
    
    Args:
        path: Path to the file inside the archive (e.g., "vcsky/fetched/model.txd")
        request: FastAPI request object to check Accept-Encoding header
        
    Returns:
        Response with file data, or None if file not found or archive not initialized
    """
    if not is_initialized():
        return None
    
    # Check if file exists in archive
    if not _archive.exists(path):
        return None
    
    # Check if client accepts brotli
    client_accepts_br = _client_accepts_brotli(request)
    
    # Check if file is a .br file
    is_br_file = _is_br_file(path)
    
    # Get media type based on file extension
    media_type = _get_media_type(path)
    
    try:
        if is_br_file:
            # .br files are stored as-is (no archive compression)
            # archive.open() returns the raw .br content
            async with _archive.open(path, keep_brotli=False) as f:
                br_data = f.read()
            
            if client_accepts_br:
                # Send the .br data with Content-Encoding: br
                headers = _get_response_headers(use_brotli=True, media_type=media_type)
                return Response(content=br_data, headers=headers)
            else:
                # Decompress .br for client
                decompressed_data = brotli.decompress(br_data)
                headers = _get_response_headers(use_brotli=False, media_type=media_type)
                return Response(content=decompressed_data, headers=headers)
        else:
            # Regular files: use archive's brotli compression
            if client_accepts_br:
                async with _archive.open(path, keep_brotli=True) as f:
                    data = f.read()
                headers = _get_response_headers(use_brotli=True, media_type=media_type)
            else:
                async with _archive.open(path, keep_brotli=False) as f:
                    data = f.read()
                headers = _get_response_headers(use_brotli=False, media_type=media_type)
            
            return Response(content=data, headers=headers)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"Error reading file from archive: {path} - {e}")
        return None


async def get_packed_file_streaming(path: str, request: Request, chunk_size: int = 65536) -> Optional[StreamingResponse]:
    """
    Get a file from the packed archive as a streaming response.
    
    Args:
        path: Path to the file inside the archive
        request: FastAPI request object to check Accept-Encoding header
        chunk_size: Size of chunks for streaming (default: 64KB)
        
    Returns:
        StreamingResponse with file data, or None if file not found
    """
    if not is_initialized():
        return None
    
    if not _archive.exists(path):
        return None
    
    client_accepts_br = _client_accepts_brotli(request)
    is_br = _is_br_file(path)
    media_type = _get_media_type(path)
    
    async def generate():
        try:
            if is_br:
                # .br file: stored as-is, open returns raw .br data
                async with _archive.open(path, keep_brotli=False) as f:
                    br_data = f.data
                
                if client_accepts_br:
                    # Send .br data directly
                    for i in range(0, len(br_data), chunk_size):
                        yield br_data[i:i + chunk_size]
                else:
                    # Decompress for client
                    decompressed_data = brotli.decompress(br_data)
                    for i in range(0, len(decompressed_data), chunk_size):
                        yield decompressed_data[i:i + chunk_size]
            else:
                # Regular file
                async with _archive.open(path, keep_brotli=client_accepts_br) as f:
                    data = f.data
                    for i in range(0, len(data), chunk_size):
                        yield data[i:i + chunk_size]
        except Exception as e:
            print(f"Error streaming file from archive: {path} - {e}")
    
    headers = _get_response_headers(use_brotli=client_accepts_br, media_type=media_type)
    
    return StreamingResponse(generate(), headers=headers)


def file_exists(path: str) -> bool:
    """
    Check if a file exists in the packed archive.
    
    Args:
        path: Path to the file inside the archive
        
    Returns:
        True if file exists, False otherwise
    """
    if not is_initialized():
        return False
    return _archive.exists(path)


def list_files(folder: Optional[str] = None) -> list:
    """
    List files in the archive.
    
    Args:
        folder: Optional folder path to filter by
        
    Returns:
        List of file paths
    """
    if not is_initialized():
        return []
    return _archive.list_files(folder)


def list_folders() -> list:
    """
    List all folders in the archive.
    
    Returns:
        List of folder paths
    """
    if not is_initialized():
        return []
    return _archive.list_folders()
