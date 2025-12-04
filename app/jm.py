# app/jm.py
import os
import subprocess
from typing import Dict, Iterable, Optional


class JMeterRunner:
    """
    Small wrapper around the JMeter CLI to:
      - Run a JMeter test plan in NON-GUI mode (optionally on remote engines)
      - Pass per-run properties (-J / -G)
      - Generate an HTML Dashboard report from a JTL file

    Notes:
      * By default we rely on 'jmeter' being on PATH. You can override via:
          - Environment: JMETER_BIN=/path/to/jmeter (or jmeter.bat on Windows)
          - Constructor: JMeterRunner(jmeter_bin="/opt/apache-jmeter/bin/jmeter")
      * stdout/stderr are suppressed by default to avoid blocking due to full pipes.
        If you need to read logs programmatically, set pipe_io=True.
    """

    def __init__(self, jmeter_bin: Optional[str] = None) -> None:
        # In your Dockerfile JMETER_HOME=/opt/jmeter and PATH includes JMETER_HOME/bin
        # So default "jmeter" works. You can still override via JMETER_BIN if needed.
        self.jmeter_bin = jmeter_bin or os.environ.get("JMETER_BIN", "jmeter")

    # ---------------------------
    # Public API
    # ---------------------------

    def run_non_gui(
        self,
        *,
        test_plan: str,
        jtl: str,
        workdir: Optional[str] = None,
        remote_hosts: Optional[Iterable[str]] = None,
        jmeter_props: Optional[Dict[str, str]] = None,
        global_props: Optional[Dict[str, str]] = None,
        report_out: Optional[str] = None,
        pipe_io: bool = False,
        extra_args: Optional[Iterable[str]] = None,
    ) -> subprocess.Popen:
        """
        Launch JMeter in non-GUI mode.
        """
        cmd = [
            self.jmeter_bin,
            "-n",
            "-t", test_plan,
            "-l", jtl,
            "-Jjmeter.save.saveservice.output_format=csv",
            "-Jjmeter.save.saveservice.autoflush=true",
        ]

        if remote_hosts:
            host_list = ",".join(h for h in remote_hosts if h)
            if host_list:
                cmd += ["-R", host_list]

        if jmeter_props:
            for k, v in jmeter_props.items():
                cmd.append(f"-J{k}={v}")

        if global_props:
            for k, v in global_props.items():
                cmd.append(f"-G{k}={v}")

        if report_out:
            cmd += ["-e", "-o", report_out]

        if extra_args:
            cmd += list(extra_args)

        stdout = subprocess.PIPE if pipe_io else subprocess.DEVNULL
        stderr = subprocess.PIPE if pipe_io else subprocess.STDOUT

        # IMPORTANT: run from JMETER_HOME/bin if possible
        # so that all report templates/resources are found correctly.
        jmeter_home = os.environ.get("JMETER_HOME")
        if jmeter_home and not workdir:
            workdir = os.path.join(jmeter_home, "bin")

        proc = subprocess.Popen(cmd, cwd=workdir, stdout=stdout, stderr=stderr)
        return proc

    def generate_html_report(
        self,
        *,
        jtl: str,
        outdir: str,
        workdir: Optional[str] = None,
        pipe_io: bool = False,
        user_properties: Optional[str] = None,
        extra_args: Optional[Iterable[str]] = None,
    ) -> int:
        """
        Generate the JMeter HTML Dashboard from an existing JTL.

        Args:
            jtl:     Path to input results .jtl (CSV or XML)
            outdir:  Output directory for the HTML dashboard (must be empty)
            workdir: Working directory for the command (optional)
        """
        cmd = [self.jmeter_bin, "-g", jtl, "-o", outdir]

        if user_properties:
            cmd += ["-q", user_properties]

        if extra_args:
            cmd += list(extra_args)

        # Run from JMETER_HOME/bin when possible so that all dashboard
        # resources (sbadmin2, content/css, content/js, etc.) are available.
        jmeter_home = os.environ.get("JMETER_HOME")
        if jmeter_home and not workdir:
            workdir = os.path.join(jmeter_home, "bin")

        # When generating report from your Flask app, workdir will be set
        # to the session folder (sdir). That is OK too, but JMETER_HOME/bin
        # is the safest default if nothing is passed in.

        stdout = subprocess.PIPE if pipe_io else subprocess.DEVNULL
        stderr = subprocess.PIPE if pipe_io else subprocess.STDOUT

        proc = subprocess.Popen(cmd, cwd=workdir, stdout=stdout, stderr=stderr)
        out, err = proc.communicate()
        code = proc.returncode

        # Best-effort: if run under a session directory, log errors
        # so you can inspect why the report was incomplete.
        try:
            if code != 0 and workdir:
                log_path = os.path.join(workdir, "jmeter_report_error.log")
                with open(log_path, "wb") as f:
                    if out:
                        f.write(out)
                    if err and err is not out:
                        f.write(b"\n--- STDERR ---\n")
                        f.write(err)
        except Exception:
            pass

        return code
