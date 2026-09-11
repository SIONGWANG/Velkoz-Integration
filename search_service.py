# search_service.py — 快速查询/定位服务


def _parse_csv_tags(tag_str):
    """从 CSV 标签字段解析出标签列表（仅标签名，不含备注）"""
    if not tag_str or str(tag_str) == 'nan':
        return []
    return [t.strip() for t in str(tag_str).split(';') if t.strip()]


def search_items(data_groups, search_id, search_tag, search_text, df=None, status_map=None):
    """在当前质检数据中搜索匹配的条目。

    Args:
        data_groups: 当前已加载的数据组列表
        search_id: 编号查询字符串（精确匹配），为空则不按编号筛选
        search_tag: 错误标签查询字符串，为空或"全部"则不按标签筛选
        search_text: 自由文本输入（模糊匹配编号或标签），为空则不启用
        df: CSV DataFrame（直接从 CSV 搜索标签，不依赖 selected_tags）
        status_map: dict {item_id: status_string}，可选

    Returns:
        list of dict: [{"id": ..., "tags": [...], "status": ...}, ...]
    """
    if not data_groups:
        return []

    results = []
    filter_by_id = bool(search_id and search_id.strip())
    filter_by_tag = bool(search_tag and search_tag != "全部")
    filter_by_text = bool(search_text and search_text.strip())

    if not filter_by_id and not filter_by_tag and not filter_by_text:
        return []

    if status_map is None:
        status_map = {}

    # 构建 CSV 标签 / 备注查找表：{item_id: [...]} / {item_id: notes}
    csv_tags_map = {}
    csv_notes_map = {}
    if df is not None and not df.empty and '图片ID' in df.columns:
        has_tags = '标签' in df.columns
        has_notes = '备注' in df.columns
        for _, row in df.iterrows():
            pid = str(row.get('图片ID', '')).strip()
            if not pid:
                continue
            if has_tags:
                csv_tags_map[pid] = _parse_csv_tags(row.get('标签', ''))
            if has_notes:
                note = row.get('备注', '')
                csv_notes_map[pid] = '' if note is None or str(note) == 'nan' else str(note)

    search_text_lower = search_text.strip().lower() if filter_by_text else ""

    for group in data_groups:
        item_id = str(group['id'])

        if filter_by_id and item_id != search_id.strip():
            continue

        # 标签匹配：按分隔符拆分后做完整标签匹配（多标签任一命中）
        if filter_by_tag:
            csv_tags = csv_tags_map.get(item_id, [])
            if search_tag not in csv_tags:
                continue

        # 自由文本匹配：模糊匹配编号、标签、备注（备注也要能搜到）
        if filter_by_text:
            id_match = search_text_lower in item_id.lower()
            csv_tags = csv_tags_map.get(item_id, [])
            tag_match = any(search_text_lower in t.lower() for t in csv_tags)
            notes_match = search_text_lower in csv_notes_map.get(item_id, '').lower()
            if not id_match and not tag_match and not notes_match:
                continue

        results.append({
            "id": item_id,
            "tags": csv_tags_map.get(item_id, []),
            "status": status_map.get(item_id, ""),
        })

    return results
