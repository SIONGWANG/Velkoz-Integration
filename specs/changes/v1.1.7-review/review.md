# OpenSpec 审查报告 — 审视之眼 V1.1.6 → V1.1.7

> 审查时间：2026-05-25
> 审查范围：`app.py` 全量（3200 行）、`utils.py`（248 行）
> 审查方法：逐行代码审查 + 状态流追踪 + 交互时序分析

---

## 一、功能模块概览

| 模块 | 核心功能 | 状态 |
|------|----------|------|
| 文件扫描 | 递归扫描目录，识别图片组和文本文件 | ✅ 正常 |
| 操作员隔离 | 不同操作员生成独立 CSV | ✅ 正常 |
| 标注员确认 | 多标注员场景下的名称确认和过滤 | ✅ 正常 |
| 分类管理 | L1/L2 分类的增删改查和持久化 | ✅ 正常 |
| 标签系统 | 快捷标签选择、同步到备注 | ⚠️ 有竞态问题 |
| 抽检复核 | 从已有记录中抽样复核 | ✅ 正常 |
| 抽检验收 | 从全量数据中抽样验收 | ✅ 正常 |
| 导出功能 | 合格数据导出、不合格归集、Excel 报表 | ✅ 正常 |
| AI 预识别 | 加载质检表显示 AI 预判结果 | ✅ 正常 |
| 快捷键 | 键盘快捷键支持 | ⚠️ DOM 依赖 |
| 冷启动动画 | 品牌展示 + 版本号显示 | ❌ 版本号 bug |

---

## 二、发现的问题

### 🔴 P0 严重问题

#### 问题 1：冷启动标题版本号显示为字面量 `{version}`

**位置**：`app.py:764-774`

**现象**：
```python
# line 655: anim_html = f"""...  ← f-string 开始
# ...
# line 764: else:
# line 765:     anim_html += """    ← 无 f 前缀！
# ...
# line 772:     <div class="coldstart-title">审视之眼 V{version}</div>  ← {version} 不会被替换
# ...
# line 774:     """                 ← 普通字符串结束
# line 775: anim_html += f"""
# line 776:     <div class="coldstart-version">v{version}</div>  ← 这行有 f，正常
```

**根因**：`else` 分支中 `anim_html += """` 缺少 `f` 前缀，`{version}` 作为普通字符串不会被 Python 替换。

**影响**：用户在冷启动页面看到 "审视之眼 V{version}" 而非实际版本号。

**修复**：将 line 765 的 `anim_html += """` 改为 `anim_html += f"""`。

---

#### 问题 2：`app.run()` 未被调用

**位置**：`app.py:3199`

**现象**：
```python
if __name__ == "__main__":
    app = AcceptanceApp()
    # 缺少 app.run()
```

**影响**：程序依赖 Streamlit 的隐式执行机制（`streamlit run app.py` 时自动执行顶层代码）。如果用户直接 `python app.py`，只初始化了 `AcceptanceApp` 但不会渲染任何 UI。

**修复**：添加 `app.run()` 调用。

---

#### 问题 3：标签同步与手动编辑存在竞态条件

**位置**：`app.py:2138`, `app.py:2156-2161`, `app.py:2287-2290`

**现象**：
标签选择变化时（line 2138）触发 `st.rerun()`，整个 app 重渲染。`render_control_panel` 执行顺序：
1. Line 2159-2161：检查 `_pending_tag_sync_`，调用 `_sync_tags_to_notes`
2. Line 2285-2290：渲染 feedback widget，检测是否手动编辑

问题：`_sync_tags_to_notes` 在 step 1 执行时，step 2 的手动编辑检测尚未发生。如果用户在上一轮手动编辑了备注，但本轮是标签变更触发的 rerun，`_manual_edit_` 标记可能在 step 1 之前就被清除或未正确设置。

**复现步骤**：
1. 选择标签 "模糊"，备注自动同步为 "模糊"
2. 手动修改备注为 "模糊区域需要重拍"（此时 `_manual_edit_` 设为 True）
3. 不保存，直接取消标签 "模糊"
4. `_sync_tags_to_notes` 检查 `_manual_edit_` 为 True，跳过同步
5. 但备注仍为 "模糊区域需要重拍"，标签已空 → 状态不一致

**影响**：标签和备注内容可能不同步。

**建议**：将手动编辑检测移到 `_sync_tags_to_notes` 内部，使用更可靠的标记机制。

---

### 🟡 P1 中等问题

#### 问题 4：`preload_next_images` 使用裸 `except` 吞掉所有异常

**位置**：`app.py:189-190`

```python
except:
    pass
```

**影响**：图片加载失败时无任何日志，调试困难。

**修复**：改为 `except Exception as e: logging.debug("预加载失败: %s", e)`。

---

#### 问题 5：`reset_task_state` 未清除 `_manual_edit_` 键

**位置**：`app.py:516-540`

**现象**：`reset_task_state` 清除了 `feedback_`、`_last_tags_` 等前缀的键，但遗漏了 `_manual_edit_` 前缀。

**影响**：切换任务后，旧任务的手动编辑标记残留，可能干扰新任务的标签同步逻辑。

**修复**：在 `prefixes` 元组中添加 `"_manual_edit_"`。

---

#### 问题 6：`_save_df_at` 始终删除 .bak 备份文件

**位置**：`app.py:1144-1145`

```python
if os.path.isfile(bak_file):
    os.remove(bak_file)
```

**现象**：写入成功后立即删除 `.bak` 文件，而 `_save_df`（line 381-411）不删除 `.bak`。

**影响**：两个写入函数行为不一致。`_save_df_at` 失去了备份保护。

**建议**：统一行为，建议保留 `.bak` 文件。

---

#### 问题 7：`page_title` 硬编码 V1.1.2

**位置**：`app.py:2785`

```python
st.set_page_config(layout="wide", page_title="审视之眼 V1.1.2 - 抽检验收版")
```

**影响**：浏览器标签页显示过时版本号。

**修复**：使用动态版本号 `os.path.basename(BASE_DIR)`。

---

#### 问题 8：`_get_missing_ids` 裸 `except` + 未指定编码

**位置**：`app.py:1296-1298`

```python
try:
    df_check = pd.read_csv(csv_file, dtype={'图片ID': str})
    saved_ids = df_check['图片ID'].tolist()
except:
    pass
```

**影响**：
1. 裸 `except` 吞掉所有异常
2. 未指定 `encoding='utf-8-sig'`，与 `_save_df` 写入编码不一致

**修复**：添加 `encoding='utf-8-sig'`，改为 `except Exception: logging.warning(...)`。

---

#### 问题 9：标签选择变更触发全局 `st.rerun()`

**位置**：`app.py:2138`

```python
if set(merged) != set(selected):
    st.session_state.selected_tags[current_id] = merged
    st.session_state[f"_pending_tag_sync_{current_id}"] = True
    st.rerun()
```

**影响**：标签 pills 选择变化会触发整个 app 重渲染，可能导致：
- A 区列表闪烁
- B 区图片重新加载
- 其他 widget 状态丢失

**建议**：使用 `st.fragment` 包裹标签选择器，避免全局 rerun。

---

### 🟢 P2 轻微问题

#### 问题 10：扫描规则空后缀验证缺失

**位置**：`app.py:2709`

```python
image_slots[i] = {"stem_suffixes": [s.strip() for s in new_val.split(",")]}
```

**现象**：用户输入空字符串时，`"".split(",")` 产生 `[""]`，会匹配所有文件。

**建议**：过滤空字符串：`[s.strip() for s in new_val.split(",") if s.strip()]`。

---

#### 问题 11：`_save_settings` 未捕获写入异常

**位置**：`app.py:301-313`

```python
def _save_settings(self):
    # ...
    with open(settings_file, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    # 无 try/except
```

**影响**：磁盘满或权限不足时，保存设置会抛出未捕获异常。

---

## 三、状态管理审查

### session_state 键完整性

| 键前缀 | 用途 | `reset_task_state` 清除 | 结果 |
|--------|------|------------------------|------|
| `preview_` | 图片预览 | ✅ | PASS |
| `feedback_` | 备注文本 | ✅ | PASS |
| `_last_tags_` | 标签同步标记 | ✅ | PASS |
| `_manual_edit_` | 手动编辑标记 | ❌ 遗漏 | **FAIL** |
| `_df_cache_` | DataFrame 缓存 | ✅ | PASS |
| `_df_lookup_` | ID 查找字典 | ✅ | PASS |
| `_display_img_cache` | 图片显示缓存 | ✅ | PASS |
| `evidence_pool_` | 截图池 | ✅ | PASS |
| `zh_` / `en_` | 文本内容 | ✅ | PASS |

### 数据持久化链路

```
_save_settings  ← _render_datasource_loader (line 2644)
                ← 冷启动操作员输入 (line 3156)
                ← 布局滑块 (line 2627, 2632)
                ← 视图切换 (line 3073, 3171)
                ← 标注员确认开关 (line 2662)
```

- ✅ 所有 UI 入口变化时都调 `_save_settings()`
- ✅ `_load_settings` 返回默认值
- ⚠️ `_save_settings` 无异常捕获

---

## 四、边界场景测试

| 场景 | 预期行为 | 实际行为 | 结果 |
|------|----------|----------|------|
| 冷启动显示版本号 | 显示实际版本 | 显示 `{version}` | **FAIL** |
| 直接 python app.py | 渲染 UI | 无 UI 输出 | **FAIL** |
| 标签→手动编辑→切换标签 | 保留手动输入 | 可能覆盖 | **WARN** |
| 切换任务后标签状态 | 完全重置 | `_manual_edit_` 残留 | **FAIL** |
| 预加载损坏图片 | 跳过并继续 | 跳过但无日志 | **WARN** |
| 磁盘满时保存设置 | 提示错误 | 抛出异常 | **FAIL** |
| 扫描规则输入空后缀 | 忽略空值 | 可能匹配所有文件 | **WARN** |

---

## 五、验收结论

| 检查项 | 结果 |
|--------|------|
| 冷启动版本号 | ❌ 显示字面量 |
| app.run() 调用 | ❌ 缺失 |
| 标签同步逻辑 | ⚠️ 有竞态 |
| 状态重置完整性 | ⚠️ 遗漏键 |
| 备份策略一致性 | ⚠️ 不一致 |
| 编码一致性 | ✅ PASS |
| 操作员隔离 | ✅ PASS |
| 导出数据验证 | ✅ PASS |
| 分页筛选联动 | ✅ PASS |
| 快捷键实现 | ⚠️ DOM 依赖 |

**总体评价：核心功能稳定，操作员隔离和数据导出可靠。主要问题集中在 UI 细节（版本号显示）和状态管理健壮性（标签同步竞态、重置遗漏）。**
