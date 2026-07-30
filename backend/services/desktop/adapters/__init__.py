"""
Desktop 平台适配器
"""

from .linux_adapter import LinuxAdapter
from .mac_adapter import MacAdapter
from .windows_adapter import WindowsAdapter

__all__ = [
    "WindowsAdapter",
    "MacAdapter", 
    "LinuxAdapter",
]
