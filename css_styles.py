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
/* 紧凑 AI 初审：修复条不撑高页面 */
.ai-fix-row { margin: 2px 0 !important; }
div[data-testid="stPopover"] > button {
    padding: 0.2rem 0.5rem !important; min-height: 24px !important;
    font-size: 0.78rem !important; border-radius: 4px !important;
    border: 1px solid #ddd !important; background-color: #fafafa !important; color: #334155 !important;
}
div[data-testid="stPopover"] > button:hover { border-color: #6366f1 !important; background-color: #eef2ff !important; color: #3730a3 !important; }
div[data-testid="stPopover"] section { padding: 0.4rem 0.6rem !important; }
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


def build_textarea_auto_js(mode="auto", max_height=400, fixed_height=68):
    """生成文本框高度脚本。
    mode:
      'auto'  — 高度随内容自适应（上限 max_height），超过上限出现滚动条
      'fixed' — 固定高度 fixed_height
    """
    mode_js = "auto" if mode == "auto" else "fixed"
    return f"""
<script>
(function() {{
    var doc = window.parent.document;
    var MODE = '{mode_js}';
    var MAX_H = {int(max_height)};
    var FIXED_H = {int(fixed_height)};

    function fit(ta) {{
        if (MODE === 'fixed') {{
            ta.style.height = FIXED_H + 'px';
            ta.style.overflowY = FIXED_H < ta.scrollHeight ? 'auto' : 'hidden';
            return;
        }}
        ta.style.height = 'auto';
        var h = ta.scrollHeight;
        if (h > MAX_H) {{
            ta.style.height = MAX_H + 'px';
            ta.style.overflowY = 'auto';
        }} else {{
            ta.style.height = h + 'px';
            ta.style.overflowY = 'hidden';
        }}
    }}

    function attach() {{
        var list = doc.querySelectorAll('div[data-testid="stTextArea"] textarea');
        for (var i = 0; i < list.length; i++) {{
            var ta = list[i];
            if (!ta.dataset.fitBound) {{
                ta.dataset.fitBound = '1';
                ta.addEventListener('input', function () {{ fit(this); }});
            }}
            fit(ta);
        }}
    }}

    attach();

    if (window.parent._taFitObserver) {{ window.parent._taFitObserver.disconnect(); }}
    window.parent._taFitObserver = new MutationObserver(function () {{ attach(); }});
    window.parent._taFitObserver.observe(doc.body, {{childList: true, subtree: true}});
}})();
</script>
"""

