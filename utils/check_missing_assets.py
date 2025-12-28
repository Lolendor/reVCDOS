import os
import sys

# Determine paths relative to this script
# Script is in /utils, so project root is one level up
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

DIR_ORIGINAL = os.path.join(PROJECT_ROOT, "vcsky")
DIR_NEW = os.path.join(PROJECT_ROOT, "vcsky_new")

def get_all_files(directory):
    file_set = set()
    if not os.path.exists(directory):
        print(f"Directory '{directory}' does not exist.")
        return file_set
        
    for root, dirs, files in os.walk(directory):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, directory)
            file_set.add(rel_path)
    return file_set

def main():
    print(f"Scanning '{DIR_ORIGINAL}'...")
    files_original = get_all_files(DIR_ORIGINAL)
    print(f"Found {len(files_original)} files.")

    print(f"Scanning '{DIR_NEW}'...")
    files_new = get_all_files(DIR_NEW)
    print(f"Found {len(files_new)} files.")

    # Find missing files (in original but not in new)
    missing_files = files_original - files_new
    
    print("\n" + "="*40)
    if missing_files:
        print(f"Found {len(missing_files)} files in 'vcsky' that are MISSING in 'vcsky_new':")
        print("="*40)
        for f in sorted(missing_files):
            print(f)
            
        print("\n" + "="*40)
        print("Possible reasons:")
        print("1. The file does not exist on the CDN (404 Error).")
        print("2. The download script failed or was interrupted.")
        print("3. These are local-only files not meant to be on the CDN.")
    else:
        print("Success! All files from the original folder are present in the new folder.")
        print("="*40)

if __name__ == "__main__":
    main()
