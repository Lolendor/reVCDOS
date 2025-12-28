import os
import asyncio
import httpx
import pathlib
from urllib.parse import urljoin

# Configuration
SOURCE_DIR = "vcsky"
DEST_DIR = "vcsky_new"
BASE_URL = "https://cdn.dos.zone/vcsky/"
CONCURRENCY = 20  # Number of simultaneous downloads

async def download_file(client, relative_path):
    url = urljoin(BASE_URL, relative_path.replace("\\", "/"))
    dest_path = os.path.join(DEST_DIR, relative_path)
    
    # Create parent directories
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    
    try:
        response = await client.get(url)
        if response.status_code == 200:
            with open(dest_path, "wb") as f:
                f.write(response.content)
            print(f"[OK] {relative_path}")
            return True
        else:
            print(f"[ERR] {response.status_code} - {url}")
            return False
    except Exception as e:
        print(f"[EXC] {e} - {url}")
        return False

async def main():
    if not os.path.exists(SOURCE_DIR):
        print(f"Source directory '{SOURCE_DIR}' not found!")
        return

    # 1. Scan for files
    print(f"Scanning '{SOURCE_DIR}' for files...")
    files_to_download = []
    for root, dirs, files in os.walk(SOURCE_DIR):
        for file in files:
            full_path = os.path.join(root, file)
            # Get path relative to SOURCE_DIR (e.g. "fetched/audio/intro.mp3")
            rel_path = os.path.relpath(full_path, SOURCE_DIR)
            files_to_download.append(rel_path)
    
    print(f"Found {len(files_to_download)} files.")
    
    # 2. Download files
    print(f"Downloading to '{DEST_DIR}'...")
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # Process in batches or use a semaphore
        semaphore = asyncio.Semaphore(CONCURRENCY)
        
        async def sem_download(rel_path):
            async with semaphore:
                return await download_file(client, rel_path)
        
        tasks = [sem_download(f) for f in files_to_download]
        results = await asyncio.gather(*tasks)
        
    success_count = sum(1 for r in results if r)
    print(f"\nDone! Downloaded {success_count}/{len(files_to_download)} files.")
    print(f"Optimized assets are in '{DEST_DIR}'.")
    print("You can now rename 'vcsky' to 'vcsky_old' and 'vcsky_new' to 'vcsky'.")

if __name__ == "__main__":
    asyncio.run(main())
