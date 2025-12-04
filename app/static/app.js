/* static/app.js */

let sessionId = null;
let pollTimer = null;

// DOM elements
const uploadBtn   = document.getElementById("uploadBtn");
const startBtn    = document.getElementById("startBtn");
const stopBtn     = document.getElementById("stopBtn");
const sidSpan     = document.getElementById("sid");
const stateSpan   = document.getElementById("state");
const elapsedSpan = document.getElementById("elapsed");
const vusSpan     = document.getElementById("vus");
const hpsSpan     = document.getElementById("hps");
const passSpan    = document.getElementById("pass");
const failSpan    = document.getElementById("fail");
const errSpan     = document.getElementById("err");
const dlJtl       = document.getElementById("dlJtl");
const dlSummary   = document.getElementById("dlSummary");
const themeToggle = document.getElementById("themeToggle");

// Report controls
const genReport   = document.getElementById("genReport");
const dlReportZip = document.getElementById("dlReportZip");

// Distributed config
const testType     = document.getElementById("testType");
const distMode     = document.getElementById("distMode");
const remoteHosts  = document.getElementById("remoteHosts");
const numSlaves    = document.getElementById("numSlaves");
const splitMethod  = document.getElementById("splitMethod");
const totalTps     = document.getElementById("totalTps");
const totalThreads = document.getElementById("totalThreads");
const rampup       = document.getElementById("rampup");
const duration     = document.getElementById("duration");
const rmiHelp      = document.getElementById("rmiHelp");

// Transaction modal elements
const passedBadge = document.getElementById("passedBadge");
const failedBadge = document.getElementById("failedBadge");
const txnTable    = document.getElementById("txnTable");
const txnFilter   = document.getElementById("txnFilter");
let txnChart = null;

// Theme toggle
themeToggle.onclick = () => {
  const html = document.documentElement;
  const current = html.getAttribute("data-bs-theme") || "light";
  html.setAttribute("data-bs-theme", current === "light" ? "dark" : "light");
};

// ---------------------------
// Chart.js setup
// ---------------------------
function makeChart(ctx, label, color) {
  return new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets: [{ label, data: [], borderColor: color, tension: 0.25, pointRadius: 0 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: { beginAtZero: true, grid: { color: 'rgba(128,128,128,0.2)' } }
      }
    }
  });
}

const hitsChart = makeChart(document.getElementById("hitsChart"), "Hits/sec", "#4e79a7");
const thrChart  = makeChart(document.getElementById("thrChart"),  "Throughput (B/s)", "#59a14f");
const respChart = makeChart(document.getElementById("respChart"), "Avg Resp (ms)", "#e15759");

function addPoint(chart, value) {
  const num = parseFloat(value);
  chart.data.labels.push('');
  chart.data.datasets[0].data.push(isNaN(num) ? 0 : num);
  if (chart.data.labels.length > 90) {
    chart.data.labels.shift();
    chart.data.datasets[0].data.shift();
  }
  chart.update('none');
}

// ---------------------------
// UI show/hide helpers
// ---------------------------
function show(el, on) { if (el) el.style.display = on ? "" : "none"; }

function refreshDistributedVisibility() {
  const dist = testType.value === "distributed";
  show(distMode, dist);
  show(remoteHosts, dist);
  show(numSlaves,  dist);
  show(rmiHelp,    dist);

  const splitEq = dist && distMode.value === "split_equal";
  show(splitMethod, splitEq);

  const tpsMode = splitEq && splitMethod.value === "tps";
  show(totalTps, tpsMode);

  const threadsMode = splitEq && splitMethod.value === "threads";
  show(totalThreads, threadsMode);
  show(rampup,       threadsMode);
  show(duration,     threadsMode);
}

(function initVisibility() {
  testType.value    = "sanity";
  distMode.value    = "replicate";
  splitMethod.value = "tps";
  refreshDistributedVisibility();
})();

testType.onchange    = refreshDistributedVisibility;
distMode.onchange    = refreshDistributedVisibility;
splitMethod.onchange = refreshDistributedVisibility;

// ---------------------------
// Upload JMX
// ---------------------------
uploadBtn.onclick = async () => {
  const fileInput = document.getElementById("file");
  if (!fileInput.files.length) { alert("Choose a .jmx file first"); return; }

  try {
    const fd = new FormData();
    fd.append("file", fileInput.files[0]);

    const res = await fetch("/upload", { method: "POST", body: fd });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.error) { alert(data.error); return; }

    sessionId = data.session_id;
    sidSpan.textContent = sessionId;
    startBtn.disabled = false;

    // Enable downloads
    dlJtl.classList.remove("disabled");
    dlSummary.classList.remove("disabled");
    dlJtl.href = `/results/${sessionId}/jtl`;
    dlSummary.href = `/results/${sessionId}/summary`;

    // Report controls
    genReport.disabled = false;
    dlReportZip.classList.add("disabled");
    dlReportZip.href = `/results/${sessionId}/report.zip`;
  } catch (err) {
    console.error("Upload error", err);
    alert("Upload failed: " + err.message);
  }
};

// ---------------------------
// Start test
// ---------------------------
startBtn.onclick = async () => {
  if (!sessionId) return;

  const payload = { session_id: sessionId, mode: testType.value };

  if (payload.mode === "distributed") {
    const hosts = (remoteHosts.value || "")
      .split(",").map(s => s.trim()).filter(Boolean);
    const n = Number(numSlaves.value);

    if (!hosts.length || n !== hosts.length) {
      return alert("Please enter comma-separated hosts and a matching # Slaves.");
    }

    payload.remote_hosts = hosts.join(",");
    payload.dist_mode = distMode.value;

    if (payload.dist_mode === "split_equal") {
      const method = splitMethod.value;
      payload.split_method = method;

      if (method === "tps") {
        const T = Number(totalTps.value);
        if (!T) return alert("Enter a valid Total TPS.");
        const shares = equalSplit(T, n);
        payload.per_host_tps = hosts.map((h, i) => ({ host: h, tps: shares[i] }));
      } else {
        const U = Number(totalThreads.value);
        if (!U) return alert("Enter a valid Total Threads.");
        const shares = equalSplit(U, n);
        payload.per_host_threads = hosts.map((h, i) => ({ host: h, users: shares[i] }));

        if (rampup.value)   payload.rampup   = Number(rampup.value);
        if (duration.value) payload.duration = Number(duration.value);
      }
    }
  }

  try {
    const res = await fetch("/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);

    stateSpan.textContent = "running";
    stopBtn.disabled = false;
    startBtn.disabled = true;

    [hitsChart, thrChart, respChart].forEach(chart => {
      chart.data.labels = [];
      chart.data.datasets[0].data = [];
      chart.update();
    });

    beginPolling();
  } catch (e) {
    console.error("Start error", e);
    alert("Start failed: " + e.message);
  }
};

function equalSplit(total, n) {
  const base = Math.floor(total / n), rem = total % n;
  return Array.from({ length: n }, (_, i) => base + (i < rem ? 1 : 0));
}

// ---------------------------
// Stop test
// ---------------------------
stopBtn.onclick = async () => {
  if (!sessionId) return;
  try {
    const res = await fetch("/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId })
    });
    await res.json();
    stateSpan.textContent = "stopping";
  } catch (e) {
    console.error("Stop error", e);
    alert("Stop failed: " + e.message);
  }
};

// ---------------------------
// Poll status
// ---------------------------
function beginPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!sessionId) return;
    try {
      const res = await fetch(`/status/${sessionId}`);
      if (!res.ok) return;
      const s = await res.json();
      if (s.error) return;

      stateSpan.textContent   = s.status;
      elapsedSpan.textContent = s.elapsed_s;
      vusSpan.textContent     = s.running_vusers;
      hpsSpan.textContent     = s.hits_sec;
      passSpan.textContent    = s.passed_txn;
      failSpan.textContent    = s.failed_txn;
      errSpan.textContent     = s.errors;

      addPoint(hitsChart, s.hits_sec);
      addPoint(thrChart,  s.throughput_bps);
      addPoint(respChart, s.avg_response_ms);

      if (["finished", "error", "stopped"].includes(s.status)) {
        clearInterval(pollTimer);
        pollTimer = null;
        stopBtn.disabled = true;
        startBtn.disabled = false;
      }
    } catch (e) {
      console.error("Polling error", e);
    }
  }, 1000);
}

// ---------------------------
// Report generation
// ---------------------------
genReport.onclick = async () => {
  if (!sessionId) return;
  genReport.disabled = true;

  try {
    const res = await fetch(`/results/${sessionId}/report/generate`, { method: "POST" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    // Give JMeter some time to build the dashboard, then enable links
    setTimeout(() => {
      dlReportZip.classList.remove("disabled");
      genReport.disabled = false;
    }, 5000);
  } catch (e) {
    console.error("Generate report error", e);
    alert("Report generation failed: " + e.message);
    genReport.disabled = false;
  }
};

// ---------------------------
// Transactions modal
// ---------------------------
function showTransactions() {
  if (!sessionId) return;
  const label = txnFilter.value.trim();
  let url = `/transactions/${sessionId}`;
  if (label) url += `?label=${encodeURIComponent(label)}`;

  fetch(url)
    .then(r => r.json())
    .then(data => {
      txnTable.innerHTML = "";
      let passedCount = 0;
      let failedCount = 0;

      data.forEach(tx => {
        const row = document.createElement("tr");

        const statusCell = tx.success
          ? '<span class="text-success">Passed</span>'
          : '<span class="text-danger">Failed</span>';

        if (tx.success) passedCount++; else failedCount++;

        row.innerHTML = `
          <td>${escapeHtml(tx.label || "")}</td>
          <td>${statusCell}</td>
          <td>${escapeHtml(tx.responseCode || "")}</td>
          <td>${tx.elapsed ?? ''}</td>
        `;
        txnTable.appendChild(row);
      });

      const ctx = document.getElementById("txnChart").getContext("2d");
      if (txnChart) txnChart.destroy();
      txnChart = new Chart(ctx, {
        type: "doughnut",
        data: {
          labels: ["Passed", "Failed"],
          datasets: [{
            data: [passedCount, failedCount],
            backgroundColor: ["#28a745", "#dc3545"]
          }]
        },
        options: { plugins: { legend: { position: "bottom" } } }
      });

      new bootstrap.Modal(document.getElementById("txnModal")).show();
    })
    .catch(err => console.error("Transactions load error", err));
}

passedBadge.onclick = showTransactions;
failedBadge.onclick = showTransactions;
txnFilter.addEventListener("keypress", function (e) {
  if (e.key === "Enter") showTransactions();
});

// ---------------------------
// Utility
// ---------------------------
function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "'");
}
