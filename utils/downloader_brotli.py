#!/usr/bin/env python3
"""
Downloader for packed files with brotli-compressed content.
Downloads and unpacks directly to disk without saving intermediate .bin file.
Shows detailed progress and statistics.
Uses separate coroutines for downloading and unpacking with asyncio.Queue.

This version uses packer_brotli format where individual files are brotli-compressed.
The stream_unpack_async from packer_brotli automatically decompresses each file.
"""

import os
import sys
import asyncio
import shutil
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import httpx
import aiofiles

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.packer_brotli import stream_unpack_async


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"


def format_time(seconds: float) -> str:
    """Format seconds to human readable time."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.1f}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def get_terminal_width() -> int:
    """Get terminal width, default to 80 if not available."""
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


@dataclass
class UnpackStats:
    """Statistics for unpacking progress."""
    start_time: float = field(default_factory=time.time)
    
    # Folder tracking
    current_folder: str = ""
    files_in_current_folder: int = 0
    unpacked_in_current_folder: int = 0
    
    # Global tracking
    total_folders: int = 0
    total_files: int = 0
    total_bytes: int = 0  # Decompressed file content bytes
    total_compressed_bytes: int = 0  # Compressed file bytes in archive
    copied_folders: int = 0
    copied_files: int = 0  # Individual file copies (references)
    
    # Download tracking
    downloaded_bytes: int = 0
    download_complete: bool = False
    
    # Per-folder stats
    folder_stats: Dict[str, Dict] = field(default_factory=dict)
    folder_file_counts: Dict[str, int] = field(default_factory=dict)
    
    # Track unpacked folders and files for copy references
    unpacked_folders: Dict[str, str] = field(default_factory=dict)
    unpacked_files: Dict[Tuple[str, str], str] = field(default_factory=dict)
    
    # Last printed line length for proper clearing
    last_line_length: int = 0
    
    def start_folder(self, folder_name: str, num_files: int):
        """Start tracking a new folder."""
        self.current_folder = folder_name
        self.files_in_current_folder = num_files
        self.unpacked_in_current_folder = 0
        self.total_folders += 1
        self.folder_stats[folder_name] = {
            'total_files': num_files,
            'unpacked_files': 0,
            'total_bytes': 0,
            'compressed_bytes': 0
        }
        self.folder_file_counts[folder_name] = num_files
    
    def file_unpacked(self, filename: str, compressed_size: int, decompressed_size: int):
        """Record a file being unpacked."""
        self.unpacked_in_current_folder += 1
        self.total_files += 1
        self.total_bytes += decompressed_size
        self.total_compressed_bytes += compressed_size
        
        if self.current_folder in self.folder_stats:
            self.folder_stats[self.current_folder]['unpacked_files'] += 1
            self.folder_stats[self.current_folder]['total_bytes'] += decompressed_size
            self.folder_stats[self.current_folder]['compressed_bytes'] += compressed_size
    
    def file_copied(self, filename: str, file_size: int):
        """Record a file being copied from a reference."""
        self.unpacked_in_current_folder += 1
        self.total_files += 1
        self.total_bytes += file_size
        self.copied_files += 1
        
        if self.current_folder in self.folder_stats:
            self.folder_stats[self.current_folder]['unpacked_files'] += 1
            self.folder_stats[self.current_folder]['total_bytes'] += file_size
    
    def add_downloaded(self, size: int):
        """Add downloaded bytes."""
        self.downloaded_bytes += size
    
    def get_elapsed(self) -> float:
        """Get elapsed time in seconds."""
        return time.time() - self.start_time
    
    def clear_line(self):
        """Clear the current line properly."""
        term_width = get_terminal_width()
        print('\r' + ' ' * min(self.last_line_length, term_width - 1) + '\r', end='', flush=True)
    
    def print_progress(self, filename: str, compressed_size: int, decompressed_size: int, is_copy: bool = False):
        """Print current progress on a single line."""
        elapsed = self.get_elapsed()
        speed = self.total_bytes / elapsed if elapsed > 0 else 0
        
        # Calculate progress percentage
        if self.files_in_current_folder > 0:
            progress_pct = self.unpacked_in_current_folder / self.files_in_current_folder * 100
        else:
            progress_pct = 0
        
        # Progress bar
        bar_len = 15
        filled = int(bar_len * self.unpacked_in_current_folder / self.files_in_current_folder) if self.files_in_current_folder > 0 else 0
        bar = '█' * filled + '░' * (bar_len - filled)
        
        # Truncate folder name if too long
        folder_display = self.current_folder
        if len(folder_display) > 25:
            folder_display = '...' + folder_display[-22:]
        
        # Truncate filename if too long
        file_display = filename
        if len(file_display) > 15:
            file_display = file_display[:12] + '...'
        
        # Download indicator
        dl_indicator = "⬇️" if not self.download_complete else "✓"
        
        # Copy indicator
        copy_marker = "📋" if is_copy else ""
        
        # Compression info
        if not is_copy and compressed_size > 0:
            ratio = decompressed_size / compressed_size if compressed_size > 0 else 1
            size_info = f"{format_size(compressed_size)}->{format_size(decompressed_size)} ({ratio:.1f}x)"
        else:
            size_info = format_size(decompressed_size)
        
        # Build the line
        line = (f"[{bar}] {progress_pct:5.1f}% | "
                f"{folder_display} | "
                f"{self.unpacked_in_current_folder}/{self.files_in_current_folder}: {copy_marker}{file_display} | "
                f"{size_info} | "
                f"{format_size(speed)}/s | "
                f"{dl_indicator} {format_size(self.downloaded_bytes)}")
        
        # Get terminal width and truncate if needed
        term_width = get_terminal_width()
        if len(line) > term_width - 1:
            line = line[:term_width - 4] + '...'
        
        # Clear previous line and print new one
        self.clear_line()
        print(line, end='', flush=True)
        self.last_line_length = len(line)
    
    def print_folder_complete(self):
        """Print folder completion message."""
        self.clear_line()
        folder_data = self.folder_stats.get(self.current_folder, {})
        compressed = folder_data.get('compressed_bytes', 0)
        decompressed = folder_data.get('total_bytes', 0)
        if compressed > 0:
            ratio = decompressed / compressed
            print(f"✓ {self.current_folder}: "
                  f"{folder_data.get('unpacked_files', 0)} files, "
                  f"{format_size(compressed)} -> {format_size(decompressed)} ({ratio:.1f}x)")
        else:
            print(f"✓ {self.current_folder}: "
                  f"{folder_data.get('unpacked_files', 0)} files, "
                  f"{format_size(decompressed)}")
    
    def print_summary(self, output_dir: str):
        """Print final summary."""
        elapsed = self.get_elapsed()
        speed = self.total_bytes / elapsed if elapsed > 0 else 0
        
        print("\n" + "=" * 60)
        print("                    UNPACKING COMPLETE")
        print("=" * 60)
        print(f"  Output directory: {output_dir}")
        print(f"  Total time:       {format_time(elapsed)}")
        print(f"  Average speed:    {format_size(speed)}/s")
        print("-" * 60)
        print(f"  Folders:          {self.total_folders}")
        if self.copied_folders > 0:
            print(f"  Copied folders:   {self.copied_folders}")
        print(f"  Files:            {self.total_files}")
        if self.copied_files > 0:
            print(f"  Copied files:     {self.copied_files}")
        print(f"  Total size:       {format_size(self.total_bytes)}")
        print("-" * 60)
        print(f"  Downloaded:       {format_size(self.downloaded_bytes)}")
        if self.total_compressed_bytes > 0:
            file_ratio = self.total_bytes / self.total_compressed_bytes if self.total_compressed_bytes > 0 else 1
            print(f"  File compression: {format_size(self.total_compressed_bytes)} -> {format_size(self.total_bytes)} ({file_ratio:.1f}x)")
        print("=" * 60)
        
        # Top 5 largest folders
        if self.folder_stats:
            print("\n  Top folders by size:")
            sorted_folders = sorted(
                self.folder_stats.items(), 
                key=lambda x: x[1]['total_bytes'], 
                reverse=True
            )[:5]
            for folder, data in sorted_folders:
                compressed = data.get('compressed_bytes', 0)
                decompressed = data.get('total_bytes', 0)
                if compressed > 0:
                    ratio = decompressed / compressed
                    print(f"    {folder}: {data['unpacked_files']} files, {format_size(decompressed)} ({ratio:.1f}x)")
                else:
                    print(f"    {folder}: {data['unpacked_files']} files, {format_size(decompressed)}")


async def download_and_unpack_async(url: str, output_dir: str, chunk_size: int = 65536, queue_maxsize: int = 100) -> None:
    """
    Download a packed file (with brotli-compressed files) and unpack directly to disk (async).
    Uses separate tasks for downloading and unpacking with asyncio.Queue for buffering.
    
    Individual files in the archive are brotli-compressed and will be decompressed
    automatically by stream_unpack_async from packer_brotli.
    
    Args:
        url: URL of the packed .bin file  
        output_dir: Directory to unpack files into
        chunk_size: Size of chunks to download
        queue_maxsize: Max size of the buffer queue (0 for unlimited)
    """
    stats = UnpackStats()
    queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=queue_maxsize)
    
    async def download_task():
        """Download data and put into queue."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), follow_redirects=True) as client:
                async with client.stream('GET', url) as response:
                    response.raise_for_status()
                    
                    content_length = response.headers.get('content-length')
                    if content_length:
                        print(f"📥 Downloading from {url}")
                        print(f"   Remote file size: {format_size(int(content_length))}")
                        print(f"   Files are brotli-compressed (will decompress)")
                    else:
                        print(f"📥 Downloading from {url}")
                        print(f"   Files are brotli-compressed (will decompress)")
                    print()
                    
                    async for chunk in response.aiter_bytes(chunk_size):
                        stats.add_downloaded(len(chunk))
                        await queue.put(chunk)
        finally:
            # Signal end of download
            await queue.put(None)
            stats.download_complete = True
    
    async def queue_to_async_iter():
        """Convert queue to async iterator for stream_unpack_async."""
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
    
    # Start download task
    download_coro = asyncio.create_task(download_task())
    
    try:
        last_folder = None
        
        async for folder_name, num_files, file_idx, filename, file_size, file_chunks, source_ref in stream_unpack_async(queue_to_async_iter()):
            # Create folder path
            folder_path = os.path.join(output_dir, folder_name)
            os.makedirs(folder_path, exist_ok=True)
            stats.unpacked_folders[folder_name] = folder_path
            
            # Check if this is a copy folder marker (file_idx == -1)
            if file_idx == -1:
                # This is a copy folder, filename is actually source folder name
                source_name = filename
                source_path = stats.unpacked_folders.get(source_name)
                
                # Print completion for previous folder if needed
                if last_folder is not None and last_folder != folder_name:
                    stats.print_folder_complete()
                
                stats.total_folders += 1
                stats.copied_folders += 1
                
                if source_path and os.path.exists(source_path):
                    # Copy files from source folder
                    copied_count = 0
                    copied_bytes = 0
                    for fname in os.listdir(source_path):
                        src_file = os.path.join(source_path, fname)
                        dst_file = os.path.join(folder_path, fname)
                        if os.path.isfile(src_file):
                            shutil.copy2(src_file, dst_file)
                            stats.unpacked_files[(folder_name, fname)] = dst_file
                            copied_count += 1
                            copied_bytes += os.path.getsize(src_file)
                    
                    stats.total_files += copied_count
                    stats.total_bytes += copied_bytes
                    stats.folder_stats[folder_name] = {
                        'total_files': copied_count,
                        'unpacked_files': copied_count,
                        'total_bytes': copied_bytes,
                        'compressed_bytes': 0
                    }
                    
                    stats.clear_line()
                    print(f"📋 {folder_name} <- {source_name}: {copied_count} files, {format_size(copied_bytes)}")
                else:
                    stats.clear_line()
                    print(f"⚠️ {folder_name}: Source folder not found: {source_name}")
                
                last_folder = folder_name
            elif file_size == -2:
                # File reference - copy from another file
                src_folder, src_filename = source_ref
                src_file_path = stats.unpacked_files.get((src_folder, src_filename))
                
                # Check if we started a new folder
                if folder_name != last_folder:
                    if last_folder is not None:
                        stats.print_folder_complete()
                    last_folder = folder_name
                    stats.start_folder(folder_name, num_files)
                
                file_path = os.path.join(folder_path, filename)
                
                if src_file_path and os.path.exists(src_file_path):
                    shutil.copy2(src_file_path, file_path)
                    stats.unpacked_files[(folder_name, filename)] = file_path
                    actual_size = os.path.getsize(file_path)
                    stats.file_copied(filename, actual_size)
                    stats.print_progress(filename, 0, actual_size, is_copy=True)
                else:
                    stats.clear_line()
                    print(f"⚠️ {folder_name}/{filename}: Source file not found: {src_folder}/{src_filename}")
            else:
                # Normal file - file_chunks yields already-decompressed data
                # Check if we started a new folder
                if folder_name != last_folder:
                    # Print completion for previous folder
                    if last_folder is not None:
                        stats.print_folder_complete()
                    
                    last_folder = folder_name
                    # Now we know the exact number of files from the binary header
                    stats.start_folder(folder_name, num_files)
                
                # Write file - chunks are already decompressed by stream_unpack_async
                file_path = os.path.join(folder_path, filename)
                decompressed_size = 0
                async with aiofiles.open(file_path, 'wb') as f:
                    async for chunk in file_chunks:
                        await f.write(chunk)
                        decompressed_size += len(chunk)
                
                stats.unpacked_files[(folder_name, filename)] = file_path
                
                # file_size is the compressed size from the archive
                # decompressed_size is the actual file size after decompression
                compressed_size = file_size
                
                # Update stats
                stats.file_unpacked(filename, compressed_size, decompressed_size)
                
                # Print progress
                stats.print_progress(filename, compressed_size, decompressed_size)
        
        # Print completion for last folder
        if last_folder is not None and last_folder in stats.folder_stats:
            if stats.folder_stats[last_folder].get('unpacked_files', 0) < stats.folder_stats[last_folder].get('total_files', 0):
                stats.print_folder_complete()
            elif stats.unpacked_in_current_folder > 0:
                stats.print_folder_complete()
        
        # Wait for download to complete (should already be done)
        await download_coro
        
    except Exception as e:
        download_coro.cancel()
        raise
    
    # Print final summary
    stats.print_summary(output_dir)


# ============== CLI ==============

def main():
    if len(sys.argv) < 3:
        print("Usage: python downloader_brotli.py <url> <output_dir>")
        print()
        print("Downloads a packed file (packer_brotli format) and unpacks directly to disk.")
        print("Files in the archive are brotli-compressed and will be decompressed automatically.")
        print("Shows detailed progress and statistics during unpacking.")
        print("Downloads and unpacks run in parallel using async queue buffering.")
        print()
        print("Example:")
        print("  python downloader_brotli.py https://example.com/files.bin ./unpacked")
        sys.exit(1)
    
    url = sys.argv[1]
    output_dir = sys.argv[2]
    
    asyncio.run(download_and_unpack_async(url, output_dir))


if __name__ == '__main__':
    main()
