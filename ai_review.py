# ai_review.py — AI 初审结果呈现辅助。
# 本模块只负责“呈现/展示” AI 质检结果（质检结果.csv），
# 绝不会写入或影响实际验收结果（合格/不合格）判定。
import html
import json
import os
import difflib

ID_COLUMN = '所在文件夹(ID)'
CONCL_COLUMN = '质检结论'
JSON_COLUMN = 'JSON_Output'
QA_CANDIDATES = ("质检结果.csv", "final_report.csv")


def find_qa_path(root):
    """在数据根目录中查找 AI 初审结果文件，找不到返回 None。"""
    if not root:
        return None
    for cand in QA_CANDIDATES:
        p = os.path.join(root, cand)
        if os.path.exists(p):
            return p
    return None


def ensure_default_qa(state):
    """确保 AI 初审结果已加载（仅当当前未加载任何质检表时自动读取默认文件）。
    手动上传的质检表不会被覆盖。返回当前数据源名称。"""
    if state.get('qa_df') is not None and not getattr(state.get('qa_df'), 'empty', True):
        return state.get('qa_source', '')
    qa_path = find_qa_path(state.get('root_path', ''))
    if not qa_path:
        return ''
    from disk_io import load_qa_report
    state['qa_df'] = load_qa_report(qa_path)
    source = f"默认文件: {os.path.basename(qa_path)}"
    state['qa_source'] = source
    return source


def parse_json_output(raw):
    """安全解析 JSON_Output 字段，失败时返回 {}。"""
    if raw is None:
        return {}
    raw = str(raw).strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    # 兼容个别编辑器/Excel 写入时把 JSON 内引号转义为双引号的情况
    try:
        return json.loads(raw.replace('""', '"'))
    except Exception:
        pass
    return {}


def normalize_conclusion(val):
    """归一化质检结论，兼容新旧两种格式。
    返回 (level, label)，level ∈ pass/fail/modified/pending/warn/unknown。
    旧格式: true / false；新格式: 合格 / 不合格 / 修改后合格 / 待定 / 修正失败。
    """
    if val is None:
        return 'unknown', '未知'
    try:
        if val != val:  # numpy nan
            return 'unknown', '未知'
    except Exception:
        pass
    v = str(val).strip().lower()
    if v in ('true', 'yes', 'pass', 'ok', 'good', '合格', '通过', '是'):
        return 'pass', '通过'
    if v in ('false', 'no', 'fail', 'ng', 'bad', '不合格', '不通过', '否'):
        return 'fail', '不通过'
    if v in ('修改后合格', '修改合格', '修正通过', 'modified'):
        return 'modified', '修改后合格'
    if v in ('待定', 'pending', '未定'):
        return 'pending', '待定'
    if v in ('修正失败', '格式异常', 'error'):
        return 'warn', '修正失败'
    return 'unknown', str(val)


def _text(v):
    """把 CSV 单元格值安全转为字符串，NaN/None -> ''。"""
    if v is None:
        return ''
    try:
        if v != v:  # numpy nan
            return ''
    except Exception:
        pass
    return str(v)


def get_ai_record(df, group_id):
    """从 QA DataFrame 中查找当前 ID 对应的初审记录（字典）。未找到返回 None。"""
    if df is None or getattr(df, 'empty', True):
        return None
    if ID_COLUMN not in df.columns:
        return None
    matched = df[df[ID_COLUMN].astype(str) == str(group_id)]
    if matched.empty:
        return None
    row = matched.iloc[-1]
    return {k: _text(v) for k, v in row.items()} if hasattr(row, 'items') else None


def build_ai_badge_map(df, ids):
    """为编号栏一次性构建 {id: level} 映射，避免逐条过滤。"""
    if df is None or getattr(df, 'empty', True):
        return {}
    if ID_COLUMN not in df.columns or CONCL_COLUMN not in df.columns:
        return {}
    id_set = {str(i) for i in ids}
    sub = df[df[ID_COLUMN].astype(str).isin(id_set)]
    mapping = {}
    for _, row in sub.iterrows():
        level, _ = normalize_conclusion(row.get(CONCL_COLUMN))
        mapping[str(row[ID_COLUMN])] = level
    return mapping


# ---- 文本差异高亮 ----

def _esc(s):
    return html.escape(s, quote=True).replace('\n', '<br>')


def build_diff_html(orig, corr):
    """根据原文与 AI 修正文计算差异。
    返回 (orig_html, corr_html, change_count, has_mark)：
      orig_html: 原文，被修改/删除的片段用 <mark>/<del> 标记（红色）。
      corr_html: 修正文，新增/修改的片段用 <mark> 标记（绿色）。
    """
    orig = orig or ''
    corr = corr or ''
    if not orig or not corr or orig == corr:
        return _esc(orig), _esc(corr), 0, False

    o_parts = []
    c_parts = []
    change_count = 0
    has_mark = False
    matcher = difflib.SequenceMatcher(None, orig, corr, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        a = orig[i1:i2]
        b = corr[j1:j2]
        if tag == 'equal':
            o_parts.append(_esc(a))
            c_parts.append(_esc(a))
        elif tag == 'replace':
            change_count += 1
            has_mark = True
            o_parts.append(
                f'<mark title="AI 建议改为：{_esc(b)}">{_esc(a)}</mark>'
                if b else f'<mark>{_esc(a)}</mark>'
            )
            if b:
                c_parts.append(f'<mark title="原文为：{_esc(a)}">{_esc(b)}</mark>')
        elif tag == 'delete':
            change_count += 1
            has_mark = True
            o_parts.append(f'<del title="AI 建议删除">{_esc(a)}</del>')
        elif tag == 'insert':
            change_count += 1
            if b:
                c_parts.append(f'<mark title="AI 建议补入">{_esc(b)}</mark>')
    return ''.join(o_parts), ''.join(c_parts), change_count, has_mark


HIGHLIGHT_CSS = """
<style>
.ai-hl-box { border:1px solid #ddd; border-radius:8px; padding:8px 12px; margin:4px 0 10px; background:#fafafa; }
.ai-hl-title { font-size:0.85rem; font-weight:600; margin-bottom:5px; color:#8b5cf6; }
.ai-hl-legend { font-size:0.78rem; color:#666; margin-bottom:7px; line-height:1.9; }
.ai-hl-pair { display:flex; gap:10px; flex-wrap:wrap; }
.ai-hl-orig, .ai-hl-corr { flex:1 1 320px; font-size:0.9rem; line-height:1.75; word-break:break-all; }
.ai-hl-orig { background:#fff5f5; border:1px solid #ffd6d6; border-radius:6px; padding:7px 9px; }
.ai-hl-corr { background:#f0fdf4; border:1px solid #c8ecd0; border-radius:6px; padding:7px 9px; }
.ai-hl-label { display:block; font-size:0.72rem; color:#888; margin-bottom:4px; }
.ai-hl-orig mark { background:#ffc4c4; color:#b71c1c; border-radius:3px; padding:0 2px; }
.ai-hl-orig del { background:#ffd0d0; color:#b71c1c; text-decoration:line-through; border-radius:3px; padding:0 3px; }
.ai-hl-corr mark { background:#b7efc5; color:#14532d; border-radius:3px; padding:0 2px; }
</style>
"""


def build_highlight_blocks(zpairs):
    """zpairs = [(标题, 原文, 修正文), ...]
    返回渲染用的 HTML 块列表（含差异的才返回）。"""
    blocks = []
    for title, orig, corr in zpairs:
        orig = _text(orig)
        corr = _text(corr)
        if not (orig and corr):
            continue
        if orig.strip() == corr.strip():
            continue
        o_html, c_html, n, has_mark = build_diff_html(orig, corr)
        if not has_mark:
            continue
        blocks.append(
            f'<div class="ai-hl-box">'
            f'<div class="ai-hl-title">🤖 {title}　AI 初审标注（检测到 {n} 处差异）</div>'
            f'<div class="ai-hl-legend">'
            f'<span style="background:#ffc4c4;color:#b71c1c;border-radius:3px;padding:0 5px">标红 / 删除线</span> = AI 认为有误的原文片段　'
            f'<span style="background:#b7efc5;color:#14532d;border-radius:3px;padding:0 5px">标绿</span> = AI 建议的修正内容（悬停可看对照）'
            f'</div>'
            f'<div class="ai-hl-pair">'
            f'<div class="ai-hl-orig"><span class="ai-hl-label">原文（AI 认为有误的片段已标红）</span>{o_html}</div>'
            f'<div class="ai-hl-corr"><span class="ai-hl-label">AI 修正建议</span>{c_html}</div>'
            f'</div></div>'
        )
    return blocks


def split_reasons(reason):
    """按中文/英文分号拆分原因列表。"""
    if not reason:
        return []
    parts = [p.strip() for p in str(reason).replace('；', ';').split(';')]
    return [p for p in parts if p]


def collect_json_issues(json_data):
    """从 JSON_Output 提取问题明细列表。"""
    if not isinstance(json_data, dict):
        return []
    issues = []
    for key in ('poster_issues', 'instruction_issues'):
        for item in json_data.get(key) or []:
            if isinstance(item, dict):
                issues.append(item)
    return issues