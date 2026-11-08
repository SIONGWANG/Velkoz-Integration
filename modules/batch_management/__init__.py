"""批次管理：候选确认、合并/拆分与扫描差异。"""

from .service import BatchService, VALID_CANDIDATE_STATUS

__all__ = ["BatchService", "VALID_CANDIDATE_STATUS"]
