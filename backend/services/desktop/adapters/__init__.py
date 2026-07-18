"""
Desktop 平台适配器
"""

from .windows_adapter import WindowsAdapter
from .mac_adapter import MacAdapter
from .linux_adapter import LinuxAdapter

__all__ = [
    "WindowsAdapter",
    "MacAdapter", 
    "LinuxAdapter",
]
