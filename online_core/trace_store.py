"""Pipeline trace recording system for the RAG engine.

Dual storage: deque buffer (fast recent lookup) + JSONL (persistence).
Records decision paths across all pipeline stages for debugging and analysis.

Usage:
    store = TraceStore("data/traces/")
    trace = PipelineTrace(trace_id="trc_a1b2c3d4e5f6", query="你好")
    trace.stages.append(StageRecord("legal_pre_filter", "BLOCKED", {...}, 0.5))
    store.record(trace)
"""

import json
import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Optional


# ── Data Models ──────────────────────────────────────────────────────────────


@dataclass
class StageRecord:
    """A single stage execution record in the pipeline trace.

    Attributes:
        stage_name: Identifier for the pipeline stage, e.g. "legal_pre_filter",
            "planner_estimator", "planner_llm", "retrieval", "reranker", "llm".
        status: Execution outcome. One of "PASSED", "BLOCKED", "ACTIVATED",
            "NOT_ACTIVATED", "SKIPPED".
        detail: Stage-specific key-value information (e.g. skip_reason,
            decision probabilities, top_k used).
        timing_ms: Wall-clock time spent in this stage, in milliseconds.
    """
    stage_name: str
    status: str
    detail: dict
    timing_ms: float


@dataclass
class PipelineTrace:
    """Complete trace of a single query through the pipeline.

    Attributes:
        trace_id: Unique identifier, format ``trc_`` + 12 hex chars.
        query: The original user query.
        stages: Chronological list of stage records.
        total_timing_ms: Sum of all stage timings.
        created_at: ISO-8601 formatted timestamp.
    """
    trace_id: str
    query: str
    stages: list[StageRecord] = field(default_factory=list)
    total_timing_ms: float = 0.0
    created_at: str = ""

    @property
    def path(self) -> str:
        """Auto-derive branch path from stage names joined by →."""
        return " → ".join(s.stage_name for s in self.stages)


# ── Trace ID Generation ─────────────────────────────────────────────────────


def _generate_trace_id() -> str:
    """Generate a trace ID in ``trc_`` + first 12 hex chars of a UUID.

    Returns:
        str: e.g. ``"trc_a1b2c3d4e5f6"``.
    """
    return f"trc_{uuid.uuid4().hex[:12]}"


# ── Serialization Helpers ────────────────────────────────────────────────────


def _trace_to_dict(trace: PipelineTrace) -> dict:
    """Serialize a PipelineTrace to a JSON-compatible dictionary.

    The serialized form uses key ``total_ms`` (shorter) and ``path`` (derived)
    for compact JSONL storage.

    Args:
        trace: The pipeline trace to serialize.

    Returns:
        dict: Serializable representation.
    """
    return {
        "trace_id": trace.trace_id,
        "query": trace.query,
        "stages": [
            {
                "stage_name": s.stage_name,
                "status": s.status,
                "detail": s.detail,
                "timing_ms": s.timing_ms,
            }
            for s in trace.stages
        ],
        "total_ms": trace.total_timing_ms,
        "path": trace.path,
        "created_at": trace.created_at,
    }


def _dict_to_trace(data: dict) -> PipelineTrace:
    """Deserialize a dictionary back into a PipelineTrace.

    Handles both the compact JSONL format (``total_ms``) and the canonical
    dataclass field name (``total_timing_ms``).

    Args:
        data: Dictionary loaded from a JSONL line.

    Returns:
        PipelineTrace: Reconstructed trace object.
    """
    stages = [
        StageRecord(
            stage_name=s["stage_name"],
            status=s["status"],
            detail=s.get("detail", {}),
            timing_ms=s.get("timing_ms", 0.0),
        )
        for s in data.get("stages", [])
    ]
    return PipelineTrace(
        trace_id=data["trace_id"],
        query=data["query"],
        stages=stages,
        total_timing_ms=data.get("total_ms", data.get("total_timing_ms", 0.0)),
        created_at=data.get("created_at", ""),
    )


# ── TraceStore ────────────────────────────────────────────────────────────────


class TraceStore:
    """Dual storage for pipeline traces: deque buffer + JSONL persistence.

    The in-memory *deque* buffer (maxlen=200) provides O(1) recent-lookup and
    listing.  Every trace is also appended immediately to a date-split JSONL
    file for durable storage and offline analysis.

    Thread-safe: a ``threading.Lock`` guards all JSONL file writes.

    Args:
        path: Directory for JSONL trace files.  Created if it does not exist.
    """

    def __init__(self, path: str = "data/traces/"):
        self._dir = Path(path)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._buffer: deque[PipelineTrace] = deque(maxlen=200)
        self._lock = threading.Lock()

    # ── Public API ───────────────────────────────────────────────────────

    def record(self, trace: PipelineTrace) -> str:
        """Record a pipeline trace.

     The trace is appended to the in-memory buffer *and* written to the
        date-split JSONL file for the current day.

        Args:
            trace: The trace to persist.  If ``trace_id`` is empty a new one
                is generated automatically; otherwise the supplied ID is used.

        Returns:
            str: The ``trace_id`` of the recorded trace.
        """
        if not trace.trace_id:
            trace.trace_id = _generate_trace_id()
        if not trace.created_at:
            trace.created_at = datetime.now().isoformat()

        self._buffer.append(trace)
        self._append_jsonl(trace)
        return trace.trace_id

    def get(self, trace_id: str) -> Optional[PipelineTrace]:
        """Look up a trace by its ID.

        The in-memory buffer is searched first (O(n) where n ≤ 200).  If not
        found, all JSONL files are scanned in reverse chronological order.

        Args:
            trace_id: The trace ID to look for.

        Returns:
            The matching trace, or ``None`` if not found.
        """
        # Buffer search (most recent first for better cache locality).
        for t in reversed(self._buffer):
            if t.trace_id == trace_id:
                return t
        # Fall back to JSONL scan.
        return self._scan_jsonl(trace_id)

    def list(self, limit: int = 50) -> list[PipelineTrace]:
        """Return the most recent *limit* traces from the buffer.

        Args:
            limit: Maximum number of traces to return.

        Returns:
            List of traces, most recent last.
        """
        return list(self._buffer)[-limit:]

    def export(self, start_date: Optional[str] = None,
               end_date: Optional[str] = None) -> Path:
        """Filter traces by date range and write to an export JSONL file.

        When no dates are given, all JSONL files in the store directory are
        exported.  Date parameters are inclusive and should be in ``YYYY-MM-DD``
        format.

        Args:
            start_date: Earliest date (inclusive), e.g. ``"2026-07-01"``.
            end_date: Latest date (inclusive), e.g. ``"2026-07-12"``.

        Returns:
            ``Path`` to the exported file.
        """
        jsonl_files = sorted(self._dir.glob("*.jsonl"))
        if start_date:
            jsonl_files = [f for f in jsonl_files
                           if f.stem >= start_date]
        if end_date:
            jsonl_files = [f for f in jsonl_files
                           if f.stem <= end_date]

        export_path = self._dir / f"export_{uuid.uuid4().hex[:8]}.jsonl"
        with self._lock:
            with open(export_path, "w", encoding="utf-8") as out:
                for jsonl_file in jsonl_files:
                    for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line:
                            out.write(line + "\n")
        return export_path

    # ── Internal Helpers ─────────────────────────────────────────────────

    def _jsonl_path(self, dt: Optional[date] = None) -> Path:
        """Return the JSONL file path for a given date (default: today).

        Args:
            dt: The date for which to get the file path.  Defaults to today.

        Returns:
            ``Path`` of the date-split JSONL file.
        """
        if dt is None:
            dt = date.today()
        return self._dir / f"{dt.isoformat()}.jsonl"

    def _append_jsonl(self, trace: PipelineTrace) -> None:
        """Append a single trace as a JSON line to the today's JSONL file.

        Thread-safe: uses the instance lock.
        """
        record = _trace_to_dict(trace)
        line = json.dumps(record, ensure_ascii=False)
        path = self._jsonl_path()
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def _scan_jsonl(self, trace_id: str) -> Optional[PipelineTrace]:
        """Scan all JSONL files for a matching trace ID.

        Files are examined in reverse chronological order so that more recent
        traces are found first.

        Args:
            trace_id: The trace ID to search for.

        Returns:
            The matching trace, or ``None``.
        """
        jsonl_files = sorted(self._dir.glob("*.jsonl"), reverse=True)
        for jsonl_file in jsonl_files:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("trace_id") == trace_id:
                        return _dict_to_trace(data)
        return None
