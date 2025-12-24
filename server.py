import os
import argparse
import httpx
import shutil
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from additions.auth import BasicAuthMiddleware
import additions.saves as saves

# --- CONFIGURATION ---
CDN_VCSKY = "https://cdn.dos.zone/vcsky/"
DIR_VCBR = "vcbr"
DIR_VCSKY = "vcsky"

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--custom_saves", action="store_true")
parser.add_argument("--login", type=str)
parser.add_argument("--password", type=str)
# Defaulting to True to prioritize local files
parser.add_argument("--vcsky_local", action="store_true", default=True)
parser.add_argument("--vcbr_local", action="store_true", default=True)
parser.add_argument("--cheats", action="store_true", help="Enable cheats in URL")
parser.add_argument("--open", action="store_true", help="Open browser on start")
args = parser.parse_args()

app = FastAPI()

if args.login and args.password:
    app.add_middleware(BasicAuthMiddleware, username=args.login, password=args.password)

if args.custom_saves:
    app.include_router(saves.router)

# Ensure directories
os.makedirs(DIR_VCBR, exist_ok=True)
os.makedirs(DIR_VCSKY, exist_ok=True)

def serve_file(path):
    """Helper to serve files with correct headers"""
    media_type = "application/octet-stream"
    if path.endswith(".wasm"): media_type = "application/wasm"
    elif path.endswith(".html"): media_type = "text/html"
    elif path.endswith(".js"): media_type = "application/javascript"
    elif path.endswith(".css"): media_type = "text/css"
    elif path.endswith(".mp3"): media_type = "audio/mpeg"

    headers = {
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Embedder-Policy": "require-corp",
        "Cache-Control": "no-cache" # Force browser to check server so we can fill cache
    }
    if path.endswith(".br"): headers["Content-Encoding"] = "br"

    return FileResponse(path, media_type=media_type, headers=headers)

async def fetch_and_cache(rel_path, local_root, remote_base):
    # Sanitize path
    clean_path = rel_path.lstrip("/")
    local_path = os.path.join(local_root, clean_path)

    # 1. SERVE LOCAL IF EXISTS
    if os.path.exists(local_path):
        # Don't log every tiny hit to keep console clean
        if not clean_path.endswith(".dff") and not clean_path.endswith(".txd"):
             print(f"[{local_root.upper()}] HIT: {clean_path}")
        return serve_file(local_path)

    # 2. DOWNLOAD IF MISSING
    print(f"[{local_root.upper()}] MISS: {clean_path} -> Downloading...")

    remote_url = f"{remote_base}{clean_path}"
    temp_path = local_path + ".tmp"

    try:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        async with httpx.AsyncClient() as client:
            async with client.stream("GET", remote_url) as r:
                if r.status_code == 200:
                    with open(temp_path, "wb") as f:
                        async for chunk in r.aiter_bytes():
                            f.write(chunk)

                    # Atomic move: Only rename if download finished successfully
                    shutil.move(temp_path, local_path)
                    print(f"[{local_root.upper()}] SAVED: {clean_path}")
                    return serve_file(local_path)
                else:
                    print(f"[{local_root.upper()}] 404 Remote: {remote_url}")
                    return Response(content="Not Found", status_code=404)
    except Exception as e:
        # Cleanup temp file if error
        if os.path.exists(temp_path):
            os.remove(temp_path)
        print(f"Error fetching {clean_path}: {e}")
        return Response(content="Server Error", status_code=500)

@app.get("/vcbr/{path:path}")
async def route_vcbr(path: str):
    # VCBR is strictly local now as you have the files
    local_path = os.path.join(DIR_VCBR, path)
    if os.path.exists(local_path):
        return serve_file(local_path)
    return Response(status_code=404)

@app.get("/vcsky/{path:path}")
async def route_vcsky(path: str):
    if path == "sha256sums.txt":
        return serve_file(os.path.join(DIR_VCSKY, path))
    return await fetch_and_cache(path, os.path.join(DIR_VCSKY, "fetched"), CDN_VCSKY)

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
