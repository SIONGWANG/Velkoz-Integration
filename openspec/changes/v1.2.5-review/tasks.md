# 任务清单 — V1.2.5 审查修复（→ V1.2.6）

## P0 — 必须修复

- [ ] **Task 1**：修复 `scan_files_from_disk` 缓存击穿机制
  - 文件：`app.py:52`
  - 方案：去掉 `_cache_buster` 的下划线前缀，或在重新扫描前调用 `scan_files_from_disk.clear()`
  - 验证：加载文件夹 → 新增文件 → 再次加载 → 确认新文件出现

- [ ] **Task 2**：修复抽检验收进度计数
  - 文件：`app.py:~1396–1404`
  - 方案：只统计采样子集 ID 内的已完成条数
  - 验证：抽检验收模式下，进度数字不超过采样总数

## P1 — 必须修复

- [ ] **Task 3**：将状态选择按钮移到提交按钮上方
  - 文件：`app.py:~2196–2236`（`render_control_panel`）
  - 方案：调整渲染顺序，将状态按钮（合格/不合格等）移到表单内备注框上方
  - 验证：用户无需滚动即可完成"选状态 → 填备注 → 提交"流程

- [ ] **Task 4**：修正"不合格"目录跳过逻辑
  - 文件：`app.py:69`
  - 方案：改为只跳过路径末端文件夹名为"不合格"的目录
  - 验证：根路径包含"不合格"字样时仍能正常扫描

- [ ] **Task 5**：替换裸 `except: pass` 为带日志的异常处理
  - 文件：`app.py:~1402, ~1427`
  - 方案：`except Exception as e: logging.warning("抽检进度读取失败: %s", e)`
  - 验证：CSV 被占用时，用户能看到 warning 日志

- [ ] **Task 6**：LRU 图片缓存加入文件修改时间作为缓存键
  - 文件：`utils.py:91–128`
  - 方案：`auto_rotate_image` 读取 `os.path.getmtime` 并传入缓存函数
  - 验证：替换图片文件后，刷新页面能看到新图片

- [ ] **Task 7**：`reset_task_state` 中清理 `_sr_working`
  - 文件：`app.py:523–551`
  - 方案：添加 `st.session_state.pop('_sr_working', None)`
  - 验证：切换任务后，自定义读取规则面板显示新任务的规则

- [ ] **Task 8**：为一键归集增加安全机制
  - 文件：`export_utils.py:22–46`
  - 方案 A（推荐）：改为先 copytree 再删除源，失败时保留源文件
  - 方案 B：归集后在目标目录生成 `_归集清单.csv`，记录每个文件夹的原始路径，方便手动回退
  - 验证：归集中断后，源数据不丢失

## P2 — 建议修复

- [ ] **Task 9**：Excel 图片缩放比例动态计算
  - 文件：`app.py:~1698–1705`
  - 方案：根据图片实际宽度计算 scale，使插入后宽度约 150px

- [ ] **Task 10**：筛选条件下的完成行为优化
  - 文件：`app.py:2316–2324`
  - 方案：筛选条件下不触发 `_batch_completed`，仅跳回筛选列表首条

- [ ] **Task 11**：`sync_state_from_history` 中对非空备注设置 `manual_edit`
  - 文件：`app.py:488–521`
  - 方案：恢复历史记录时，如果备注非空且与标签不同，设置 `manual_edit = True`

- [ ] **Task 12**：fallback 图片匹配增加 UI 提示
  - 文件：`app.py:1267–1273`
  - 方案：fallback 时在 group 中标记 `fallback_image = True`，UI 显示警告

- [ ] **Task 13**：导航预加载使用筛选后的索引
  - 文件：`app.py:623–659`
  - 方案：`preload_next_images` 使用 `filtered_ids` 的索引

- [ ] **Task 14**：快捷键匹配改为 data 属性方式
  - 文件：`app.py:905–966`
  - 方案：Python 端为按钮注入 `data-hotkey` 属性，JS 端按属性匹配
