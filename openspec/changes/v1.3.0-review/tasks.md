# V1.3.0 任务清单

- [x] P0-1: `_save_df` 改为流式写入 + 同句柄 fsync（`with open` + `df.to_csv(f)` + `f.flush()` + `os.fsync(f.fileno())`）
- [x] P0-2: `_save_df` 中 `os.replace` 重试增强至 5 次/300-1500ms
- [x] P0-3: `_fsync_path` 改为 `try/finally` 确保 fd 关闭
- [x] `_save_df` 异常清理中 `os.remove(tmp_file)` 加 `except OSError`
- [x] 更新维护日志
- [x] 归档 openspec
