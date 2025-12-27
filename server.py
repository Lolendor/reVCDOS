import os
import sys
import asyncio
import argparse
import hashlib
from typing import Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
import additions.saves as saves
from additions.auth import BasicAuthMiddleware
from additions.cache import proxy_and_cache, get_local_file
from additions.packed import init_packed_archive, get_packed_file, is_initialized as packed_is_initialized

# Add utils path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))

# --- CONFIGURATION ---
VCSKY_BASE_URL = "https://cdn.dos.zone/vcsky/"
VCBR_BASE_URL = "https://br.cdn.dos.zone/vcsky/"

def request_to_url(request: Request, path: str, base_url: str):
    return f"{base_url}{path}"

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--custom_saves", action="store_true")
parser.add_argument("--login", type=str)
parser.add_argument("--password", type=str)
# Defaulting local flags to False to allow network fallback (Smart Cache)
parser.add_argument("--vcsky_local", action="store_true", default=False, help="Serve vcsky from local directory instead of proxy")
parser.add_argument("--vcbr_local", action="store_true", default=False, help="Serve vcbr from local directory instead of proxy")
parser.add_argument("--vcsky_url", type=str, default=VCSKY_BASE_URL, help="Custom vcsky proxy URL")
parser.add_argument("--vcbr_url", type=str, default=VCBR_BASE_URL, help="Custom vcbr proxy URL")
parser.add_argument("--vcsky_cache", action="store_true", default=True, help="Cache vcsky files locally. If files are not found in the local directory, they will be downloaded from the specified URL and saved to the local directory.")
parser.add_argument("--vcbr_cache", action="store_true", default=True, help="Cache vcbr files locally. If files are not found in the local directory, they will be downloaded from the specified URL and saved to the local directory.")
parser.add_argument("--cheats", action="store_true", help="Enable cheats in URL")
parser.add_argument("--open", action="store_true", help="Open browser on start")
args = parser.parse_args()


def _md5_hash(text: str) -> str:
    """Get MD5 hash of text."""
    return hashlib.md5(text.encode()).hexdigest()


def _is_url(path: str) -> bool:
    """Check if path is a URL."""
    return path.startswith("http://") or path.startswith("https://")


def _is_md5_hash(text: str) -> bool:
    """Check if text is a valid MD5 hash (32 hex characters)."""
    if len(text) != 32:
        return False
    try:
        int(text, 16)
        return True
    except ValueError:
        return False


def _get_unpacked_dir(source: str) -> str:
    """
    Get unpacked directory path for a source.
    
    If source IS a valid MD5 hash (32 hex chars), uses it directly.
    Otherwise computes MD5 hash from the source string.
    """
    # Check if source itself is a valid MD5 hash
    if _is_md5_hash(source):
        return os.path.join("unpacked", source.lower())
    
    # Compute hash from source
    source_hash = _md5_hash(source)
    return os.path.join("unpacked", source_hash)


def _check_unpacked_exists(unpacked_dir: str) -> bool:
    """Check if unpacked directory exists and has content."""
    if not os.path.isdir(unpacked_dir):
        return False
    
    # Check if vcsky or vcbr subdirectory exists with files
    for subdir in ["vcsky", "vcbr"]:
        subdir_path = os.path.join(unpacked_dir, subdir)
        if os.path.isdir(subdir_path):
            # Check if there are any files in subdirectories
            for root, dirs, files in os.walk(subdir_path):
                if files:
                    return True
    
    return False


async def _unpack_from_url(url: str, output_dir: str) -> bool:
    """
    Unpack archive directly from URL using streaming download.
    Uses downloader_brotli for efficient stream unpacking.
    """
    try:
        from utils.downloader_brotli import download_and_unpack_async
        print(f"Streaming and unpacking from URL: {url}")
        print(f"Output directory: {output_dir}")
        await download_and_unpack_async(url, output_dir)
        return True
    except Exception as e:
        print(f"Error unpacking from URL: {e}")
        return False


async def _unpack_from_file(file_path: str, output_dir: str) -> bool:
    """
    Unpack archive from local file.
    Uses packer_brotli.unpack_file for unpacking.
    """
    try:
        from utils.packer_brotli import unpack_file
        print(f"Unpacking local file: {file_path}")
        print(f"Output directory: {output_dir}")
        
        # Run sync unpack in executor
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, unpack_file, file_path, output_dir)
        return True
    except Exception as e:
        print(f"Error unpacking file: {e}")
        return False


def pack_source(source: str) -> Optional[str]:
    """
    Pack folder contents into {hash}.bin archive.
    
    If source is an MD5 hash, uses unpacked/{hash}/ folder.
    Otherwise uses the folder path directly.
    
    Packs all subfolders (vcsky/, vcbr/, etc.) by:
    1. Creating archive from first subfolder using pack_folder()
    2. Adding remaining subfolders using add_folder()
    
    Args:
        source: Folder path or MD5 hash
        
    Returns:
        Output filename (e.g., "abc123...def.bin") or None if failed
    """
    from utils.packer_brotli import pack_folder, add_folder
    
    # Resolve source to folder path and output hash
    if _is_md5_hash(source):
        folder_path = os.path.join("unpacked", source.lower())
        output_hash = source.lower()
    else:
        folder_path = source.rstrip('/\\')
        output_hash = _md5_hash(os.path.basename(folder_path))
    
    if not os.path.isdir(folder_path):
        print(f"Error: Folder not found: {folder_path}")
        return None
    
    output_file = f"{output_hash}.bin"
    
    # Get immediate subdirectories (vcsky, vcbr, etc.)
    subdirs = sorted([d for d in os.listdir(folder_path)
                     if os.path.isdir(os.path.join(folder_path, d)) and not d.startswith('.')])
    
    if not subdirs:
        print(f"Error: No subdirectories found in {folder_path}")
        return None
    
    print(f"Packing {len(subdirs)} subfolders from {folder_path} to {output_file}")
    print(f"Subfolders: {', '.join(subdirs)}")
    print()
    
    # Pack first subfolder (creates new archive)
    first_subdir = os.path.join(folder_path, subdirs[0])
    print(f"=== Creating archive from {subdirs[0]} ===")
    pack_folder(first_subdir, output_file)
    
    # Add remaining subfolders
    for subdir_name in subdirs[1:]:
        subdir_path = os.path.join(folder_path, subdir_name)
        print(f"\n=== Adding {subdir_name} ===")
        add_folder(output_file, subdir_path)
    
    final_size = os.path.getsize(output_file)
    print(f"\n=== Packing complete ===")
    print(f"Output: {output_file} ({final_size:,} bytes)")
    
    return output_file


async def setup_unpacked(source: str) -> tuple:
    """
    Setup unpacked mode - unpack archive if needed and return local paths.
    
    Args:
        source: Local file path, URL to packed archive, or MD5 hash of existing unpacked folder
        
    Returns:
        Tuple of (vcsky_local_path, vcbr_local_path) or (None, None) if failed
    """
    unpacked_dir = _get_unpacked_dir(source)
    
    # Check if source is just an MD5 hash (use existing folder only)
    is_hash_only = _is_md5_hash(source)
    
    # Check if already unpacked
    if _check_unpacked_exists(unpacked_dir):
        print(f"Using existing unpacked directory: {unpacked_dir}")
    elif is_hash_only:
        # Source is MD5 hash but folder doesn't exist - error
        print(f"Error: Unpacked folder not found for hash: {source}")
        print(f"Expected directory: {unpacked_dir}")
        return None, None
    else:
        # Need to unpack
        print(f"Unpacking to: {unpacked_dir}")
        os.makedirs(unpacked_dir, exist_ok=True)
        
        if _is_url(source):
            # Stream unpack from URL
            success = await _unpack_from_url(source, unpacked_dir)
        else:
            # Unpack from local file
            if not os.path.isfile(source):
                print(f"Error: Archive file not found: {source}")
                return None, None
            success = await _unpack_from_file(source, unpacked_dir)
        
        if not success:
            print(f"Failed to unpack from: {source}")
            return None, None
    
    # Determine vcsky and vcbr paths
    vcsky_path = None
    vcbr_path = None
    
    # Check for vcsky folder
    vcsky_candidate = os.path.join(unpacked_dir, "vcsky")
    if os.path.isdir(vcsky_candidate):
        vcsky_path = vcsky_candidate
        print(f"  vcsky: {vcsky_path}")
    
    # Check for vcbr folder
    vcbr_candidate = os.path.join(unpacked_dir, "vcbr")
    if os.path.isdir(vcbr_candidate):
        vcbr_path = vcbr_candidate
        print(f"  vcbr: {vcbr_path}")
    
    if not vcsky_path and not vcbr_path:
        print(f"Warning: No vcsky or vcbr folders found in {unpacked_dir}")
        # Maybe the folders are directly in unpacked_dir without vcsky/vcbr prefix
        # Check if there's a subfolder that looks like the archive name
        for item in os.listdir(unpacked_dir):
            item_path = os.path.join(unpacked_dir, item)
            if os.path.isdir(item_path):
                vcsky_sub = os.path.join(item_path, "vcsky")
                vcbr_sub = os.path.join(item_path, "vcbr")
                if os.path.isdir(vcsky_sub):
                    vcsky_path = vcsky_sub
                if os.path.isdir(vcbr_sub):
                    vcbr_path = vcbr_sub
    
    return vcsky_path, vcbr_path


app = FastAPI()

if args.login and args.password:
    app.add_middleware(BasicAuthMiddleware, username=args.login, password=args.password)

if args.custom_saves:
    app.include_router(saves.router)

# Ensure directories
os.makedirs("vcbr", exist_ok=True)
os.makedirs("vcsky", exist_ok=True)


# vcsky routes - packed archive, local, or proxy
@app.api_route("/vcsky/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def vc_sky_proxy(request: Request, path: str):
    local_path = os.path.join("vcsky", path)
    
    # 1. Strict Local Mode (No Network)
    if args.vcsky_local:
        if response := get_local_file(local_path, request):
            return response
        raise HTTPException(status_code=404, detail="File not found")
        
    # 2. Smart Cache Mode (Local -> Network -> Cache)
    # If caching is enabled (default), proxy_and_cache checks local first.
    url = request_to_url(request, path, args.vcsky_url)
    if args.vcsky_cache:
        return await proxy_and_cache(request, url, local_path)
        
    # 3. Proxy Only Mode (No Local Cache)
    return await proxy_and_cache(request, url, disable_cache=True)

@app.api_route("/vcbr/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def vc_br_proxy(request: Request, path: str):
    # Try packed archive first if enabled
    if args.packed and packed_is_initialized():
        packed_path = f"vcbr/{path}"
        if response := await get_packed_file(packed_path, request):
            return response
    
    # Try local directory
    if VCBR_LOCAL_PATH:
        local_path = os.path.join(VCBR_LOCAL_PATH, path)
        if response := get_local_file(local_path, request):
            return response
        raise HTTPException(status_code=404, detail="File not found")
    url = request_to_url(request, path, args.vcbr_url)
    if args.vcbr_cache:
        cache_path = os.path.join("vcbr", path)
        return await proxy_and_cache(request, url, cache_path)
    return await proxy_and_cache(request, url, disable_cache=True)


@app.get("/")
async def read_index():
    if os.path.exists("dist/index.html"):
        with open("dist/index.html", "r", encoding="utf-8") as f:
            content = f.read()
        custom_saves_val = "1" if args.custom_saves else "0"
        content = content.replace(
            'new URLSearchParams(window.location.search).get("custom_saves") === "1"',
            f'"{custom_saves_val}" === "1"'
        )
        return Response(content, media_type="text/html", headers={
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp"
        })
    return Response("index.html not found", status_code=404)

app.mount("/", StaticFiles(directory="dist"), name="root")


async def init_server():
    """Initialize server components that need async init."""
    global VCSKY_LOCAL_PATH, VCBR_LOCAL_PATH
    
    # Handle --unpacked mode first (takes precedence)
    if args.unpacked:
        vcsky_path, vcbr_path = await setup_unpacked(args.unpacked)
        if vcsky_path:
            VCSKY_LOCAL_PATH = vcsky_path
        if vcbr_path:
            VCBR_LOCAL_PATH = vcbr_path
    
    # Handle --packed mode
    if args.packed:
        # init_packed_archive handles both local paths and URLs
        # If URL is provided, it will download the file if not present locally
        result = await init_packed_archive(args.packed)
        if result is None:
            print(f"Warning: Failed to initialize packed archive from: {args.packed}")


def start_server(app=app, host="0.0.0.0", port=args.port):
    import uvicorn
    import webbrowser
    import threading

    url = f"http://localhost:{args.port}"
    if args.cheats:
        url += "/?cheats=1"

    print(f"GTA VC Caching Server Running at {url}")

    if args.open:
        def open_browser():
            webbrowser.open(url)
        threading.Timer(1.5, open_browser).start()

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    start_server()
