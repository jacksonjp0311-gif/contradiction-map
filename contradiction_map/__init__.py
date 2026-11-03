"""Local, bounded evidence extraction for repository contradiction candidates."""
__version__ = "0.1.0"
from .engine import ScanCancelled, ScanError, scan, scan_files

__all__ = ["scan", "scan_files", "ScanError", "ScanCancelled"]
