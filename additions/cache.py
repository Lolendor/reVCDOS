import os
import httpx
import tempfile
import shutil
import brotli
from fastapi import Request
from fastapi.responses import StreamingResponse, FileResponse
from starlette.background import BackgroundTask

def _get_file_headers(local_path: str) -> dict:
    headers = {
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Embedder-Policy": "require-corp"
    }
    
    if local_path.endswith(".br"):
        headers["Content-Encoding"] = "br"
        headers["Content-Type"] = "application/octet-stream"
    
    return headers

def _get_media_type(local_path: str) -> str:
    """Get appropriate media type based on file extension."""
    if local_path.endswith(".wasm.br") or local_path.endswith(".wasm"):
        return "application/wasm"
    if local_path.endswith(".br"):
        return "application/octet-stream"
    return None  # Let FileResponse auto-detect

def get_local_file(local_path: str, request: Request = None) -> FileResponse | StreamingResponse | None:
    """
    Get a local file as response. If it's a .br file and client doesn't accept brotli,
    decompress it on the fly.
    
    Args:
        local_path: Path to the local file
        request: Optional request object to check Accept-Encoding header
        
    Returns:
        FileResponse, StreamingResponse (for decompressed .br), or None if file not found
    """
    if not os.path.isfile(local_path):
        return None
    
    headers = _get_file_headers(local_path)
    media_type = _get_media_type(local_path)
    
    # Check if we need to decompress .br file for client
    is_br_file = local_path.endswith(".br")
    need_decompress = is_br_file and request and not _client_accepts_brotli(request)
    
    if need_decompress:
        # Stream decompressed content
        headers.pop("Content-Encoding", None)
        headers["Content-Type"] = "application/octet-stream"
        
        def iterate_decompressed():
            with open(local_path, "rb") as f:
                decompressor = brotli.Decompressor()
                while chunk := f.read(65536):  # 64KB chunks
                    yield decompressor.process(chunk)
        
        return StreamingResponse(
            iterate_decompressed(),
            media_type="application/octet-stream",
            headers=headers
        )
    
    if media_type:
        return FileResponse(local_path, media_type=media_type, headers=headers)
    return FileResponse(local_path, headers=headers)

def _client_accepts_brotli(request: Request) -> bool:
    """Check if client accepts brotli encoding."""
    accept_encoding = request.headers.get("accept-encoding", "")
    return "br" in accept_encoding.lower()

async def proxy_and_cache(request: Request, url: str, local_path: str = None, disable_cache: bool = False):
    """
    Proxy request to upstream URL and optionally cache the response.
    
    Args:
        request: FastAPI request object
        url: Upstream URL to proxy to
        local_path: Local file path for caching (required if disable_cache is False)
        disable_cache: If True, just proxy without caching or reading from local file
    """
    if not disable_cache and local_path:
        if response := get_local_file(local_path, request):
            return response
    
    # Check if this is a .br file and client doesn't support brotli
    is_br_file = url.endswith(".br")
    client_accepts_br = _client_accepts_brotli(request)
    need_decompress = is_br_file and not client_accepts_br
    
    client = httpx.AsyncClient(timeout=None)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length", "accept-encoding"]}
    
    req = client.build_request(request.method, url, headers=headers)
    r = await client.send(req, stream=True)
    
    excluded_headers = {"transfer-encoding", "connection", "keep-alive", "upgrade"}
    response_headers = {k: v for k, v in r.headers.items() if k.lower() not in excluded_headers}
    response_headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response_headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    
    # If decompressing, remove content-encoding from response
    if need_decompress:
        response_headers.pop("content-encoding", None)
        response_headers.pop("Content-Encoding", None)
        # Also remove content-length since it will change after decompression
        response_headers.pop("content-length", None)
        response_headers.pop("Content-Length", None)
    
    if r.status_code != 200 or disable_cache or not local_path:
        async def stream_with_decompress():
            decompressor = brotli.Decompressor() if need_decompress else None
            try:
                async for chunk in r.aiter_raw():
                    if decompressor:
                        yield decompressor.process(chunk)
                    else:
                        yield chunk
            finally:
                await r.aclose()
                await client.aclose()
        
        return StreamingResponse(
            stream_with_decompress(),
            status_code=r.status_code,
            headers=response_headers
        )

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    
    temp_file = tempfile.NamedTemporaryFile(delete=False, dir=os.path.dirname(local_path))
    temp_file_path = temp_file.name
    
    async def iterate_and_save():
        success = False
        decompressor = brotli.Decompressor() if need_decompress else None
        try:
            async for chunk in r.aiter_raw():
                # Always save raw (compressed) data to cache
                temp_file.write(chunk)
                # Yield decompressed data if needed
                if decompressor:
                    yield decompressor.process(chunk)
                else:
                    yield chunk
            temp_file.close()
            
            # If we reached here, the stream was fully consumed
            shutil.move(temp_file_path, local_path)
            success = True
        finally:
            if not success:
                temp_file.close()
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
            await r.aclose()
            await client.aclose()

    return StreamingResponse(
        iterate_and_save(),
        status_code=r.status_code,
        headers=response_headers
    )
