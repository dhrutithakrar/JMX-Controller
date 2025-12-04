
# app/parser.py
import csv
import os
from collections import deque
from typing import List, Dict, Any, Optional


# JMeter CSV default columns (for reference):
# timeStamp(0), elapsed(1), label(2), responseCode(3), responseMessage(4),
# threadName(5), dataType(6), success(7), failureMessage(8),
# bytes(9), sentBytes(10), grpThreads(11), allThreads(12),
# URL(13), Latency(14), IdleTime(15), Connect(16)
TS_IDX = 0
ELAPSED_IDX = 1
LABEL_IDX = 2
RESP_CODE_IDX = 3
SUCCESS_IDX = 7
BYTES_IDX = 9
ALL_THREADS_IDX = 12


def _to_int(s: Any, default: int = 0) -> int:
    """Safe int conversion."""
    try:
        return int(str(s).strip())
    except Exception:
        return default


def _to_float(s: Any, default: float = 0.0) -> float:
    """Safe float conversion."""
    try:
        return float(str(s).strip())
    except Exception:
        return default


def _is_header(row: List[str]) -> bool:
    """Detect JMeter CSV header line."""
    return bool(row) and row[TS_IDX].lower() == "timestamp"  # 'timeStamp' -> lower == 'timestamp'


class JTLParser:
    """
    Lightweight CSV JTL parser tailored for:
      - tail_metrics(): windowed, real-time metrics
      - summary(): whole-file aggregates
      - recent_transactions(): last N transactions with optional label filter

    Assumes JTL is written as CSV (jm.py sets jmeter.save.saveservice.output_format=csv).
    """

    def __init__(self, jtl_path: str):
        self.path = jtl_path

    # ---------------------------
    # Internal helpers
    # ---------------------------
    def _zero_metrics(self) -> Dict[str, Any]:
        return {
            "hits_sec": 0.0,
            "passed": 0,
            "failed": 0,
            "active_threads": 0,
            "avg_resp_ms": 0.0,
            "throughput_bps": 0.0,
            "errors": 0,
        }

    def _file_empty(self) -> bool:
        try:
            return not os.path.exists(self.path) or os.path.getsize(self.path) == 0
        except Exception:
            return True

    # ---------------------------
    # Public API
    # ---------------------------
    def tail_metrics(self, window: int = 200) -> Dict[str, Any]:
        """
        Compute real-time metrics over the last `window` samples:
          - hits_sec: samples/sec
          - passed/failed: count
          - active_threads: 'allThreads' from the LAST row
          - avg_resp_ms: average elapsed over window
          - throughput_bps: sum(bytes) / duration (bytes/sec)
          - errors: alias of failed
        """
        if self._file_empty():
            return self._zero_metrics()

        rows = deque(maxlen=max(1, window))
        try:
            with open(self.path, newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or _is_header(row):
                        continue
                    rows.append(row)
        except Exception:
            return self._zero_metrics()

        if not rows:
            return self._zero_metrics()

        total = len(rows)
        first_ts = _to_int(rows[0][TS_IDX], default=0)
        last_ts = _to_int(rows[-1][TS_IDX], default=0)
        dur_sec = max(1.0, (last_ts - first_ts) / 1000.0)

        passed = 0
        bytes_sum = 0
        elapsed_sum = 0

        for r in rows:
            # success
            if len(r) > SUCCESS_IDX and r[SUCCESS_IDX].strip().lower() == "true":
                passed += 1
            # bytes
            if len(r) > BYTES_IDX:
                bytes_sum += _to_int(r[BYTES_IDX], default=0)
            # elapsed
            if len(r) > ELAPSED_IDX:
                elapsed_sum += _to_int(r[ELAPSED_IDX], default=0)

        failed = max(0, total - passed)
        avg_resp_ms = elapsed_sum / max(1, total)
        hits_sec = total / dur_sec
        throughput_bps = bytes_sum / dur_sec

        # active threads from the last row's 'allThreads'
        active_threads = 0
        if len(rows[-1]) > ALL_THREADS_IDX:
            active_threads = _to_int(rows[-1][ALL_THREADS_IDX], default=0)

        return {
            "hits_sec": round(hits_sec, 2),
            "passed": passed,
            "failed": failed,
            "active_threads": active_threads,
            "avg_resp_ms": round(avg_resp_ms, 2),
            "throughput_bps": round(throughput_bps, 2),
            "errors": failed,
        }

    def summary(self) -> Dict[str, Any]:
        """
        Whole-file aggregates:
          - samples
          - passed/failed
          - avg_resp_ms (simple mean)
          - throughput_bps (bytes / overall duration)
        """
        if self._file_empty():
            return {"samples": 0, "passed": 0, "failed": 0, "avg_resp_ms": 0.0, "throughput_bps": 0.0}

        total = 0
        passed = 0
        bytes_sum = 0
        resp_sum = 0
        first_ts: Optional[int] = None
        last_ts: Optional[int] = None

        try:
            with open(self.path, newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or _is_header(row):
                        continue
                    total += 1

                    # timestamps
                    ts = _to_int(row[TS_IDX], default=None if first_ts is None else 0)
                    if ts is not None:
                        if first_ts is None:
                            first_ts = ts
                        last_ts = ts

                    # success
                    if len(row) > SUCCESS_IDX and row[SUCCESS_IDX].strip().lower() == "true":
                        passed += 1

                    # bytes
                    if len(row) > BYTES_IDX:
                        bytes_sum += _to_int(row[BYTES_IDX], default=0)

                    # elapsed
                    if len(row) > ELAPSED_IDX:
                        resp_sum += _to_int(row[ELAPSED_IDX], default=0)
        except Exception:
            # best-effort; return partial aggregates
            failed = max(0, total - passed)
            dur_sec = max(1.0, ((last_ts or 0) - (first_ts or 0)) / 1000.0)
            avg_resp_ms = resp_sum / max(1, total)
            throughput_bps = bytes_sum / dur_sec
            return {
                "samples": total,
                "passed": passed,
                "failed": failed,
                "avg_resp_ms": round(avg_resp_ms, 2),
                "throughput_bps": round(throughput_bps, 2),
            }

        failed = max(0, total - passed)
        dur_sec = max(1.0, ((last_ts or 0) - (first_ts or 0)) / 1000.0)
        avg_resp_ms = resp_sum / max(1, total)
        throughput_bps = bytes_sum / dur_sec

        return {
            "samples": total,
            "passed": passed,
            "failed": failed,
            "avg_resp_ms": round(avg_resp_ms, 2),
            "throughput_bps": round(throughput_bps, 2),
        }

    def recent_transactions(self, limit: int = 50, label_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Return the last `limit` transactions (label, success, responseCode, elapsed),
        optionally filtering by substring in the label.
        """
        if self._file_empty():
            return []

        rows: List[List[str]] = []
        try:
            with open(self.path, newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or _is_header(row):
                        continue
                    rows.append(row)
        except Exception:
            return []

        rows = rows[-max(1, limit):]
        txns: List[Dict[str, Any]] = []

        for r in rows:
            label = r[LABEL_IDX] if len(r) > LABEL_IDX else "unknown"
            success = (len(r) > SUCCESS_IDX and r[SUCCESS_IDX].strip().lower() == "true")
            code = r[RESP_CODE_IDX] if len(r) > RESP_CODE_IDX else ""
            elapsed = _to_int(r[ELAPSED_IDX], default=None) if len(r) > ELAPSED_IDX else None

            tx = {"label": label, "success": success, "responseCode": code, "elapsed": elapsed}
            if label_filter:
                if label_filter.lower() in label.lower():
                    txns.append(tx)
            else:
                txns.append(tx)

        return txns
