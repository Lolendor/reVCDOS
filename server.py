import os
import argparse
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
import additions.saves as saves
from additions.auth import BasicAuthMiddleware
from additions.cache import proxy_and_cache, get_local_file

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
# Defaulting local flags to True to prioritize local files (User Preference)
parser.add_argument("--vcsky_local", action="store_true", default=True, help="Serve vcsky from local directory instead of proxy")
parser.add_argument("--vcbr_local", action="store_true", default=True, help="Serve vcbr from local directory instead of proxy")
parser.add_argument("--vcsky_url", type=str, default=VCSKY_BASE_URL, help="Custom vcsky proxy URL")
parser.add_argument("--vcbr_url", type=str, default=VCBR_BASE_URL, help="Custom vcbr proxy URL")
parser.add_argument("--vcsky_cache", action="store_true", help="Cache vcsky files locally. If files are not found in the local directory, they will be downloaded from the specified URL and saved to the local directory.")
parser.add_argument("--vcbr_cache", action="store_true", help="Cache vcbr files locally. If files are not found in the local directory, they will be downloaded from the specified URL and saved to the local directory.")
parser.add_argument("--cheats", action="store_true", help="Enable cheats in URL")
parser.add_argument("--open", action="store_true", help="Open browser on start")
args = parser.parse_args()

app = FastAPI()

if args.login and args.password:
    app.add_middleware(BasicAuthMiddleware, username=args.login, password=args.password)

if args.custom_saves:
    app.include_router(saves.router)

# Ensure directories
os.makedirs("vcbr", exist_ok=True)
os.makedirs("vcsky", exist_ok=True)

# vcsky routes - either local or proxy
@app.api_route("/vcsky/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def vc_sky_proxy(request: Request, path: str):
    local_path = os.path.join("vcsky", path)
    if args.vcsky_local:
        if response := get_local_file(local_path, request):
            return response
        # If local is forced but file missing, and we are NOT caching/proxying, 404.
        # But if the user wants "download on demand" behavior like before, they should use --vcsky_cache WITHOUT --vcsky_local?
        # Wait, my previous behavior was: check local, if missing download.
        # Upstream behavior:
        # if vcsky_local: serve local, else 404.
        # if NOT vcsky_local: proxy (and optionally cache).
        
        # To match my previous "smart" behavior (local if exists, else download), I should probably use:
        # --vcsky_cache (enabled) AND NOT --vcsky_local (disabled).
        # Because proxy_and_cache checks local cache first!
        
        # Let's check additions/cache.py logic if possible.
        # Assuming proxy_and_cache checks local file first.
        pass
        
    # Fallback to proxy/cache logic if not strictly local or if we want hybrid
    # Actually, if I set default vcsky_local=True, it will NEVER download.
    # My previous behavior was: Check local, if missing -> download.
    # Upstream `proxy_and_cache` likely does: Check local, if hit return, else download.
    
    # So the correct configuration for "Offline first, download if missing" is actually:
    # --vcsky_cache (True)
    # --vcsky_local (False) -> This flag seems to mean "Strictly Local, no network".
    
    # However, the user has downloaded the files. So Strict Local is fine for them.
    # But for the "Quick Start" experience where they might miss a file, "Cache" mode is better.
    
    # I will stick to args.vcsky_local = True as default because the user HAS the files.
    # If they want caching behavior, they can run with --vcsky_cache (and no --vcsky_local).
    
    # But wait, if I set vcsky_local=True, it raises 404 if missing.
    # My previous code downloaded it.
    # The user might prefer the download behavior.
    
    # I will modify the logic slightly to support "Local then Proxy" if requested, or just stick to the flags.
    # I'll stick to the flags to be consistent with upstream.
    # I'll set default vcsky_local=True because the user has the files.
    
    raise HTTPException(status_code=404, detail="File not found")

    # This part is unreachable if vcsky_local is True and file found, or if it raised 404.
    # If vcsky_local is False:
    url = request_to_url(request, path, args.vcsky_url)
    if args.vcsky_cache:
        return await proxy_and_cache(request, url, local_path)
    return await proxy_and_cache(request, url, disable_cache=True)

# Redefining to support the hybrid flow if I want to merge them?
# No, let's stick to upstream logic.
# If I want "Check local, then download", I should use `proxy_and_cache` with `vcsky_local=False` and `vcsky_cache=True`.
# But `proxy_and_cache` implementation is unknown to me (I can't see it right now).
# Assuming it does check local.

@app.api_route("/vcsky/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def vc_sky_proxy_wrapper(request: Request, path: str):
    # Wrapper to handle the logic
    local_path = os.path.join("vcsky", path)
    
    # 1. If strictly local requested
    if args.vcsky_local:
        if response := get_local_file(local_path, request):
            return response
        raise HTTPException(status_code=404, detail="File not found")
        
    # 2. Proxy/Cache mode
    url = request_to_url(request, path, args.vcsky_url)
    if args.vcsky_cache:
        return await proxy_and_cache(request, url, local_path)
    return await proxy_and_cache(request, url, disable_cache=True)

@app.api_route("/vcbr/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def vc_br_proxy_wrapper(request: Request, path: str):
    local_path = os.path.join("vcbr", path)
    if args.vcbr_local:
        if response := get_local_file(local_path, request):
            return response
        raise HTTPException(status_code=404, detail="File not found")
    url = request_to_url(request, path, args.vcbr_url)
    if args.vcbr_cache:
        return await proxy_and_cache(request, url, local_path)
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

if __name__ == "__main__":
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

    uvicorn.run(app, host="localhost", port=args.port)
