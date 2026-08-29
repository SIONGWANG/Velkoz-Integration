# 审查报告 — 审视之眼 V1.2.5

> 审查时间：2026-05-26
> 审查范围：`app.py`（~3195 行）、`utils.py`（~248 行）、`export_utils.py`（~176 行）
> 审查方法：逐模块代码审查 + 交互流程推演 + 边界场景分析

---

## 一、发现的问题

### P0 — 必须修复（影响数据正确性）

#### Issue 1：扫描缓存击穿机制失效 — 重新加载文件夹无效

**位置**：`scan_files_from_disk`（app.py:52）

**现象**：
```python
@st.cache_data(show_spinner=False)
def scan_files_from_disk(path, _cache_buster=0, rules_json=None):
```

`_cache_buster` 参数以下划线开头，按 Streamlit `@st.cache_data` 规范，**以下划线开头的参数不参与缓存键计算**。因此 `_cache_buster` 递增并不能击穿缓存。

**复现步骤**：
1. 加载文件夹 → 正常显示数据
2. 在文件系统中新增若干数据文件夹
3. 再次点击「加载文件夹」→ 数据未更新，仍显示旧结果

**影响**：用户在一次会话中无法重新扫描目录获取新数据，必须重启应用。

**修复**：
```python
# 方案 A：去掉下划线前缀，使其参与缓存键
def scan_files_from_disk(path, cache_buster=0, rules_json=None):

# 方案 B（推荐）：在重新扫描前主动清除缓存
scan_files_from_disk.clear()
```

---

#### Issue 2：抽检验收进度计数错误 — 读取全量 CSV 而非采样子集

**位置**：`render_inspection_panel`（app.py:~1396–1404）

**现象**：
```python
total = len(st.session_state.data_groups)  # 采样子集大小，如 20
done = 0
samp_csv = self.get_csv_filename()
if os.path.exists(samp_csv):
    try:
        df_done = pd.read_csv(samp_csv, dtype=str)
        done = len(df_done)  # ← 读取的是全量 CSV，可能有 500 条
    except:
        pass
st.caption(f"已验收 {done}/{total} 条")
```

`done` 读取的是 CSV 全量行数（包含历史数据），而 `total` 是采样子集大小。显示效果为 `已验收 520/20 条`，进度条直接打满。

**修复**：只统计采样子集内的已完成条数：
```python
if os.path.exists(samp_csv):
    try:
        df_done = pd.read_csv(samp_csv, dtype=str)
        sampled_ids = {str(g['id']) for g in st.session_state.data_groups}
        done = len(df_done[df_done['图片ID'].astype(str).isin(sampled_ids)])
    except Exception:
        pass
```

---

### P1 — 必须修复（影响交互体验）

#### Issue 3：状态选择按钮在提交按钮下方 — 交互顺序倒置

**位置**：`render_control_panel`（app.py:~2196–2236）

**现象**：控件渲染顺序为：
1. 备注输入框 + 「仅保存」/「提交并下一条」按钮（**表单内**，line 2196–2220）
2. 合格/不合格/修改后合格/待定 按钮（**表单外**，line 2222–2236）

用户必须**先填写备注 → 看到提交按钮 → 往下滚选状态 → 再往上滚点提交**。而提交时如果未选状态会报错"请补充验收结果"，造成反复滚动。

**修复**：将状态按钮移到表单内部、备注框上方（或至少在提交按钮正上方）。

---

#### Issue 4：`scan_files_from_disk` 跳过路径含"不合格"的目录

**位置**：`scan_files_from_disk`（app.py:69）

**现象**：
```python
if "不合格" in root:
    continue
```

如果根路径本身或中间路径段包含"不合格"三个字，整个目录树都会被跳过。例如：
- 路径 `D:\项目\不合格品返修数据\` → 扫描结果为空
- 路径 `D:\不合格批次_20260526\` → 扫描结果为空

**修复**：只跳过明确的状态子文件夹，而非路径任意位置：
```python
# 只跳过路径末端的"不合格"子文件夹（而非任意位置）
folder_name = os.path.basename(root)
if folder_name == "不合格":
    continue
```

---

#### Issue 5：抽检复核/抽检验收面板裸 `except: pass` 吞掉所有异常

**位置**：`render_inspection_panel`（app.py:~1402, ~1427）

**现象**：
```python
except:
    pass
```

两处裸 except 吞掉所有异常（包括 `KeyboardInterrupt`、`SystemExit`），且无日志。如果 CSV 读取出错（如文件被占用、编码错误），用户看到的是"已验收 0/N 条"，无法判断是真没做还是读取失败。

**修复**：
```python
except Exception as e:
    logging.warning("抽检进度读取失败: %s", e)
```

---

#### Issue 6：LRU 图片缓存不会感知文件变更

**位置**：`_load_and_rotate`（utils.py:91）

**现象**：
```python
@lru_cache(maxsize=100)
def _load_and_rotate(path):
```

LRU 缓存以文件路径为键。如果用户在会话期间替换了图片文件（如修改后重新导出），缓存仍返回旧图片。唯一清除缓存的时机是 `reset_task_state`（切换任务时），但用户在同一条数据内修改图片后无法看到更新。

**修复**：在缓存键中加入文件修改时间：
```python
def auto_rotate_image(img_path):
    mtime = os.path.getmtime(img_path)
    return _load_and_rotate_with_mtime(img_path, mtime)

@lru_cache(maxsize=100)
def _load_and_rotate_with_mtime(path, mtime):
    # ... 原有逻辑
```

---

#### Issue 7：`_sr_working`（自定义规则工作副本）未随任务重置清理

**位置**：`reset_task_state`（app.py:523–551）

**现象**：`_sr_working` 是扫描规则的工作副本，存储在 `session_state` 中。`reset_task_state` 清理了大量 key，但遗漏了 `_sr_working`。切换任务后，旧的工作副本规则仍残留，可能导致用户看到上一个任务的规则配置。

**修复**：在 `reset_task_state` 中添加：
```python
st.session_state.pop('_sr_working', None)
```

---

#### Issue 8：一键归集不可撤销 — 无回收站/备份机制

**位置**：`collect_reject_folders`（export_utils.py:22–46）

**现象**：
```python
shutil.move(src_path, dst_path)  # 源文件夹被物理删除
```

归集操作使用 `shutil.move`，执行后原始位置的数据被删除。虽然有确认对话框，但无撤销机制。如果误操作或归集后发现判断有误，数据需要手动从 `{日期}不合格` 文件夹搬回。

**修复**：归集前先复制到目标，确认完成后再删除源文件夹；或改为 `shutil.copytree` + 延迟删除（记录待删除列表，用户确认后再删）。

---

### P2 — 建议修复（提升体验质量）

#### Issue 9：`export_excel_with_images` 图片缩放比例硬编码

**位置**：`export_excel_with_images`（app.py:~1702）

**现象**：
```python
'x_scale': 0.15, 'y_scale': 0.15
```

所有图片统一缩小到 15%。对于 4000×3000 的大图，插入后约为 600×450 像素，尚可接受；但对于 400×300 的小图，插入后仅 60×45 像素，几乎不可见。

**修复**：根据图片实际尺寸动态计算缩放比例，使插入后图片宽度约为 150–200 像素。

---

#### Issue 10：筛选条件下触发「本轮完成」显示全量统计

**位置**：`render_completion_panel`（app.py:2326–2397）

**现象**：当用户在筛选条件（如"未检"）下提交完最后一条时，`_batch_completed = True` 触发完成面板。但完成面板统计的是全量数据（合格/不合格/总数等），而非当前筛选子集。用户可能困惑："我只看了未检的，为什么显示 500 条全部完成？"

**修复**：在完成面板中区分"本轮筛选完成"和"全部完成"，或在筛选条件下不触发完成面板，仅跳回筛选列表。

---

#### Issue 11：`_sync_tags_to_notes` 可能覆盖用户已有的手写备注

**位置**：`_sync_tags_to_notes`（app.py:438–486）

**现象**：从 CSV 恢复历史记录时，如果用户之前的备注恰好等于旧标签文本（如备注为"位置改变"，与标签"位置改变"相同），`manual_edit` 标志不会被设置。此时如果用户选择新标签，旧备注会被新标签覆盖，用户的手写内容丢失。

**修复**：在 `sync_state_from_history` 中，如果备注非空且与标签不同，主动设置 `manual_edit = True`。

---

#### Issue 12：`_build_group_from_path` 的 fallback 图片匹配可能选错图

**位置**：`_build_group_from_path`（app.py:1267–1273）

**现象**：
```python
if not has_any_image:
    raw_images = [f for f in files if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
    if raw_images:
        raw_images.sort()
        image_results[0] = raw_images[0]
```

当图片文件名不符合预期格式时，fallback 取文件夹内第一张图片作为"原图"。如果文件夹内有缩略图、临时文件等，可能显示错误的图片。

**修复**：fallback 时增加提示标记，让 UI 可以标注"⚠️ 图片未按规则匹配"。

---

#### Issue 13：导航按钮（上一条/下一条）未与筛选联动的边界情况

**位置**：`go_to_previous_group` / `go_to_next_group`（app.py:623–659）

**现象**：`go_to_previous_group` 和 `go_to_next_group` 方法内部调用 `_get_filtered_ids()`，但预加载使用的是全量 `all_groups` 的索引。如果筛选条件使索引与全量索引不一致，预加载的目标可能不是下一条要查看的数据。

**修复**：预加载使用 `filtered_ids` 的索引而非全量索引。

---

#### Issue 14：快捷键 JS 注入的按钮匹配规则脆弱

**位置**：`inject_hotkeys`（app.py:905–966）

**现象**：快捷键通过按钮文本内容匹配（如 `b.innerText.includes('⬅️ 上一条')`）。如果按钮文本被截断、换行或 Streamlit 渲染差异导致文本不完全匹配，快捷键将失效。且 `view-2`、`view-3`、`view-4` 的匹配逻辑依赖 `btns.filter` 的顺序，如果页面上有其他包含"🔍"的按钮，可能匹配错误。

**修复**：为需要快捷键的按钮添加 `data-hotkey` 属性（由 Python 端通过 `components.html` 注入），而非依赖文本匹配。

---

### P3 — 可选优化

#### Issue 15：`show_large_image` 对话框不显示分辨率信息

**位置**：`show_large_image`（app.py:1718–1731）

**现象**：大图查看对话框只显示图片和文件名，不显示分辨率。用户在四宫格模式下可以看到分辨率（`render_b_image_area` 有 `st.caption(res)`），但在大图模式下反而看不到。

---

#### Issue 16：`PRELOAD_AHEAD = 5` 配合多图片槽位可能导致内存压力

**位置**：`preload_next_images`（app.py:176–192）

**现象**：每个 group 最多有 N 个图片槽位（默认 2），预加载 5 个 group 意味着同时加载 10 张原图到 LRU 缓存。对于高分辨率图片（4K），单张可达 30–50MB。加上显示缓存（MAX_CACHE_BYTES = 100MB），总内存占用可能较高。

---

#### Issue 17：姓名截断为 6 字符可能丢失关键后缀信息

**位置**：`render_a_zone`（app.py:1888）

**现象**：
```python
display_name = user_name if len(user_name) <= 6 else user_name[:5] + '…'
```

对于带后缀的姓名（如"杨佳奇返修"= 5 字符、"张三新标修改"= 6 字符），6 字符截断刚好够用。但更长的组合（如"杨佳奇生产返修"= 7 字符）会被截断，用户无法在列表中看到完整标注员信息。

---

## 二、问题汇总

| 编号 | 优先级 | 问题 | 位置 |
|------|--------|------|------|
| 1 | P0 | 扫描缓存击穿机制失效（_cache_buster 不参与缓存键） | app.py:52 |
| 2 | P0 | 抽检验收进度计数错误（读取全量 CSV） | app.py:~1396 |
| 3 | P1 | 状态选择按钮在提交按钮下方（交互顺序倒置） | app.py:~2196–2236 |
| 4 | P1 | 路径含"不合格"即跳过扫描 | app.py:69 |
| 5 | P1 | 裸 `except: pass` 吞掉抽检进度异常 | app.py:~1402, 1427 |
| 6 | P1 | LRU 图片缓存不感知文件变更 | utils.py:91 |
| 7 | P1 | `_sr_working` 未随任务重置清理 | app.py:523–551 |
| 8 | P1 | 一键归集不可撤销 | export_utils.py:22–46 |
| 9 | P2 | Excel 图片缩放比例硬编码 | app.py:~1702 |
| 10 | P2 | 筛选完成触发全量统计完成面板 | app.py:2326–2397 |
| 11 | P2 | 标签同步可能覆盖手写备注 | app.py:438–486 |
| 12 | P2 | fallback 图片匹配可能选错图 | app.py:1267–1273 |
| 13 | P2 | 导航预加载使用全量索引 | app.py:623–659 |
| 14 | P2 | 快捷键 JS 按钮匹配规则脆弱 | app.py:905–966 |
| 15 | P3 | 大图对话框不显示分辨率 | app.py:1718–1731 |
| 16 | P3 | 预加载可能造成内存压力 | app.py:176–192 |
| 17 | P3 | 姓名截断可能丢失后缀信息 | app.py:1888 |

**统计：P0 × 2 | P1 × 6 | P2 × 6 | P3 × 3**
