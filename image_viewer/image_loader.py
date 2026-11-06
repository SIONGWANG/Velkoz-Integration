# image_loader.py — 独立查看器图片异步解码 / 缓存
"""为 2K/4K/8K 大图切换提供非阻塞加载。

设计要点：
- 解码在 QThreadPool 工作线程完成，GUI 线程只做 QPixmap 转换与显示，切换图片不冻结界面。
- 大图先解码一张低分辨率预览（JPEG 等支持 ScaledSize 的格式可直接降采样解码），
  先显示预览并立即套用锁定视图；原图解码完成后再无缝替换为全分辨率。
- 两级 LRU 缓存：全分辨率 QPixmap + 预览 QPixmap，按字节预算淘汰；
  当前显示的图片受保护，不会被缓存淘汰。
- 预取上一张/下一张：正常顺序翻图时直接命中缓存，切换无解码等待。
"""
import os
from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, QSize, Qt
from PySide6.QtGui import QImageReader, QImageIOHandler, QPixmap

# 预览图最长边（像素）；足够在 2K/4K 屏上临时铺满，同时解码很快
PREVIEW_MAX_EDGE = 2048
# 全分辨率缓存预算（字节）。8K 单张约 132MB，约可保留 3~4 张（当前图 + 前后预取）
FULL_CACHE_BYTES = 512 * 1024 * 1024
# 预览缓存预算（字节）。单张预览约 4~10MB
PREVIEW_CACHE_BYTES = 96 * 1024 * 1024


class _TaskSignals(QObject):
    preview = Signal(str, object)      # path, QImage
    full = Signal(str, object)         # path, QImage
    failed = Signal(str, str)          # path, error
    done = Signal(str, str, int)       # path, kind ("ui"/"prefetch"), task id


class _DecodeTask(QRunnable):
    """单张图片的解码任务：先发预览（若需要），再发全图。"""

    def __init__(self, loader, path, token, kind, want_preview, tid):
        super().__init__()
        self.loader = loader
        self.path = path
        self.token = token
        self.kind = kind
        self.want_preview = bool(want_preview)
        self.tid = tid
        self.signals = _TaskSignals()
        self.setAutoDelete(True)

    def _cancelled(self):
        loader = self.loader
        if getattr(loader, "_shutdown", False):
            return True
        if self.kind == "ui":
            return not loader.is_wanted(self.path)
        return not loader.is_current_prefetch(self.token)

    def _read_preview(self):
        reader = QImageReader(self.path)
        size = reader.size()
        if not size.isValid() or size.isEmpty():
            return None
        if max(size.width(), size.height()) <= PREVIEW_MAX_EDGE:
            return None
        try:
            if not reader.supportsOption(QImageIOHandler.ScaledSize):
                # 不支持降采样的格式（如部分 PNG）直接跳预览，避免为了预览再整张解码一次
                return None
        except Exception:
            return None
        scaled = size.scaled(QSize(PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE), Qt.KeepAspectRatio)
        reader.setScaledSize(scaled)
        img = reader.read()
        return None if img.isNull() else img

    def _read_full(self):
        reader = QImageReader(self.path)
        img = reader.read()
        if img.isNull():
            return None, reader.errorString() or "无法解码图片"
        return img, ""

    def run(self):
        try:
            if self._cancelled():
                return
            if self.want_preview:
                preview = self._read_preview()
                if preview is not None and not self._cancelled():
                    self.signals.preview.emit(self.path, preview)
            if self._cancelled():
                return
            img, err = self._read_full()
            if err:
                self.signals.failed.emit(self.path, err)
            elif img is not None and not self._cancelled():
                self.signals.full.emit(self.path, img)
        except Exception as e:
            self.signals.failed.emit(self.path, str(e))
        finally:
            self.signals.done.emit(self.path, self.kind, self.tid)


class ImageLoader(QObject):
    """图片解码服务（GUI 线程对象）。"""

    preview_ready = Signal(str)        # path（预览已入缓存）
    image_ready = Signal(str)          # path（全分辨率已入缓存）
    load_failed = Signal(str, str)     # path, error

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ui_pool = QThreadPool(self)
        # 2 个线程：用户快速连按时，新请求不必等待上一个大图解码结束
        self._ui_pool.setMaxThreadCount(2)
        self._prefetch_pool = QThreadPool(self)
        self._prefetch_pool.setMaxThreadCount(1)

        self._full = OrderedDict()      # path -> QPixmap
        self._preview = OrderedDict()   # path -> QPixmap
        self._full_bytes = 0
        self._preview_bytes = 0
        self._protected = set()

        self._prefetch_token = 0
        self._ui_paths = set()          # 正在请求的图片（双图模式下可同时有多张）
        self._wanted_paths = set()      # 当前真正需要显示的图片；其余任务尽快放弃
        self._task_seq = 0
        self._shutdown = False
        self._tasks = []

    # ── 查询 ──
    @staticmethod
    def image_info(path):
        """快速读取图片尺寸（不完整解码）。失败返回 None。"""
        if not path or not os.path.isfile(path):
            return None
        try:
            reader = QImageReader(path)
            size = reader.size()
            if size.isValid() and not size.isEmpty():
                return (size.width(), size.height())
        except Exception:
            pass
        return None

    def cached_pixmap(self, path):
        pm = self._full.get(path)
        if pm is not None:
            self._full.move_to_end(path)
        return pm

    def cached_preview(self, path):
        pm = self._preview.get(path)
        if pm is not None:
            self._preview.move_to_end(path)
        return pm

    def has_preview(self, path):
        return path in self._preview

    def is_wanted(self, path):
        return path in self._wanted_paths

    def is_current_prefetch(self, token):
        return token == self._prefetch_token

    def set_wanted(self, *paths):
        """当前两个面板真正需要显示的图片；其余 UI 解码任务会尽快放弃。"""
        self._wanted_paths = {p for p in paths if p}

    def set_protected(self, *paths):
        """标记当前显示的图片，缓存淘汰时跳过。"""
        self._protected = {p for p in paths if p}
        self._trim_full()
        self._trim_preview()

    # ── 请求加载 ──
    def request(self, path, want_preview=None):
        """请求加载全分辨率（GUI 线程调用，立即返回）。"""
        if not path:
            return
        if self.cached_pixmap(path) is not None:
            return
        if path in self._ui_paths:
            return
        if not os.path.isfile(path):
            self.load_failed.emit(path, f"图片不存在或无法读取：{path}")
            return
        self._wanted_paths.add(path)
        self._ui_paths.add(path)
        # 新请求让旧的预取尽量让路；正在解码的任务结束时会自行检查是否仍被需要
        self._prefetch_token += 1
        if want_preview is None:
            want_preview = not self.has_preview(path)
        self._start(path, 0, "ui", want_preview, self._ui_pool)

    def prefetch(self, paths):
        """预取若干图片（低优先级线程池，立即返回）。"""
        self._prefetch_token += 1
        token = self._prefetch_token
        seen = set()
        for path in paths or []:
            if not path or path in seen:
                continue
            seen.add(path)
            if not os.path.isfile(path):
                continue
            if self.cached_pixmap(path) is not None:
                continue
            # 正在作为当前显示请求解码的图不重复预取
            if path in self._ui_paths:
                continue
            self._start(path, token, "prefetch",
                        not self.has_preview(path), self._prefetch_pool)

    def load_synchronous(self, path):
        """同步解码全图（仅用于截图等必须立即拿到原图的场景）。"""
        if not path:
            return QPixmap()
        pm = self.cached_pixmap(path)
        if pm is not None:
            return pm
        try:
            reader = QImageReader(path)
            img = reader.read()
            if img.isNull():
                return QPixmap()
            pm = QPixmap.fromImage(img)
            self._store_full(path, pm)
            return pm
        except Exception:
            return QPixmap()

    def _start(self, path, token, kind, want_preview, pool):
        self._task_seq += 1
        task = _DecodeTask(self, path, token, kind, want_preview, self._task_seq)
        task.signals.preview.connect(self._on_preview_image)
        task.signals.full.connect(self._on_full_image)
        task.signals.failed.connect(self._on_failed)
        task.signals.done.connect(self._on_done)
        self._tasks.append(task)
        pool.start(task)

    # ── 工作线程结果（GUI 线程槽） ──
    def _on_preview_image(self, path, img):
        if img is None or img.isNull():
            return
        pm = QPixmap.fromImage(img)
        if pm.isNull():
            return
        self._store_preview(path, pm)
        self.preview_ready.emit(path)

    def _on_full_image(self, path, img):
        if img is None or img.isNull():
            return
        pm = QPixmap.fromImage(img)
        if pm.isNull():
            self.load_failed.emit(path, "图片转换为显示格式失败")
            return
        self._store_full(path, pm)
        self._ui_paths.discard(path)
        self.image_ready.emit(path)

    def _on_failed(self, path, err):
        self._ui_paths.discard(path)
        # 解码失败后不再自动重试，避免无限循环
        self._wanted_paths.discard(path)
        self.load_failed.emit(path, err)

    def _on_done(self, path, kind, tid):
        self._tasks = [t for t in self._tasks if getattr(t, "tid", None) != tid]
        if kind == "ui":
            self._ui_paths.discard(path)
            # 快速切走再切回时，旧任务可能已判定取消并退出；只要图片仍被需要且未缓存，
            # 就在这里重新排队，保证面板最终一定能显示全分辨率原图。
            if (not self._shutdown and path in self._wanted_paths
                    and self.cached_pixmap(path) is None):
                self.request(path)

    # ── 缓存 ──
    def _store_preview(self, path, pm):
        if path in self._preview:
            self._preview_bytes -= self._pm_bytes(self._preview.pop(path))
        self._preview[path] = pm
        self._preview_bytes += self._pm_bytes(pm)
        self._trim_preview()

    def _store_full(self, path, pm):
        if path in self._full:
            self._full_bytes -= self._pm_bytes(self._full.pop(path))
        self._full[path] = pm
        self._full_bytes += self._pm_bytes(pm)
        self._trim_full()

    @staticmethod
    def _pm_bytes(pm):
        try:
            return int(pm.width()) * int(pm.height()) * 4
        except Exception:
            return 0

    @staticmethod
    def _evict_one(cache, sizes, protected):
        for key in list(cache.keys()):
            if key in protected:
                continue
            cache.pop(key, None)
            return sizes.get(key, 0)
        return None

    def _trim_full(self):
        if self._full_bytes <= FULL_CACHE_BYTES:
            return
        sizes = {k: self._pm_bytes(v) for k, v in self._full.items()}
        while self._full_bytes > FULL_CACHE_BYTES:
            freed = self._evict_one(self._full, sizes, self._protected)
            if freed is None:
                break
            self._full_bytes -= freed

    def _trim_preview(self):
        if self._preview_bytes <= PREVIEW_CACHE_BYTES:
            return
        sizes = {k: self._pm_bytes(v) for k, v in self._preview.items()}
        while self._preview_bytes > PREVIEW_CACHE_BYTES:
            freed = self._evict_one(self._preview, sizes, self._protected)
            if freed is None:
                break
            self._preview_bytes -= freed

    def shutdown(self):
        """停止接受新结果，等待/放弃正在运行的解码。"""
        self._shutdown = True
        self._prefetch_token += 1
        self._ui_paths.clear()
        self._wanted_paths.clear()
        try:
            self._prefetch_pool.clear()
            self._ui_pool.clear()
        except Exception:
            pass
        try:
            self._ui_pool.waitForDone(800)
            self._prefetch_pool.waitForDone(200)
        except Exception:
            pass
