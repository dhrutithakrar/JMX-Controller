@app.route("/start", methods=["POST"])
def start_test():
    data = request.get_json() or {}
    sid = data.get("session_id", "demo")
    
    # DEMO MODE for Vercel - bypass all JMeter code
    if IS_VERCEL:
        # Create a demo session if it doesn't exist
        if sid not in SESSIONS:
            SESSIONS[sid] = {
                "status": "running",
                "started_at": time.time(),
                "ended_at": None,
                "mode": "demo",
                "jtls": [],
                "dir": None
            }
        else:
            SESSIONS[sid]["status"] = "running"
            SESSIONS[sid]["started_at"] = time.time()
        
        return jsonify({
            "message": "demo_started",
            "session_id": sid,
            "status": "running"
        })
    
    # Real JMeter code for non-Vercel environments
    if not sid or sid not in SESSIONS:
        return jsonify({"error": "Invalid session"}), 400

    entry = SESSIONS[sid]
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

        dist_mode = data.get("dist_mode", "replicate")
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
        split_method = data.get("split_method", "tps")
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