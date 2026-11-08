-- 审视之眼 Pro 1.25.2 格式检查
-- 只保存模板、检查索引和问题，不保存图片/TXT/CSV 原文。

CREATE TABLE IF NOT EXISTS format_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    scope_id TEXT,
    active_version_id TEXT,
    builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS format_template_versions (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    note TEXT,
    created_at TEXT,
    created_by TEXT,
    UNIQUE(template_id, version),
    FOREIGN KEY(template_id) REFERENCES format_templates(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS format_check_runs (
    id TEXT PRIMARY KEY,
    batch_id TEXT,
    root_path TEXT NOT NULL,
    template_id TEXT,
    template_version_id TEXT,
    rule_snapshot_json TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    scanned_tasks INTEGER NOT NULL DEFAULT 0,
    passed_tasks INTEGER NOT NULL DEFAULT 0,
    failed_tasks INTEGER NOT NULL DEFAULT 0,
    warning_tasks INTEGER NOT NULL DEFAULT 0,
    skipped_tasks INTEGER NOT NULL DEFAULT 0,
    integrity_status TEXT NOT NULL DEFAULT 'ok',
    report_path TEXT,
    operator TEXT,
    previous_run_id TEXT,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS format_check_task_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    data_group_id TEXT,
    task_id TEXT,
    task_path TEXT NOT NULL,
    detected_mode TEXT,
    result TEXT NOT NULL,
    image_count INTEGER NOT NULL DEFAULT 0,
    txt_count INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    issue_count INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT,
    UNIQUE(run_id, task_path),
    FOREIGN KEY(run_id) REFERENCES format_check_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS format_check_issues (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_result_id TEXT,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    task_id TEXT,
    task_path TEXT,
    file_path TEXT,
    expected TEXT,
    actual TEXT,
    message TEXT,
    suggestion TEXT,
    FOREIGN KEY(run_id) REFERENCES format_check_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_format_template_versions_template ON format_template_versions(template_id);
CREATE INDEX IF NOT EXISTS idx_format_runs_started ON format_check_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_format_runs_batch ON format_check_runs(batch_id);
CREATE INDEX IF NOT EXISTS idx_format_task_results_run ON format_check_task_results(run_id);
CREATE INDEX IF NOT EXISTS idx_format_task_results_path ON format_check_task_results(task_path);
CREATE INDEX IF NOT EXISTS idx_format_issues_run ON format_check_issues(run_id);
CREATE INDEX IF NOT EXISTS idx_format_issues_code ON format_check_issues(code);
