# css_styles.py — CSS/JS 常量提取自 app.py
# 从 app.py run() 方法中提取的全局样式和状态按钮脚本

MAIN_CSS = """
<style>
header[data-testid="stHeader"], footer {display: none;}
.appview-container .main .block-container {
    padding-top: 0.4rem !important; padding-bottom: 1rem !important; padding-left: 0.6rem; padding-right: 0.6rem;
}
div[data-testid="column"] { padding: 0 4px !important; }
div[data-testid="column"]:nth-of-type(2) > div[data-testid="stVerticalBlock"] {
    display: flex; flex-direction: column; justify-content: flex-start; min-height: auto; gap: 0.3rem !important;
}
div[data-testid="stImage"] img {
    border-top-left-radius: 6px; border-top-right-radius: 6px;
    max-height: calc(100vh * var(--b-height-percent, 85) / 100 - 200px) !important; object-fit: contain !important;
    width: auto !important; margin: 0 auto !important;
}
div[data-testid="stImage"] { margin-bottom: -8px !important; text-align: center; }
.compact-btn button {
    width: 100%; border-radius: 0px; border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;
    border: 1px solid #ddd; border-top: none; background-color: #f8f9fa;
    color: #444; font-size: 0.8rem; padding: 0.1rem 0; min-height: 0px; line-height: 1.2;
    margin-bottom: 1px;
}
.compact-btn button:hover { background-color: #e2e6ea; }
.switch-btn button {
    width: 100%; border-radius: 4px; border: 1px solid #ddd; background-color: #fff;
    padding: 0.1rem 0.2rem; font-weight: bold; margin-bottom: 2px; font-size: 0.85rem; min-height: 0px;
}
.switch-btn button:hover { border-color: #007bff; color: #007bff; background-color: #f0f8ff;}
/* stPills — metric卡片按钮风格 (L1/L2/标签/不一致) */
div[data-testid="stPills"] { gap: 6px; flex-wrap: wrap; margin-bottom: 0.3rem; }
div[data-testid="stPills"] > button, div[data-testid="stPills"] > label {
    background-color: #f0f2f6 !important; border-radius: 4px !important; padding: 5px 8px !important;
    font-size: 0.8rem !important; text-align: center !important; border: 2px solid transparent !important; color: #333 !important;
}
div[data-testid="stPills"] > button:hover, div[data-testid="stPills"] > label:hover {
    border-color: #6366f1 !important; background-color: #eef2ff !important;
}
div[data-testid="stPills"] > [aria-checked="true"] {
    border-color: #6366f1 !important; background-color: #e0e7ff !important; color: #3730a3 !important; font-weight: 600 !important;
}
/* stSegmentedControl — metric卡片按钮风格 (最终结果备选) */
div[data-testid="stSegmentedControl"] > * {
    background-color: #f0f2f6 !important; border-radius: 4px !important; padding: 6px 8px !important;
    font-size: 0.85rem !important; text-align: center !important; border: 2px solid transparent !important; color: #333 !important;
}
div[data-testid="stSegmentedControl"] > *:hover {
    border-color: #6366f1 !important; background-color: #eef2ff !important;
}
div[data-testid="stSegmentedControl"] > [data-selected="true"] {
    border-color: #6366f1 !important; background-color: #e0e7ff !important; color: #3730a3 !important; font-weight: 600 !important;
}
/* ═══ Ribbon标签栏样式 ═══ */
/* 标签按钮：Office风格底部边框指示器 */
div[data-testid="stHorizontalBlock"] .stButton > button {
    border-radius: 4px 4px 0 0 !important;
    border: 1px solid #e0e0e0 !important;
    border-bottom: 2px solid transparent !important;
    background: #f5f5f5 !important;
    color: #555 !important;
    font-size: 0.82rem !important;
    font-weight: 500 !important;
    padding: 6px 16px !important;
    min-height: 32px !important;
    transition: all 0.15s;
}
div[data-testid="stHorizontalBlock"] .stButton > button:hover {
    background: #e8eaf6 !important;
    border-color: #c5cae9 !important;
    color: #333 !important;
}
div[data-testid="stHorizontalBlock"] .stButton > button[kind="primary"] {
    background: #fff !important;
    border-color: #6366f1 !important;
    border-bottom: 2px solid #6366f1 !important;
    color: #4338ca !important;
    font-weight: 600 !important;
}
/* 统计卡片紧凑 */
div[data-testid="stMetric"] { padding: 4px 8px !important; margin-bottom: 2px !important; }
div[data-testid="stMetric"] p { font-size: 0.7rem !important; }
div[data-testid="stMetric"] div[data-testid="stMetricValue"] { font-size: 0.9rem !important; }

/* stButton — 默认按钮风格 */
div[data-testid="stButton"] button[kind="secondary"] {
    background-color: #f0f2f6 !important; border: 2px solid transparent !important;
    border-radius: 4px !important; padding: 5px 10px !important;
    font-size: 0.8rem !important; text-align: center !important; color: #333 !important;
}
div[data-testid="stButton"] button[kind="secondary"]:hover {
    border-color: #6366f1 !important; background-color: #eef2ff !important;
}
div[data-testid="stButton"] button[kind="primary"] {
    background-color: #e0e7ff !important; border: 2px solid #6366f1 !important;
    border-radius: 4px !important; padding: 5px 10px !important;
    font-size: 0.8rem !important; text-align: center !important;
    color: #3730a3 !important; font-weight: 600 !important;
}
div[data-testid="stButton"] button[kind="primary"]:hover {
    background-color: #c7d2fe !important;
}
div[data-testid="stButton"] { margin-bottom: 0px; }
div[data-testid="stButton"] button:disabled {
    background-color: #e0e0e0 !important;
    color: #888 !important;
    border-color: #d0d0d0 !important;
    cursor: not-allowed;
    opacity: 0.8;
}
div[data-testid="stButton"] button:disabled:hover {
    background-color: #e0e0e0 !important;
    color: #888 !important;
    border-color: #d0d0d0 !important;
}
/* 状态按钮颜色 — 通过 JS 注入 data-status 属性后生效 */
button[data-status="pass"]    { background-color: #dcfce7 !important; color: #166534 !important; border-color: #86efac !important; }
button[data-status="pass"]:hover { background-color: #bbf7d0 !important; }
button[data-status="fail"]    { background-color: #fee2e2 !important; color: #991b1b !important; border-color: #fca5a5 !important; }
button[data-status="fail"]:hover { background-color: #fecaca !important; }
button[data-status="modified"] { background-color: #dbeafe !important; color: #1e40af !important; border-color: #93c5fd !important; }
button[data-status="modified"]:hover { background-color: #bfdbfe !important; }
button[data-status="pending"]  { background-color: #fef9c3 !important; color: #854d0e !important; border-color: #fde047 !important; }
button[data-status="pending"]:hover { background-color: #fef08a !important; }
button[data-status="pass"][kind="primary"]    { background-color: #16a34a !important; color: #fff !important; border-color: #16a34a !important; }
button[data-status="fail"][kind="primary"]    { background-color: #dc2626 !important; color: #fff !important; border-color: #dc2626 !important; }
button[data-status="modified"][kind="primary"] { background-color: #2563eb !important; color: #fff !important; border-color: #2563eb !important; }
button[data-status="pending"][kind="primary"]  { background-color: #ca8a04 !important; color: #fff !important; border-color: #ca8a04 !important; }
div[data-testid="stMetric"] { background-color: #f0f2f6; padding: 8px 12px !important; border-radius: 6px; text-align: center; margin-bottom: 6px !important; }
div[data-testid="stMetric"] p { margin: 0 !important; font-size: 0.8rem !important; }
div[data-testid="stMetric"] div[data-testid="stMetricValue"] { font-size: 1rem !important; }
details[data-testid="stExpander"] { margin-bottom: 0.3rem !important; }
details[data-testid="stExpander"] summary { padding: 0.3rem 0.5rem !important; font-size: 0.85rem !important; }
div[data-testid="stHorizontalBlock"] { gap: 0.3rem !important; }
/* 细滚动条 — 不遮挡，但让用户知道能滚 */
div[data-testid="element-container"]::-webkit-scrollbar { width: 4px; }
div[data-testid="element-container"]::-webkit-scrollbar-track { background: transparent; }
div[data-testid="element-container"]::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.12); border-radius: 2px; }
div[data-testid="element-container"]::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.25); }
div[data-testid="element-container"] { scrollbar-width: thin; scrollbar-color: rgba(0,0,0,0.12) transparent; }
/* B区纵向间距 */
hr { margin: 0.3rem 0 !important; }
div[data-testid="column"]:nth-of-type(2) div[data-testid="stCaptionContainer"] { margin: 0 !important; }
div[data-testid="column"]:nth-of-type(2) div[data-testid="stNotification"] { padding: 0.2rem 0.5rem !important; }
div[data-testid="column"]:nth-of-type(2) textarea { margin-bottom: -4px !important; }
div[data-testid="column"]:nth-of-type(2) details[data-testid="stExpander"] { margin: 0 !important; }
/* ═══ 护眼模式（深色主题） ═══ */
html[data-theme="dark"] body,
html[data-theme="dark"] .stApp,
html[data-theme="dark"] .main .block-container {
    background-color: #1a1a2e !important; color: #e0e0e0 !important;
}
html[data-theme="dark"] div[data-testid="stSidebar"] { background-color: #16213e !important; }
html[data-theme="dark"] div[data-testid="stMarkdown"] { color: #e0e0e0 !important; }
html[data-theme="dark"] div[data-testid="stMetric"] { background-color: #1e293b !important; color: #e0e0e0 !important; }
html[data-theme="dark"] div[data-testid="stMetric"] p { color: #94a3b8 !important; }
html[data-theme="dark"] div[data-testid="stMetric"] div[data-testid="stMetricValue"] { color: #f1f5f9 !important; }
html[data-theme="dark"] div[data-testid="stButton"] button[kind="secondary"] {
    background-color: #1e293b !important; color: #cbd5e1 !important; border-color: #334155 !important;
}
html[data-theme="dark"] div[data-testid="stButton"] button[kind="secondary"]:hover {
    background-color: #334155 !important; border-color: #6366f1 !important; color: #fff !important;
}
html[data-theme="dark"] div[data-testid="stButton"] button[kind="primary"] {
    background-color: #312e81 !important; color: #c7d2fe !important; border-color: #6366f1 !important;
}
html[data-theme="dark"] div[data-testid="stSelectbox"] > div > div { background-color: #1e293b !important; color: #e0e0e0 !important; }
html[data-theme="dark"] div[data-testid="stTextInput"] > div > div > input { background-color: #1e293b !important; color: #e0e0e0 !important; border-color: #334155 !important; }
html[data-theme="dark"] div[data-testid="stTextArea"] > div > div > textarea { background-color: #1e293b !important; color: #e0e0e0 !important; border-color: #334155 !important; }
html[data-theme="dark"] div[data-testid="stRadio"] > label { color: #e0e0e0 !important; }
html[data-theme="dark"] div[data-testid="stRadio"] > label > p { color: #e0e0e0 !important; }
html[data-theme="dark"] div[data-testid="stCheckbox"] > label { color: #e0e0e0 !important; }
html[data-theme="dark"] div[data-testid="stCheckbox"] > label > p { color: #e0e0e0 !important; }
html[data-theme="dark"] div[data-testid="stExpander"] { background-color: #1e293b !important; border-color: #334155 !important; }
html[data-theme="dark"] div[data-testid="stExpander"] summary { color: #e0e0e0 !important; }
html[data-theme="dark"] div[data-testid="stSuccess"] { background-color: #064e3b !important; }
html[data-theme="dark"] div[data-testid="stError"] { background-color: #7f1d1d !important; }
html[data-theme="dark"] div[data-testid="stWarning"] { background-color: #78350f !important; }
html[data-theme="dark"] div[data-testid="stInfo"] { background-color: #1e3a5f !important; }
html[data-theme="dark"] hr { border-color: #334155 !important; }
html[data-theme="dark"] div[data-testid="stProgress"] > div > div { background-color: #334155 !important; }
html[data-theme="dark"] div[data-testid="stProgress"] > div > div > div { background-color: #6366f1 !important; }
html[data-theme="dark"] .stTabs [data-baseweb="tab-list"] { background-color: #1e293b !important; }
html[data-theme="dark"] .stTabs [data-baseweb="tab"] { color: #94a3b8 !important; }
html[data-theme="dark"] .stTabs [aria-selected="true"] { color: #c7d2fe !important; border-bottom-color: #6366f1 !important; }
html[data-theme="dark"] details[data-testid="stExpander"] { background-color: #1e293b !important; border-color: #334155 !important; }
html[data-theme="dark"] details[data-testid="stExpander"] summary { color: #e0e0e0 !important; }
/* 深色模式下图片区域背景 */
html[data-theme="dark"] .bz-img-cell { background-color: #0f172a !important; }
html[data-theme="dark"] .bz-img-info { color: #94a3b8 !important; }
/* 深色模式下状态按钮颜色适配 */
html[data-theme="dark"] button[data-status="pass"] { background-color: #064e3b !important; color: #6ee7b7 !important; border-color: #10b981 !important; }
html[data-theme="dark"] button[data-status="fail"] { background-color: #7f1d1d !important; color: #fca5a5 !important; border-color: #ef4444 !important; }
html[data-theme="dark"] button[data-status="modified"] { background-color: #1e3a5f !important; color: #93c5fd !important; border-color: #3b82f6 !important; }
html[data-theme="dark"] button[data-status="pending"] { background-color: #713f12 !important; color: #fde047 !important; border-color: #eab308 !important; }
html[data-theme="dark"] button[data-status="pass"][kind="primary"] { background-color: #047857 !important; color: #fff !important; }
html[data-theme="dark"] button[data-status="fail"][kind="primary"] { background-color: #b91c1c !important; color: #fff !important; }
html[data-theme="dark"] button[data-status="modified"][kind="primary"] { background-color: #1d4ed8 !important; color: #fff !important; }
html[data-theme="dark"] button[data-status="pending"][kind="primary"] { background-color: #a16207 !important; color: #fff !important; }
/* ═══ B区分层布局 ═══ */
.bz-split-container { display: flex; flex-direction: column; height: 100%; position: relative; }
.bz-image-area {
    flex: 0 0 auto;
    overflow: hidden;
    min-height: 120px;
    position: relative;
}
.bz-image-area img {
    border-radius: 6px; object-fit: contain !important;
    max-height: calc(100vh * var(--bz-image-ratio, 65) / 100 - 80px) !important;
    width: auto !important; margin: 0 auto !important;
    display: block !important;
}
.bz-divider {
    height: 6px; background: #e0e0e0; cursor: ns-resize; position: relative;
    border-radius: 3px; margin: 2px 0; flex-shrink: 0; z-index: 10;
    transition: background 0.15s;
}
.bz-divider:hover, .bz-divider:active { background: #6366f1; }
.bz-image-area { transition: flex 0.05s ease-out; }
.bz-divider::after {
    content: ''; position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
    width: 20px; height: 2px; background: #999; border-radius: 1px;
}
.bz-divider:hover::after { background: #fff; }
.bz-bottom-area {
    flex: 1 1 auto; min-height: 180px; overflow-y: auto;
    padding-top: 6px; border-top: 1px solid #f0f0f0;
}
.bz-bottom-area textarea { min-height: 50px !important; }
/* ═══ 1×N 单行横向图片布局 ═══ */
.bz-linear-row {
    display: flex !important; flex-direction: row !important; flex-wrap: nowrap !important;
    gap: 4px; width: 100%; align-items: flex-start !important; justify-content: center;
    overflow: visible;
}
.bz-linear-row .bz-img-cell {
    flex: 1 1 0 !important; min-width: 0; max-width: none;
    position: relative; border-radius: 4px;
    display: inline-flex !important; flex-direction: column; align-items: center;
}
.bz-linear-row .bz-img-cell img {
    width: 100% !important; height: auto !important;
    max-height: 35vh !important;
    object-fit: contain !important; display: block; border-radius: 4px;
}
.bz-img-cell.bz-empty { display: none !important; }
.bz-img-info { display: flex; align-items: center; justify-content: space-between; padding: 2px 4px; font-size: 0.75rem; color: #666; flex-shrink: 0; }
.bz-view-selector { margin-bottom: 4px; }
/* 强制Streamlit列容器不干扰flex布局 */
div[data-testid="stHorizontalBlock"] > div { flex-shrink: 1 !important; min-width: 0 !important; }
/* C区文件列表样式 */
.bz-c-sidebar { max-height: 300px; overflow-y: auto; }
/* ═══ 顶部通栏布局 ═══ */
.bz-topbar {
    background: #f8f9fa; border-bottom: 1px solid #e0e0e0;
    padding: 6px 12px; margin-bottom: 6px; border-radius: 6px;
}
.bz-topbar-collapsed { display: none !important; }
.bz-topbar-toggle {
    position: absolute; top: 4px; right: 8px; z-index: 20;
    background: #fff; border: 1px solid #ddd; border-radius: 4px;
    padding: 2px 8px; cursor: pointer; font-size: 0.75rem; color: #666;
}
.bz-topbar-toggle:hover { border-color: #6366f1; color: #6366f1; }
/* ═══ 左侧精简文件列表 ═══ */
.bz-sidebar-mini { overflow-y: auto; max-height: calc(100vh - 120px); }
.bz-sidebar-mini .stRadio { font-size: 0.8rem !important; }
/* ═══ 布局模式切换 ═══ */
.bz-layout-switch { margin-bottom: 4px; }
</style>
"""

STATUS_BUTTON_JS = """
<script>
(function() {
    var doc = window.parent.document;
    function tagStatusButtons() {
        var btns = doc.querySelectorAll('div[data-testid="stButton"] button');
        btns.forEach(function(btn) {
            if (btn.dataset.status) return;
            var t = btn.innerText.trim();
            if (t === '合格') btn.dataset.status = 'pass';
            else if (t === '不合格') btn.dataset.status = 'fail';
            else if (t === '修改后合格') btn.dataset.status = 'modified';
            else if (t === '待定') btn.dataset.status = 'pending';
        });
    }
    tagStatusButtons();
    new MutationObserver(tagStatusButtons).observe(doc.body, {childList: true, subtree: true});
})();
</script>
"""

BZ_DRAG_JS = """
<script>
(function() {
    var doc = window.parent.document;
    function initDraggers() {
        doc.querySelectorAll('.bz-divider').forEach(function(divider) {
            if (divider.dataset.dragInit) return;
            divider.dataset.dragInit = '1';
            var container = divider.parentElement;
            var imageArea = divider.previousElementSibling;
            var bottomArea = divider.nextElementSibling;
            if (!imageArea || !bottomArea) return;
            var startY, startH;
            divider.addEventListener('mousedown', function(e) {
                e.preventDefault();
                startY = e.clientY;
                startH = imageArea.offsetHeight;
                function onMove(ev) {
                    var delta = ev.clientY - startY;
                    var newH = Math.max(120, Math.min(startH + delta, container.offsetHeight - 200));
                    imageArea.style.flex = '0 0 ' + newH + 'px';
                    var ratio = Math.round(newH / container.offsetHeight * 100);
                    doc.documentElement.style.setProperty('--bz-image-ratio', ratio);
                }
                function onUp() {
                    doc.removeEventListener('mousemove', onMove);
                    doc.removeEventListener('mouseup', onUp);
                    doc.body.style.cursor = '';
                    doc.body.style.userSelect = '';
                }
                doc.body.style.cursor = 'ns-resize';
                doc.body.style.userSelect = 'none';
                doc.addEventListener('mousemove', onMove);
                doc.addEventListener('mouseup', onUp);
            });
        });
    }
    initDraggers();
    new MutationObserver(initDraggers).observe(doc.body, {childList: true, subtree: true});
})();
</script>
"""

BZ_TOPBAR_TOGGLE_JS = """
<script>
(function() {
    var doc = window.parent.document;
    function initTopbarToggles() {
        doc.querySelectorAll('[data-bz-toggle]').forEach(function(btn) {
            if (btn.dataset.toggleInit) return;
            btn.dataset.toggleInit = '1';
            btn.addEventListener('click', function() {
                var targetId = btn.dataset.bzToggle;
                var target = doc.getElementById(targetId);
                if (!target) return;
                var isHidden = target.style.display === 'none';
                target.style.display = isHidden ? '' : 'none';
                btn.textContent = isHidden ? '▼ 收起' : '▶ 展开';
            });
        });
    }
    initTopbarToggles();
    new MutationObserver(initTopbarToggles).observe(doc.body, {childList: true, subtree: true});
})();
</script>
"""
