"""Terminal adapters: one per way of reaching a console."""

from .base import Adapter, KEY_CODES
from .bgjob import BgJobAdapter
from .ghostty import GhosttyAdapter
from .iterm2 import ITerm2Adapter

__all__ = ["Adapter", "KEY_CODES", "ITerm2Adapter", "GhosttyAdapter", "BgJobAdapter"]
