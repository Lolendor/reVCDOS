#!/usr/bin/env python3
"""
File packer that packs files from a folder and its subfolders into a single file.
Uses ULEB128 encoding for string and file lengths.
Supports folder and file deduplication.
Uses Brotli compression (quality 11) with parallel processing for maximum compression.
All strings (folder names, file names) are also Brotli compressed.

Format:
- For each folder:
  - Folder type (1 byte): 0 = normal folder, 1 = copy of another folder
  - Compressed folder name length (ULEB128)
  - Compressed folder name bytes (Brotli)
  - If type == 0 (normal folder):
    - Number of files in folder (ULEB128)
    - For each file:
      - Compressed filename length (ULEB128)
      - Compressed filename bytes (Brotli)
      - File type (1 byte): 0 = content, 1 = reference to another file
      - If file type == 0:
        - File content length (ULEB128) - compressed size
        - File content bytes (Brotli compressed)
      - If file type == 1:
        - Compressed source folder path length (ULEB128)
        - Compressed source folder path bytes (Brotli)
        - Compressed source filename length (ULEB128)
        - Compressed source filename bytes (Brotli)
  - If type == 1 (copy folder):
    - Compressed source folder name length (ULEB128)
    - Compressed source folder name bytes (Brotli)

Supports both sync and async operations with parallel Brotli compression.
Also provides PackedArchive class for reading files directly from archive.
"""

import os
import sys
import asyncio
import hashlib
import shutil
import io
import aiofiles
import brotli
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Iterator, Tuple, Generator, AsyncIterator, AsyncGenerator, Union, Dict, List, Set, Optional, BinaryIO
from dataclasses import dataclass, field
from contextlib import asynccontextmanager

# Brotli compression settings
BROTLI_QUALITY = 11  # Maximum compression
BROTLI_LGWIN = 24    # Window size (max)
BROTLI_MODE = brotli.MODE_GENERIC

# Files to ignore during packing (macOS, Windows, etc. junk files)
IGNORED_FILES = {
    '.DS_Store',
    '._.DS_Store',
    'Thumbs.db',
    'desktop.ini'
}

# File patterns to ignore (starting with)
IGNORED_PREFIXES = ('._',)


def should_ignore_file(filename: str) -> bool:
    """Check if a file should be ignored during packing."""
    if filename in IGNORED_FILES:
        return True
    for prefix in IGNORED_PREFIXES:
        if filename.startswith(prefix):
            return True
    return False


def is_already_brotli(filename: str) -> bool:
    """Check if file is already brotli-compressed (.br extension)."""
    return filename.lower().endswith('.br')


def compress_brotli(data: bytes) -> bytes:
    """Compress data using Brotli with maximum quality."""
    return brotli.compress(data, quality=BROTLI_QUALITY, lgwin=BROTLI_LGWIN, mode=BROTLI_MODE)


def decompress_brotli(data: bytes) -> bytes:
    """Decompress Brotli-compressed data."""
    return brotli.decompress(data)


def compress_string(s: str) -> bytes:
    """Compress a string (folder/file name) using Brotli."""
    return compress_brotli(s.encode('utf-8'))


def decompress_string(data: bytes) -> str:
    """Decompress a Brotli-compressed string."""
    return decompress_brotli(data).decode('utf-8')


def compress_file_task(args: Tuple[str, str, str]) -> Tuple[str, str, str, bytes, int, int, bool]:
    """
    Compress a file using Brotli (or keep as-is for .br files). Used for parallel processing.
    Args: (file_path, rel_path, filename)
    Returns: (rel_path, filename, file_path, data, original_size, final_size, is_precompressed)
    
    For .br files: returns data as-is (already brotli-compressed)
    For other files: returns brotli-compressed data
    """
    file_path, rel_path, filename = args
    with open(file_path, 'rb') as f:
        content = f.read()
    original_size = len(content)
    
    # .br files are already brotli-compressed - store as-is
    if is_already_brotli(filename):
        return (rel_path, filename, file_path, content, original_size, original_size, True)
    
    # Compress other files
    compressed = compress_brotli(content)
    compressed_size = len(compressed)
    return (rel_path, filename, file_path, compressed, original_size, compressed_size, False)


def encode_uleb128(value: int) -> bytes:
    """Encode an unsigned integer as ULEB128 bytes."""
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        result.append(byte)
        if value == 0:
            break
    return bytes(result)


def decode_uleb128(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Decode ULEB128 bytes to an unsigned integer. Returns (value, bytes_read)."""
    result = 0
    shift = 0
    bytes_read = 0
    while True:
        byte = data[offset + bytes_read]
        bytes_read += 1
        result |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            break
        shift += 7
    return result, bytes_read


def uleb128_size(value: int) -> int:
    """Calculate the size of a ULEB128 encoded value."""
    size = 0
    while True:
        value >>= 7
        size += 1
        if value == 0:
            break
    return size


# ============== FOLDER/FILE SIGNATURE ==============

@dataclass
class FolderSignature:
    """Signature of a folder for deduplication."""
    path: str
    file_count: int
    files: Dict[str, str]  # filename -> content hash
    total_hash: str  # combined hash of all files
    
    @staticmethod
    def compute_file_hash(file_path: str) -> str:
        """Compute MD5 hash of a file."""
        hasher = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    @classmethod
    def from_folder(cls, folder_path: str, rel_path: str) -> 'FolderSignature':
        """Create signature from a folder."""
        files = {}
        file_list = sorted(os.listdir(folder_path))
        
        # Only include regular files, not subdirectories, and skip ignored files
        for filename in file_list:
            if should_ignore_file(filename):
                continue
            file_path = os.path.join(folder_path, filename)
            if os.path.isfile(file_path):
                files[filename] = cls.compute_file_hash(file_path)
        
        # Compute total hash from sorted file hashes
        total_hasher = hashlib.md5()
        for filename in sorted(files.keys()):
            total_hasher.update(filename.encode('utf-8'))
            total_hasher.update(files[filename].encode('utf-8'))
        
        return cls(
            path=rel_path,
            file_count=len(files),
            files=files,
            total_hash=total_hasher.hexdigest()
        )
    
    def matches(self, other: 'FolderSignature') -> bool:
        """Check if this folder has identical content to another."""
        if self.file_count != other.file_count:
            return False
        if self.total_hash != other.total_hash:
            return False
        return self.files == other.files


@dataclass
class FileInfo:
    """Information about a file for deduplication."""
    folder_path: str
    filename: str
    full_path: str
    size: int
    hash: str


def find_duplicates(folder_path: str, parent_dir: str) -> Tuple[Dict[str, str], Dict[str, Tuple[str, str]]]:
    """
    Scan folder structure and find duplicates.
    Returns:
        - folder_duplicates: dict mapping duplicate folder path -> source folder path
        - file_duplicates: dict mapping (folder_path, filename) -> (source_folder, source_filename)
    """
    folder_path = folder_path.rstrip('/\\')
    
    # First pass: collect all folder and file signatures
    folder_signatures: Dict[str, FolderSignature] = {}
    all_files: Dict[str, List[FileInfo]] = {}  # hash -> list of files with that hash
    
    for root, dirs, files in os.walk(folder_path):
        if not files:
            continue
        
        rel_path = os.path.relpath(root, parent_dir)
        sig = FolderSignature.from_folder(root, rel_path)
        folder_signatures[rel_path] = sig
        
        # Collect individual file info (skip ignored files)
        for filename in files:
            if should_ignore_file(filename):
                continue
            file_path = os.path.join(root, filename)
            if os.path.isfile(file_path):
                file_size = os.path.getsize(file_path)
                file_hash = sig.files.get(filename) or FolderSignature.compute_file_hash(file_path)
                
                file_info = FileInfo(
                    folder_path=rel_path,
                    filename=filename,
                    full_path=file_path,
                    size=file_size,
                    hash=file_hash
                )
                
                if file_hash not in all_files:
                    all_files[file_hash] = []
                all_files[file_hash].append(file_info)
    
    # Find folder duplicates
    folder_duplicates: Dict[str, str] = {}
    seen_folder_hashes: Dict[str, str] = {}
    
    for rel_path in sorted(folder_signatures.keys()):
        sig = folder_signatures[rel_path]
        
        if sig.total_hash in seen_folder_hashes:
            source_path = seen_folder_hashes[sig.total_hash]
            source_sig = folder_signatures[source_path]
            
            if sig.matches(source_sig):
                folder_duplicates[rel_path] = source_path
                print(f"  Duplicate folder: {rel_path} -> {source_path}")
        else:
            seen_folder_hashes[sig.total_hash] = rel_path
    
    # Find file duplicates (only for files not in duplicate folders)
    file_duplicates: Dict[Tuple[str, str], Tuple[str, str]] = {}
    
    for file_hash, file_list in all_files.items():
        if len(file_list) <= 1:
            continue
        
        # Sort by path to ensure consistent ordering
        file_list.sort(key=lambda f: (f.folder_path, f.filename))
        
        # First file is the source
        source = file_list[0]
        
        # Skip if source is in a duplicate folder
        if source.folder_path in folder_duplicates:
            continue
        
        for dup in file_list[1:]:
            # Skip if this file is in a duplicate folder (will be copied with folder)
            if dup.folder_path in folder_duplicates:
                continue
            
            # Check if reference would save space
            # Reference format: 1 byte type + source_folder_len + source_folder + source_filename_len + source_filename
            ref_size = (1 + 
                       uleb128_size(len(source.folder_path.encode('utf-8'))) + 
                       len(source.folder_path.encode('utf-8')) +
                       uleb128_size(len(source.filename.encode('utf-8'))) + 
                       len(source.filename.encode('utf-8')))
            
            # Content format: 1 byte type + content_len + content
            content_size = 1 + uleb128_size(dup.size) + dup.size
            
            if ref_size < content_size:
                file_duplicates[(dup.folder_path, dup.filename)] = (source.folder_path, source.filename)
                print(f"  Duplicate file: {dup.folder_path}/{dup.filename} -> {source.folder_path}/{source.filename} (saves {content_size - ref_size} bytes)")
    
    return folder_duplicates, file_duplicates


# ============== SYNC FUNCTIONS ==============

# Type constants
FOLDER_TYPE_NORMAL = 0
FOLDER_TYPE_COPY = 1
FILE_TYPE_CONTENT = 0
FILE_TYPE_REFERENCE = 1


def pack_folder(folder_path: str, output_file: str, deduplicate: bool = True, max_workers: int = None) -> None:
    """
    Pack all files from folder and subfolders into a single file (sync).
    Uses parallel Brotli compression for maximum speed with quality 11.
    
    Args:
        folder_path: Path to folder to pack
        output_file: Output file path
        deduplicate: If True, detect and deduplicate identical folders and files
        max_workers: Maximum number of parallel compression workers (default: CPU count)
    """
    folder_path = folder_path.rstrip('/\\')
    parent_dir = os.path.dirname(folder_path) or '.'
    
    if max_workers is None:
        max_workers = os.cpu_count() or 4
    
    # Find duplicates if deduplication is enabled
    folder_duplicates: Dict[str, str] = {}
    file_duplicates: Dict[Tuple[str, str], Tuple[str, str]] = {}
    
    if deduplicate:
        print("Scanning for duplicates...")
        folder_duplicates, file_duplicates = find_duplicates(folder_path, parent_dir)
        if folder_duplicates or file_duplicates:
            print(f"Found {len(folder_duplicates)} duplicate folder(s), {len(file_duplicates)} duplicate file(s)")
        else:
            print("No duplicates found")
        print()
    
    folder_bytes_saved = 0
    file_bytes_saved = 0
    total_original_size = 0
    total_compressed_size = 0
    
    # First pass: collect all files that need compression
    print("Collecting files for compression...")
    files_to_compress: List[Tuple[str, str, str]] = []  # (file_path, rel_path, filename)
    folder_structure: List[Tuple[str, List[str], bool, str]] = []  # (rel_path, files, is_duplicate, source_path)
    
    for root, dirs, files in os.walk(folder_path):
        # Filter out ignored files
        files = [f for f in files if not should_ignore_file(f)]
        if not files:
            continue
        
        rel_path = os.path.relpath(root, parent_dir)
        
        if rel_path in folder_duplicates:
            source_path = folder_duplicates[rel_path]
            folder_structure.append((rel_path, list(files), True, source_path))
            for filename in files:
                file_path = os.path.join(root, filename)
                folder_bytes_saved += os.path.getsize(file_path)
        else:
            folder_structure.append((rel_path, sorted(files), False, None))
            for filename in sorted(files):
                file_key = (rel_path, filename)
                if file_key not in file_duplicates:
                    file_path = os.path.join(root, filename)
                    files_to_compress.append((file_path, rel_path, filename))
                else:
                    file_path = os.path.join(root, filename)
                    file_bytes_saved += os.path.getsize(file_path)
    
    print(f"Compressing {len(files_to_compress)} files using {max_workers} workers (Brotli quality {BROTLI_QUALITY})...")
    
    # Parallel compression of all files
    compressed_files: Dict[Tuple[str, str], bytes] = {}  # (rel_path, filename) -> compressed_data
    precompressed_files: Set[Tuple[str, str]] = set()  # Track which files were already .br
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(compress_file_task, args): args for args in files_to_compress}
        completed = 0
        
        for future in as_completed(futures):
            rel_path, filename, file_path, data, original_size, final_size, is_precompressed = future.result()
            compressed_files[(rel_path, filename)] = data
            if is_precompressed:
                precompressed_files.add((rel_path, filename))
                print(f"  [{completed + 1}/{len(files_to_compress)}] Stored as-is (.br): {rel_path}/{filename} ({original_size} bytes)")
            else:
                total_original_size += original_size
                total_compressed_size += final_size
                ratio = (final_size / original_size * 100) if original_size > 0 else 0
                print(f"  [{completed + 1}/{len(files_to_compress)}] Compressed: {rel_path}/{filename} ({original_size} -> {final_size} bytes, {ratio:.1f}%)")
            completed += 1
    
    print(f"\nWriting packed file...")
    
    # Write the packed file
    with open(output_file, 'wb') as out:
        for rel_path, files, is_duplicate, source_path in folder_structure:
            folder_name_bytes = rel_path.encode('utf-8')
            
            if is_duplicate:
                source_path_bytes = source_path.encode('utf-8')
                
                # Write copy folder entry
                out.write(bytes([FOLDER_TYPE_COPY]))
                out.write(encode_uleb128(len(folder_name_bytes)))
                out.write(folder_name_bytes)
                out.write(encode_uleb128(len(source_path_bytes)))
                out.write(source_path_bytes)
                
                print(f"  Copy folder: {rel_path} -> {source_path}")
            else:
                # Write normal folder entry
                out.write(bytes([FOLDER_TYPE_NORMAL]))
                out.write(encode_uleb128(len(folder_name_bytes)))
                out.write(folder_name_bytes)
                out.write(encode_uleb128(len(files)))
                
                for filename in files:
                    filename_bytes = filename.encode('utf-8')
                    
                    out.write(encode_uleb128(len(filename_bytes)))
                    out.write(filename_bytes)
                    
                    # Check if this file is a duplicate
                    file_key = (rel_path, filename)
                    if file_key in file_duplicates:
                        source_folder, source_filename = file_duplicates[file_key]
                        source_folder_bytes = source_folder.encode('utf-8')
                        source_filename_bytes = source_filename.encode('utf-8')
                        
                        # Write file reference
                        out.write(bytes([FILE_TYPE_REFERENCE]))
                        out.write(encode_uleb128(len(source_folder_bytes)))
                        out.write(source_folder_bytes)
                        out.write(encode_uleb128(len(source_filename_bytes)))
                        out.write(source_filename_bytes)
                        
                        print(f"    Ref: {rel_path}/{filename} -> {source_folder}/{source_filename}")
                    else:
                        # Write compressed file content
                        compressed_content = compressed_files[(rel_path, filename)]
                        
                        out.write(bytes([FILE_TYPE_CONTENT]))
                        out.write(encode_uleb128(len(compressed_content)))
                        out.write(compressed_content)
    
    total_size = os.path.getsize(output_file)
    print(f"\nPacked to {output_file} ({total_size} bytes)")
    if total_original_size > 0:
        overall_ratio = total_compressed_size / total_original_size * 100
        print(f"Compression: {total_original_size} -> {total_compressed_size} bytes ({overall_ratio:.1f}%)")
    if folder_bytes_saved > 0 or file_bytes_saved > 0:
        print(f"Deduplication saved: {folder_bytes_saved + file_bytes_saved} bytes (folders: {folder_bytes_saved}, files: {file_bytes_saved})")


def unpack_file(input_file: str, output_dir: str) -> None:
    """Unpack a packed file back to folder structure (sync). Decompresses Brotli-compressed content."""
    with open(input_file, 'rb') as f:
        data = f.read()
    
    # Track unpacked folders and files for copy references
    unpacked_folders: Dict[str, str] = {}  # rel_path -> absolute path
    unpacked_files: Dict[Tuple[str, str], str] = {}  # (folder, filename) -> absolute path
    
    offset = 0
    while offset < len(data):
        # Read folder type
        folder_type = data[offset]
        offset += 1
        
        # Read folder name
        folder_name_len, bytes_read = decode_uleb128(data, offset)
        offset += bytes_read
        folder_name = data[offset:offset + folder_name_len].decode('utf-8')
        offset += folder_name_len
        
        folder_path = os.path.join(output_dir, folder_name)
        os.makedirs(folder_path, exist_ok=True)
        unpacked_folders[folder_name] = folder_path
        
        if folder_type == FOLDER_TYPE_COPY:
            # Read source folder name
            source_name_len, bytes_read = decode_uleb128(data, offset)
            offset += bytes_read
            source_name = data[offset:offset + source_name_len].decode('utf-8')
            offset += source_name_len
            
            # Copy files from source folder
            source_path = unpacked_folders.get(source_name)
            if source_path and os.path.exists(source_path):
                for filename in os.listdir(source_path):
                    src_file = os.path.join(source_path, filename)
                    dst_file = os.path.join(folder_path, filename)
                    if os.path.isfile(src_file):
                        shutil.copy2(src_file, dst_file)
                        unpacked_files[(folder_name, filename)] = dst_file
                print(f"Copied folder: {folder_name} <- {source_name}")
            else:
                print(f"Warning: Source folder not found: {source_name}")
        else:
            # Normal folder - read files
            num_files, bytes_read = decode_uleb128(data, offset)
            offset += bytes_read
            
            print(f"Folder: {folder_name} ({num_files} files)")
            
            for _ in range(num_files):
                filename_len, bytes_read = decode_uleb128(data, offset)
                offset += bytes_read
                filename = data[offset:offset + filename_len].decode('utf-8')
                offset += filename_len
                
                file_path = os.path.join(folder_path, filename)
                
                # Read file type
                file_type = data[offset]
                offset += 1
                
                if file_type == FILE_TYPE_REFERENCE:
                    # Read source reference
                    src_folder_len, bytes_read = decode_uleb128(data, offset)
                    offset += bytes_read
                    src_folder = data[offset:offset + src_folder_len].decode('utf-8')
                    offset += src_folder_len
                    
                    src_filename_len, bytes_read = decode_uleb128(data, offset)
                    offset += bytes_read
                    src_filename = data[offset:offset + src_filename_len].decode('utf-8')
                    offset += src_filename_len
                    
                    # Copy from source file
                    src_file_path = unpacked_files.get((src_folder, src_filename))
                    if src_file_path and os.path.exists(src_file_path):
                        shutil.copy2(src_file_path, file_path)
                        unpacked_files[(folder_name, filename)] = file_path
                        print(f"  Copied: {filename} <- {src_folder}/{src_filename}")
                    else:
                        print(f"  Warning: Source file not found: {src_folder}/{src_filename}")
                else:
                    # Read content
                    content_len, bytes_read = decode_uleb128(data, offset)
                    offset += bytes_read
                    content = data[offset:offset + content_len]
                    offset += content_len
                    
                    # .br files are stored as-is (not brotli-compressed), write directly
                    if is_already_brotli(filename):
                        with open(file_path, 'wb') as f:
                            f.write(content)
                        unpacked_files[(folder_name, filename)] = file_path
                        print(f"  Unpacked: {filename} ({content_len} bytes, stored as-is)")
                    else:
                        # Decompress with Brotli
                        decompressed = decompress_brotli(content)
                        with open(file_path, 'wb') as f:
                            f.write(decompressed)
                        unpacked_files[(folder_name, filename)] = file_path
                        print(f"  Unpacked: {filename} ({content_len} -> {len(decompressed)} bytes)")
    
    print(f"\nUnpacked to {output_dir}")


def stream_unpack(chunks: Iterator[bytes]) -> Generator[Tuple[str, str, int, Generator[bytes, None, None], Tuple[str, str]], None, None]:
    """
    Stream unpack a packed file from an iterable of byte chunks (sync).
    Decompresses Brotli-compressed content.
    
    Yields tuples of: (folder_name, file_name, decompressed_size, file_chunks_generator, source_ref)
    - For normal files: (folder_name, filename, size, chunks_gen, None)
    - For file references: (folder_name, filename, -2, None, (src_folder, src_filename))
    - For folder copies: (folder_name, source_folder, -1, None, None)
    """
    buffer = bytearray()
    chunk_iter = iter(chunks)
    
    def read_bytes(n: int) -> bytes:
        nonlocal buffer
        while len(buffer) < n:
            try:
                chunk = next(chunk_iter)
                buffer.extend(chunk)
            except StopIteration:
                if len(buffer) < n:
                    raise EOFError(f"Expected {n} bytes, got {len(buffer)}")
        result = bytes(buffer[:n])
        del buffer[:n]
        return result
    
    def read_uleb128() -> int:
        result = 0
        shift = 0
        while True:
            byte_data = read_bytes(1)
            byte = byte_data[0]
            result |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7
        return result
    
    def file_chunk_generator_decompressed(compressed_size: int) -> Generator[bytes, None, None]:
        """Read compressed data, decompress, and yield as single chunk."""
        compressed_data = read_bytes(compressed_size)
        decompressed = decompress_brotli(compressed_data)
        yield decompressed
    
    try:
        while True:
            try:
                folder_type = read_bytes(1)[0]
            except EOFError:
                break
            
            folder_name_len = read_uleb128()
            folder_name = read_bytes(folder_name_len).decode('utf-8')
            
            if folder_type == FOLDER_TYPE_COPY:
                source_name_len = read_uleb128()
                source_name = read_bytes(source_name_len).decode('utf-8')
                yield (folder_name, source_name, -1, None, None)
            else:
                num_files = read_uleb128()
                
                for _ in range(num_files):
                    filename_len = read_uleb128()
                    filename = read_bytes(filename_len).decode('utf-8')
                    
                    file_type = read_bytes(1)[0]
                    
                    if file_type == FILE_TYPE_REFERENCE:
                        src_folder_len = read_uleb128()
                        src_folder = read_bytes(src_folder_len).decode('utf-8')
                        src_filename_len = read_uleb128()
                        src_filename = read_bytes(src_filename_len).decode('utf-8')
                        yield (folder_name, filename, -2, None, (src_folder, src_filename))
                    else:
                        compressed_len = read_uleb128()
                        # We can't know decompressed size without decompressing,
                        # so we pass compressed_len and decompress in the generator
                        yield (folder_name, filename, compressed_len, file_chunk_generator_decompressed(compressed_len), None)
    except EOFError:
        pass


def stream_unpack_to_disk(chunks: Iterator[bytes], output_dir: str) -> None:
    """Stream unpack directly to disk (sync)."""
    unpacked_folders: Dict[str, str] = {}
    unpacked_files: Dict[Tuple[str, str], str] = {}
    
    for folder_name, filename, file_size, file_chunks, source_ref in stream_unpack(chunks):
        folder_path = os.path.join(output_dir, folder_name)
        os.makedirs(folder_path, exist_ok=True)
        unpacked_folders[folder_name] = folder_path
        
        if file_size == -1:
            # Copy folder
            source_name = filename
            source_path = unpacked_folders.get(source_name)
            if source_path and os.path.exists(source_path):
                for fname in os.listdir(source_path):
                    src_file = os.path.join(source_path, fname)
                    dst_file = os.path.join(folder_path, fname)
                    if os.path.isfile(src_file):
                        shutil.copy2(src_file, dst_file)
                        unpacked_files[(folder_name, fname)] = dst_file
                print(f"Copied folder: {folder_name} <- {source_name}")
        elif file_size == -2:
            # File reference
            src_folder, src_filename = source_ref
            src_file_path = unpacked_files.get((src_folder, src_filename))
            file_path = os.path.join(folder_path, filename)
            if src_file_path and os.path.exists(src_file_path):
                shutil.copy2(src_file_path, file_path)
                unpacked_files[(folder_name, filename)] = file_path
                print(f"Copied: {folder_name}/{filename} <- {src_folder}/{src_filename}")
        else:
            file_path = os.path.join(folder_path, filename)
            with open(file_path, 'wb') as f:
                for chunk in file_chunks:
                    f.write(chunk)
            unpacked_files[(folder_name, filename)] = file_path
            print(f"Unpacked: {folder_name}/{filename} ({file_size} bytes)")
    
    print(f"\nStream unpacked to {output_dir}")


# ============== ASYNC FUNCTIONS ==============

async def pack_folder_async(folder_path: str, output_file: str, deduplicate: bool = True, max_workers: int = None) -> None:
    """
    Pack all files from folder and subfolders into a single file (async).
    Uses parallel Brotli compression for maximum speed with quality 11.
    
    Args:
        folder_path: Path to folder to pack
        output_file: Output file path
        deduplicate: If True, detect and deduplicate identical folders and files
        max_workers: Maximum number of parallel compression workers (default: CPU count)
    """
    folder_path = folder_path.rstrip('/\\')
    parent_dir = os.path.dirname(folder_path) or '.'
    
    if max_workers is None:
        max_workers = os.cpu_count() or 4
    
    # Find duplicates if deduplication is enabled
    folder_duplicates: Dict[str, str] = {}
    file_duplicates: Dict[Tuple[str, str], Tuple[str, str]] = {}
    
    if deduplicate:
        print("Scanning for duplicates...")
        folder_duplicates, file_duplicates = await asyncio.get_event_loop().run_in_executor(
            None, find_duplicates, folder_path, parent_dir
        )
        if folder_duplicates or file_duplicates:
            print(f"Found {len(folder_duplicates)} duplicate folder(s), {len(file_duplicates)} duplicate file(s)")
        else:
            print("No duplicates found")
        print()
    
    folder_bytes_saved = 0
    file_bytes_saved = 0
    total_original_size = 0
    total_compressed_size = 0
    
    # First pass: collect all files that need compression
    print("Collecting files for compression...")
    files_to_compress: List[Tuple[str, str, str]] = []  # (file_path, rel_path, filename)
    folder_structure: List[Tuple[str, List[str], bool, str]] = []  # (rel_path, files, is_duplicate, source_path)
    
    for root, dirs, files in os.walk(folder_path):
        # Filter out ignored files
        files = [f for f in files if not should_ignore_file(f)]
        if not files:
            continue
        
        rel_path = os.path.relpath(root, parent_dir)
        
        if rel_path in folder_duplicates:
            source_path = folder_duplicates[rel_path]
            folder_structure.append((rel_path, list(files), True, source_path))
            for filename in files:
                file_path = os.path.join(root, filename)
                folder_bytes_saved += os.path.getsize(file_path)
        else:
            folder_structure.append((rel_path, sorted(files), False, None))
            for filename in sorted(files):
                file_key = (rel_path, filename)
                if file_key not in file_duplicates:
                    file_path = os.path.join(root, filename)
                    files_to_compress.append((file_path, rel_path, filename))
                else:
                    file_path = os.path.join(root, filename)
                    file_bytes_saved += os.path.getsize(file_path)
    
    print(f"Compressing {len(files_to_compress)} files using {max_workers} workers (Brotli quality {BROTLI_QUALITY})...")
    
    # Parallel compression using ProcessPoolExecutor with asyncio
    compressed_files: Dict[Tuple[str, str], bytes] = {}  # (rel_path, filename) -> compressed_data
    precompressed_files: Set[Tuple[str, str]] = set()  # Track which files were already .br
    loop = asyncio.get_event_loop()
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_args = {
            loop.run_in_executor(executor, compress_file_task, args): args
            for args in files_to_compress
        }
        
        completed = 0
        for coro in asyncio.as_completed(future_to_args.keys()):
            result = await coro
            rel_path, filename, file_path, data, original_size, final_size, is_precompressed = result
            compressed_files[(rel_path, filename)] = data
            if is_precompressed:
                precompressed_files.add((rel_path, filename))
                print(f"  [{completed + 1}/{len(files_to_compress)}] Stored as-is (.br): {rel_path}/{filename} ({original_size} bytes)")
            else:
                total_original_size += original_size
                total_compressed_size += final_size
                ratio = (final_size / original_size * 100) if original_size > 0 else 0
                print(f"  [{completed + 1}/{len(files_to_compress)}] Compressed: {rel_path}/{filename} ({original_size} -> {final_size} bytes, {ratio:.1f}%)")
            completed += 1
    
    print(f"\nWriting packed file...")
    
    # Write the packed file
    async with aiofiles.open(output_file, 'wb') as out:
        for rel_path, files, is_duplicate, source_path in folder_structure:
            folder_name_bytes = rel_path.encode('utf-8')
            
            if is_duplicate:
                source_path_bytes = source_path.encode('utf-8')
                
                await out.write(bytes([FOLDER_TYPE_COPY]))
                await out.write(encode_uleb128(len(folder_name_bytes)))
                await out.write(folder_name_bytes)
                await out.write(encode_uleb128(len(source_path_bytes)))
                await out.write(source_path_bytes)
                
                print(f"  Copy folder: {rel_path} -> {source_path}")
            else:
                await out.write(bytes([FOLDER_TYPE_NORMAL]))
                await out.write(encode_uleb128(len(folder_name_bytes)))
                await out.write(folder_name_bytes)
                await out.write(encode_uleb128(len(files)))
                
                for filename in files:
                    filename_bytes = filename.encode('utf-8')
                    
                    await out.write(encode_uleb128(len(filename_bytes)))
                    await out.write(filename_bytes)
                    
                    file_key = (rel_path, filename)
                    if file_key in file_duplicates:
                        source_folder, source_filename = file_duplicates[file_key]
                        source_folder_bytes = source_folder.encode('utf-8')
                        source_filename_bytes = source_filename.encode('utf-8')
                        
                        await out.write(bytes([FILE_TYPE_REFERENCE]))
                        await out.write(encode_uleb128(len(source_folder_bytes)))
                        await out.write(source_folder_bytes)
                        await out.write(encode_uleb128(len(source_filename_bytes)))
                        await out.write(source_filename_bytes)
                        
                        print(f"    Ref: {rel_path}/{filename} -> {source_folder}/{source_filename}")
                    else:
                        # Write compressed file content
                        compressed_content = compressed_files[(rel_path, filename)]
                        
                        await out.write(bytes([FILE_TYPE_CONTENT]))
                        await out.write(encode_uleb128(len(compressed_content)))
                        await out.write(compressed_content)
    
    total_size = os.path.getsize(output_file)
    print(f"\nPacked to {output_file} ({total_size} bytes)")
    if total_original_size > 0:
        overall_ratio = total_compressed_size / total_original_size * 100
        print(f"Compression: {total_original_size} -> {total_compressed_size} bytes ({overall_ratio:.1f}%)")
    if folder_bytes_saved > 0 or file_bytes_saved > 0:
        print(f"Deduplication saved: {folder_bytes_saved + file_bytes_saved} bytes (folders: {folder_bytes_saved}, files: {file_bytes_saved})")


async def unpack_file_async(input_file: str, output_dir: str) -> None:
    """Unpack a packed file back to folder structure (async). Decompresses Brotli-compressed content."""
    async with aiofiles.open(input_file, 'rb') as f:
        data = await f.read()
    
    unpacked_folders: Dict[str, str] = {}
    unpacked_files: Dict[Tuple[str, str], str] = {}
    
    offset = 0
    while offset < len(data):
        folder_type = data[offset]
        offset += 1
        
        folder_name_len, bytes_read = decode_uleb128(data, offset)
        offset += bytes_read
        folder_name = data[offset:offset + folder_name_len].decode('utf-8')
        offset += folder_name_len
        
        folder_path = os.path.join(output_dir, folder_name)
        os.makedirs(folder_path, exist_ok=True)
        unpacked_folders[folder_name] = folder_path
        
        if folder_type == FOLDER_TYPE_COPY:
            source_name_len, bytes_read = decode_uleb128(data, offset)
            offset += bytes_read
            source_name = data[offset:offset + source_name_len].decode('utf-8')
            offset += source_name_len
            
            source_path = unpacked_folders.get(source_name)
            if source_path and os.path.exists(source_path):
                for filename in os.listdir(source_path):
                    src_file = os.path.join(source_path, filename)
                    dst_file = os.path.join(folder_path, filename)
                    if os.path.isfile(src_file):
                        shutil.copy2(src_file, dst_file)
                        unpacked_files[(folder_name, filename)] = dst_file
                print(f"Copied folder: {folder_name} <- {source_name}")
        else:
            num_files, bytes_read = decode_uleb128(data, offset)
            offset += bytes_read
            
            print(f"Folder: {folder_name} ({num_files} files)")
            
            for _ in range(num_files):
                filename_len, bytes_read = decode_uleb128(data, offset)
                offset += bytes_read
                filename = data[offset:offset + filename_len].decode('utf-8')
                offset += filename_len
                
                file_path = os.path.join(folder_path, filename)
                
                file_type = data[offset]
                offset += 1
                
                if file_type == FILE_TYPE_REFERENCE:
                    src_folder_len, bytes_read = decode_uleb128(data, offset)
                    offset += bytes_read
                    src_folder = data[offset:offset + src_folder_len].decode('utf-8')
                    offset += src_folder_len
                    
                    src_filename_len, bytes_read = decode_uleb128(data, offset)
                    offset += bytes_read
                    src_filename = data[offset:offset + src_filename_len].decode('utf-8')
                    offset += src_filename_len
                    
                    src_file_path = unpacked_files.get((src_folder, src_filename))
                    if src_file_path and os.path.exists(src_file_path):
                        shutil.copy2(src_file_path, file_path)
                        unpacked_files[(folder_name, filename)] = file_path
                        print(f"  Copied: {filename} <- {src_folder}/{src_filename}")
                else:
                    # Read content
                    content_len, bytes_read = decode_uleb128(data, offset)
                    offset += bytes_read
                    content = data[offset:offset + content_len]
                    offset += content_len
                    
                    # .br files are stored as-is (not brotli-compressed), write directly
                    if is_already_brotli(filename):
                        async with aiofiles.open(file_path, 'wb') as f:
                            await f.write(content)
                        unpacked_files[(folder_name, filename)] = file_path
                        print(f"  Unpacked: {filename} ({content_len} bytes, stored as-is)")
                    else:
                        # Decompress with Brotli
                        decompressed = decompress_brotli(content)
                        async with aiofiles.open(file_path, 'wb') as f:
                            await f.write(decompressed)
                        unpacked_files[(folder_name, filename)] = file_path
                        print(f"  Unpacked: {filename} ({content_len} -> {len(decompressed)} bytes)")
    
    print(f"\nUnpacked to {output_dir}")


async def stream_unpack_async(
    chunks: AsyncIterator[bytes]
) -> AsyncGenerator[Tuple[str, int, int, str, int, AsyncGenerator[bytes, None], Tuple[str, str]], None]:
    """
    Stream unpack a packed file from an async iterable of byte chunks.
    Decompresses Brotli-compressed content.
    
    Yields tuples of:
    - For normal files: (folder_name, num_files, file_idx, filename, decompressed_size, chunks_gen, None)
    - For file references: (folder_name, num_files, file_idx, filename, -2, None, (src_folder, src_filename))
    - For folder copies: (folder_name, 0, -1, source_folder, -1, None, None)
    """
    buffer = bytearray()
    chunk_aiter = chunks.__aiter__()
    
    async def read_bytes(n: int) -> bytes:
        nonlocal buffer
        while len(buffer) < n:
            try:
                chunk = await chunk_aiter.__anext__()
                buffer.extend(chunk)
            except StopAsyncIteration:
                if len(buffer) < n:
                    raise EOFError(f"Expected {n} bytes, got {len(buffer)}")
        result = bytes(buffer[:n])
        del buffer[:n]
        return result
    
    async def read_uleb128() -> int:
        result = 0
        shift = 0
        while True:
            byte_data = await read_bytes(1)
            byte = byte_data[0]
            result |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7
        return result
    
    async def file_chunk_generator_decompressed(compressed_size: int) -> AsyncGenerator[bytes, None]:
        """Read compressed data, decompress with Brotli, and yield as single chunk."""
        compressed_data = await read_bytes(compressed_size)
        decompressed = decompress_brotli(compressed_data)
        yield decompressed
    
    try:
        while True:
            try:
                folder_type = (await read_bytes(1))[0]
            except EOFError:
                break
            
            folder_name_len = await read_uleb128()
            folder_name_bytes = await read_bytes(folder_name_len)
            folder_name = folder_name_bytes.decode('utf-8')
            
            if folder_type == FOLDER_TYPE_COPY:
                source_name_len = await read_uleb128()
                source_name_bytes = await read_bytes(source_name_len)
                source_name = source_name_bytes.decode('utf-8')
                yield (folder_name, 0, -1, source_name, -1, None, None)
            else:
                num_files = await read_uleb128()
                
                for file_idx in range(num_files):
                    filename_len = await read_uleb128()
                    filename_bytes = await read_bytes(filename_len)
                    filename = filename_bytes.decode('utf-8')
                    
                    file_type = (await read_bytes(1))[0]
                    
                    if file_type == FILE_TYPE_REFERENCE:
                        src_folder_len = await read_uleb128()
                        src_folder_bytes = await read_bytes(src_folder_len)
                        src_folder = src_folder_bytes.decode('utf-8')
                        src_filename_len = await read_uleb128()
                        src_filename_bytes = await read_bytes(src_filename_len)
                        src_filename = src_filename_bytes.decode('utf-8')
                        yield (folder_name, num_files, file_idx, filename, -2, None, (src_folder, src_filename))
                    else:
                        compressed_len = await read_uleb128()
                        # We compress and decompress in the generator
                        yield (folder_name, num_files, file_idx, filename, compressed_len, file_chunk_generator_decompressed(compressed_len), None)
    except EOFError:
        pass


async def stream_unpack_to_disk_async(chunks: AsyncIterator[bytes], output_dir: str) -> None:
    """Stream unpack directly to disk (async)."""
    unpacked_folders: Dict[str, str] = {}
    unpacked_files: Dict[Tuple[str, str], str] = {}
    
    async for folder_name, num_files, file_idx, filename, file_size, file_chunks, source_ref in stream_unpack_async(chunks):
        folder_path = os.path.join(output_dir, folder_name)
        os.makedirs(folder_path, exist_ok=True)
        unpacked_folders[folder_name] = folder_path
        
        if file_idx == -1:
            # Copy folder
            source_name = filename
            source_path = unpacked_folders.get(source_name)
            if source_path and os.path.exists(source_path):
                for fname in os.listdir(source_path):
                    src_file = os.path.join(source_path, fname)
                    dst_file = os.path.join(folder_path, fname)
                    if os.path.isfile(src_file):
                        shutil.copy2(src_file, dst_file)
                        unpacked_files[(folder_name, fname)] = dst_file
                print(f"Copied folder: {folder_name} <- {source_name}")
        elif file_size == -2:
            # File reference
            src_folder, src_filename = source_ref
            src_file_path = unpacked_files.get((src_folder, src_filename))
            file_path = os.path.join(folder_path, filename)
            if src_file_path and os.path.exists(src_file_path):
                shutil.copy2(src_file_path, file_path)
                unpacked_files[(folder_name, filename)] = file_path
                print(f"Copied: {folder_name}/{filename} <- {src_folder}/{src_filename}")
        else:
            file_path = os.path.join(folder_path, filename)
            async with aiofiles.open(file_path, 'wb') as f:
                async for chunk in file_chunks:
                    await f.write(chunk)
            unpacked_files[(folder_name, filename)] = file_path
            print(f"Unpacked: {folder_name}/{filename} ({file_idx+1}/{num_files}, {file_size} bytes)")
    
    print(f"\nStream unpacked to {output_dir}")


# ============== PACKED ARCHIVE CLASS ==============

@dataclass
class FileEntry:
    """Information about a file in the archive."""
    folder: str
    filename: str
    file_type: int  # FILE_TYPE_CONTENT or FILE_TYPE_REFERENCE
    data_offset: int  # Position of file content/reference data in archive
    compressed_size: int  # Size of compressed data (0 for references)
    # For references:
    ref_folder: Optional[str] = None
    ref_filename: Optional[str] = None


class PackedArchiveFile:
    """
    A file-like object for reading a single file from a PackedArchive.
    Supports read(), readline(), and async iteration.
    """
    
    def __init__(self, data: bytes, keep_brotli: bool = False):
        """
        Initialize with the file data.
        
        Args:
            data: The file data (compressed or decompressed based on keep_brotli)
            keep_brotli: If True, data is still brotli-compressed
        """
        self._data = data
        self._keep_brotli = keep_brotli
        self._position = 0
    
    @property
    def data(self) -> bytes:
        """Get all file data."""
        return self._data
    
    def read(self, size: int = -1) -> bytes:
        """Read up to size bytes. If size is -1, read all remaining data."""
        if size == -1:
            result = self._data[self._position:]
            self._position = len(self._data)
        else:
            result = self._data[self._position:self._position + size]
            self._position += len(result)
        return result
    
    def readline(self, size: int = -1) -> bytes:
        """Read a line (up to newline or size bytes)."""
        if self._position >= len(self._data):
            return b''
        
        # Find newline
        newline_pos = self._data.find(b'\n', self._position)
        if newline_pos == -1:
            # No newline, read to end
            end = len(self._data)
        else:
            end = newline_pos + 1
        
        if size != -1:
            end = min(end, self._position + size)
        
        result = self._data[self._position:end]
        self._position = end
        return result
    
    def readlines(self) -> List[bytes]:
        """Read all remaining lines."""
        lines = []
        while True:
            line = self.readline()
            if not line:
                break
            lines.append(line)
        return lines
    
    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to position. whence: 0=start, 1=current, 2=end."""
        if whence == 0:
            self._position = offset
        elif whence == 1:
            self._position += offset
        elif whence == 2:
            self._position = len(self._data) + offset
        self._position = max(0, min(self._position, len(self._data)))
        return self._position
    
    def tell(self) -> int:
        """Return current position."""
        return self._position
    
    def __len__(self) -> int:
        """Return total size."""
        return len(self._data)
    
    def __iter__(self):
        """Iterate over lines."""
        return self
    
    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line


class PackedArchive:
    """
    Async class to read files from a packed archive as if it were a folder.
    
    Usage:
        archive = PackedArchive('packed.bin')
        await archive.init()
        
        async with archive.open('vcsky/fetched/model.txd') as f:
            data = f.read()  # Read all
            # or
            chunk = f.read(1024)  # Read 1024 bytes
            # or
            for line in f:
                print(line)
        
        # With keep_brotli=True to get compressed data
        async with archive.open('vcsky/fetched/model.txd', keep_brotli=True) as f:
            compressed_data = f.read()
        
        # List files
        files = archive.list_files()
        folders = archive.list_folders()
    """
    
    def __init__(self, archive_path: str):
        """
        Initialize the archive reader.
        
        Args:
            archive_path: Path to the .bin archive file
        """
        self._path = archive_path
        self._file: Optional[BinaryIO] = None
        self._entries: Dict[str, FileEntry] = {}  # full_path -> FileEntry
        self._folders: Dict[str, List[str]] = {}  # folder_path -> list of filenames
        self._folder_copies: Dict[str, str] = {}  # copy_folder -> source_folder
        self._initialized = False
    
    async def init(self) -> None:
        """
        Initialize the archive by reading the index.
        Must be called before using open().
        """
        if self._initialized:
            return
        
        async with aiofiles.open(self._path, 'rb') as f:
            data = await f.read()
        
        self._parse_index(data)
        self._initialized = True
    
    def _parse_index(self, data: bytes) -> None:
        """Parse the archive to build the file index."""
        offset = 0
        
        while offset < len(data):
            # Read folder type
            folder_type = data[offset]
            offset += 1
            
            # Read folder name
            folder_name_len, bytes_read = decode_uleb128(data, offset)
            offset += bytes_read
            folder_name = data[offset:offset + folder_name_len].decode('utf-8')
            offset += folder_name_len
            
            if folder_type == FOLDER_TYPE_COPY:
                # Read source folder name
                source_name_len, bytes_read = decode_uleb128(data, offset)
                offset += bytes_read
                source_name = data[offset:offset + source_name_len].decode('utf-8')
                offset += source_name_len
                
                self._folder_copies[folder_name] = source_name
                # Copy entries from source folder
                if source_name in self._folders:
                    self._folders[folder_name] = list(self._folders[source_name])
                    for filename in self._folders[source_name]:
                        src_path = f"{source_name}/{filename}"
                        dst_path = f"{folder_name}/{filename}"
                        if src_path in self._entries:
                            src_entry = self._entries[src_path]
                            self._entries[dst_path] = FileEntry(
                                folder=folder_name,
                                filename=filename,
                                file_type=src_entry.file_type,
                                data_offset=src_entry.data_offset,
                                compressed_size=src_entry.compressed_size,
                                ref_folder=src_entry.ref_folder,
                                ref_filename=src_entry.ref_filename
                            )
            else:
                # Normal folder
                num_files, bytes_read = decode_uleb128(data, offset)
                offset += bytes_read
                
                self._folders[folder_name] = []
                
                for _ in range(num_files):
                    filename_len, bytes_read = decode_uleb128(data, offset)
                    offset += bytes_read
                    filename = data[offset:offset + filename_len].decode('utf-8')
                    offset += filename_len
                    
                    self._folders[folder_name].append(filename)
                    
                    file_type = data[offset]
                    offset += 1
                    
                    full_path = f"{folder_name}/{filename}"
                    
                    if file_type == FILE_TYPE_REFERENCE:
                        # Read source reference
                        src_folder_len, bytes_read = decode_uleb128(data, offset)
                        offset += bytes_read
                        src_folder = data[offset:offset + src_folder_len].decode('utf-8')
                        offset += src_folder_len
                        
                        src_filename_len, bytes_read = decode_uleb128(data, offset)
                        offset += bytes_read
                        src_filename = data[offset:offset + src_filename_len].decode('utf-8')
                        offset += src_filename_len
                        
                        self._entries[full_path] = FileEntry(
                            folder=folder_name,
                            filename=filename,
                            file_type=FILE_TYPE_REFERENCE,
                            data_offset=0,
                            compressed_size=0,
                            ref_folder=src_folder,
                            ref_filename=src_filename
                        )
                    else:
                        # Read content length and record position
                        compressed_len, bytes_read = decode_uleb128(data, offset)
                        offset += bytes_read
                        
                        self._entries[full_path] = FileEntry(
                            folder=folder_name,
                            filename=filename,
                            file_type=FILE_TYPE_CONTENT,
                            data_offset=offset,
                            compressed_size=compressed_len
                        )
                        
                        # Skip content
                        offset += compressed_len
    
    def list_folders(self) -> List[str]:
        """List all folders in the archive."""
        if not self._initialized:
            raise RuntimeError("Archive not initialized. Call init() first.")
        return list(self._folders.keys())
    
    def list_files(self, folder: Optional[str] = None) -> List[str]:
        """
        List files in the archive.
        
        Args:
            folder: If provided, list files only in this folder.
                   If None, list all files with full paths.
        """
        if not self._initialized:
            raise RuntimeError("Archive not initialized. Call init() first.")
        
        if folder is not None:
            return list(self._folders.get(folder, []))
        else:
            return list(self._entries.keys())
    
    def exists(self, path: str) -> bool:
        """Check if a file exists in the archive."""
        if not self._initialized:
            raise RuntimeError("Archive not initialized. Call init() first.")
        return path in self._entries
    
    @asynccontextmanager
    async def open(self, path: str, keep_brotli: bool = False):
        """
        Open a file from the archive.
        
        Args:
            path: Path to the file, e.g., 'vcsky/fetched/model.txd'
            keep_brotli: If False (default), decompress the data.
                        If True, return the raw data (for brotli passthrough).
        
        Yields:
            PackedArchiveFile object for reading the file data.
        
        Raises:
            FileNotFoundError: If the file doesn't exist in the archive.
        
        Note:
            Files with .br extension are stored without compression in the archive,
            so they are returned as-is regardless of keep_brotli setting.
        """
        if not self._initialized:
            raise RuntimeError("Archive not initialized. Call init() first.")
        
        if path not in self._entries:
            raise FileNotFoundError(f"File not found in archive: {path}")
        
        entry = self._entries[path]
        original_filename = entry.filename
        
        # Resolve references
        while entry.file_type == FILE_TYPE_REFERENCE:
            ref_path = f"{entry.ref_folder}/{entry.ref_filename}"
            if ref_path not in self._entries:
                raise FileNotFoundError(f"Reference target not found: {ref_path}")
            entry = self._entries[ref_path]
        
        # Read the data
        async with aiofiles.open(self._path, 'rb') as f:
            await f.seek(entry.data_offset)
            data = await f.read(entry.compressed_size)
        
        # .br files are stored as-is (not brotli-compressed in archive)
        # So we return them directly without decompression
        if is_already_brotli(original_filename):
            yield PackedArchiveFile(data, keep_brotli=False)
        elif keep_brotli:
            # Return raw brotli-compressed data from archive
            yield PackedArchiveFile(data, keep_brotli=True)
        else:
            # Decompress brotli data
            decompressed_data = decompress_brotli(data)
            yield PackedArchiveFile(decompressed_data, keep_brotli=False)
    
    async def read_file(self, path: str, keep_brotli: bool = False) -> bytes:
        """
        Read and return the entire file content.
        
        Args:
            path: Path to the file
            keep_brotli: If False, decompress. If True, return compressed.
        
        Returns:
            File content as bytes.
        """
        async with self.open(path, keep_brotli=keep_brotli) as f:
            return f.read()


# ============== ADD FOLDER FUNCTION ==============

def add_folder(archive_path: str, folder_path: str, max_workers: int = None) -> None:
    """
    Add a folder to an existing archive by appending to the end.
    
    Note: This appends to the archive without deduplication against existing content.
    The new folder will be added as a top-level folder in the archive.
    
    Args:
        archive_path: Path to existing .bin archive
        folder_path: Path to folder to add
        max_workers: Number of parallel compression workers
    """
    folder_path = folder_path.rstrip('/\\')
    parent_dir = os.path.dirname(folder_path) or '.'
    
    if max_workers is None:
        max_workers = os.cpu_count() or 4
    
    if not os.path.isfile(archive_path):
        raise FileNotFoundError(f"Archive not found: {archive_path}")
    
    if not os.path.isdir(folder_path):
        raise NotADirectoryError(f"Not a directory: {folder_path}")
    
    # Collect files to compress
    print(f"Adding {folder_path} to {archive_path}")
    print("Collecting files for compression...")
    
    files_to_compress: List[Tuple[str, str, str]] = []
    folder_structure: List[Tuple[str, List[str]]] = []
    
    for root, dirs, files in os.walk(folder_path):
        # Filter out ignored files
        files = [f for f in files if not should_ignore_file(f)]
        if not files:
            continue
        
        rel_path = os.path.relpath(root, parent_dir)
        folder_structure.append((rel_path, sorted(files)))
        
        for filename in sorted(files):
            file_path = os.path.join(root, filename)
            files_to_compress.append((file_path, rel_path, filename))
    
    print(f"Compressing {len(files_to_compress)} files using {max_workers} workers...")
    
    # Parallel compression
    compressed_files: Dict[Tuple[str, str], bytes] = {}
    total_original = 0
    total_compressed = 0
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(compress_file_task, args): args for args in files_to_compress}
        completed = 0
        
        for future in as_completed(futures):
            rel_path, filename, file_path, data, original_size, final_size, is_precompressed = future.result()
            compressed_files[(rel_path, filename)] = data
            if is_precompressed:
                print(f"  [{completed + 1}/{len(files_to_compress)}] Stored as-is (.br): {rel_path}/{filename} ({original_size} bytes)")
            else:
                total_original += original_size
                total_compressed += final_size
                ratio = (final_size / original_size * 100) if original_size > 0 else 0
                print(f"  [{completed + 1}/{len(files_to_compress)}] Compressed: {rel_path}/{filename} ({original_size} -> {final_size} bytes, {ratio:.1f}%)")
            completed += 1
    
    print(f"\nAppending to archive...")
    
    # Append to archive
    with open(archive_path, 'ab') as out:
        for rel_path, files in folder_structure:
            folder_name_bytes = rel_path.encode('utf-8')
            
            out.write(bytes([FOLDER_TYPE_NORMAL]))
            out.write(encode_uleb128(len(folder_name_bytes)))
            out.write(folder_name_bytes)
            out.write(encode_uleb128(len(files)))
            
            for filename in files:
                filename_bytes = filename.encode('utf-8')
                
                out.write(encode_uleb128(len(filename_bytes)))
                out.write(filename_bytes)
                
                compressed_content = compressed_files[(rel_path, filename)]
                
                out.write(bytes([FILE_TYPE_CONTENT]))
                out.write(encode_uleb128(len(compressed_content)))
                out.write(compressed_content)
    
    new_size = os.path.getsize(archive_path)
    print(f"\nAdded to {archive_path} (total size: {new_size} bytes)")
    if total_original > 0:
        ratio = total_compressed / total_original * 100
        print(f"Compression: {total_original} -> {total_compressed} bytes ({ratio:.1f}%)")


async def add_folder_async(archive_path: str, folder_path: str, max_workers: int = None) -> None:
    """
    Add a folder to an existing archive (async version).
    """
    folder_path = folder_path.rstrip('/\\')
    parent_dir = os.path.dirname(folder_path) or '.'
    
    if max_workers is None:
        max_workers = os.cpu_count() or 4
    
    if not os.path.isfile(archive_path):
        raise FileNotFoundError(f"Archive not found: {archive_path}")
    
    if not os.path.isdir(folder_path):
        raise NotADirectoryError(f"Not a directory: {folder_path}")
    
    print(f"Adding {folder_path} to {archive_path}")
    print("Collecting files for compression...")
    
    files_to_compress: List[Tuple[str, str, str]] = []
    folder_structure: List[Tuple[str, List[str]]] = []
    
    for root, dirs, files in os.walk(folder_path):
        # Filter out ignored files
        files = [f for f in files if not should_ignore_file(f)]
        if not files:
            continue
        
        rel_path = os.path.relpath(root, parent_dir)
        folder_structure.append((rel_path, sorted(files)))
        
        for filename in sorted(files):
            file_path = os.path.join(root, filename)
            files_to_compress.append((file_path, rel_path, filename))
    
    print(f"Compressing {len(files_to_compress)} files using {max_workers} workers...")
    
    compressed_files: Dict[Tuple[str, str], bytes] = {}
    total_original = 0
    total_compressed = 0
    loop = asyncio.get_event_loop()
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_args = {
            loop.run_in_executor(executor, compress_file_task, args): args
            for args in files_to_compress
        }
        
        completed = 0
        for coro in asyncio.as_completed(future_to_args.keys()):
            result = await coro
            rel_path, filename, file_path, data, original_size, final_size, is_precompressed = result
            compressed_files[(rel_path, filename)] = data
            if is_precompressed:
                print(f"  [{completed + 1}/{len(files_to_compress)}] Stored as-is (.br): {rel_path}/{filename} ({original_size} bytes)")
            else:
                total_original += original_size
                total_compressed += final_size
                ratio = (final_size / original_size * 100) if original_size > 0 else 0
                print(f"  [{completed + 1}/{len(files_to_compress)}] Compressed: {rel_path}/{filename} ({original_size} -> {final_size} bytes, {ratio:.1f}%)")
            completed += 1
    
    print(f"\nAppending to archive...")
    
    async with aiofiles.open(archive_path, 'ab') as out:
        for rel_path, files in folder_structure:
            folder_name_bytes = rel_path.encode('utf-8')
            
            await out.write(bytes([FOLDER_TYPE_NORMAL]))
            await out.write(encode_uleb128(len(folder_name_bytes)))
            await out.write(folder_name_bytes)
            await out.write(encode_uleb128(len(files)))
            
            for filename in files:
                filename_bytes = filename.encode('utf-8')
                
                await out.write(encode_uleb128(len(filename_bytes)))
                await out.write(filename_bytes)
                
                compressed_content = compressed_files[(rel_path, filename)]
                
                await out.write(bytes([FILE_TYPE_CONTENT]))
                await out.write(encode_uleb128(len(compressed_content)))
                await out.write(compressed_content)
    
    new_size = os.path.getsize(archive_path)
    print(f"\nAdded to {archive_path} (total size: {new_size} bytes)")
    if total_original > 0:
        ratio = total_compressed / total_original * 100
        print(f"Compression: {total_original} -> {total_compressed} bytes ({ratio:.1f}%)")


# ============== CLI ==============

def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  Pack:   python packer_brotli.py pack <folder_path> <output_file> [--no-dedup] [--workers N]")
        print("  Unpack: python packer_brotli.py unpack <input_file> <output_dir>")
        print("  Add:    python packer_brotli.py add <archive_file> <folder_path> [--workers N]")
        print()
        print("Options:")
        print("  --no-dedup    Disable folder and file deduplication during packing")
        print("  --workers N   Number of parallel compression workers (default: CPU count)")
        print()
        print("Example:")
        print("  python packer_brotli.py pack vcsky packed.bin")
        print("  python packer_brotli.py pack vcsky packed.bin --workers 8")
        print("  python packer_brotli.py unpack packed.bin unpacked/")
        print("  python packer_brotli.py add packed.bin vcbr  # Add vcbr folder to existing archive")
        print()
        print("Features:")
        print("  - Brotli compression with quality 11 (maximum compression)")
        print("  - Parallel file compression for maximum speed")
        print("  - Folder and file deduplication to reduce archive size")
        print("  - PackedArchive class for reading files directly from archive")
        print()
        print("Deduplication: Identical folders and files are detected by comparing")
        print("content hashes. Duplicates reference the original instead of storing")
        print("content twice, reducing archive size. File references are only created")
        print("when the reference path is shorter than storing the file content.")
        print()
        print("PackedArchive Usage:")
        print("  archive = PackedArchive('packed.bin')")
        print("  await archive.init()")
        print("  async with archive.open('vcsky/file.txd') as f:")
        print("      data = f.read()")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == 'pack':
        if len(sys.argv) < 4:
            print("Usage: python packer_brotli.py pack <folder_path> <output_file> [--no-dedup] [--workers N]")
            sys.exit(1)
        folder_path = sys.argv[2]
        output_file = sys.argv[3]
        deduplicate = '--no-dedup' not in sys.argv
        
        # Parse --workers option
        max_workers = None
        if '--workers' in sys.argv:
            try:
                workers_idx = sys.argv.index('--workers')
                max_workers = int(sys.argv[workers_idx + 1])
            except (IndexError, ValueError):
                print("Error: --workers requires a numeric argument")
                sys.exit(1)
        
        if not os.path.isdir(folder_path):
            print(f"Error: {folder_path} is not a directory")
            sys.exit(1)
        
        pack_folder(folder_path, output_file, deduplicate=deduplicate, max_workers=max_workers)
    
    elif command == 'unpack':
        if len(sys.argv) < 4:
            print("Usage: python packer_brotli.py unpack <input_file> <output_dir>")
            sys.exit(1)
        input_file = sys.argv[2]
        output_dir = sys.argv[3]
        
        if not os.path.isfile(input_file):
            print(f"Error: {input_file} is not a file")
            sys.exit(1)
        
        unpack_file(input_file, output_dir)
    
    elif command == 'add':
        if len(sys.argv) < 4:
            print("Usage: python packer_brotli.py add <archive_file> <folder_path> [--workers N]")
            sys.exit(1)
        archive_path = sys.argv[2]
        folder_path = sys.argv[3]
        
        # Parse --workers option
        max_workers = None
        if '--workers' in sys.argv:
            try:
                workers_idx = sys.argv.index('--workers')
                max_workers = int(sys.argv[workers_idx + 1])
            except (IndexError, ValueError):
                print("Error: --workers requires a numeric argument")
                sys.exit(1)
        
        if not os.path.isfile(archive_path):
            print(f"Error: {archive_path} is not a file")
            sys.exit(1)
        
        if not os.path.isdir(folder_path):
            print(f"Error: {folder_path} is not a directory")
            sys.exit(1)
        
        add_folder(archive_path, folder_path, max_workers=max_workers)
    
    else:
        print(f"Unknown command: {command}")
        print("Use 'pack', 'unpack', or 'add'")
        sys.exit(1)


if __name__ == '__main__':
    main()
