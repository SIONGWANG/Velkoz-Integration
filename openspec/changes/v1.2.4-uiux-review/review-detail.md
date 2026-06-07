# 审视之眼 Pro V1.2.4 — UI/UX 交互审查（详细版）

> 每个问题都附带：**现在什么样 → 用户遇到什么问题 → 应该改成什么样**
> 方便你直接对照代码理解

---

## 一、切图卡顿 — 最影响体验的 3 个问题

### 问题 1：点列表切图时，整个页面闪一下

**现在什么样：**

你在 A 区列表里点击一个新 ID，代码是这样执行的（`app.py:1957-1969`）：

```python
# 用户在 A 区列表点了一个新 ID
selected_id = st.radio("List", page_ids, ...)
if selected_id != st.session_state.current_id:
    st.session_state.current_id = selected_id
    ...
    st.rerun(scope="app")   # ← 这一行：重新执行整个 app.py 的 3160 行代码
```

`st.rerun(scope="app")` 的意思是：**把整个页面从头到尾重新跑一遍**。

**用户遇到什么问题：**

- 点击列表里任何一个 ID，页面会**闪一下**（白屏 0.5-1 秒）
- 闪的过程中，A 区的统计数字、筛选按钮、分页器全部重新渲染
- B 区的图片、文本框、AI 质检结果全部重新渲染
- C 区的分类选择、备注框、状态按钮全部重新渲染
- 但实际上**只有 B 区和 C 区的内容变了**，A 区完全没必要重绘

**应该改成什么样：**

`render_a_zone` 已经被 `@st.fragment` 装饰了（`app.py:1819`），意味着 A 区本身就是独立的 fragment。只需要把 `st.rerun(scope="app")` 去掉，让 Streamlit 自己处理 fragment 内部的更新就行。

```python
# 改成这样：
if selected_id != st.session_state.current_id:
    st.session_state.current_id = selected_id
    st.session_state.focus_img_idx = 0
    st.session_state.needs_scroll_top = True
    st.session_state._batch_completed = False
    st.session_state._completion_balloons_shown = False
    # 不调用 st.rerun()，让 fragment 自然刷新
    # B 区和 C 区会在下一个 rerun 周期自动读取新的 current_id
```

但这里有个难点：B 区和 C 区是另外两个 fragment，A 区 fragment 改了 session_state 后，B/C 不会自动感知。所以需要一个轻量级的方式通知 B/C 刷新——可以用 `st.session_state` 里设一个标记，B/C fragment 在渲染时检查这个标记。

---

### 问题 2：选标签时，整个页面闪一下

**现在什么样：**

你在 C 区点了一个快捷标签（比如「指令有歧义」），代码是这样执行的（`app.py:2046-2049`）：

```python
# 用户在标签 pills 里点了一个标签
merged = (freq_selected or []) + (other_selected or [])
if set(merged) != set(selected):
    st.session_state.selected_tags[current_id] = merged
    st.session_state[f"_pending_tag_sync_{current_id}"] = True
    st.rerun()   # ← 这一行：又是整个页面重绘
```

**用户遇到什么问题：**

- 每选一个标签/取消一个标签，整个页面都闪一下
- A 区列表重新渲染、B 区图片重新加载、C 区所有控件重新渲染
- 但实际上**只有备注框的内容需要更新**（标签同步到备注），其他都不需要动
- 如果用户连续选 3 个标签，就闪 3 次

**应该改成什么样：**

`render_tag_selector` 已经是 `@st.fragment`（`app.py:1991`），所以标签选择的变化本身就在 fragment 内部处理。关键是去掉 `st.rerun()`：

```python
# 改成这样：
merged = (freq_selected or []) + (other_selected or [])
if set(merged) != set(selected):
    st.session_state.selected_tags[current_id] = merged
    st.session_state[f"_pending_tag_sync_{current_id}"] = True
    # 不调用 st.rerun()！
    # 标记 _pending_tag_sync_ 已经设好了
    # 下一次 C 区 fragment 自然刷新时会检查这个标记并同步到备注
```

难点在于：`render_control_panel`（C 区）和 `render_tag_selector`（标签区）是两个独立的 fragment。标签变了之后，C 区的备注框不会自动更新。解决方案是让 `render_control_panel` 在开头检查 `_pending_tag_sync_` 标记（实际上代码 `app.py:2070-2072` 已经有这个逻辑了），然后用 fragment 内部的局部刷新来更新备注框。

---

### 问题 3：整个项目有 15 处 st.rerun()，大部分不需要

**现在什么样：**

我数了一下，`app.py` 里调用 `st.rerun()` 的地方：

| 行号 | 场景 | 是否必要 |
|------|------|----------|
| 422 | `_rerun_app()` 兼容方法 | 工具方法，看调用方 |
| 846 | 标注员确认跳过 | ✅ 必要（需要切换到验收界面） |
| 880 | 标注员确认开始 | ✅ 必要 |
| 1210 | 开始抽检复核 | ✅ 必要 |
| 1240 | 开始抽检验收 | ✅ 必要 |
| 1415 | 退出抽检验收 | ✅ 必要 |
| 1444 | 退出抽检复核 | ✅ 必要 |
| 1766 | 分类管理删除 L1 | ❌ 可以去掉 |
| 1773 | 分类管理添加 L1 | ❌ 可以去掉 |
| 1792 | 分类管理删除 L2 | ❌ 可以去掉 |
| 1798 | 分类管理添加 L2 | ❌ 可以去掉 |
| 1817 | 分类管理恢复默认 | ❌ 可以去掉 |
| 1967 | **列表选择切图** | ❌ 应去掉 |
| 2049 | **标签选择** | ❌ 应去掉 |
| 2559 | 应用布局 | ✅ 必要 |
| 2582 | 加载文件夹 | ✅ 必要 |
| 2729 | 清除导出结果 | ❌ 可以去掉 |
| 2745 | 清除报表 | ❌ 可以去掉 |
| 2756/2763 | 导出操作 | ✅ 必要 |
| 2805 | 确认导出 | ✅ 必要 |
| 2827/2830 | Excel 报表 | ✅ 必要 |

**不必要的 8 处**，每次都会触发整个页面白屏重绘。

**应该改成什么样：**

- 分类管理的增删：用 `@st.fragment` 包裹 `render_category_manager`（目前没有），在 fragment 内部用 `st.rerun(scope="fragment")` 只刷新分类面板
- 清除导出结果/报表：直接修改 session_state，让 fragment 自然刷新
- 列表选择和标签选择：见问题 1 和问题 2

---

## 二、操作流程 — 质检时手要来回跑

### 问题 4：C 区的操作顺序是「反人类」的

**现在什么样：**

C 区从上到下的排列顺序是：

```
┌─ C 区 ──────────────────────────┐
│ ① 信息栏（谁的数据、ID）          │
│ ② 错误截图（粘贴按钮 + 预览）     │  ← 占很大空间
│ ── 分隔线 ──                     │
│ ③ 上一条 / 下一条 导航            │
│ ── 分隔线 ──                     │
│ ④ L1 分类选择                    │
│ ⑤ L2 分类选择                    │
│ ⑥ 备注输入框                     │
│ ⑦ 💾 仅保存 / 🚀 提交并下一条     │  ← 在 form 里
│ ── 空行 ──                       │
│ ⑧ 状态按钮（合格/不合格/...）     │  ← 在 form 外面！
│ ── 空行 ──                       │
│ ⑨ 快捷标签选择                   │
│ ── 分隔线 ──                     │
│ ⑩ 标签管理（折叠）               │
└──────────────────────────────────┘
```

**用户遇到什么问题：**

用户的操作流程是：**看图 → 判定状态 → 选分类 → 写备注 → 提交**。

但现在的顺序是：
1. 先看到信息栏和错误截图（不需要）
2. 跳过导航（不需要）
3. 选 L1/L2 分类（第 ④⑤ 步）
4. 写备注（第 ⑥ 步）
5. 点保存/提交按钮（第 ⑦ 步）—— 但这时候还没选状态！
6. **往回滚上去**选状态（第 ⑧ 步）—— 状态按钮在提交按钮下面
7. 再**往下滚**回来点提交

每次质检都要**来回滚动 2-3 次**，非常浪费时间。

而且「保存/提交」在 `st.form` 里（`app.py:2196`），但「状态按钮」在 form 外面（`app.py:2222-2236`）。这意味着用户必须**先选好状态，再填备注，再点提交**——但视觉上状态按钮在提交按钮下面，违反直觉。

**应该改成什么样：**

```
┌─ C 区（改后）────────────────────┐
│ ① 信息栏（紧凑，一行）            │
│ ② 上一条 / 下一条 导航            │
│ ── 分隔线 ──                     │
│ ③ 状态按钮（合格/不合格/...）     │  ← 提到最前面！
│ ④ L1 分类选择                    │
│ ⑤ L2 分类选择                    │
│ ⑥ 快捷标签选择                   │  ← 紧跟分类后面
│ ⑦ 备注输入框                     │
│ ⑧ 💾 仅保存 / 🚀 提交并下一条     │
│ ── 分隔线 ──                     │
│ ⑨ 错误截图（默认折叠）            │  ← 折叠起来
│ ⑩ 标签管理（折叠）               │
└──────────────────────────────────┘
```

这样用户从上到下操作，**不需要来回滚动**。

---

### 问题 5：快捷键太少，高频操作没有快捷键

**现在什么样：**

快捷键（`app.py:905-966`）只支持：
- `←` 上一条
- `→` 下一条
- `空格` 提交并下一条
- `1-4` 切换图片视图
- `` ` `` 打开系统查看器

**用户遇到什么问题：**

质检时最高频的操作是**判定状态**（合格/不合格/修改后合格/待定），但这个操作**没有快捷键**。用户必须每次用鼠标去点状态按钮。

如果每天质检 500 条数据，每条省 2 秒（不用移鼠标去点状态按钮），一天就能省 17 分钟。

**应该改成什么样：**

在 `inject_hotkeys` 方法中增加状态快捷键：

```javascript
// 现有的快捷键映射
const simpleMap = { 'ArrowLeft': 'prev', 'ArrowRight': 'next', ' ': 'submit' };

// 增加状态快捷键
const statusMap = { 'q': '合格', 'w': '不合格', 'e': '修改后合格', 'r': '待定' };
if (statusMap[e.key]) {
    // 找到对应的状态按钮并点击
    const btn = doc.querySelector(`[data-hotkey="status-${statusMap[e.key]}"]`);
    if (btn) btn.click();
    return;
}

// 增加跳转未检项
if (e.key === 'n') {
    const btn = doc.querySelector('[data-hotkey="next-unchecked"]');
    if (btn) btn.click();
    return;
}
```

同时在 C 区的状态按钮上打标记：

```python
# app.py:2232 附近
if st.button(opt, key=f"sbtn_{opt}", ...):
    pass
# 需要给按钮加上 data-hotkey 属性（通过 JS 注入或 components.html）
```

---

### 问题 6：截图区域占太多空间，且无法单张删除

**现在什么样：**

C 区的错误截图区域（`app.py:2105-2136`）：

```
┌─ 📷 错误截图 (0/3) ─────────────┐
│ [📋 粘贴 (Ctrl+V)]  [🗑️ 清空截图]│
│                                   │
│ 如果有截图，显示 3 列预览图：      │
│ ┌────┐ ┌────┐ ┌────┐            │
│ │图1 │ │图2 │ │图3 │            │
│ └────┘ └────┘ └────┘            │
└───────────────────────────────────┘
```

**用户遇到什么问题：**

1. 这个区域**始终展开**，即使 90% 的时候用户不需要截图。占了 C 区约 200px 高度，把下面更重要的状态按钮和分类选择推到了需要滚动的位置
2. 如果粘贴错了第 2 张截图，只能点「🗑️ 清空截图」把 3 张全删了，然后重新粘贴
3. 没有提示用户「怎么截图」，新手不知道要先按 `Win+Shift+S` 截图到剪贴板

**应该改成什么样：**

```python
# 把错误截图区域改成折叠式
with st.expander("📷 错误截图 (0/3)", expanded=False):  # 默认折叠
    st.caption("💡 提示：先用 Win+Shift+S 截图，再点粘贴按钮")

    # 每张预览图右上角加一个删除按钮
    if st.session_state[pool_key]:
        cols = st.columns(3)
        for idx, img in enumerate(st.session_state[pool_key]):
            with cols[idx]:
                st.image(img, use_container_width=True)
                if st.button("✕", key=f"del_img_{group['id']}_{idx}"):
                    st.session_state[pool_key].pop(idx)
                    st.rerun(scope="fragment")  # 只刷新这个 fragment
```

---

## 三、信息展示 — 看不清、看不懂

### 问题 7：A 区底部功能堆叠太多，要滚动很久

**现在什么样：**

A 区从上到下的排列：

```
┌─ A 区 ──────────────────────────┐
│ 🟢 合格 42  │ 🔵 修改后合格 8    │
│ 🔴 不合格 5 │ ⚪ 未检 120        │
│ 🟡 待定 3   │ 📊 合格率 28.6%    │  ← 6 个统计卡片，占 3 行
│ [进度条 28.6%]                    │
│                                   │
│ [全部][未检][合格][修改后合格]     │  ← 6 个筛选按钮，占 3 行
│ [不合格][待定]                    │
│                                   │
│ ◀ [1][2][3][4] ▶                 │  ← 分页器
│ ○ 00123456 · 张三                 │  ← 列表开始
│ ○ 00123457 · 李四                 │
│ ○ 00123458 · 王五                 │
│ ...（每页 20 条）                  │
│ 第 1/5 页 (共 89 项)              │  ← 列表结束
│ ── 分隔线 ──                     │
│ 📊 抽检复核（折叠）               │
│ ── 分隔线 ──                     │
│ 📤 导出管理（折叠）               │
│ ── 分隔线 ──                     │
│ 🤖 AI 预识别（折叠）              │
│ ── 分隔线 ──                     │
│ ⚙️ 更多设置（折叠）               │
│ ── 分隔线 ──                     │
│ 📋 分类管理（折叠）               │
└───────────────────────────────────┘
```

**用户遇到什么问题：**

- 统计卡片占了约 180px（3 行 × 2 列），筛选按钮占了约 120px（3 行 × 2 列），加起来 300px
- 列表区实际只占 A 区的 40% 左右
- 底部 5 个 expander 纵向排列，即使折叠了也占 5 行标题高度
- 如果用户想切换到「抽检复核」或「导出管理」，需要先滚动过整个列表

**应该改成什么样：**

**方案 A**（推荐）：统计卡片改为单行紧凑模式

```
┌─ A 区（改后）─────────────────────┐
│ 已完成 58/178 │ 合格率 28.6%      │  ← 2 个核心卡片，占 1 行
│                                   │
│ [全部][未检][合格][修改后合格]     │  ← 筛选按钮改 2 行
│ [不合格][待定]                    │
│                                   │
│ ◀ [1][2][3][4] ▶                 │
│ ○ 00123456 · 张三                 │
│ ○ 00123457 · 李四                 │
│ ...                               │
│                                   │
│ ┌─ [📋列表] [📊抽检] [📤导出] ──┐│  ← Tab 导航
│ │ 抽检复核 / 导出管理 / AI 识别  ││
│ │ 的内容在这里                    ││
│ └────────────────────────────────┘│
└───────────────────────────────────┘
```

**方案 B**：保持现有布局，但把统计卡片改为 1 行 6 列

```
│ 🟢42 │ 🔴5 │ 🔵8 │ 🟡3 │ ⚪120 │ 📊28.6% │  ← 一行搞定
```

---

### 问题 8：L1/L2 分类的蓝色和绿色 emoji 跟状态颜色冲突

**现在什么样：**

```python
# app.py:2172
l1_display = [f"🔵 {opt}" for opt in l1_options]   # L1 用蓝色圆点

# app.py:2182
l2_display = [f"🟢 {opt}" for opt in l2_opts]      # L2 用绿色圆点
```

而状态的颜色映射是：
- 合格 = 🟢 绿色
- 修改后合格 = 🔵 蓝色

**用户遇到什么问题：**

- L1 分类用 🔵，「修改后合格」也用 🔵 → 视觉混淆
- L2 分类用 🟢，「合格」也用 🟢 → 视觉混淆
- 用户在 C 区看到一排蓝色圆点（L1）和一排绿色圆点（L2），可能会跟状态指标搞混

**应该改成什么样：**

L1/L2 分类去掉颜色 emoji，改用无色标记：

```python
# 方案 1：纯文字，不加 emoji
l1_display = l1_options  # 直接显示文字

# 方案 2：用中性符号
l1_display = [f"▪ {opt}" for opt in l1_options]

# 方案 3：用数字序号
l1_display = [f"{i+1}. {opt}" for i, opt in enumerate(l1_options)]
```

同时，筛选按钮和状态按钮在视觉上做区分：

```css
/* 筛选按钮：描边样式 */
.filter-btn { border: 2px solid #6366f1; background: transparent; }

/* 状态按钮：填充样式 */
.status-btn { background: #6366f1; color: white; }
```

---

### 问题 9：导出确认弹窗缺少关键信息

**现在什么样：**

导出确认面板（`app.py:2766-2805`）显示：

```
┌─ ⚠️ 确认导出 ────────────────────┐
│ 📊 当前质检状态                    │
│ 🟢 合格 42  │ 🔴 不合格 5         │
│ 🔵 修改后 8 │ 🟡 待定 3           │
│ ⚪ 未检 120  │ 📦 总计 178        │
│                                   │
│ ✅ 本次将导出 50 条合格数据         │
│ ❌ 🟡 待定 3 条 + ⚪ 未检 120 条   │
│   不会被导出                       │
│                                   │
│ [❌ 取消]  [✅ 已知悉，确认导出]    │
└───────────────────────────────────┘
```

**用户遇到什么问题：**

- 不知道数据会**导出到哪里**（路径是什么？）
- 不知道导出的文件夹叫什么名字
- 不知道大概要多久（50 条 vs 500 条耗时差很多）
- 不知道导出后怎么找文件

**应该改成什么样：**

在确认面板中增加导出路径预览和预估时间：

```python
# 计算导出路径（复用 export_qualifies_with_check 中的逻辑）
folder_name = os.path.basename(raw_path.rstrip('\\'))
timestamp = datetime.datetime.now().strftime("%m%d")
export_dir = os.path.join(parent_dir, f"{folder_name}_合格数据_{timestamp}_{total}组")

st.info(
    f"📁 导出目标：`{export_dir}`\n\n"
    f"✅ 本次将导出 **{export_count}** 条合格数据\n\n"
    f"⏱️ 预计耗时：约 {export_count * 0.5:.0f} 秒\n\n"
    f"❌ 🟡 待定 {s['pending']} 条 + ⚪ 未检 {s['unchecked']} 条**不会**被导出"
)
```

---

## 四、性能 — 感知不到但影响体验的底层问题

### 问题 10：图片缓存不是真正的 LRU，网络驱动器会卡

**现在什么样：**

```python
# app.py:141-174
def get_display_image_bytes(img_path):
    cache = st.session_state.get("_display_img_cache", {})
    cache_key = f"{img_path}_{os.path.getmtime(img_path)}"  # ← 每次访问都读文件系统
    if cache_key in cache:
        return cache[cache_key]
    # ... 处理图片 ...
    cache[cache_key] = entry
    # 淘汰策略：删除最早的（FIFO）
    while len(cache) > MAX_CACHE_ENTRIES:
        oldest = next(iter(cache))  # ← dict 的最早插入 key
        del cache[oldest]
```

**用户遇到什么问题：**

1. **每次查缓存都要调 `os.path.getmtime()`**：这是文件系统调用。如果数据在网络驱动器（如 `\\server\share\...`），每次调用可能要 50-200ms。切一张图就要查 4 次（4 张图），那就是 200-800ms 的额外延迟
2. **淘汰策略是 FIFO 不是 LRU**：假设缓存了图 1-50，然后用户回到图 1 查看，图 1 仍然可能是最先被淘汰的（因为它是最早插入的）。真正的 LRU 应该把「最近访问的」移到最后
3. **没有反向预加载**：用户点「上一条」时不会预加载更前面的图

**应该改成什么样：**

```python
from collections import OrderedDict

class ImageCache:
    def __init__(self, max_entries=50, max_bytes=100*1024*1024):
        self._cache = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._total_bytes = 0

    def get(self, key):
        if key in self._cache:
            self._cache.move_to_end(key)  # ← LRU：访问后移到最后
            return self._cache[key]
        return None

    def put(self, key, value):
        if key in self._cache:
            self._cache.move_to_end(key)
            return
        # 淘汰最久未访问的
        while len(self._cache) >= self._max_entries:
            _, evicted = self._cache.popitem(last=False)
            self._total_bytes -= len(evicted[0])
        self._cache[key] = value
        self._total_bytes += len(value[0])
```

另外，mtime 应该在扫描时一次性读取并存在 group 数据里，而不是每次查缓存时读文件系统。

---

### 问题 11：每次保存都全量读写 CSV

**现在什么样：**

```python
# app.py:1025-1045 (save_to_disk)
if os.path.isfile(csv_file):
    df = pd.read_csv(csv_file, dtype=str, encoding='utf-8-sig')  # ← 读整个文件
    mask = df['图片ID'] == target_id
    if mask.any():
        for key, value in record.items():
            df.loc[mask, key] = str(value)   # ← 改一行
    else:
        df = pd.concat([df, new_df], ...)    # ← 追加一行
    self._save_df(df)  # ← 写整个文件
```

**用户遇到什么问题：**

- 假设 CSV 有 1000 条记录，每条保存都要：读 1000 行 → 改 1 行 → 写 1000 行
- 如果数据在网络驱动器上，每次保存可能要 1-2 秒
- 用户在快速质检时（连续点「提交并下一条」），会感觉到明显的保存延迟

**应该改成什么样：**

**短期方案**：缓存 DataFrame 在内存中，保存时只修改内存副本，定期或退出时才写磁盘

```python
def save_to_disk(self, group, ...):
    # 从内存缓存读
    df = self._get_cached_df()
    # 修改内存中的 df
    ...
    # 标记为 dirty，不立即写磁盘
    st.session_state._df_dirty = True

# 在切图、退出、导出时才真正写磁盘
def _flush_df_if_dirty(self):
    if st.session_state.get('_df_dirty'):
        self._save_df(st.session_state._df_cache)
        st.session_state._df_dirty = False
```

**长期方案**：改用 SQLite 替代 CSV，支持行级读写

---

### 问题 12：预加载是同步阻塞的

**现在什么样：**

```python
# app.py:176-192
def preload_next_images(current_idx, all_groups):
    for i in range(1, preload_count + 1):
        target_idx = current_idx + i
        group = all_groups[target_idx]
        for img_path in group.get("images", []):
            _load_and_rotate(img_path)         # ← 同步读取，阻塞主线程
            get_display_image_bytes(img_path)   # ← 同步缩放，阻塞主线程
```

**用户遇到什么问题：**

- 用户点「下一条」→ 触发预加载 → 预加载 3 张图 × 每张 100-300ms = 300-900ms 的阻塞
- 在这个阻塞期间，页面是卡住的（白屏或无响应）
- 如果数据在网络驱动器上，每张图可能要 500ms-1s，预加载就会卡 1.5-3 秒

**应该改成什么样：**

```python
import threading

def preload_next_images(current_idx, all_groups):
    def _preload():
        for i in range(1, preload_count + 1):
            target_idx = current_idx + i
            group = all_groups[target_idx]
            for img_path in group.get("images", []):
                try:
                    _load_and_rotate(img_path)
                    get_display_image_bytes(img_path)
                except Exception:
                    pass

    # 在后台线程中预加载，不阻塞主线程
    thread = threading.Thread(target=_preload, daemon=True)
    thread.start()
```

---

## 五、小细节 — 不影响功能但影响体验

### 问题 13：保存/提交成功后只显示一行字就没了

**现在什么样：**

```python
# app.py:2313-2315
if is_save:
    st.toast("✓ 已保存", icon="💾")
elif is_submit:
    st.toast("✓ 已提交", icon="🚀")
```

`st.toast()` 是一个 3 秒后自动消失的小弹窗。

**用户遇到什么问题：**

- 不知道自己是第几条（进度感缺失）
- 提交后不知道下一条是哪个 ID
- 保存失败时如果用 `st.toast()` 也是 3 秒消失，用户可能没看到就错过了

**应该改成什么样：**

```python
if is_save:
    done = len(self.get_processed_ids())
    total = len(nav_ids)
    st.toast(f"✓ 已保存 ({done}/{total})", icon="💾")
elif is_submit:
    done = len(self.get_processed_ids())
    total = len(nav_ids)
    next_id = nav_ids[curr_idx + 1] if curr_idx < len(nav_ids) - 1 else "已完成"
    st.toast(f"✓ 已提交 ({done}/{total})，下一条: {next_id}", icon="🚀")
```

---

### 问题 14：四宫格模式下图片的打开按钮太小

**现在什么样：**

```python
# app.py:2476
st.button("📂", key=f"open_bimg_{group['id']}_{i}", ...)
```

配合 CSS（通过 JS 注入 `app.py:2486-2489`）：

```javascript
btn.style.fontSize = '0.7rem';
btn.style.padding = '0 2px';
btn.style.minHeight = '20px';
```

**用户遇到什么问题：**

- 📂 按钮只有 0.7rem（约 11px），在高清屏上非常小
- 在触摸设备上几乎无法准确点击
- 双击图片打开系统查看器的功能通过 JS 注入实现，可靠性取决于 Streamlit 版本

**应该改成什么样：**

```javascript
btn.style.fontSize = '0.85rem';   // 从 0.7rem 增大到 0.85rem
btn.style.padding = '2px 6px';    // 增大点击区域
btn.style.minHeight = '28px';     // 增大最小高度
```

或者直接让**点击四宫格里的图片就弹出高清大图 dialog**（复用已有的 `show_large_image` 方法），这样就不需要那个小按钮了。

---

### 问题 15：L1/L2 的「sticky」机制用户不知道

**现在什么样：**

```python
# app.py:2177-2178
if l1_sel != st.session_state.sticky_l1:
    st.session_state.sticky_l1 = l1_sel
    st.session_state.sticky_l2 = None  # ← L1 变了，L2 自动清空
```

**用户遇到什么问题：**

- 用户给第 1 条选了 L1=空间编辑，L2=位置改变
- 切到第 2 条时，L1 自动保留「空间编辑」（sticky），L2 自动保留「位置改变」
- 但如果用户在第 2 条换了 L1=色彩编辑，L2 会自动清空
- 用户不知道这个行为，可能以为 L2 数据丢失了

**应该改成什么样：**

在 L1/L2 区域增加一行小字说明：

```python
st.caption("💡 默认沿用上一条的分类选择")
```

当 L2 因为 L1 变化而清空时，显示一个 toast：

```python
if l1_sel != st.session_state.sticky_l1:
    st.session_state.sticky_l1 = l1_sel
    if st.session_state.sticky_l2:
        st.toast("L1 已变更，L2 分类已重置", icon="ℹ️")
    st.session_state.sticky_l2 = None
```

---

### 问题 16：冷启动动画太高，操作区要滚动才能看到

**现在什么样：**

```python
# app.py:789
components.html(anim_html, height=600)  # 动画占 600px 高度
```

加上下面的操作员输入、路径输入、加载按钮，整个冷启动页面超过 900px。在 1080p 屏幕上，用户需要**往下滚动**才能看到「加载文件夹」按钮。

**应该改成什么样：**

```python
components.html(anim_html, height=400)  # 从 600px 减到 400px
```

或者把动画和操作区并排显示（左动画 + 右操作），利用横向空间。

---

### 问题 17：快捷键开关每次刷新都重置为「开启」

**现在什么样：**

```python
# app.py:2847
enable_hotkeys = st.toggle("⌨️ 启用快捷键 (1-4, ~)", value=True)
```

**用户遇到什么问题：**

- 如果用户关闭了快捷键（toggle=False），但页面刷新后又变回开启状态
- 因为 `value=True` 是硬编码的默认值，没有持久化到 settings

**应该改成什么样：**

```python
enable_hotkeys = st.toggle("⌨️ 启用快捷键",
    value=st.session_state.get('hotkeys_enabled', True),
    key='hotkeys_toggle')
if enable_hotkeys != st.session_state.get('hotkeys_enabled', True):
    st.session_state.hotkeys_enabled = enable_hotkeys
    self._save_settings()  # 持久化
```

---

## 六、汇总表

| 编号 | 问题 | 现在的位置 | 用户感知 | 修复难度 |
|------|------|-----------|----------|----------|
| 1 | 列表切图全局 rerun | `:1967` | 每次切图闪一下 | 中 |
| 2 | 标签选择全局 rerun | `:2049` | 每选标签闪一下 | 中 |
| 3 | 15 处 st.rerun 过多 | 全文 | 各种操作闪一下 | 中 |
| 4 | C 区操作流反人类 | `:2062-2325` | 每次质检来回滚动 | 中 |
| 5 | 快捷键缺状态切换 | `:905-966` | 高频操作没快捷键 | 低 |
| 6 | 截图区占空间+无法单删 | `:2105-2136` | C 区空间浪费 | 低 |
| 7 | A 区底部堆叠 | `:1819-1989` | 列表空间被挤压 | 中 |
| 8 | 颜色 emoji 冲突 | `:2172,2182` | 视觉混淆 | 低 |
| 9 | 导出确认缺路径 | `:2766-2805` | 不知道导到哪 | 低 |
| 10 | 图片缓存非 LRU | `:141-174` | 网络驱动器卡顿 | 中 |
| 11 | CSV 全量读写 | `:1025-1045` | 保存延迟 | 高 |
| 12 | 预加载同步阻塞 | `:176-192` | 预加载时卡顿 | 中 |
| 13 | Toast 太简短 | `:2313-2315` | 缺进度感 | 低 |
| 14 | 四宫格按钮太小 | `:2476-2489` | 触摸设备点不准 | 低 |
| 15 | Sticky 机制不透明 | `:2177-2178` | 以为数据丢失 | 低 |
| 16 | 冷启动动画太高 | `:789` | 要滚动才能操作 | 低 |
| 17 | 快捷键开关不持久 | `:2847` | 刷新后重置 | 低 |

建议从 **问题 1+2+3**（消除不必要的 rerun）和 **问题 4**（C 区操作流重构）开始改，这 4 个对体验提升最大。
