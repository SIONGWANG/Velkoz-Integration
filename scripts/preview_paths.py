"""只读路径解析预览（v1.25.11）。

用法：

    python\python.exe scripts\preview_paths.py "D:\Testdata\02-待验数据" ^
        --reviewer 王少雄 --reviewer 刘斯畅 --reviewer 朱代 ^
        --team 河南铸星 --team 醒醒文化 --team 驻马店

只读取目录名，不写数据库、不创建/修改/删除任何文件。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.workspace.path_schema import detect_batches, summarize  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读预览批次路径解析结果")
    parser.add_argument("root", help="待验数据或申请终验根目录")
    parser.add_argument("--reviewer", action="append", default=[], help="验收员名称，可重复")
    parser.add_argument("--team", action="append", default=[], help="团队名称，可重复")
    parser.add_argument("--category", action="append", default=[], help="类目名称，可重复")
    parser.add_argument("--ignore", action="append", default=[], help="忽略关键词，可重复")
    parser.add_argument("--limit", type=int, default=200, help="最多扫描多少个批次")
    parser.add_argument("--max-depth", type=int, default=8, help="最大目录深度")
    parser.add_argument("--out", default="", help="把报告写入 UTF-8 文件，便于查看")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args()
    root = os.path.normpath(args.root)
    if not os.path.isdir(root):
        print(f"目录不存在：{root}")
        return 1

    original_stdout = sys.stdout
    if args.out:
        sys.stdout = open(args.out, "w", encoding="utf-8")

    print(f"根目录：{root}")
    print("模式：只读（不写数据库、不创建/修改/删除文件）")
    print()

    batches = detect_batches(
        root,
        max_depth=args.max_depth,
        limit=args.limit,
        reviewer_names=args.reviewer,
        team_names=args.team,
        category_names=args.category,
        category_levels={name: 0 for name in args.category},
        ignore_patterns=args.ignore,
    )
    summary = summarize(batches)
    print(
        "扫描结果：{batches} 个批次 / {data_groups} 条数据 / "
        "有记录 {with_records} / 验收员冲突 {conflicts} / 未匹配 {unmatched}".format(**summary)
    )
    print(f"团队：{'、'.join(summary['teams']) or '未识别'}")
    print(f"类目：{'；'.join(summary['categories']) or '未识别'}")
    print()

    current_group = None
    for item in batches:
        category = " / ".join(
            part for part in (item.category_l1, item.category_l2, item.category_l3) if part
        ) or "未分类"
        group = f"{category} ｜ 团队：{item.team or '未识别'} ｜ 日期：{item.date or '未识别'}"
        if group != current_group:
            print("=" * 80)
            print(group)
            print("=" * 80)
            current_group = group

        print(f"批次：{item.name}")
        print(f"  相对路径：{' / '.join(item.segments) or '（根目录）'}")
        print(f"  大类/小类/三级类：{item.category_l1 or '-'} / {item.category_l2 or '-'} / {item.category_l3 or '-'}")
        print(f"  团队/日期：{item.team or '-'} / {item.date or '-'}")
        print(f"  编号数据：{len(item.data_groups)} 条" + (f"（{item.data_groups[0]} … {item.data_groups[-1]}）" if item.data_groups else ""))
        if item.groups:
            print(f"  额外层级：{'、'.join(item.groups)}")
        print(f"  验收记录：{item.record_text}")
        if item.record_folders:
            for folder in item.record_folders:
                matched = "、".join(folder.matched_reviewers) or "未匹配"
                files = "、".join(folder.files[:3]) + ("…" if len(folder.files) > 3 else "")
                print(f"    - {folder.name}  →  {matched}" + (f" ｜ {files}" if files else " ｜ 无文件"))
        print(f"  验收员：{item.reviewer_text}")
        print(f"  状态：{item.status}（扫描完成并确认绑定后才会变更）")
        for warning in item.warnings:
            print(f"  ⚠️ {warning}")
        print()

    if args.out:
        sys.stdout.close()
        sys.stdout = original_stdout
        print(f"报告已写入：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
