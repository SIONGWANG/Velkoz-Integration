# 任务清单：V1.3.5 app.py 拆分重构

## Phase 1: 提取 CSS 常量（零风险）
- [x] 创建 `css_styles.py`，移入 `MAIN_CSS` 和 `STATUS_BUTTON_JS`
- [x] 修改 `app.py` run() 中内联 CSS/JS 为一行引用
- [x] 验证 UI 渲染一致

## Phase 2: 提取模块级函数（低风险）
- [x] 创建 `disk_io.py`，移入 7 个模块级函数
- [x] 修改 `app.py` 导入
- [x] 验证 @st.cache_data 行为不变

## Phase 3: 提取 DataMixin（中等风险）
- [x] 创建 `data_mixin.py`，类 `DataMixin`，23 个方法
- [x] 提取 `_handle_scanning` 方法（从 run() 内联逻辑）
- [x] 修改 `app.py` 类定义为多继承
- [x] 验证 CSV 读写、导航、扫描流程

## Phase 4: 提取 SettingsMixin（中等风险）
- [x] 创建 `settings_mixin.py`，类 `SettingsMixin`，19 个方法
- [x] 验证设置保存/加载、分类管理、扫描规则面板

## Phase 5: 提取 ExportMixin（中等风险）
- [x] 创建 `export_mixin.py`，类 `ExportMixin`，10 个方法（含 3 个 @st.dialog）
- [x] 验证所有弹窗和导出功能

## Phase 6: 提取 Zone Mixins（中等风险）
- [x] 创建 `zone_a.py` → `ZoneAMixin`（1 个方法）
- [x] 创建 `zone_b.py` → `ZoneBMixin`（5 个方法）
- [x] 创建 `zone_c.py` → `ZoneCMixin`（1 个方法 + 常量）
- [x] 全流程端到端验证

## Phase 7: 清理（低风险）
- [x] 清理未使用导入
- [x] 验证最终行数（243 行，目标 ~200 行）
- [x] 更新维护日志
- [x] 更新 openspec 状态为 completed
