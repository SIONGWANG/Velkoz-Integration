"""导出模块 — 纯计算逻辑，不依赖 Streamlit UI"""

import os
import shutil
import logging


def get_missing_ids(all_loaded_ids, csv_file):
    """返回 (missing_ids, total) — 在 CSV 中找不到的 ID 列表"""
    saved_ids = []
    if os.path.exists(csv_file):
        try:
            import pandas as pd
            df_check = pd.read_csv(csv_file, dtype={'图片ID': str}, encoding='utf-8-sig')
            saved_ids = df_check['图片ID'].tolist()
        except Exception:
            logging.warning("get_missing_ids: CSV 读取失败: %s", csv_file)
    missing_ids = [uid for uid in all_loaded_ids if uid not in saved_ids]
    return missing_ids, len(all_loaded_ids)


def collect_reject_folders(reject_df, target_dir):
    """将不合格/待定文件夹归集到 target_dir（先复制再删除源），返回 (count, missing, error_msg)"""
    if reject_df.empty:
        return 0, 0, "⚠️ 记录中没有任何不合格/待定数据。"
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    count = 0
    missing = 0
    errors = []
    for _, row in reject_df.iterrows():
        src_path = row['路径']
        dst_path = os.path.join(target_dir, str(row['图片ID']))
        if os.path.exists(src_path):
            try:
                if os.path.exists(dst_path):
                    shutil.rmtree(dst_path)
                shutil.copytree(src_path, dst_path)
                shutil.rmtree(src_path)
                count += 1
            except Exception as e:
                errors.append(str(row['图片ID']))
                logging.warning("归集失败 %s → %s: %s", src_path, dst_path, e)
        else:
            missing += 1

    msg = f"✅ 已归集 {count} 组数据至：\n{target_dir}"
    if errors:
        msg += f"\n⚠️ {len(errors)} 条归集失败（源数据已保留），详见日志"
    if missing > 0:
        msg += f"\n⚠️ {missing} 条记录源路径不存在（可能已归集过），已跳过"
    return count, missing, msg


def copy_qualified_folders(qualified_df, target_dir, id_to_group=None):
    """将合格文件夹复制到 target_dir，返回 (count, missing, error_ids)"""
    if qualified_df.empty:
        return 0, 0, []
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    count = 0
    missing = 0
    error_ids = []
    ignored = {'Thumbs.db', 'desktop.ini', '.DS_Store'}

    for _, row in qualified_df.iterrows():
        folder_id = str(row['图片ID'])
        if id_to_group and folder_id in id_to_group:
            src_path = id_to_group[folder_id]['root']
        else:
            src_path = row['路径']
        dst_path = os.path.join(target_dir, folder_id)
        if os.path.exists(src_path):
            try:
                if os.path.exists(dst_path):
                    shutil.rmtree(dst_path)
                shutil.copytree(src_path, dst_path, ignore=shutil.ignore_patterns(*ignored))

                src_norm = {f.lower() for f in os.listdir(src_path)} - {i.lower() for i in ignored}
                dst_norm = {f.lower() for f in os.listdir(dst_path)} - {i.lower() for i in ignored}
                if src_norm != dst_norm:
                    missing_files = src_norm - dst_norm
                    extra_files = dst_norm - src_norm
                    detail = []
                    if missing_files:
                        detail.append(f"缺失: {', '.join(sorted(missing_files))}")
                    if extra_files:
                        detail.append(f"多出: {', '.join(sorted(extra_files))}")
                    error_ids.append(f"{folder_id}(文件不一致: {'; '.join(detail)})")
                else:
                    count += 1
            except Exception as e:
                error_ids.append(f"{folder_id}({str(e)})")
        else:
            missing += 1

    return count, missing, error_ids


def verify_exported_data(expected_ids, export_dir, id_to_group, df):
    """验证导出结果，返回 {"success": bool, "message": str}"""
    if not export_dir or not os.path.exists(export_dir):
        return {"success": False, "message": f"❌ 导出文件夹不存在：{export_dir}"}

    exported_folders = [d for d in os.listdir(export_dir) if os.path.isdir(os.path.join(export_dir, d))]
    exported_ids = set(exported_folders)

    missing_in_folder = expected_ids - exported_ids
    extra_in_folder = exported_ids - expected_ids

    ignored_lower = {'thumbs.db', 'desktop.ini', '.ds_store'}
    missing_files = []
    file_mismatches = []

    for folder_id in exported_folders:
        dst_path = os.path.join(export_dir, folder_id)
        try:
            dst_all = set(os.listdir(dst_path))
        except Exception:
            missing_files.append(folder_id)
            continue
        dst_norm = {f.lower() for f in dst_all} - ignored_lower

        has_jpg = any(f.lower().endswith(('.jpg', '.png', '.jpeg')) for f in dst_norm)
        if not has_jpg:
            missing_files.append(folder_id)
            continue

        group = id_to_group.get(folder_id)
        if group:
            src_path = group['root']
        else:
            record = df[df['图片ID'].astype(str) == str(folder_id)]
            if not record.empty:
                src_path = str(record.iloc[-1]['路径'])
            else:
                continue

        if not os.path.exists(src_path):
            file_mismatches.append(f"{folder_id}(源文件夹不存在: {src_path})")
            continue

        try:
            src_all = set(os.listdir(src_path))
        except Exception:
            file_mismatches.append(f"{folder_id}(无法读取源文件夹)")
            continue
        src_norm = {f.lower() for f in src_all} - ignored_lower

        missing_in_dst = src_norm - dst_norm
        extra_in_dst = dst_norm - src_norm

        if missing_in_dst or extra_in_dst:
            parts = [folder_id]
            if missing_in_dst:
                parts.append(f"缺失{len(missing_in_dst)}个: {', '.join(sorted(missing_in_dst))}")
            if extra_in_dst:
                parts.append(f"多出{len(extra_in_dst)}个: {', '.join(sorted(extra_in_dst))}")
            file_mismatches.append(" | ".join(parts))

    message_parts = []
    has_error = False

    if not missing_in_folder and not extra_in_folder and not missing_files and not file_mismatches:
        message_parts.append(f"✅ 共 {len(expected_ids)} 条，文件逐一对齐，无差异")
        message_parts.append(f"✓ {len(expected_ids)}个ID完全匹配，文件数量一致")
    else:
        has_error = True
        if missing_in_folder:
            message_parts.append(f"❌ CSV中有但导出文件夹缺失({len(missing_in_folder)}个): {', '.join(sorted(missing_in_folder))}")
        if extra_in_folder:
            message_parts.append(f"❌ 导出文件夹多出({len(extra_in_folder)}个): {', '.join(sorted(extra_in_folder))}")
        if missing_files:
            message_parts.append(f"❌ 无图片文件的文件夹({len(missing_files)}个): {', '.join(missing_files)}")
        if file_mismatches:
            message_parts.append(f"⚠️ 文件数量不一致({len(file_mismatches)}个):")
            for fm in file_mismatches:
                message_parts.append(f"  - {fm}")

    return {"success": not has_error, "message": "\n".join(message_parts)}
