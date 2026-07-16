# viewer/image_sync_manager.py — ImageSyncManager: 主程序与Dock的通信管理
import socket
import json
from typing import Dict


class ImageSyncManager:
    """ImageSyncManager: 管理主程序与ImageDock的Socket通信
    
    主程序作为Client，向Dock Server发送消息。
    Dock需要手动启动: python viewer/image_dock.py
    """

    HOST = '127.0.0.1'
    PORT = 56789

    def __init__(self):
        pass

    def send(self, message: Dict) -> bool:
        """发送消息到Dock（Dock需要手动启动）"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect((self.HOST, self.PORT))
                data = json.dumps(message, ensure_ascii=False).encode('utf-8')
                s.sendall(len(data).to_bytes(4, 'big'))
                s.sendall(data)
                return True
        except Exception:
            return False

    def update_images(self, sample_id: str, current_index: int, total: int, images: list):
        """发送图片更新消息"""
        message = {
            'type': 'update_images',
            'sample_id': sample_id,
            'current_index': current_index,
            'total': total,
            'images': images
        }
        self.send(message)


_manager_instance = None

def get_sync_manager() -> ImageSyncManager:
    """获取ImageSyncManager单例"""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = ImageSyncManager()
    return _manager_instance
