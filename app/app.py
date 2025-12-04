# app/app.py
import csv
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import List, Dict

from flask import Flask, request, render_template, jsonify, send_file, send_from_directory, abort
from werkzeug.utils import secure_filename

from app.jm import JMeterRunner
from app.parser import JTLParser
from flask import jsonify
from flask_cors import CORS

# Add at top of app.py
IS_VERCEL = 'VERCEL' in os.environ

# Point Flask to templates/static inside app/
app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

BASE_TMP = Path("/tmp/jmx_controller_sessions")
BASE_TMP.mkdir(parents=True, exist_ok=True)

SESSIONS: Dict[str, Dict] = {}
ALLOWED_EXT = {".jmx"}


# ------------------------------
# Helpers
# ------------------------------
def _new_session_dir():
    sid = uuid.uuid4().hex
    sdir = BASE_TMP / sid
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "results").mkdir(exist_ok=True)
    return sid, sdir


def _safe_json():
    return request.get_json(silent=True) or {}


def _equal_split(total: int, n: int) -> List[int]:
    """Split 'total' into 'n' parts as evenly as possible."""
    if n <= 0:
        return []
    base = total // n
    rem = total % n
    return [base + (1 if i < rem else 0) for i in range(n)]


def _merge_jtls(jtls: List[Path], out_csv: Path) -> None:
    """
    Merge multiple CSV JTLs into a single CSV file (append rows, skip header lines).
    """
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as out_f:
        writer = csv.writer(out_f)
        wrote_header = False
        for jtl in jtls:
            if not jtl.exists() or jtl.stat().st_size == 0:
                continue
            with jtl.open(newline="") as in_f:
                reader = csv.reader(in_f)
                for row in reader:
                    if not row:
                        continue
                    if row[0] == "timeStamp":
                        if not wrote_header:
                            writer.writerow(row)
                            wrote_header = True
                        continue
                    writer.writerow(row)


# ------------------------------
# Routes
# ------------------------------

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload_jmx():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded"}), 400
    name = secure_filename(file.filename)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"error": "Only .jmx files allowed"}), 400

    sid, sdir = _new_session_dir()
    jmx_path = sdir / name
    file.save(str(jmx_path))

    # Pre-create a default JTL for single-engine runs
    jtl_path = sdir / "results" / "result.jtl"
    jtl_path.touch(exist_ok=True)

    SESSIONS[sid] = {
        "dir": sdir,
        "status": "uploaded",
        "proc": None,          # single-process handle (sanity/replicate)
        "procs": None,         # list of per-host processes (split_equal)
        "started_at": None,
        "ended_at": None,
        "jmx": jmx_path.name,
        "jtls": ["result.jtl"],

        # HTML report (combined) settings
        "report_dir": str(sdir / "results" / "html"),
        "report_status": "not_started",
        "mode": "sanity",       # 'sanity' or 'distributed'
        "dist_mode": None,      # 'replicate' or 'split_equal'
    }
    return jsonify({"session_id": sid, "filename": jmx_path.name})


@app.route("/start", methods=["POST"])
def start_test():
    data = _safe_json()
    sid = data.get("session_id")
    
    # Vercel demo mode - no JMeter
    if IS_VERCEL:
        return jsonify({
            "message": "started (demo mode)", 
            "session_id": sid,
            "warning": "JMeter disabled on Vercel demo. Full features on Docker."
        })
        
    if not sid or sid not in SESSIONS:
        return jsonify({"error": "Invalid session"}), 400

    entry = SESSIONS.get[sid]
    if entry["status"] == "running":
        return jsonify({"message": "Already running", "session_id": sid})

    sdir: Path = entry["dir"]
    jmx = sdir / entry["jmx"]

    jm = JMeterRunner()
    mode = data.get("mode", "sanity")
    entry["mode"] = mode

    # ------------------ SANITY (single-engine) ------------------
    if mode == "sanity":
        jtl = sdir / "results" / "result.jtl"
        jtl.touch(exist_ok=True)

        proc = jm.run_non_gui(test_plan=str(jmx), jtl=str(jtl), workdir=str(sdir))
        entry["proc"] = proc
        entry["procs"] = None
        entry["jtls"] = ["result.jtl"]
        entry["status"] = "running"
        entry["started_at"] = time.time()
        entry["ended_at"] = None

        def _monitor():
            code = proc.wait()
            entry["status"] = "finished" if code == 0 else "error"
            entry["ended_at"] = time.time()

        threading.Thread(target=_monitor, daemon=True).start()
        return jsonify({"message": "started", "session_id": sid})

    # ------------------ DISTRIBUTED ------------------
    if mode == "distributed":
        rh = data.get("remote_hosts", "")
        hosts = [h.strip() for h in rh.split(",") if h.strip()]
        if not hosts:
            return jsonify({"error": "Provide remote_hosts"}), 400

        dist_mode = data.get("dist_mode", "replicate")  # default replicate
        entry["dist_mode"] = dist_mode

        # ---------- REPLICATE ----------
        if dist_mode == "replicate":
            combined_jtl = sdir / "results" / "result-combined.jtl"
            combined_jtl.touch(exist_ok=True)

            proc = jm.run_non_gui(
                test_plan=str(jmx),
                jtl=str(combined_jtl),
                workdir=str(sdir),
                remote_hosts=hosts,
            )
            entry["proc"] = proc
            entry["procs"] = None
            entry["jtls"] = ["result-combined.jtl"]
            entry["status"] = "running"
            entry["started_at"] = time.time()
            entry["ended_at"] = None

            def _monitor_repl():
                code = proc.wait()
                entry["status"] = "finished" if code == 0 else "error"
                entry["ended_at"] = time.time()

            threading.Thread(target=_monitor_repl, daemon=True).start()
            return jsonify({"message": "started", "session_id": sid})

        # ---------- SPLIT_EQUAL ----------
        split_method = data.get("split_method", "tps")  # 'tps' or 'threads'
        entry["split_method"] = split_method

        if split_method == "tps":
            per = data.get("per_host_tps") or []
            if len(per) != len(hosts):
                return jsonify({"error": "remote_hosts/per_host_tps mismatch"}), 400

            procs = []
            jtls = []
            for item in per:
                host = item["host"]
                tps = int(item["tps"])
                jtl = sdir / "results" / f"result-{host}.jtl"
                jtl.touch(exist_ok=True)
                p = jm.run_non_gui(
                    test_plan=str(jmx),
                    jtl=str(jtl),
                    workdir=str(sdir),
                    remote_hosts=[host],
                    jmeter_props={"tps": tps},
                )
                procs.append(p)
                jtls.append(jtl.name)

        else:  # threads
            per = data.get("per_host_threads") or []
            if len(per) != len(hosts):
                return jsonify({"error": "remote_hosts/per_host_threads mismatch"}), 400

            rampup = str(data.get("rampup", 60))
            duration = data.get("duration")
            common_props = {"rampup": rampup}
            if duration is not None:
                common_props["duration"] = str(duration)

            procs = []
            jtls = []
            for item in per:
                host = item["host"]
                users = int(item["users"])
                jtl = sdir / "results" / f"result-{host}.jtl"
                jtl.touch(exist_ok=True)
                props = {"threads": users, **common_props}
                p = jm.run_non_gui(
                    test_plan=str(jmx),
                    jtl=str(jtl),
                    workdir=str(sdir),
                    remote_hosts=[host],
                    jmeter_props=props,
                )
                procs.append(p)
                jtls.append(jtl.name)

        entry["proc"] = None
        entry["procs"] = procs
        entry["jtls"] = jtls
        entry["status"] = "running"
        entry["started_at"] = time.time()
        entry["ended_at"] = None

        def _monitor_all():
            codes = [p.wait() for p in procs]
            entry["status"] = "finished" if all(c == 0 for c in codes) else "error"
            entry["ended_at"] = time.time()

        threading.Thread(target=_monitor_all, daemon=True).start()
        return jsonify({"message": "started", "session_id": sid})

    return jsonify({"error": "Unknown mode"}), 400


@app.route("/stop", methods=["POST"])
def stop_test():
    data = _safe_json()
    sid = data.get("session_id")
    if not sid or sid not in SESSIONS:
        return jsonify({"error": "Invalid session"}), 400

    entry = SESSIONS[sid]
    status = entry.get("status")

    if status != "running":
        return jsonify({"message": "Not running", "session_id": sid})

    entry["status"] = "stopping"

    procs = entry.get("procs")
    if procs:
        for p in procs:
            try:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()
            except Exception:
                pass
    else:
        p = entry.get("proc")
        if p:
            try:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()
            except Exception:
                pass

    entry["ended_at"] = time.time()
    entry["status"] = "stopped"
    return jsonify({"message": "stopping", "session_id": sid})


@app.route("/status/<sid>", methods=["GET"])
def status(sid):
    if IS_VERCEL:
        return jsonify({
            "status": "demo",
            "elapsed_s": 10,
            "running_vusers": 5,
            "hits_sec": 12.3,
            "passed_txn": 45,
            "failed_txn": 2,
            "errors": 0,
            "throughput_bps": 1234.5,
            "avg_response_ms": 156.2
        })
    entry = SESSIONS.get(sid)
    if not entry:
        return jsonify({"error": "Invalid session"}), 404

    sdir = entry["dir"]
    jtls = entry.get("jtls") or []

    totals = {
        "hits_sec": 0.0, "passed": 0, "failed": 0, "active_threads": 0,
        "throughput_bps": 0.0, "errors": 0,
        "avg_resp_ms_sum": 0.0, "samples": 0,
    }

    for name in jtls:
        jtl = sdir / "results" / name
        metrics = JTLParser(str(jtl)).tail_metrics()
        samples = metrics.get("passed", 0) + metrics.get("failed", 0)

        totals["hits_sec"]       += metrics.get("hits_sec", 0.0)
        totals["passed"]         += metrics.get("passed", 0)
        totals["failed"]         += metrics.get("failed", 0)
        totals["active_threads"] += metrics.get("active_threads", 0)
        totals["throughput_bps"] += metrics.get("throughput_bps", 0.0)
        totals["errors"]         += metrics.get("errors", 0)
        totals["avg_resp_ms_sum"]+= metrics.get("avg_resp_ms", 0.0) * max(1, samples)
        totals["samples"]        += samples

    avg_resp_ms = totals["avg_resp_ms_sum"] / max(1, totals["samples"])

    if entry["status"] in ["finished", "error", "stopped"]:
        elapsed = int(entry["ended_at"] - entry["started_at"]) if entry["ended_at"] else 0
    else:
        elapsed = int(time.time() - entry["started_at"]) if entry["started_at"] else 0

    resp = {
        "status": entry["status"],
        "elapsed_s": elapsed,
        "running_vusers": totals["active_threads"],
        "hits_sec": round(totals["hits_sec"], 2),
        "passed_txn": totals["passed"],
        "failed_txn": totals["failed"],
        "errors": totals["errors"],
        "throughput_bps": round(totals["throughput_bps"], 2),
        "avg_response_ms": round(avg_resp_ms, 2),
    }
    return jsonify(resp)


@app.route("/transactions/<sid>", methods=["GET"])
def transactions(sid):
    entry = SESSIONS.get(sid)
    if not entry:
        return jsonify({"error": "Invalid session"}), 404

    label_filter = request.args.get("label")
    sdir = entry["dir"]
    jtls = entry.get("jtls") or []
    all_txns = []

    for name in jtls:
        jtl = sdir / "results" / name
        txns = JTLParser(str(jtl)).recent_transactions(limit=50, label_filter=label_filter)
        all_txns.extend(txns)

    return jsonify(all_txns[-50:])


@app.route("/results/<sid>/jtl", methods=["GET"])
def download_jtl(sid):
    entry = SESSIONS.get(sid)
    if not entry:
        return jsonify({"error": "Invalid session"}), 404

    sdir = entry["dir"]
    jtls = entry.get("jtls") or []
    if not jtls:
        return abort(404)

    if len(jtls) == 1:
        jtl = sdir / "results" / jtls[0]
        if not jtl.exists():
            return abort(404)
        return send_file(str(jtl), as_attachment=True, download_name=f"{sid}.jtl")

    zip_base = sdir / "results" / "jtls"
    zip_path = Path(str(zip_base) + ".zip")
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(zip_base), "zip", str(sdir / "results"))
    return send_file(str(zip_path), as_attachment=True, download_name=f"{sid}-jtls.zip")


@app.route("/results/<sid>/summary", methods=["GET"])
def download_summary(sid):
    entry = SESSIONS.get(sid)
    if not entry:
        return jsonify({"error": "Invalid session"}), 404

    sdir = entry["dir"]
    jtls = entry.get("jtls") or []

    total = {
        "samples": 0,
        "passed": 0,
        "failed": 0,
        "avg_resp_ms_sum": 0.0,
        "throughput_bps_sum": 0.0,
    }

    for name in jtls:
        jtl = sdir / "results" / name
        summary = JTLParser(str(jtl)).summary()
        total["samples"] += summary.get("samples", 0)
        total["passed"]  += summary.get("passed", 0)
        total["failed"]  += summary.get("failed", 0)
        total["avg_resp_ms_sum"] += summary.get("avg_resp_ms", 0.0) * max(
            1, summary.get("samples", 0)
        )
        total["throughput_bps_sum"] += summary.get("throughput_bps", 0.0)

    samples = max(1, total["samples"])
    avg_resp_ms = total["avg_resp_ms_sum"] / samples
    avg_throughput = total["throughput_bps_sum"] / max(1, len(jtls))

    summary_csv = sdir / "results" / "summary.csv"
    with summary_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Metric", "Value"])
        w.writerow(["Samples", total["samples"]])
        w.writerow(["Passed", total["passed"]])
        w.writerow(["Failed", total["failed"]])
        w.writerow(["Avg Response (ms)", round(avg_resp_ms, 2)])
        w.writerow(["Throughput (B/s)", round(avg_throughput, 2)])

    return send_file(
        str(summary_csv),
        as_attachment=True,
        download_name=f"{sid}-summary.csv",
    )



# ------------------------------
# HTML report (JMeter dashboard)
# ------------------------------

@app.route("/results/<sid>/report/generate", methods=["POST"])
def generate_report(sid):
    entry = SESSIONS.get(sid)
    if not entry:
        return jsonify({"error": "Invalid session"}), 404

    sdir = entry["dir"]
    jtls = [sdir / "results" / name for name in entry.get("jtls") or []]
    if not jtls:
        return jsonify({"error": "No results"}), 400

    combined = sdir / "results" / "combined.jtl"
    _merge_jtls(jtls, combined)

    outdir = Path(entry["report_dir"])
    if outdir.exists():
        shutil.rmtree(outdir, ignore_errors=True)

    entry["report_status"] = "generating"
    jm = JMeterRunner()

    def _gen():
        # Do NOT override workdir here; JMeterRunner will default to JMETER_HOME/bin
        code = jm.generate_html_report(jtl=str(combined), outdir=str(outdir))
        entry["report_status"] = "ready" if code == 0 else "error"

    threading.Thread(target=_gen, daemon=True).start()
    return jsonify({"message": "report_generation_started"})



@app.route("/results/<sid>/report.zip", methods=["GET"])
def download_report_zip(sid):
    entry = SESSIONS.get(sid)
    if not entry:
        return jsonify({"error": "Invalid session"}), 404

    outdir = Path(entry["report_dir"])
    if not outdir.exists():
        return jsonify({"error": "Report not found"}), 404

    base = outdir.parent / "html-report"
    zip_path = Path(str(base) + ".zip")
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(base), "zip", str(outdir))
    return send_file(str(zip_path), as_attachment=True, download_name=f"{sid}-report.zip")


# ------------------------------
# Cleanup (optional helper)
# ------------------------------

@app.route("/results/<sid>/delete", methods=["POST"])
def delete_session(sid):
    entry = SESSIONS.pop(sid, None)
    if not entry:
        return jsonify({"error": "Invalid session"}), 404

    sdir = entry["dir"]
    try:
        shutil.rmtree(sdir, ignore_errors=True)
    except Exception:
        pass

    return jsonify({"message": "deleted", "session_id": sid})



