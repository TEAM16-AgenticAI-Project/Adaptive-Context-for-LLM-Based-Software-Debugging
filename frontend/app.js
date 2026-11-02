"use strict";

const $ = (id) => document.getElementById(id);
let activeSource = "example";

// --- source tab switching -------------------------------------------------
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    activeSource = tab.dataset.source;
    document.querySelectorAll(".source").forEach((s) => {
      s.classList.toggle("hidden", s.dataset.source !== activeSource);
    });
  });
});

// --- health badge + example prefill --------------------------------------
async function init() {
  try {
    const h = await fetch("/api/health").then((r) => r.json());
    $("health").textContent = h.groq_key_present
      ? "groq key detected on server"
      : "sim mode (no groq key on server)";
    if (!h.groq_key_present) {
      // if no key, keep groq option but default to sim
      $("llm").value = "sim";
    }
  } catch {
    $("health").textContent = "backend unreachable";
  }
  try {
    const ex = await fetch("/api/example").then((r) => r.json());
    if (!$("bug-report").value) $("bug-report").value = ex.bug_report;
    $("local-path").value = ex.local_path;
    window.__examplePath = ex.local_path;
  } catch { /* example optional */ }
}
init();

// --- run ------------------------------------------------------------------
$("run").addEventListener("click", async () => {
  const bug = $("bug-report").value.trim();
  if (!bug) { setStatus("Please describe the bug first.", true); return; }

  const fd = new FormData();
  fd.append("bug_report", bug);
  fd.append("mode", $("mode").value);
  fd.append("llm", $("llm").value);
  fd.append("budget", $("budget").value);
  fd.append("rounds", $("rounds").value);
  fd.append("top_k", $("topk").value);
  fd.append("prefetch", $("prefetch").value);

  try {
    if (activeSource === "example") {
      fd.append("local_path", window.__examplePath || $("local-path").value);
    } else if (activeSource === "upload") {
      const f = $("zip").files[0];
      if (!f) { setStatus("Choose a .zip file.", true); return; }
      fd.append("upload", f);
    } else if (activeSource === "git") {
      const u = $("git-url").value.trim();
      if (!u) { setStatus("Enter a git URL.", true); return; }
      fd.append("git_url", u);
    } else {
      const p = $("local-path").value.trim();
      if (!p) { setStatus("Enter a server path.", true); return; }
      fd.append("local_path", p);
    }
  } catch (e) { setStatus(String(e), true); return; }

  $("run").disabled = true;
  setStatus("Analyzing… retrieving, compressing, and reasoning over the repo.");
  try {
    const res = await fetch("/api/debug", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    render(data);
    setStatus("Done.");
  } catch (e) {
    setStatus("Error: " + e.message, true);
  } finally {
    $("run").disabled = false;
  }
});

function setStatus(msg, isError) {
  const el = $("status");
  el.textContent = msg;
  el.classList.toggle("error", !!isError);
}

// --- render ---------------------------------------------------------------
function render(data) {
  $("result-panel").classList.remove("hidden");
  const d = data.diagnosis || {};
  const conf = (d.confidence || "").toLowerCase();
  const located = d.file && d.function;

  const verdict = $("verdict");
  if (located && conf === "high") {
    verdict.className = "verdict good";
    verdict.textContent = `Diagnosed ${d.function} in ${d.file} (high confidence)`;
  } else if (located) {
    verdict.className = "verdict low";
    verdict.textContent = `Best guess: ${d.function || "?"} in ${d.file || "?"} (${conf || "low"} confidence)`;
  } else {
    verdict.className = "verdict low";
    verdict.textContent = "Could not localize a single function with the given context/settings.";
  }

  $("diagnosis").innerHTML = `
    <p><strong>File:</strong> ${esc(d.file) || "<span class='muted'>—</span>"}</p>
    <p><strong>Function:</strong> ${esc(d.function) || "<span class='muted'>—</span>"}</p>
    <p><strong>Cause:</strong> ${esc(d.cause) || "<span class='muted'>—</span>"}</p>
    <p><strong>Confidence:</strong> ${esc(d.confidence) || "<span class='muted'>—</span>"}</p>
    <p class="muted">stop: ${esc(data.stop_reason)} ·
       ${data.repo_stats.files} files, ${data.repo_stats.chunks} chunks ·
       ${data.stats.llm_calls} LLM calls · ${fmt(data.stats.total_tokens)} tokens ·
       ${(data.stats.wall_s || 0).toFixed(2)}s</p>`;

  $("fix").textContent = d.fix || "(no single-line fix suggested)";

  const LEVELS = ["full", "trimmed", "outline", "signature", "note"];
  const ev = $("evidence");
  ev.innerHTML = "";
  (data.context || []).forEach((c) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${esc(c.chunk_id)}</span><span class="lvl">${LEVELS[c.level] ?? c.level}</span>`;
    ev.appendChild(li);
  });
  if (!(data.context || []).length) ev.innerHTML = "<li class='muted'>no evidence recorded</li>";

  const tr = $("trace");
  tr.innerHTML = "";
  (data.trace || []).forEach((t) => {
    const row = document.createElement("div");
    row.className = "row";
    const action = t.action === "diagnose" ? "diag" : "req";
    row.innerHTML = `round ${t.round}: <span class="${action}">${t.action || "?"}</span> ·
      ${t.chunks ?? "?"} chunks · ${t.dropped ?? 0} dropped ·
      ${t.over_budget ? "⚠ over budget · " : ""}${fmt(t.context_tokens)} ctx tokens`;
    tr.appendChild(row);
  });
  if (!(data.trace || []).length) tr.innerHTML = "<div class='row muted'>single-shot (baseline)</div>";

  $("raw").textContent = JSON.stringify(data, null, 2);
}

function esc(s) {
  return (s == null ? "" : String(s)).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function fmt(n) { return (n == null ? "?" : Number(n).toLocaleString()); }
