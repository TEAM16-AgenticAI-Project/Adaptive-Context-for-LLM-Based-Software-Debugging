"use strict";

const $ = (id) => document.getElementById(id);
const state = { config: null, examples: [], source: "example", last: null, activeRun: 0 };

// ------------------------------------------------------------------ helpers
function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    n.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  return n;
}
const fmt = (n) => (n === null || n === undefined ? "-" : Number(n).toLocaleString());
const secs = (s) => {
  if (s === null || s === undefined) return "-";
  const n = Number(s);
  return n < 0.1 ? `${Math.max(1, Math.round(n * 1000))} ms` : `${n.toFixed(2)}s`;
};

async function getJSON(url) {
  const r = await fetch(url);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
}

function setStatus(msg, kind = "") {
  const s = $("status");
  s.textContent = msg;
  s.className = "status " + kind;
}

// ------------------------------------------------------------------ views
document.querySelectorAll(".view-btn").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".view-btn").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${b.dataset.view}`));
}));

// ------------------------------------------------------------------ source tabs
function selectSource(src) {
  state.source = src;
  document.querySelectorAll("#source-tabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.source === src));
  document.querySelectorAll(".source").forEach((s) => s.classList.toggle("hidden", s.dataset.source !== src));
  if (src === "example") fillExampleReport(true);
  if (src === "generated") loadGenerated(true);
  updateSimWarning();
}

// The offline simulator only recognises the generated bug pattern. Say so before the run,
// instead of leaving the user with "no diagnosis" on real code.
function updateSimWarning() {
  const w = $("sim-warning");
  if (!w || !state.config) return;
  const llm = (document.querySelector('input[name="llm"]:checked') || {}).value || "sim";
  const real = state.source !== "generated";
  if (llm === "sim" && real) {
    w.textContent = "The simulator cannot diagnose real code, so this run will show the retrieved evidence but no diagnosis. Use the Generated case tab, or set GROQ_API_KEY on the server for real diagnoses.";
    w.classList.remove("hidden");
  } else w.classList.add("hidden");
}
document.querySelectorAll("#source-tabs .tab").forEach((t) => t.addEventListener("click", () => selectSource(t.dataset.source)));

// ------------------------------------------------------------------ init
async function init() {
  try {
    const [cfg, exs] = await Promise.all([getJSON("/api/config"), getJSON("/api/examples")]);
    state.config = cfg;
    state.examples = exs;
  } catch (e) {
    $("server-pill").textContent = "server unreachable";
    $("server-pill").className = "pill bad";
    setStatus("Cannot reach the server: " + e.message, "error");
    return;
  }
  const cfg = state.config;
  const groq = cfg.llms.find((l) => l.id === "groq");
  $("server-pill").textContent = groq.available ? "Groq connected" : "Simulator only";
  $("server-pill").className = "pill " + (groq.available ? "good" : "warn");
  $("footer-info").textContent = `v${cfg.version} · rate limit ${cfg.limits.rate_limit_per_min}/min · uploads up to ${cfg.limits.max_upload_mb} MB`;

  // sources the server allows
  const tabFor = (s) => document.querySelector(`#source-tabs .tab[data-source="${s}"]`);
  tabFor("local").classList.toggle("hidden", !cfg.sources.local);
  if (!cfg.sources.git) {
    tabFor("git").classList.add("disabled");
    tabFor("git").title = "Cloning is disabled on this server";
    $("git-hint").textContent = "Cloning git repositories is disabled on this server.";
    $("git-url").disabled = true;
  } else {
    $("git-hint").textContent = `Public repository on ${cfg.git_hosts.join(", ")}. A browser URL is fine; a /tree/branch link picks that branch.`;
  }
  $("upload-hint").textContent = `A .zip of a Python project, up to ${cfg.limits.max_upload_mb} MB.`;

  // examples
  const sel = $("example-select");
  const groups = {};
  state.examples.forEach((e) => { (groups[e.group] = groups[e.group] || []).push(e); });
  for (const [g, list] of Object.entries(groups)) {
    const og = el("optgroup", { label: g });
    list.forEach((e) => og.appendChild(el("option", { value: e.id, text: `${e.id}: ${e.title}` })));
    sel.appendChild(og);
  }
  sel.addEventListener("change", () => fillExampleReport(true));
  if (!state.examples.length) {
    tabFor("example").classList.add("hidden");
    selectSource("generated");
  }

  // systems
  const box = $("systems");
  cfg.systems.forEach((s) => {
    const id = `sys-${s.id}`;
    box.appendChild(el("label", { class: "check" + (s.available ? "" : " disabled"), title: s.note || s.description },
      el("input", { type: "checkbox", id, value: s.id, disabled: !s.available,
                    checked: s.available && cfg.default_systems.includes(s.id) }),
      el("span", {}, el("b", { text: s.label }), el("small", { text: s.available ? s.description : s.note }))));
  });

  // llms
  const lb = $("llms");
  cfg.llms.forEach((l) => {
    lb.appendChild(el("label", { class: "radio" + (l.available ? "" : " disabled"), title: l.note },
      el("input", { type: "radio", name: "llm", value: l.id, disabled: !l.available, checked: l.id === cfg.default_llm,
                    onchange: updateSimWarning }),
      el("span", {}, el("b", { text: l.label }), el("small", { text: l.note }))));
  });

  // advanced defaults
  $("budget").value = cfg.defaults.budget;
  $("rounds").value = cfg.defaults.rounds;
  $("top-k").value = cfg.defaults.top_k;
  $("prefetch").value = cfg.defaults.prefetch;
  $("verify-box").classList.toggle("hidden", !cfg.verify_tests);
  $("model").disabled = !groq.available;

  // how-it-works system table
  const ht = $("how-systems");
  cfg.systems.forEach((s) => ht.appendChild(el("tr", {}, el("th", { text: s.label }), el("td", { text: s.description + (s.available ? "" : ` (${s.note})`) }))));

  if (state.examples.length) fillExampleReport(true);
  // no real model on this server: start on the one source the simulator can solve
  if (cfg.default_llm === "sim") selectSource("generated");
  else updateSimWarning();
}

function currentExample() {
  return state.examples.find((e) => e.id === $("example-select").value);
}

function fillExampleReport(overwrite) {
  const ex = currentExample();
  if (!ex) return;
  if (overwrite || !$("bug-report").value.trim()) $("bug-report").value = ex.bug_report;
  $("example-hint").textContent = ex.has_answer ? "This example has a known answer, so each run is scored after it finishes." : "";
  $("example-download").href = `/api/examples/${encodeURIComponent(ex.id)}/download`;
}

let genTimer = null;
async function loadGenerated(overwrite) {
  const seed = $("gen-seed").value || "0";
  const size = $("gen-size").value;
  try {
    const g = await getJSON(`/api/generated?seed=${encodeURIComponent(seed)}&size=${encodeURIComponent(size)}`);
    if (overwrite) $("bug-report").value = g.bug_report;
    $("gen-hint").textContent = `${g.files} files, ${g.chunks} chunks. The bug is ${g.depth} call${g.depth === 1 ? "" : "s"} below the function named in the report. The simulator can solve these.`;
  } catch (e) {
    $("gen-hint").textContent = "Could not load this case: " + e.message;
  }
}
["gen-seed", "gen-size"].forEach((id) => $(id).addEventListener("input", () => {
  clearTimeout(genTimer);
  genTimer = setTimeout(() => loadGenerated(true), 250);
}));
$("gen-random").addEventListener("click", () => {
  $("gen-seed").value = Math.floor(Math.random() * 100000);
  loadGenerated(true);
});

// ------------------------------------------------------------------ run
$("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const fd = new FormData();
  const systems = [...document.querySelectorAll("#systems input:checked")].map((i) => i.value);
  if (!systems.length) return setStatus("Choose at least one system.", "error");
  const llm = (document.querySelector('input[name="llm"]:checked') || {}).value || "sim";
  fd.append("systems", systems.join(","));
  fd.append("llm", llm);
  fd.append("bug_report", $("bug-report").value.trim());
  for (const [k, id] of [["budget", "budget"], ["rounds", "rounds"], ["top_k", "top-k"], ["prefetch", "prefetch"]]) fd.append(k, $(id).value);
  if ($("model").value.trim()) fd.append("model", $("model").value.trim());
  if (state.config.verify_tests && $("verify").checked) {
    fd.append("verify", "1");
    if ($("test-path").value.trim()) fd.append("test_path", $("test-path").value.trim());
  }

  const src = state.source;
  if (src === "example") {
    if (!currentExample()) return setStatus("Choose an example.", "error");
    fd.append("example", currentExample().id);
  } else if (src === "generated") {
    fd.append("gen_seed", $("gen-seed").value || "0");
    fd.append("gen_size", $("gen-size").value);
  } else if (src === "upload") {
    const f = $("zip").files[0];
    if (!f) return setStatus("Choose a .zip file.", "error");
    if (f.size > state.config.limits.max_upload_mb * 1024 * 1024) return setStatus(`The file is larger than ${state.config.limits.max_upload_mb} MB.`, "error");
    fd.append("upload", f);
  } else if (src === "git") {
    const u = $("git-url").value.trim();
    if (!u) return setStatus("Enter a repository URL.", "error");
    fd.append("git_url", u);
  } else {
    const p = $("local-path").value.trim();
    if (!p) return setStatus("Enter a server path.", "error");
    fd.append("local_path", p);
  }
  if (!fd.get("bug_report") && (src === "upload" || src === "git" || src === "local")) return setStatus("Describe the bug first.", "error");

  $("run").disabled = true;
  const started = performance.now();
  const tick = setInterval(() => setStatus(`Running ${systems.length} system${systems.length > 1 ? "s" : ""} with ${llm}... ${((performance.now() - started) / 1000).toFixed(0)}s`, "busy"), 500);
  try {
    const r = await fetch("/api/debug", { method: "POST", body: fd });
    const body = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
    if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
    state.last = body;
    render(body);
    setStatus(`Done in ${body.elapsed_s.toFixed(1)}s.`, "ok");
  } catch (e) {
    setStatus("Error: " + e.message, "error");
  } finally {
    clearInterval(tick);
    $("run").disabled = false;
  }
});

$("download-json").addEventListener("click", () => {
  if (!state.last) return;
  const blob = new Blob([JSON.stringify(state.last, null, 2)], { type: "application/json" });
  const a = el("a", { href: URL.createObjectURL(blob), download: "debug-result.json" });
  document.body.appendChild(a);
  a.click();
  a.remove();
});

// ------------------------------------------------------------------ render
function verdictOf(run, hasAnswer) {
  if (run.error) return { cls: "bad", text: "error" };
  const d = run.diagnosis;
  if (hasAnswer && run.score) {
    if (run.score.func_hit) return { cls: "good", text: run.score.grounded ? "correct, grounded" : "correct, not grounded" };
    if (run.score.file_hit) return { cls: "warn", text: "right file, wrong function" };
    return { cls: "bad", text: d.function ? "wrong function" : "no diagnosis" };
  }
  if (!d.function) return { cls: "warn", text: "no diagnosis" };
  return { cls: d.confidence === "high" ? "good" : "warn", text: `${d.confidence || "?"} confidence` };
}

function render(body) {
  $("empty").classList.add("hidden");
  $("output").classList.remove("hidden");
  const hasAnswer = !!body.answer;

  // summary
  const sl = $("summary-line");
  sl.replaceChildren(
    el("b", { text: body.repo.label }),
    ` · ${fmt(body.repo.files)} files, ${fmt(body.repo.chunks)} chunks · model: ${body.llm.model} · budget ${fmt(body.settings.budget)} tokens`);
  const al = $("answer-line");
  if (hasAnswer) {
    al.replaceChildren(el("span", { class: "tag" , text: "known answer" }), " ", el("code", { text: body.answer.functions.join(", ") }),
      el("small", { text: "  (used for scoring only, never shown to the model)" }));
    al.classList.remove("hidden");
  } else al.classList.add("hidden");

  const nl = $("note-line");
  const noDiag = body.runs.every((r) => !r.error && !r.diagnosis.function);
  if (body.llm.id === "sim" && body.repo.source !== "generated" && noDiag) {
    nl.textContent = "The simulator only recognises the generated bug pattern, so it returns no diagnosis on this code. The evidence lists below still show what each system retrieved. Choose Groq for a real diagnosis, or try a Generated case.";
    nl.classList.remove("hidden");
  } else nl.classList.add("hidden");

  // comparison table
  const t = $("compare");
  const head = ["System", "Result", "File / function", ...(hasAnswer ? ["Grounded", "Fix check"] : []), "Evidence", "LLM calls", "Tokens", "Rounds", "Time", "Stop"];
  t.replaceChildren(el("thead", {}, el("tr", {}, head.map((h) => el("th", { text: h })))));
  const tb = el("tbody");
  body.runs.forEach((r, i) => {
    const v = verdictOf(r, hasAnswer);
    const cells = [el("td", {}, el("a", { href: "#", class: "link", text: r.label, onclick: (e) => { e.preventDefault(); showRun(i); } })),
                   el("td", {}, el("span", { class: `badge ${v.cls}`, text: v.text }))];
    if (r.error) {
      cells.push(el("td", { colspan: String(head.length - 2), class: "err", text: r.error }));
    } else {
      const d = r.diagnosis;
      cells.push(el("td", { class: "mono", text: d.function ? `${d.file} :: ${d.function}` : "-" }));
      if (hasAnswer) {
        cells.push(el("td", { text: r.score ? (r.score.grounded ? "yes" : "no") : "-" }));
        cells.push(el("td", { text: fixText(r) }));
      }
      const answerSeen = r.evidence.some((e) => e.is_answer);
      cells.push(el("td", { text: `${r.evidence.length}${hasAnswer ? (answerSeen ? " (answer in prompt)" : " (answer missing)") : ""}` }));
      cells.push(el("td", { text: fmt(r.stats.llm_calls) }), el("td", { text: fmt(r.stats.total_tokens) }),
                 el("td", { text: fmt(r.stats.rounds) }), el("td", { text: secs(r.stats.wall_s) }),
                 el("td", { class: "mono small", text: r.stop_reason || "-" }));
    }
    tb.appendChild(el("tr", {}, cells));
  });
  t.appendChild(tb);

  // run tabs
  const rt = $("run-tabs");
  rt.replaceChildren(...body.runs.map((r, i) => el("button", { type: "button", class: "tab", "data-i": String(i), text: r.label, onclick: () => showRun(i) })));
  showRun(0);
}

function fixText(r) {
  if (!r.diagnosis.fix) return "no fix";
  if (r.score && r.score.fix_ok !== undefined) return r.score.fix_ok ? "passes" : "fails";
  const v = r.verification;
  if (!v) return "-";
  if (v.skipped) return "not run";
  return v.passed ? "passes" : (v.applied === false ? "not applied" : "fails");
}

function showRun(i) {
  state.activeRun = i;
  document.querySelectorAll("#run-tabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.i === String(i)));
  const r = state.last.runs[i];
  const box = $("run-detail");
  if (r.error) return box.replaceChildren(el("p", { class: "err", text: r.error }));
  const d = r.diagnosis;

  const diag = el("div", { class: "cards" },
    el("div", { class: "card" }, el("h3", { text: "Diagnosis" }),
      kv("File", d.file), kv("Function", d.function), kv("Confidence", d.confidence), kv("Stop reason", r.stop_reason),
      el("p", { class: "cause", text: d.cause || "No cause given." })),
    el("div", { class: "card" }, el("h3", { text: "Suggested fix" }),
      el("pre", { class: "code", text: d.fix || "(no fix suggested)" }),
      verificationBlock(r)));

  const stats = el("div", { class: "stats" },
    stat("LLM calls", fmt(r.stats.llm_calls)), stat("Retrievals", fmt(r.stats.retrieval_calls)),
    stat("Tool calls", fmt(r.stats.tool_calls)), stat("Input tokens", fmt(r.stats.input_tokens)),
    stat("Output tokens", fmt(r.stats.output_tokens)), stat("Rounds", fmt(r.stats.rounds)),
    stat("Over-budget rounds", fmt(r.stats.over_budget_rounds)), stat("Wall time", secs(r.stats.wall_s)));

  const ev = el("div", { class: "evidence" });
  r.evidence.forEach((e, n) => {
    const node = $("tpl-evidence").content.firstElementChild.cloneNode(true);
    node.querySelector(".ev-id").textContent = `${n + 1}. ${e.path}::${e.name}` + (e.start ? `  (L${e.start}-${e.end})` : "");
    const tags = node.querySelector(".ev-tags");
    tags.appendChild(el("span", { class: `lvl lvl-${e.level_name}`, text: e.level_name }));
    if (e.is_answer) tags.appendChild(el("span", { class: "lvl answer", text: "buggy function" }));
    node.querySelector("pre").textContent = e.text || "(not available)";
    ev.appendChild(node);
  });
  if (!r.evidence.length) ev.appendChild(el("p", { class: "muted", text: "No evidence recorded." }));

  const tr = el("div", { class: "trace" });
  (r.trace || []).forEach((t) => tr.appendChild(traceRow(t)));
  if (!(r.trace || []).length) tr.appendChild(el("p", { class: "muted", text: "Single call: static systems do not loop." }));

  box.replaceChildren(diag, stats,
    el("h3", { text: `Evidence in the final prompt (${r.evidence.length})` }),
    el("p", { class: "muted", text: "Exactly what the model saw, in prompt order, at the compression level the budget allowed. Click to expand." }),
    ev,
    el("h3", { text: "Round-by-round trace" }), tr);
}

function kv(k, v) {
  return el("p", { class: "kv" }, el("span", { text: k }), el("code", { text: v || "-" }));
}
function stat(k, v) {
  return el("div", { class: "stat" }, el("b", { text: v }), el("span", { text: k }));
}

function verificationBlock(r) {
  if (!r.diagnosis.fix) return el("p", { class: "muted", text: "Fix check: nothing to check, no fix was suggested." });
  if (r.score && r.score.fix_ok !== undefined) {
    return el("p", { class: r.score.fix_ok ? "ok-text" : "bad-text", text: r.score.fix_ok ? "Fix check: the patched case passes its test." : "Fix check: the patched case does not pass its test." });
  }
  const v = r.verification;
  if (!v) return null;
  if (v.skipped) return el("p", { class: "muted", text: "Fix check not run: " + v.reason });
  return el("div", {},
    el("p", { class: v.passed ? "ok-text" : "bad-text", text: `Fix check: ${v.passed ? "test passes" : "test fails"} (${v.mode || "?"}, ${v.reason || ""})` }),
    v.output ? el("details", {}, el("summary", { text: "Test output" }), el("pre", { class: "code", text: v.output })) : null);
}

function traceRow(t) {
  const parts = [];
  if (!t.allowed && !t.action && !t.error && !t.invalid) {          // static systems: one call, no loop
    return el("div", { class: "row" }, el("b", { text: `Round ${t.round || 1}` }), " ",
      el("span", { class: "act act-diagnose", text: "single call" }),
      ` · ${fmt(t.chunks)} chunks · ${fmt(t.prompt_tokens || t.context_tokens)} prompt tokens`);
  }
  const calls = t.calls || [];
  const diagnosed = calls.some((c) => c.name === "diagnose");
  const action = t.error ? "LLM error" : t.invalid ? "invalid reply" : (t.action || (diagnosed ? "diagnose" : "?"));
  parts.push(el("span", { class: `act act-${String(action).replace(/\s/g, "-")}`, text: action }));
  parts.push(` · ${fmt(t.chunks)} chunks`);
  if (t.dropped) parts.push(` · ${t.dropped} dropped`);
  if (t.dropped_pinned && t.dropped_pinned.length) parts.push(` · ${t.dropped_pinned.length} requested dropped`);
  if (t.over_budget) parts.push(el("span", { class: "warn-text", text: " · over budget" }));
  parts.push(` · ${fmt(t.prompt_tokens || t.context_tokens)} prompt tokens`);
  const asked = [];
  if (t.symbols && t.symbols.length) asked.push("asked for " + t.symbols.join(", "));
  const toolCalls = calls.filter((c) => c.name !== "diagnose");
  if (toolCalls.length) asked.push("tools: " + toolCalls.map((c) => `${c.name}(${Object.values(c.args || {}).join(", ")})`).join("; "));
  if (t.progress !== undefined && t.action === "request") asked.push(`${t.progress} new chunk${t.progress === 1 ? "" : "s"}`);
  return el("div", { class: "row" }, el("b", { text: `Round ${t.round}` }), " ",
    t.allowed ? el("span", { class: "allowed", text: `[${t.allowed}]` }) : null, " ", ...parts,
    asked.length ? el("div", { class: "asked", text: asked.join(" · ") }) : null);
}

init();
