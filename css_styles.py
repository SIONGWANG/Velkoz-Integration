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

/* ── A区：合格率卡片（v1.24.5） ── */
.vk-stat-card {
    border: 1px solid rgba(128,128,128,.20);
    border-radius: 10px;
    padding: 9px 10px 8px;
    background: rgba(128,128,128,.06);
    margin-bottom: 8px;
}
.vk-stat-head { display: flex; align-items: baseline; justify-content: space-between; gap: 6px; }
.vk-stat-head .vk-label { font-size: .74rem; opacity: .72; letter-spacing: .02em; }
.vk-stat-head .vk-rate { font-size: 1.32rem; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; }
.vk-stack {
    display: flex; height: 9px; border-radius: 5px; overflow: hidden;
    background: rgba(128,128,128,.20); margin: 8px 0 7px;
}
.vk-stack > span { display: block; height: 100%; transition: width .35s ease; }
.vk-stack > span + span { box-shadow: inset 1px 0 0 rgba(255,255,255,.22); }
.vk-stat-sub { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 2px 6px; font-size: .72rem; opacity: .78; }
.vk-stat-sub b { font-variant-numeric: tabular-nums; font-weight: 600; }
.vk-stat-note { font-size: .66rem; opacity: .55; margin-top: 3px; line-height: 1.35; }

/* ── A区：3×2 状态筛选按钮（st.button 带 key 后出现 st-key-* 类） ── */
div[class*="st-key-fbtn_"] button {
    position: relative;
    width: 100%;
    min-height: 44px;
    padding: 5px 4px 5px 14px !important;
    border-radius: 8px !important;
    border: 1px solid rgba(128,128,128,.26) !important;
    background: rgba(128,128,128,.07) !important;
    color: inherit !important;
    transition: background .2s ease, border-color .2s ease, color .2s ease;
}
div[class*="st-key-fbtn_"] button p {
    white-space: pre-line !important;
    text-align: center !important;
    font-size: .70rem !important;
    line-height: 1.22 !important;
    margin: 0 !important;
    font-variant-numeric: tabular-nums;
}
div[class*="st-key-fbtn_"] button p strong {
    font-size: .80rem !important;
    font-weight: 700 !important;
    line-height: 1.15 !important;
}
div[class*="st-key-fbtn_"] button::before {
    content: "";
    position: absolute;
    left: 6px; top: 50%;
    width: 6px; height: 6px;
    border-radius: 50%;
    transform: translateY(-50%);
    transition: opacity .2s ease;
}
div[class*="st-key-fbtn_"] button:hover { border-color: rgba(128,128,128,.5) !important; }

div.st-key-fbtn_all button::before { background: #475569; }
div.st-key-fbtn_unchecked button::before { background: #9ca3af; }
div.st-key-fbtn_pass button::before { background: #22c55e; }
div.st-key-fbtn_modified button::before { background: #3b82f6; }
div.st-key-fbtn_fail button::before { background: #ef4444; }
div.st-key-fbtn_hold button::before { background: #f59e0b; }

div[class*="st-key-fbtn_"] button[data-testid="stBaseButton-primary"] {
    color: #fff !important;
    border-color: transparent !important;
}
div[class*="st-key-fbtn_"] button[data-testid="stBaseButton-primary"]::before { opacity: 0; }
div.st-key-fbtn_all button[data-testid="stBaseButton-primary"] { background: #475569 !important; }
div.st-key-fbtn_unchecked button[data-testid="stBaseButton-primary"] { background: #9ca3af !important; }
div.st-key-fbtn_pass button[data-testid="stBaseButton-primary"] { background: #22c55e !important; }
div.st-key-fbtn_modified button[data-testid="stBaseButton-primary"] { background: #3b82f6 !important; }
div.st-key-fbtn_fail button[data-testid="stBaseButton-primary"] { background: #ef4444 !important; }
div.st-key-fbtn_hold button[data-testid="stBaseButton-primary"] { background: #f59e0b !important; }

/* ── 自定义读取规则：说明与示例 ── */
.vk-rule-tip {
    border-left: 3px solid #409eff;
    background: rgba(64,158,255,.08);
    border-radius: 6px;
    padding: 7px 9px;
    font-size: .76rem;
    line-height: 1.55;
    margin: 2px 0 8px;
}
.vk-rule-tip code {
    background: rgba(128,128,128,.14);
    padding: 0 3px;
    border-radius: 3px;
}
.vk-rule-sample {
    font-family: Consolas, "Courier New", monospace;
    font-size: .78rem;
    background: rgba(128,128,128,.12);
    border-radius: 5px;
    padding: 4px 7px;
    display: inline-block;
    margin: 2px 0;
    word-break: break-all;
}
.vk-rule-hero {
    border: 1px solid rgba(64,158,255,.25);
    background: linear-gradient(135deg, rgba(64,158,255,.10), rgba(64,158,255,.03));
    border-radius: 10px;
    padding: 10px 11px;
    margin: 2px 0 10px;
}
.vk-rule-hero-title { font-size: .80rem; font-weight: 600; line-height: 1.45; }
.vk-rule-flow { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; margin: 8px 0 6px; }
.vk-rule-flow-text { font-size: .70rem; opacity: .75; line-height: 1.55; margin: 6px 0 4px; word-break: break-word; }
.vk-flow-node {
    font-size: .72rem; padding: 3px 7px; border-radius: 999px;
    background: rgba(64,158,255,.14); color: #2563eb; white-space: nowrap;
}
.vk-flow-arrow { font-size: .72rem; opacity: .55; }
.vk-rule-hero-note { font-size: .68rem; opacity: .65; line-height: 1.5; }

.vk-rule-sec { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 4px 0 6px; }
.vk-rule-sec-num {
    display: inline-flex; align-items: center; justify-content: center;
    width: 18px; height: 18px; border-radius: 50%;
    background: #409eff; color: #fff; font-size: .68rem; font-weight: 700;
}
.vk-rule-sec-title { font-size: .86rem; font-weight: 700; }
.vk-rule-sec-hint { font-size: .68rem; opacity: .6; }

.vk-rule-chips { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin: 2px 0 4px; }
.vk-chip {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: .70rem; line-height: 1.3;
    padding: 3px 7px; border-radius: 6px;
    background: rgba(128,128,128,.10);
    border: 1px solid rgba(128,128,128,.18);
    word-break: break-all;
}
.vk-chip b {
    display: inline-flex; align-items: center; justify-content: center;
    width: 14px; height: 14px; border-radius: 50%;
    background: rgba(128,128,128,.22); font-size: .62rem; font-weight: 700;
}
.vk-chip-plus { font-size: .70rem; opacity: .45; }
.vk-rule-regex {
    font-family: Consolas, "Courier New", monospace;
    font-size: .68rem; opacity: .6;
    margin: 2px 0 8px; word-break: break-all;
}

.vk-blk-head { display: flex; align-items: baseline; justify-content: space-between; gap: 6px; margin-bottom: 2px; }
.vk-blk-no { font-size: .76rem; font-weight: 700; white-space: nowrap; }
.vk-blk-desc {
    flex: 1 1 auto; min-width: 0; font-size: .70rem; opacity: .62;
    text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.vk-slot-preview {
    font-size: .70rem; opacity: .72; line-height: 1.5;
    background: rgba(128,128,128,.08); border-radius: 5px;
    padding: 4px 7px; margin-top: 3px; word-break: break-all;
}
.vk-slot-preview code { background: rgba(128,128,128,.16); padding: 0 3px; border-radius: 3px; }
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

