# 维克兹彩蛋台词系统 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在审视之眼pro中加入10条维克兹改编台词彩蛋，通过条件触发以 st.toast() 形式呈现在右下角。

**架构：** 新建 `easter_eggs.py` 模块，包含台词定义、冷却检查函数、连续判定追踪函数。在现有 6 个文件的特定位置插入一行函数调用。不改变任何现有逻辑。

**技术栈：** Python, Streamlit (`st.toast`, `st.session_state`), `time` 模块

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `easter_eggs.py` | **新建** | 台词定义、冷却检查、连续判定追踪、toast 触发 |
| `data_mixin.py:543-565` | 修改 | 首次加载数据 + 抽检启动 + 撤销判定 触发点 |
| `zone_c.py:241-277` | 修改 | 标记不合格/合格 + 连续判定 + 粘贴截图 + 疲劳提醒 触发点 |
| `zone_b.py:236-240` | 修改 | 全检完成 触发点 |
| `export_mixin.py:398-515` | 修改 | 导出完成 触发点 |

---

### 任务 1：新建 easter_eggs.py

**文件：**
- 创建：`easter_eggs.py`

- [ ] **步骤 1：创建 easter_eggs.py**

```python
# easter_eggs.py — 维克兹彩蛋台词系统
import streamlit as st
import time


# === 台词定义 ===
QUOTES = {
    "first_load": "我看到了……所有数据。",
    "first_fail": "知识，通过解构获取。",
    "consecutive_fail_3": "有序的混乱……数据需要更多审视。",
    "consecutive_pass_5": "你的标注模式……可以预测。质量稳定。",
    "batch_complete": "分析完毕。批次已全部审视。",
    "sampling_start": "审视之眼……有目的地选取。",
    "paste_screenshot": "检测到异常。已记录。",
    "export_report": "知识，已封存。报告已导出。",
    "fatigue_50": "集中你的注意力。疲劳会影响判断。",
    "undo_status": "重新校准中……",
}

# 冷却时间（秒）
COOLDOWN = 30


def _trigger_toast(trigger_id: str, cooldown: int = COOLDOWN, once: bool = False):
    """检查冷却条件并触发 toast。返回 True 表示已触发。"""
    now = time.time()
    once_key = f"_ee_once_{trigger_id}"
    cd_key = f"_ee_cd_{trigger_id}"

    if once and st.session_state.get(once_key, False):
        return False

    last = st.session_state.get(cd_key, 0)
    if now - last < cooldown:
        return False

    quote = QUOTES.get(trigger_id, "")
    if not quote:
        return False

    st.toast(f"👁️ 审视之眼\n「{quote}」")
    st.session_state[cd_key] = now
    if once:
        st.session_state[once_key] = True
    return True


def init_consecutive_counters():
    """初始化连续判定计数器（仅在不存在时设置）"""
    if "_ee_consecutive_pass" not in st.session_state:
        st.session_state._ee_consecutive_pass = 0
    if "_ee_consecutive_fail" not in st.session_state:
        st.session_state._ee_consecutive_fail = 0
    if "_ee_total_judged" not in st.session_state:
        st.session_state._ee_total_judged = 0


def on_status_judged(status: str):
    """判定结果后调用，更新连续计数并触发相应台词"""
    init_consecutive_counters()

    st.session_state._ee_total_judged += 1

    if status == "不合格":
        st.session_state._ee_consecutive_fail += 1
        st.session_state._ee_consecutive_pass = 0

        if st.session_state._ee_consecutive_fail == 1:
            _trigger_toast("first_fail", once=True)
        if st.session_state._ee_consecutive_fail >= 3:
            _trigger_toast("consecutive_fail_3")

    elif status in ("合格", "修改后合格"):
        st.session_state._ee_consecutive_pass += 1
        st.session_state._ee_consecutive_fail = 0

        if st.session_state._ee_consecutive_pass >= 5:
            _trigger_toast("consecutive_pass_5")

    else:
        # 待定等其他状态，重置连续计数
        st.session_state._ee_consecutive_pass = 0
        st.session_state._ee_consecutive_fail = 0

    # 疲劳提醒
    if st.session_state._ee_total_judged == 50:
        _trigger_toast("fatigue_50", once=True)


def on_data_loaded():
    """首次加载数据成功时调用"""
    _trigger_toast("first_load", once=True)


def on_batch_complete():
    """全检完成时调用"""
    _trigger_toast("batch_complete")


def on_sampling_start():
    """抽检模式启动时调用"""
    _trigger_toast("sampling_start", once=True)


def on_screenshot_pasted():
    """粘贴错误截图时调用"""
    _trigger_toast("paste_screenshot")


def on_export_complete():
    """导出报告完成时调用"""
    _trigger_toast("export_report")


def on_undo_status():
    """撤销/修改已判定状态时调用"""
    _trigger_toast("undo_status")
```

- [ ] **步骤 2：验证文件语法**

运行：`python -c "import ast; ast.parse(open('easter_eggs.py', encoding='utf-8').read()); print('OK')"`
预期：`OK`

---

### 任务 2：首次加载数据触发

**文件：**
- 修改：`data_mixin.py:543-565`（`_handle_scanning` 方法，数据加载成功后）

- [ ] **步骤 1：在 data_mixin.py 顶部添加 import**

在 `from disk_io import scan_files_from_disk, preload_next_images` 之后添加：

```python
from easter_eggs import on_data_loaded
```

- [ ] **步骤 2：在数据加载成功后插入触发调用**

在 `_handle_scanning` 方法中，`if scanned:` 分支内，`st.session_state.confirm_pending = True` 之后（标注员确认模式）和 `st.session_state.pending_groups = []` 之后（非确认模式），各插入一行：

标注员确认模式分支（约 line 551 后）：
```python
                on_data_loaded()
```

非确认模式分支（约 line 556 后）：
```python
                on_data_loaded()
```

- [ ] **步骤 3：验证语法**

运行：`python -c "import ast; ast.parse(open('data_mixin.py', encoding='utf-8').read()); print('OK')"`
预期：`OK`

---

### 任务 3：抽检模式启动触发

**文件：**
- 修改：`data_mixin.py:508-522`（`start_sampling_acceptance` 方法）

- [ ] **步骤 1：在 data_mixin.py 顶部添加 import**

如果任务 2 已添加 `from easter_eggs import on_data_loaded`，则扩展为：

```python
from easter_eggs import on_data_loaded, on_sampling_start
```

- [ ] **步骤 2：在抽检启动后插入触发调用**

在 `start_sampling_acceptance` 方法中，`st.session_state.sampling_acceptance_mode = True` 之后（line 519 后）插入：

```python
            on_sampling_start()
```

- [ ] **步骤 3：验证语法**

运行：`python -c "import ast; ast.parse(open('data_mixin.py', encoding='utf-8').read()); print('OK')"`
预期：`OK`

---

### 任务 4：判定结果触发（不合格/合格 + 连续计数 + 疲劳提醒）

**文件：**
- 修改：`zone_c.py:241-277`（保存成功后的处理逻辑）

- [ ] **步骤 1：在 zone_c.py 顶部添加 import**

在 `from disk_io import preload_next_images` 之后添加：

```python
from easter_eggs import on_status_judged, on_screenshot_pasted, on_undo_status
```

- [ ] **步骤 2：在提交成功后插入判定触发**

在 `zone_c.py` 的保存成功逻辑中，`if is_submit:` 分支内，`st.toast("✓ 已提交", icon="🚀")` 之后（约 line 261 后）插入：

```python
                        on_status_judged(status_sel)
```

- [ ] **步骤 3：在仅保存成功后也插入判定触发**

在 `if is_save:` 分支内，`st.toast("✓ 已保存", icon="💾")` 之后（约 line 259 后）插入：

```python
                    on_status_judged(status_sel)
```

- [ ] **步骤 4：验证语法**

运行：`python -c "import ast; ast.parse(open('zone_c.py', encoding='utf-8').read()); print('OK')"`
预期：`OK`

---

### 任务 5：粘贴截图触发

**文件：**
- 修改：`zone_c.py:71-72`（粘贴成功后）

- [ ] **步骤 1：在截图粘贴成功后插入触发**

在 `zone_c.py` 中，`paste_result.image_data is not None` 判断内，`st.session_state[pool_key].append(paste_result.image_data)` 之后（约 line 72 后）插入：

```python
                        on_screenshot_pasted()
```

- [ ] **步骤 2：验证语法**

运行：`python -c "import ast; ast.parse(open('zone_c.py', encoding='utf-8').read()); print('OK')"`
预期：`OK`

---

### 任务 6：撤销判定触发

**文件：**
- 修改：`zone_c.py:142-158`（状态按钮点击处理）

- [ ] **步骤 1：在状态按钮区域添加撤销检测**

在 `zone_c.py` 的状态按钮渲染循环之后、`st.markdown('</div>', unsafe_allow_html=True)` 之前（约 line 158 前），插入撤销检测逻辑。具体位置在 `for` 循环结束后：

```python
        # 检测状态变更（撤销判定彩蛋）
        prev_status_key = f"_ee_prev_status_{group['id']}"
        prev_status = st.session_state.get(prev_status_key)
        if prev_status and status_sel and status_sel != prev_status:
            on_undo_status()
        if status_sel:
            st.session_state[prev_status_key] = status_sel
```

注意：这段代码需要放在 `status_sel = st.session_state.get('status_pills')` 之后（line 143），以及按钮循环之后。由于 `status_sel` 在 line 143 已赋值，这段代码放在 line 158（`st.markdown('</div>')`）之后即可。

- [ ] **步骤 2：在 sync_state_from_history 中记录初始状态**

在 `data_mixin.py` 的 `sync_state_from_history` 方法中，加载到历史记录后，记录初始状态用于后续撤销检测。在方法末尾 `st.session_state.last_loaded_id = current_id` 之前（约 line 253 前）插入：

```python
        # 记录初始状态用于撤销检测
        loaded_status = st.session_state.get('status_pills')
        if loaded_status:
            st.session_state[f"_ee_prev_status_{current_id}"] = loaded_status
```

- [ ] **步骤 3：验证语法**

运行：`python -c "import ast; ast.parse(open('zone_c.py', encoding='utf-8').read()); print('OK')"` 和 `python -c "import ast; ast.parse(open('data_mixin.py', encoding='utf-8').read()); print('OK')"`
预期：两个都输出 `OK`

---

### 任务 7：全检完成触发

**文件：**
- 修改：`zone_b.py:236-240`（`render_completion_panel` 方法）

- [ ] **步骤 1：在 zone_b.py 顶部添加 import**

在 `from disk_io import get_display_image_bytes` 之后添加：

```python
from easter_eggs import on_batch_complete
```

- [ ] **步骤 2：在全检完成时插入触发**

在 `render_completion_panel` 方法中，`st.session_state._completion_balloons_shown = True` 之后（约 line 240 后）插入：

```python
            on_batch_complete()
```

- [ ] **步骤 3：验证语法**

运行：`python -c "import ast; ast.parse(open('zone_b.py', encoding='utf-8').read()); print('OK')"`
预期：`OK`

---

### 任务 8：导出完成触发

**文件：**
- 修改：`export_mixin.py:398-515`（`render_export_panel` 方法）

- [ ] **步骤 1：在 export_mixin.py 顶部添加 import**

在 `from export_utils import collect_reject_folders, verify_exported_data as _export_verify` 之后添加：

```python
from easter_eggs import on_export_complete
```

- [ ] **步骤 2：在一键导出成功后插入触发**

在 `render_export_panel` 方法中，一键导出合格数据成功后（`st.balloons()` 之后），约 line 440 后插入：

```python
                    on_export_complete()
```

- [ ] **步骤 3：在确认导出成功后插入触发**

在确认导出成功后（`st.balloons()` 之后），约 line 487 后插入：

```python
                        on_export_complete()
```

- [ ] **步骤 4：在 Excel 报表导出成功后插入触发**

在 Excel 导出成功后（`st.balloons()` 之后），约 line 511 后插入：

```python
                    on_export_complete()
```

- [ ] **步骤 5：验证语法**

运行：`python -c "import ast; ast.parse(open('export_mixin.py', encoding='utf-8').read()); print('OK')"`
预期：`OK`

---

### 任务 9：集成验证

- [ ] **步骤 1：全量语法检查**

运行：
```bash
cd "E:/数乘乘美学编辑版A测1.3.1/02_Distributed/梁超/审视之眼pro/1.3.6"
python -c "
import ast
files = ['easter_eggs.py', 'data_mixin.py', 'zone_c.py', 'zone_b.py', 'export_mixin.py']
for f in files:
    ast.parse(open(f, encoding='utf-8').read())
    print(f'{f}: OK')
print('All files OK')
"
```
预期：所有文件输出 `OK`

- [ ] **步骤 2：检查 import 链完整性**

运行：
```bash
cd "E:/数乘乘美学编辑版A测1.3.1/02_Distributed/梁超/审视之眼pro/1.3.6"
python -c "from easter_eggs import on_data_loaded, on_status_judged, on_batch_complete, on_sampling_start, on_screenshot_pasted, on_export_complete, on_undo_status; print('All imports OK')"
```
预期：`All imports OK`

- [ ] **步骤 3：冒烟测试（启动应用）**

运行：`cd "E:/数乘乘美学编辑版A测1.3.1/02_Distributed/梁超/审视之眼pro/1.3.6" && py -m streamlit run app.py --server.port=8502`

验证：
1. 应用正常启动，无报错
2. 冷启动动画正常显示
3. 加载数据后，右下角应出现 toast：「👁️ 审视之眼\n「我看到了……所有数据。」」
