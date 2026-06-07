# V1.3.0 审查提案

## 背景

V1.3.0 从 V1.2.5.2 复制而来。V1.2.5.2 中根据 openspec 审查建议重新引入了 `_fsync_path` 调用（P0-4: 确保数据落盘），但该调用在 Windows 上导致 CSV 文件自锁——用户首次保存即报"CSV 被占用"。此问题在 5/21 曾出现并修复（移除 fsync），本次通过同句柄 fsync 方案彻底解决。

## P0 修复清单

| 编号 | 问题 | 影响范围 | 修复方案 |
|------|------|----------|----------|
| P0-1 | `_save_df` 中 `pd.to_csv()` 关闭文件后立即 `os.open()` 重新打开做 fsync，Windows 上句柄未释放导致 `PermissionError` | `_save_df` | 流式写入 + 同句柄 fsync：`with open()` + `df.to_csv(f)` + `f.flush()` + `os.fsync(f.fileno())` |
| P0-2 | `os.replace` 重试间隔过短（3 次/100-300ms），对 Windows 文件同步盘/杀毒软件持锁无效 | `_save_df` | 增加重试至 5 次/300-1500ms |
| P0-3 | `_fsync_path` 中 `os.fsync()` 抛异常时 `os.close(fd)` 不执行，文件描述符泄漏 | `_fsync_path` | `try/finally` 确保 fd 关闭 |

## 历史追溯

| 时间 | 版本 | 事件 |
|------|------|------|
| 2026-05-21 | V1.1.7 | 首次发现 `_fsync_path` 导致 Windows 自锁，移除 fsync 修复 |
| 2026-05-26 12:00 | V1.2.5.2 | openspec 审查建议重新加回 fsync（P0-4 "确保数据落盘"） |
| 2026-05-26 15:00 | V1.3.0 | 从 V1.2.5.2 复制，fsync 问题复现 |
| 2026-05-26 18:00 | V1.3.0 | 同句柄 fsync 修复，既保留数据落盘保障又消除竞争窗口 |
