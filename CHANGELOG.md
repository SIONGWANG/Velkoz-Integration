# 变更日志

本文件记录 Velkoz-Integration（审视之眼pro）的版本变更。

## v1.22.0 — 独立查看器单图/双图并排

基准 tag：`v1.21.4`。只给 `image_viewer/` 独立查看器增加单图/双图并排，不改动 `viewer/` 悬浮工作台，不做自动更新器。

### 新增
- **面板化架构** (`image_viewer/window.py`)
  - 新增 `ImagePanel`：每侧独立持有画布、当前图片路径、标注/撤销重做/选择框状态，以及按图片路径隔离的标注字典。
  - 窗口持 `panels[0/1]` 与 `active_side`，所有操作（缩放、平移、标注、截图、上一张/下一张）统一路由到 `active_panel()`。
  - 标注绑定图片路径，切换或交换左右图片不会串图。
- **单图 / 双图并排** (`image_viewer/window.py`, `image_viewer/config.py`)
  - 工具栏新增「双图」切换；`layout_mode` 配置项（`single`/`dual`，默认 `single`）。
  - 双图用 `QSplitter` 并排，左右缩放/平移独立；点击任一侧激活并高亮。
  - 支持 0/1/2/3/4/6/10 张：0 张空状态；1 张另一侧提示“无比较图”；≥2 张按列表顺序默认取当前与下一张，两侧均可独立选图；不截断全量列表、不硬限制为 2/4 张。
- **设置面板** (`settings_mixin.py`)
  - 「查看布局」单选（单图/双图并排），保存后热更新运行中的查看器。

### 兼容与安全
- 上传协议字段不变（`kind/path/sample_id/source_image/crop_rect/ts`）；`source_image` 记录活动面板的源图。
- 截图从对应面板原图像素裁剪并烧录该图片标注，不使用屏幕截图。
- 原 `viewer_mode`（builtin/system）语义保留；布局使用独立键 `layout_mode`，旧 `settings.json` 缺字段自动为 `single`，非法值回退并记录日志。

### 未在本版本实施
- 打包清单修复与统一版本号来源（v1.22.1）。
- 版本清单/下载/校验与自动更新、回滚（v1.23.x）。

## v1.21.4 — 快捷键生命周期与查看器进程生命周期修复

基准 tag：`v1.21.3`。本版本只修快捷键与独立查看器生命周期，不含双图模式与自动更新器。

### 修复
- **主程序网页快捷键** (`settings_mixin.py`)
  - 方向键等快捷键改为「仅在真正命中并触发按钮时才 `preventDefault`」，按钮不存在时不再阻止默认行为。
  - 输入框 / 文本域 / 下拉框 / contenteditable 聚焦时一律放行，空格、方向键、字母正常编辑。
  - 快捷键处理整体加 try/catch，单个快捷键异常不再导致全部监听器失效。
  - 保持「先移除旧监听器、再注册新监听器」的幂等注册。

- **查看器快捷键实时同步** (`settings_mixin.py`, `image_viewer/__init__.py`, `image_viewer/protocol.py`, `image_viewer/runner.py`)
  - 保存/恢复快捷键后，通过协议向运行中的查看器发送快捷键更新命令，立即生效，无需关闭重开。
  - 快捷键更新命令不携带 `images`，查看器侧仅在命令真正包含 `images` 时才重载图片，避免更新快捷键导致图片重载、缩放/平移被重置。

- **查看器关闭/重启生命周期** (`image_viewer/__init__.py`, `image_viewer/protocol.py`, `image_viewer/runner.py`, `image_viewer/window.py`)
  - `close_image_viewer()` 不再只删除锁文件：改为发送显式 `close` 命令 → 等待进程退出 → 超时兜底终止锁文件中记录的那一个 PID（不误杀其他进程）。
  - runner 收到 `close` 命令后停止心跳/轮询定时器并退出事件循环，正常释放锁。
  - 查看器窗口 `closeEvent` 释放锁并请求应用退出，确保进程真正结束。
  - 心跳新鲜但 PID 已死（硬崩溃残留锁）仍识别为未存活，可重新启动。

- **快捷键配置健壮性** (`image_viewer/config.py`)
  - 新增 `normalize_shortcuts()`：缺失字段回退默认、空值/非法值回退默认、重复键冲突消解（后者回退默认）。
  - 配置读取失败时安全回退默认值并写入可诊断日志（不记录图片内容或个人数据）。
  - `set_shortcuts()` 改为覆盖式绑定 `QAction.setShortcut`，不会残留重复 QShortcut/QAction。

### 兼容性
- 保留 `prev/next/zoom_in/zoom_out/fit/hundred/close` 旧配置字段，旧 `settings.json` 直接可用。
- 查看器上传消息协议字段不变（`kind/path/sample_id/source_image/crop_rect/ts`）。
- 不改变主程序质检、导航、提交、快速查询、Excel 导入导出、截图回传行为。

### 未在本版本实施
- 双图并排模式（v1.22.0）。
- 打包清单修复与统一版本号来源（v1.22.1）。
- 版本清单/下载/校验与自动更新、回滚（v1.23.x）。
