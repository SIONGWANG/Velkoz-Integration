# V1.3.0 审查报告

## 审查维度

1. **CSV 写入可靠性**：`_save_df` 文件锁竞争、fsync 实现方式
2. **异常处理健壮性**：文件描述符泄漏、异常清理连锁失败
3. **重试策略有效性**：Windows 环境下的重试间隔是否充足

## 根因分析

### P0-1：_save_df 自锁（核心问题）

- **问题**：`_save_df` 中 `pd.to_csv(tmp_file)` 写完关闭文件后，立即用 `os.open(tmp_file, O_RDONLY)` 重新打开做 fsync。在 Windows NTFS 上，`pd.to_csv` 的 `close()` 调用后内核级文件句柄释放存在延迟（写回缓存、OpLock、杀毒软件异步扫描），导致 `os.open()` 抛 `PermissionError`
- **位置**：`_save_df`（app.py:389-390）→ `_fsync_path`（app.py:424）
- **影响**：用户首次保存即报"CSV 被占用"，后续保存全部失败
- **复现条件**：Windows 环境 + 文件同步盘/杀毒软件实时扫描（本例为 `D:\验收\` 目录）
- **修复**：`with open(tmp_file) as f` + `df.to_csv(f)` + `f.flush()` + `os.fsync(f.fileno())`，全程同一句柄，不存在二次打开窗口

### P0-2：os.replace 重试不足

- **问题**：原重试 3 次、间隔 100-300ms，对 Windows 文件同步盘/杀毒软件的持锁时长不够
- **位置**：`_save_df`（app.py:398-404）
- **影响**：即使 fsync 问题解决，`os.replace` 仍可能因外部锁失败
- **修复**：重试增至 5 次、间隔 300-1500ms

### P0-3：_fsync_path 文件描述符泄漏

- **问题**：`os.open()` 成功但 `os.fsync()` 抛异常时，`os.close(fd)` 不执行，fd 泄漏
- **位置**：`_fsync_path`（app.py:421-428）
- **影响**：累积后加剧文件锁定（当前 `_save_df` 已不调用此方法，但修复供其他场景安全使用）
- **修复**：`try/finally` 确保 fd 关闭

## 修复前后对比

```
修复前（存在竞争窗口）:
  df.to_csv(tmp_file)          # pandas: open → write → close（句柄释放延迟）
  _fsync_path(tmp_file)        # os.open(O_RDONLY) → PermissionError!
  os.replace(tmp_file, csv)    # .tmp 仍被锁 → PermissionError!
  → "CSV被占用"

修复后（同句柄，无竞争）:
  with open(tmp_file) as f:
      df.to_csv(f)             # 写入已打开的句柄
      f.flush()                # 刷 Python 缓冲
      os.fsync(f.fileno())     # 刷 OS 缓冲，同句柄
  # with 退出 → 自动 close
  os.replace(tmp_file, csv)    # 文件已完全释放 → 成功
```

## 历史教训

此问题在 5/21 已通过移除 fsync 修复，但 5/26 的 openspec 审查（V1.2.5.2 P0-4）建议重新加回 fsync 以"确保数据落盘"。审查时未考虑到 Windows 上 `os.open()+os.fsync()` 的二次打开竞争问题。同句柄 fsync 方案兼顾了数据安全和 Windows 兼容性。
