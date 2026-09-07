# search_service.py — 快速查询/定位服务


def search_items(data_groups, search_id, search_tag, selected_tags, status_map=None):
    """在当前质检数据中搜索匹配的条目。

    Args:
        data_groups: 当前已加载的数据组列表
        search_id: 编号查询字符串（精确匹配），为空则不按编号筛选
        search_tag: 错误标签查询字符串，为空或"全部"则不按标签筛选
        selected_tags: dict {item_id: [tag1, tag2, ...]}
        status_map: dict {item_id: status_string}，可选

    Returns:
        list of dict: [{"id": ..., "tags": [...], "status": ...}, ...]
    """
    if not data_groups:
        return []

    results = []
    filter_by_id = bool(search_id and search_id.strip())
    filter_by_tag = bool(search_tag and search_tag != "全部")

    if not filter_by_id and not filter_by_tag:
        return []

    if status_map is None:
        status_map = {}

    for group in data_groups:
        item_id = str(group['id'])

        if filter_by_id and item_id != search_id.strip():
            continue

        if filter_by_tag:
            item_tags = selected_tags.get(item_id, [])
            if search_tag not in item_tags:
                continue

        results.append({
            "id": item_id,
            "tags": selected_tags.get(item_id, []),
            "status": status_map.get(item_id, ""),
        })

    return results
