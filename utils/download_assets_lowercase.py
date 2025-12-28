import os
import asyncio
import httpx
from urllib.parse import urljoin

# Determine paths relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# Files and Directories
DIFFERENCE_FILE = os.path.join(SCRIPT_DIR, "difference_lowercase.txt")
DEST_DIR = os.path.join(PROJECT_ROOT, "vcsky_new")
BASE_URL = "https://cdn.dos.zone/vcsky/"
CONCURRENCY = 20

async def download_file(client, relative_path):
    # Check if file already exists in destination
    dest_path = os.path.join(DEST_DIR, relative_path)
    if os.path.exists(dest_path):
        # print(f"[SKIP] {relative_path} (already exists)")
        return True

    # Create parent directories
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    # Strategy 1: Try Lowercase URL (Most likely correct for this CDN)
    lowercase_path = relative_path.replace("\\", "/").lower()
    url_lower = urljoin(BASE_URL, lowercase_path)
    
    try:
        response = await client.get(url_lower)
        if response.status_code == 200:
            with open(dest_path, "wb") as f:
                f.write(response.content)
            print(f"[OK-Lower] {relative_path}")
            return True
        elif response.status_code == 404:
            # Strategy 2: Try Original Casing URL (Fallback)
            original_url_path = relative_path.replace("\\", "/")
            url_orig = urljoin(BASE_URL, original_url_path)
            
            # Only try if it's actually different
            if url_orig != url_lower:
                print(f"[RETRY] {relative_path} (trying original case)")
                response = await client.get(url_orig)
                if response.status_code == 200:
                    with open(dest_path, "wb") as f:
                        f.write(response.content)
                    print(f"[OK-Orig] {relative_path}")
                    return True
            
            print(f"[ERR-404] {relative_path}")
            return False
        else:
            print(f"[ERR] {response.status_code} - {url_lower}")
            return False
    except Exception as e:
        print(f"[EXC] {e} - {relative_path}")
        return False

def parse_difference_file(filepath):
    files = []
    if not os.path.exists(filepath):
        print(f"Difference file not found: {filepath}")
        return files
        
    with open(filepath, "r") as f:
        lines = f.readlines()
    
    # Skip headers (look for lines that start with 'fetched\')
    # The file format has headers like "Scanning...", "Found...", "======"
    # We only want the file paths.
    
    start_parsing = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("===") and "MISSING" in line:
            # The header says "Found X files ... MISSING ..."
            # The next line is "===="
            # The lines after that are files.
            continue
        if line.startswith("==="):
            start_parsing = True
            continue
            
        # Heuristic: Valid file paths start with "fetched\" or "fetched/"
        if line.lower().startswith("fetched"):
            files.append(line)
            
    return files

async def main():
    print(f"Reading missing files from '{DIFFERENCE_FILE}'...")
    files_to_download = parse_difference_file(DIFFERENCE_FILE)
    
    if not files_to_download:
        print("No files found to download. Check the difference file format.")
        return

    print(f"Found {len(files_to_download)} missing files.")
    print(f"Downloading to '{DEST_DIR}'...")
    print("Skipping files that already exist.")
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        semaphore = asyncio.Semaphore(CONCURRENCY)
        
        async def sem_download(rel_path):
            async with semaphore:
                return await download_file(client, rel_path)
        
        tasks = [sem_download(f) for f in files_to_download]
        results = await asyncio.gather(*tasks)
        
    success_count = sum(1 for r in results if r)
    print(f"\nDone! Processed {len(files_to_download)} files.")
    print(f"Successfully downloaded: {success_count}")
    print(f"Failed: {len(files_to_download) - success_count}")

if __name__ == "__main__":
    asyncio.run(main())
