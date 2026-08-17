# OpenSpec 审查文档索引

## 目录结构

```
openspec/
├── README.md                    # 本文件
├── archive/                     # 归档目录
│   └── v1.3.6-robustness-optimization-completed-2026-05-28/
└── changes/                     # 变更目录
    ├── v1.3.6-robustness-optimization/
    ├── v1.3.7-csv-selector/
    └── v1.3.7-packaging/
```

## 审查记录

### V1.3.7 CSV 历史文件选择器

- **状态**：completed
- **时间**：2026-06-02
- **文档**：`changes/v1.3.7-csv-selector/`
- **内容**：支持跨天查看历史验收记录 CSV 文件

**改动内容**：
1. `data_mixin.py` — 新增 `_scan_existing_csvs()` + 修改 `get_csv_filename()` 支持选择覆盖
2. `settings_mixin.py` — 侧边栏添加 CSV 文件选择器下拉框

---

### V1.3.7 打包发布

- **状态**：in-progress
- **时间**：2026-05-28
- **文档**：`changes/v1.3.7-packaging/`
- **内容**：打包 V1.3.7 版本为可分发的 ZIP 压缩包

**打包范围**：
1. 核心代码（app.py + 所有模块）
2. 配置文件（config/、categories_config.json、scan_rules.json）
3. 启动脚本（启动_采集质检版.bat）
4. 操作手册（用户操作手册_v1.3.0.html）

---

### V1.3.6 鲁棒性优化审查

- **状态**：completed
- **时间**：2026-05-28
- **归档**：`archive/v1.3.6-robustness-optimization-completed-2026-05-28/`
- **文档**：
  - `.openspec.yaml` - 元数据
  - `proposal.md` - 优化提案
  - `review.md` - 审查报告
  - `tasks.md` - 任务清单

**优化内容**：
1. 编码降级策略掩盖数据问题（P1）✅
2. 标签同步可能覆盖用户输入（P2）✅
3. 图片预加载失败用户不知道（P2）✅
4. QA报告加载失败静默处理（P2）✅
5. 配置文件损坏用户不知道（P2）✅

**优化目标**：静默失败 → 明确提示，让用户知道发生了什么

## 审查流程

1. **提案阶段**：创建 `proposal.md`，明确优化目标和范围
2. **实施阶段**：修改代码，实现优化功能
3. **审查阶段**：创建 `review.md`，验证优化效果
4. **归档阶段**：将完成的审查文档复制到 `archive/` 目录

## 版本对应关系

| 版本 | OpenSpec文档 | 状态 |
|------|--------------|------|
| V1.3.7 | `changes/v1.3.7-csv-selector/` | completed |
| V1.3.7 | `changes/v1.3.7-packaging/` | in-progress |
| V1.3.6 | `changes/v1.3.6-robustness-optimization/` | completed |

## 使用说明

1. 查看当前审查：`changes/` 目录下的文档
2. 查看历史审查：`archive/` 目录下的文档
3. 创建新审查：在 `changes/` 目录下创建新的审查目录

---

**维护人**：Claude Code
**更新时间**：2026-05-28