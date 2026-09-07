# excel_importer.py — 质检记录恢复：Excel 解析 / 匹配 / 预览 / 恢复
import os
import pandas as pd
import datetime

VALID_RESULTS = {"合格", "不合格", "修改后合格", "待定"}
REQUIRED_COL_ID = "图片ID"
CSV_COLUMNS = ["姓名", "图片ID", "一级", "二级", "结果", "备注", "标签", "错误截图", "质检时间", "路径"]


def parse_excel(uploaded_file):
    """解析上传的 Excel，返回 (df, error_msg)"""
    try:
        df = pd.read_excel(uploaded_file, dtype=str, engine="openpyxl")
    except ImportError:
        return None, "缺少 openpyxl 库，请运行: pip install openpyxl"
    except Exception as e:
        return None, f"Excel 读取失败: {e}"

    if df.empty:
        return None, "Excel 为空"

    col_lower = {c.strip().lower(): c for c in df.columns}
    id_col = col_lower.get("图片id") or col_lower.get("图片id") or col_lower.get("imageid") or col_lower.get("id")
    if not id_col:
        id_col = col_lower.get("图片id")
    if not id_col:
        for orig, low in col_lower.items():
            if "id" in low.lower() or "图片" in low:
                id_col = orig
                break
    if not id_col:
        return None, "Excel 中未找到唯一ID列（图片ID）"

    if id_col != "图片ID":
        df = df.rename(columns={id_col: "图片ID"})

    df["图片ID"] = df["图片ID"].astype(str).str.strip()
    df = df[df["图片ID"].notna() & (df["图片ID"] != "") & (df["图片ID"] != "nan")]
    df = df.drop_duplicates(subset=["图片ID"], keep="last").reset_index(drop=True)

    if "结果" in df.columns:
        df["结果"] = df["结果"].astype(str).str.strip()
    if "标签" in df.columns:
        df["标签"] = df["标签"].astype(str).str.strip()
    if "备注" in df.columns:
        df["备注"] = df["备注"].astype(str).str.strip()
    if "一级" in df.columns:
        df["一级"] = df["一级"].astype(str).str.strip()
    if "二级" in df.columns:
        df["二级"] = df["二级"].astype(str).str.strip()
    if "姓名" in df.columns:
        df["姓名"] = df["姓名"].astype(str).str.strip()

    return df, None


def build_import_preview(df_excel, data_groups, current_csv_df=None):
    """构建匹配预览，返回 preview dict"""
    current_ids = [str(g["id"]) for g in data_groups]
    current_id_set = set(current_ids)
    current_id_counts = {}
    for gid in current_ids:
        current_id_counts[gid] = current_id_counts.get(gid, 0) + 1

    excel_ids = df_excel["图片ID"].tolist()
    excel_id_set = set(excel_ids)
    excel_id_counts = {}
    for eid in excel_ids:
        excel_id_counts[eid] = excel_id_counts.get(eid, 0) + 1

    excel_duplicate_ids = [eid for eid, cnt in excel_id_counts.items() if cnt > 1]
    current_duplicate_ids = [cid for cid, cnt in current_id_counts.items() if cnt > 1]

    excel_lookup = {}
    for _, row in df_excel.iterrows():
        excel_lookup[str(row["图片ID"])] = row.to_dict()

    matched = []
    unmatched_current = []
    orphan_excel = []
    duplicates = []
    anomalies = []

    current_csv_lookup = {}
    if current_csv_df is not None and not current_csv_df.empty and "图片ID" in current_csv_df.columns:
        for _, row in current_csv_df.iterrows():
            pid = str(row.get("图片ID", "")).strip()
            if pid:
                current_csv_lookup[pid] = row.to_dict()

    for cid in current_ids:
        if cid in excel_duplicate_ids:
            duplicates.append({"id": cid, "reason": "Excel中存在重复ID"})
            continue
        if cid not in excel_id_set:
            unmatched_current.append({"id": cid})
            continue
        excel_row = excel_lookup[cid]
        result_raw = excel_row.get("结果", "")
        result_val = str(result_raw).strip() if pd.notna(result_raw) else ""
        if result_val and result_val not in VALID_RESULTS and result_val != "nan":
            anomalies.append({"id": cid, "reason": f"质检结果无法识别: {result_val}"})
            continue
        if not result_val or result_val == "nan":
            anomalies.append({"id": cid, "reason": "质检结果为空"})
            continue

        existing_record = current_csv_lookup.get(cid)
        has_existing = existing_record is not None and pd.notna(existing_record.get("结果", "")) and str(existing_record.get("结果", "")).strip() != ""

        matched.append({
            "id": cid,
            "excel_row": excel_row,
            "overwrite": has_existing,
            "existing_result": str(existing_record.get("结果", "")).strip() if has_existing else "",
        })

    for eid in excel_ids:
        if eid not in current_id_set and eid not in excel_duplicate_ids:
            orphan_excel.append({"id": eid, "result": str(excel_lookup[eid].get("结果", "")).strip()})

    overwrite_count = sum(1 for m in matched if m["overwrite"])
    new_count = sum(1 for m in matched if not m["overwrite"])

    return {
        "matched": matched,
        "unmatched_current": unmatched_current,
        "orphan_excel": orphan_excel,
        "duplicates": duplicates,
        "anomalies": anomalies,
        "total_current": len(current_ids),
        "total_excel": len(excel_ids),
        "total_matched": len(matched),
        "new_count": new_count,
        "overwrite_count": overwrite_count,
        "total_unmatched_current": len(unmatched_current),
        "total_orphan": len(orphan_excel),
        "total_duplicates": len(duplicates),
        "total_anomalies": len(anomalies),
    }


def apply_restore(preview, data_groups, current_csv_df=None, csv_path=None):
    """执行恢复：将匹配结果写入当前 CSV 并返回操作摘要"""
    if csv_path is None:
        return None, "未指定 CSV 路径"

    csv_columns_backup = CSV_COLUMNS.copy()

    if current_csv_df is None or current_csv_df.empty:
        existing_df = pd.DataFrame(columns=csv_columns_backup)
    else:
        existing_df = current_csv_df.copy()
        for col in csv_columns_backup:
            if col not in existing_df.columns:
                existing_df[col] = ""

    for item in preview["matched"]:
        eid = item["id"]
        row = item["excel_row"]
        record = {}
        for col in csv_columns_backup:
            val = row.get(col, "")
            if pd.isna(val):
                val = ""
            record[col] = str(val).strip() if val else ""
        record["图片ID"] = eid

        if not record.get("质检时间"):
            record["质检时间"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        mask = existing_df["图片ID"] == eid
        if mask.any():
            for col in csv_columns_backup:
                existing_df.loc[mask, col] = record.get(col, "")
        else:
            new_row = pd.DataFrame([record], columns=csv_columns_backup)
            existing_df = pd.concat([existing_df, new_row], ignore_index=True)

    existing_df = existing_df.drop_duplicates(subset=["图片ID"], keep="last").reset_index(drop=True)

    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    tmp_file = csv_path + ".tmp"
    bak_file = csv_path + ".bak"
    try:
        with open(tmp_file, "w", encoding="utf-8-sig", newline="") as f:
            existing_df.to_csv(f, index=False)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(csv_path):
            try:
                os.replace(csv_path, bak_file)
            except OSError:
                pass
        for attempt in range(5):
            try:
                os.replace(tmp_file, csv_path)
                break
            except PermissionError:
                import time
                time.sleep(0.3 * (attempt + 1))
    except Exception as e:
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        raise e

    summary = {
        "file_name": "",
        "total_current": preview["total_current"],
        "total_excel": preview["total_excel"],
        "restored": preview["total_matched"],
        "new_count": preview["new_count"],
        "overwrite_count": preview["overwrite_count"],
        "unmatched_current": preview["total_unmatched_current"],
        "orphan": preview["total_orphan"],
        "duplicates": preview["total_duplicates"],
        "anomalies": preview["total_anomalies"],
    }
    return summary, None
