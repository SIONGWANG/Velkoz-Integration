# 维克兹彩蛋台词系统 - 设计规格

## 概述

在"审视之眼pro"中加入维克兹（Vel'Koz）台词彩蛋，将虚空之眼的经典台词改编为质检语境，通过条件触发以 Toast 浮窗形式呈现。

## 设计决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 触发方式 | 条件触发 | 有仪式感，不随机打扰 |
| 呈现形式 | `st.toast()` 浮窗 | 轻量、不打断工作流、不导致布局跳动 |
| 台词风格 | 改编为质检语境 | 有梗又不违和，致敬原作 |
| 位置 | 右下角（默认） | 不遮挡主要内容区域 |

## 触发场景与台词

### 1. 首次加载数据成功

- **原版**: "I see... everything."
- **改编**: 「我看到了……所有数据。」
- **触发条件**: `st.session_state` 中数据列表从空变为非空的首次加载

### 2. 标记第一个不合格

- **原版**: "Knowledge through disintegration."
- **改编**: 「知识，通过解构获取。」
- **触发条件**: 用户首次将某条数据标记为"不合格"（仅第一次）

### 3. 连续 3 个不合格

- **原版**: "Organized chaos."
- **改编**: 「有序的混乱……数据需要更多审视。」
- **触发条件**: 连续判定 3 条数据为"不合格"（中间不能有合格/修改后合格）

### 4. 连续 5 个合格

- **原版**: "Your patterns are predictable."
- **改编**: 「你的标注模式……可以预测。质量稳定。」
- **触发条件**: 连续判定 5 条数据为"合格"

### 5. 全检完成

- **原版**: "Analysis complete."
- **改编**: 「分析完毕。批次已全部审视。」
- **触发条件**: 所有待检数据均已判定（与现有 `st.balloons()` 同时触发）

### 6. 抽检模式启动

- **原版**: "The eye selects with purpose."
- **改编**: 「审视之眼……有目的地选取。」
- **触发条件**: 用户启动抽检模式

### 7. 粘贴错误截图

- **原版**: "Anomaly detected."
- **改编**: 「检测到异常。已记录。」
- **触发条件**: 用户通过粘贴按钮成功粘贴一张错误截图

### 8. 导出报告

- **原版**: "Knowledge, secured."
- **改编**: 「知识，已封存。报告已导出。」
- **触发条件**: 导出操作成功完成

### 9. 连续工作超过 50 条

- **原版**: "Focus your attention."
- **改编**: 「集中你的注意力。疲劳会影响判断。」
- **触发条件**: 本次会话中累计判定数据达到 50 条

### 10. 撤销判定

- **原版**: "Recalibrating..."
- **改编**: 「重新校准中……」
- **触发条件**: 用户修改了已判定数据的状态（从 A 状态改为 B 状态）

## 呈现样式

Toast 内容格式：

```
👁️ 审视之眼
「改编台词内容」
```

- 首行：眼睛图标 + "审视之眼" 标识
- 次行：台词，用中文引号「」包裹
- 停留时间：3-4 秒后自动消失
- 位置：页面右下角（Streamlit `st.toast` 默认位置）

## 频率控制机制

为避免频繁弹出干扰工作：

1. **冷却时间**: 同一触发条件 30 秒内不重复触发
2. **单次会话去重**: 部分台词（如"首次标记不合格"）在整个会话中只触发一次
3. **实现方式**: 通过 `st.session_state` 记录每条台词的最后触发时间戳

### session_state 键设计

```python
# 记录每条台词最后触发的时间戳
st.session_state["toast_cooldown_{trigger_id}"] = timestamp

# 记录单次触发的台词是否已触发
st.session_state["toast_once_{trigger_id}"] = True

# 累计判定计数
st.session_state["toast_total_judged"] = int

# 连续合格/不合格计数
st.session_state["toast_consecutive_pass"] = int
st.session_state["toast_consecutive_fail"] = int
```

## 技术实现要点

### 调用方式

```python
st.toast("👁️ 审视之眼\n「我看到了……所有数据。」")
```

### 触发检查函数

建议在 `utils.py` 或新建 `easter_eggs.py` 中实现：

```python
def check_and_trigger_toast(trigger_id: str, quote: str, cooldown: int = 30, once: bool = False):
    """检查冷却条件并触发 toast"""
    import time
    now = time.time()

    # 单次触发检查
    if once and st.session_state.get(f"toast_once_{trigger_id}", False):
        return

    # 冷却检查
    last = st.session_state.get(f"toast_cooldown_{trigger_id}", 0)
    if now - last < cooldown:
        return

    # 触发
    st.toast(f"👁️ 审视之眼\n「{quote}」")
    st.session_state[f"toast_cooldown_{trigger_id}"] = now
    if once:
        st.session_state[f"toast_once_{trigger_id}"] = True
```

### 需要插入触发点的文件

| 触发点 | 所在文件 | 大致位置 |
|--------|----------|----------|
| 首次加载数据 | `data_mixin.py` | 数据加载成功后 |
| 标记不合格 | `zone_c.py` | 状态按钮点击处理 |
| 连续判定 | `zone_c.py` | 状态按钮点击处理后 |
| 全检完成 | `zone_b.py` | `st.balloons()` 附近 |
| 抽检启动 | `zone_a.py` 或 `settings_mixin.py` | 抽检逻辑处 |
| 粘贴截图 | `zone_c.py` | 粘贴按钮回调 |
| 导出完成 | `export_mixin.py` | 导出成功后 |
| 撤销判定 | `zone_c.py` | 状态修改处理 |

## 不涉及的范围

- 不修改任何现有功能逻辑
- 不改变布局或样式
- 不新增依赖包（`st.toast` 是 Streamlit 原生 API）
- 不影响性能（toast 是纯前端展示，无额外开销）
