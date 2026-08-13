# viewer/__init__.py
from .image_sync_manager import ImageSyncManager, get_sync_manager
from .dock_manager import get_dock_manager, is_dock_available

__all__ = ['ImageSyncManager', 'get_sync_manager', 'get_dock_manager', 'is_dock_available']