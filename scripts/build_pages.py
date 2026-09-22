#!/usr/bin/env python3
"""Build a static, backend-free REPLAY build into docs/ for GitHub Pages.

GitHub Pages serves static files only; the live FastAPI /stream endpoint cannot run
there. This script produces docs/index.html: the same UI, but REPLAY runs entirely in
the browser from embedded data (the real recorded agent numbers + report), reproducing
the exact event sequence and pacing of ui/app.py:_replay_events.

LIVE needs Bedrock + boto3 (a real backend), so on Pages both toggle chips stay VISIBLE
but the page is locked to REPLAY (LIVE chip disabled) to avoid cost. Flip LIVE_ENABLED
to true in the appended engine below when you are ready to run LIVE from the backend.

Run:  .venv/bin/python build_pages.py
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # repo root (this script lives in scripts/)
UI = HERE / "ui"
DOCS = HERE / "docs"
DOCS.mkdir(exist_ok=True)

# Reuse the app's own replay parser so the embedded numbers are the real recorded ones.
sys.path.insert(0, str(UI))
import app as ui_app  # noqa: E402

agents, report = ui_app._load_replay()
DATA = {"agents": agents, "report": report}

html = (UI / "index.html").read_text()

# 1) Inject the embedded replay data as a global, just before the main <script>.
inject = (
    "<script>window.__REPLAY__ = "
    + json.dumps(DATA, default=str)
    + ";</script>\n<script>"
)
# There is exactly one opening <script> with the app logic (no src). Replace the first
# bare "<script>" that is followed by the app code (contains "EventSource").
idx = html.find("<script>\n  // each specialist")
if idx == -1:
    # fallback: first bare <script> tag
    idx = html.find("<script>")
assert idx != -1, "could not find app <script> tag"
html = html[:idx] + inject + html[idx + len("<script>"):]

# 2) Replace the investigate() body's EventSource wiring with a client-side emitter.
#    We keep all the es.addEventListener(...) handlers intact by providing a tiny shim
#    `es` object that supports addEventListener + close, then a driver that dispatches
#    the same events with the same pacing as _replay_events.
old_es = 'es = new EventSource(`/stream?replay=${mode===\"replay\"?1:0}&q=${q}`);'
assert old_es in html, "EventSource line not found (source UI changed?)"
new_es = "es = makeReplaySource();  // static build: client-side replay, no backend"
html = html.replace(old_es, new_es)

# 3) Append the client-side replay engine + hide LIVE (no backend on Pages).
engine = r"""
  // ---- static GitHub Pages build: client-side REPLAY (no backend) ----
  // Reproduces ui/app.py:_replay_events: same event names, same ordering, same pacing.
  function makeReplaySource() {
    const listeners = {};
    const es = {
      addEventListener: (name, fn) => { (listeners[name] ||= []).push(fn); },
      close: () => { es._closed = true; },
      _closed: false,
    };
    const fire = (name, dataObj) => {
      if (es._closed) return;
      (listeners[name] || []).forEach(fn => fn({ data: JSON.stringify(dataObj) }));
    };
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    const D = window.__REPLAY__ || { agents: [], report: "" };
    const supervisor = D.agents.find(a => a.key === "supervisor");
    const specialists = D.agents.filter(a => a.key !== "supervisor");

    // Defer the driver to the next tick so the caller can register its
    // addEventListener handlers FIRST. Without this, run_started fires
    // synchronously before any listener exists and the reveal is lost
    // (the crew meter would stay hidden even though its numbers update).
    setTimeout(() => {
    (async () => {
      fire("run_started", {});
      await wait(600);
      for (const sp of specialists) { fire("agent_started", { key: sp.key }); await wait(250); }
      for (const sp of specialists) {
        const dwell = Math.min(Math.max((sp.latency_ms || 1500) / 1000, 1.0), 3.5);
        await wait(dwell * 1000);
        fire("agent_finished", {
          key: sp.key, stop_reason: sp.stop_reason,
          total_tokens: sp.total_tokens, cost_usd: sp.cost_usd,
        });
      }
      await wait(500);
      if (supervisor) fire("supervisor_finished", {
        total_tokens: supervisor.total_tokens, cost_usd: supervisor.cost_usd,
      });
      await wait(400);
      fire("final_report", { report: D.report });
      fire("run_done", {});
    })();
    }, 0);

    return es;
  }

  // ---- Pages LIVE switch ----------------------------------------------------
  // LIVE needs the local FastAPI backend (Bedrock + boto3), which a static host
  // cannot run. For now we keep BOTH toggle chips VISIBLE but lock the page to
  // REPLAY to avoid any cost. When you are ready to test LIVE, set this to true
  // (and serve the UI from the FastAPI backend, not Pages).
  const LIVE_ENABLED = false;

  (function lockToReplay() {
    if (LIVE_ENABLED) return;                 // LIVE on: leave both chips working
    mode = "replay";                          // LIVE off: force replay, keep chips shown
    const live = document.getElementById("chipLive");
    const replay = document.getElementById("chipReplay");
    if (replay) replay.setAttribute("aria-pressed", "true");
    if (live) {
      live.setAttribute("aria-pressed", "false");
      live.setAttribute("aria-disabled", "true");
      live.title = "LIVE is disabled here (needs the local backend). Replay only.";
      live.style.opacity = "0.45";
      live.style.cursor = "not-allowed";
      // Swallow clicks so LIVE cannot be selected while disabled.
      live.onclick = (e) => { e.stopImmediatePropagation(); e.preventDefault(); };
    }
  })();
</script>
"""
# Put the engine right before the final </script> that closes the app block.
# Replace the LAST "</script>" (which closes the app script) with engine (which itself
# ends in </script>).
last = html.rfind("</script>")
assert last != -1
html = html[:last] + engine + html[last + len("</script>"):]

(DOCS / "index.html").write_text(html)
# A .nojekyll file so GitHub Pages serves the folder as-is (no Jekyll processing).
(DOCS / ".nojekyll").write_text("")
print(f"wrote {DOCS/'index.html'} ({len(html)} bytes)")
print(f"embedded agents: {[a['key'] for a in agents]}; report {len(report)} chars")
