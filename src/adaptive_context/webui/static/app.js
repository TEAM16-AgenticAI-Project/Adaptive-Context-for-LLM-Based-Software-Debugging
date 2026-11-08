"use strict";

const $ = (id) => document.getElementById(id);
let activeSource = "example";
let EXAMPLES = [];

// ---------------------------------------------------------------- source tabs
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    activeSource = tab.dataset.source;
    document.querySelectorAll(".source").forEach((s) => s.classList.toggle("hidden", s.dataset.source !== activeSource));
  });
});

// ---------------------------------------------------------------- startup: health + test cases
async function init() {
  try {
    const h = await fetch("/api/health").then((r) => r.json());
    $("health").textContent = [
      h.groq_key ? "Groq key found" : "no GROQ_API_KEY on the server (use sim)",
      h.agent ? "agent available" : "agent modes need langgraph",
    ].join(" · ");
    if (!h.groq_key) $("llm").value = "sim";
  } catch {
    $("health").textContent = "backend unreachable";
  }
  try {
    EXAMPLES = await fetch("/api/examples").then((r) => r.json());
  } catch { EXAMPLES = []; }
  const sel = $("example");
  const groups = {};
  EXAMPLES.forEach((e) => {
    if (!groups[e.group]) {
      groups[e.group] = document.createElement("optgroup");
      groups[e.group].label = e.group;
      sel.appendChild(groups[e.group]);
    }
    const o = document.createElement("option");
    o.value = e.id;
    o.textContent = e.title + (e.multi ? "  (multi-file)" : "") + (e.ready ? "" : "  (unavailable)");
    o.disabled = !e.ready;
    groups[e.group].appendChild(o);
  });
  sel.addEventListener("change", pickExample);
  if (EXAMPLES.length) pickExample();
}

function pickExample() {
  const e = EXAMPLES.find((x) => x.id === $("example").value);
  if (!e) return;
  $("bug-report").value = e.bug_report;
  $("test-cmd").value = e.test_cmd || "";
  $("example-note").textContent = e.note || "";
}
init();

// ---------------------------------------------------------------- run
$("run").addEventListener("click", async () => {
  const bug = $("bug-report").value.trim();
  if (!bug) return setStatus("Describe the bug first.", true);
  const fd = new FormData();
  fd.append("bug_report", bug);
  fd.append("mode", $("mode").value);
  fd.append("llm", $("llm").value);
  fd.append("test_cmd", $("test-cmd").value.trim());
  fd.append("budget", $("budget").value);
  fd.append("rounds", $("rounds").value);
  fd.append("top_k", $("topk").value);
  fd.append("max_verify", $("max-verify").value);
  fd.append("time_limit", $("time-limit").value);
  if (activeSource === "example") {
    fd.append("example", $("example").value);
  } else if (activeSource === "upload") {
    const f = $("zip").files[0];
    if (!f) return setStatus("Choose a .zip file.", true);
    fd.append("upload", f);
  } else if (activeSource === "git") {
    const u = $("git-url").value.trim();
    if (!u) return setStatus("Enter a git URL.", true);
    fd.append("git_url", u);
  } else {
    const p = $("local-path").value.trim();
    if (!p) return setStatus("Enter a folder path.", true);
    fd.append("local_path", p);
  }

  $("run").disabled = true;
  $("result-panel").classList.add("hidden");
  $("progress-panel").classList.remove("hidden");
  $("log").textContent = "";
  setStatus("Running… the live log shows each round, tool call and test run.");
  try {
    const res = await fetch("/api/debug", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    await follow(data.job);
  } catch (e) {
    setStatus("Error: " + e.message, true);
  } finally {
    $("run").disabled = false;
  }
});

async function follow(job) {
  let since = 0;
  for (;;) {
    const s = await fetch(`/api/jobs/${job}?since=${since}`).then((r) => r.json());
    if (s.log.length) {
      const el = $("log");
      el.textContent += s.log.join("\n") + "\n";
      el.scrollTop = el.scrollHeight;
    }
    since = s.next;
    $("elapsed").textContent = `${Math.round(s.elapsed)} s`;
    if (s.status === "done") { render(s.result); setStatus("Done."); return; }
    if (s.status === "error") { setStatus("Error: " + s.error, true); return; }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

function setStatus(msg, isError) {
  const el = $("status");
  el.textContent = msg;
  el.classList.toggle("error", !!isError);
}

// ---------------------------------------------------------------- render
function places(d) {
  const out = [];
  if (d.file) out.push(`${d.file}::${d.function}`);
  (d.locations || []).forEach((l) => out.push(`${l.file}::${l.function}`));
  return out;
}

function render(data) {
  $("result-panel").classList.remove("hidden");
  const d = data.diagnosis || {};
  const v = data.verification || {};
  const files = (data.changes && data.changes.files) || [];
  const where = `${places(d).length} place(s) in ${new Set(files.map((f) => f.file)).size || 1} file(s)`;

  const verdict = $("verdict");
  if (v.passed) {
    verdict.className = "verdict good";
    verdict.textContent = `Verified: the failing test passes after the fix in ${where}`
      + (v.related_files && v.related_files.length ? ", and no related test broke." : ".");
  } else if (d.valid) {
    verdict.className = "verdict low";
    verdict.textContent = Object.keys(v).length
      ? `Fix proposed in ${where}, but the check failed: ${v.reason || data.stop_reason}`
      : `Diagnosed ${d.file}::${d.function} (${d.confidence || "?"} confidence, not checked by tests)`;
  } else {
    verdict.className = "verdict bad";
    verdict.textContent = `No diagnosis (stop: ${data.stop_reason})`;
  }

  // changes: one diff per file
  $("changes-sub").textContent = files.length ? `${files.length} file(s)` : "";
  const ch = $("changes");
  ch.innerHTML = "";
  files.forEach((f) => {
    const box = document.createElement("div");
    box.className = "file";
    box.innerHTML = `<div class="file-head"><span>${esc(f.file)}</span><span class="muted">${esc(f.places.join(", "))}</span></div>`;
    const pre = document.createElement("pre");
    pre.className = "code diff";
    pre.innerHTML = f.diff.split("\n").map((ln) => {
      const cls = ln.startsWith("+++") || ln.startsWith("---") ? "meta" : ln.startsWith("+") ? "add"
        : ln.startsWith("-") ? "del" : ln.startsWith("@@") ? "hunk" : "";
      return `<span class="${cls}">${esc(ln)}</span>`;
    }).join("\n");
    box.appendChild(pre);
    ch.appendChild(box);
  });
  const problems = (data.changes && data.changes.problems) || [];
  if (!files.length) {
    ch.innerHTML = d.fix
      ? `<p class="muted">The fix could not be shown as a diff${problems.length ? ": " + esc(problems.join("; ")) : ""}.</p><pre class="code">${esc(d.fix)}</pre>`
      : "<p class='muted'>No change proposed.</p>";
  }

  // diagnosis
  const others = (d.locations || []).map((l) => `<li><code>${esc(l.file)}::${esc(l.function)}</code></li>`).join("");
  const s = data.stats || {};
  $("diagnosis").innerHTML = `
    <p><b>Root cause:</b> <code>${esc(d.file) || "—"}::${esc(d.function) || "—"}</code></p>
    ${others ? `<p><b>Also changes:</b></p><ul class="plain">${others}</ul>` : ""}
    <p><b>Cause:</b> ${esc(d.cause) || "<span class='muted'>—</span>"}</p>
    <p><b>Confidence:</b> ${esc(d.confidence) || "—"} · <b>stop:</b> ${esc(data.stop_reason)}</p>
    <p class="muted">${data.repo.files} files, ${data.repo.chunks} chunks · ${s.llm_calls} LLM calls ·
      ${s.tool_calls || 0} tool calls · ${fmt(s.total_tokens)} tokens · ${(s.wall_s || 0).toFixed(1)} s</p>`;

  // fix check
  const checks = (data.trace || []).filter((t) => t.verification);
  if (!Object.keys(v).length) {
    $("check").innerHTML = "<p class='muted'>Not run. Choose <b>agent + fix check</b> and give a test command.</p>";
  } else {
    const list = (xs) => xs && xs.length ? xs.map((x) => `<li><code>${esc(x)}</code></li>`).join("") : "";
    $("check").innerHTML = `
      <p><b>Failing test:</b> ${v.target_passed ? "<span class='ok'>passes</span>" : "<span class='no'>still fails</span>"}</p>
      <p><b>Related test files run:</b> ${(v.related_files || []).length}</p>
      ${v.regressions && v.regressions.length ? `<p><b>Broken by the fix:</b></p><ul class="plain">${list(v.regressions)}</ul>` : ""}
      ${v.related_failing && v.related_failing.length ? `<p><b>Related tests still failing:</b></p><ul class="plain">${list(v.related_failing)}</ul>` : ""}
      <p><b>Attempts:</b></p><ol class="plain">${checks.map((t) => `<li>round ${t.round}: ${esc(t.verification.reason)}
        (${esc((t.verification.files_changed || []).join(", "))})</li>`).join("")}</ol>`;
  }

  // rounds
  const tr = $("trace");
  tr.innerHTML = "";
  (data.trace || []).forEach((t) => {
    const row = document.createElement("div");
    row.className = "row";
    let body;
    if (t.invalid) {
      body = `<span class="no">invalid reply</span>`;
    } else if (t.calls) {
      body = t.calls.map((c, i) => {
        if (c.name === "diagnose") {
          const a = c.args || {};
          const more = (a.other_changes || []).length;
          const ver = t.verification ? ` → fix check: <span class="${t.verification.passed ? "ok" : "no"}">${esc(t.verification.reason)}</span>`
            : t.note ? ` → <span class="muted">${esc(t.note)}</span>` : "";
          return `<span class="diag">diagnose</span> ${esc(a.file)}::${esc(a.function)}${more ? ` +${more} more place(s)` : ""}${ver}`;
        }
        const r = (t.results || [])[i];
        return `<span class="req">${esc(c.name)}</span>(${esc(argText(c.args))}) → ${esc(r ? r.summary : "")}`;
      }).join("<br>");
    } else {
      body = `<span class="${t.action === "diagnose" ? "diag" : "req"}">${esc(t.action || "?")}</span>`;
    }
    row.innerHTML = `<div class="round">round ${t.round} <span class="muted">· ${t.chunks ?? "?"} chunks · ${fmt(t.context_tokens)} tokens${t.dropped ? ` · ${t.dropped} dropped` : ""}</span></div>${body}`;
    tr.appendChild(row);
  });
  if (!(data.trace || []).length) tr.innerHTML = "<div class='row muted'>single shot (baseline)</div>";

  // evidence
  const LEVELS = ["full", "trimmed", "outline", "signature", "note"];
  const ev = $("evidence");
  ev.innerHTML = "";
  (data.context || []).forEach((c) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${esc(c.chunk_id)}</span><span class="lvl">${LEVELS[c.level] ?? c.level}</span>`;
    ev.appendChild(li);
  });
  if (!(data.context || []).length) ev.innerHTML = "<li class='muted'>none recorded</li>";

  $("raw").textContent = JSON.stringify(data, null, 2);
}

function argText(a) {
  const vals = Object.values(a || {}).map((x) => String(x));
  const s = vals.join(", ");
  return s.length > 80 ? s.slice(0, 77) + "…" : s;
}
function esc(s) {
  return (s == null ? "" : String(s)).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function fmt(n) { return n == null ? "?" : Number(n).toLocaleString(); }
