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

    uvicorn.run(app, host="0.0.0.0", port=args.port)
