#!/usr/bin/env python3
"""Claude Code hook → panel recorder.

Wire into ~/.claude/settings.json `hooks`:

  PreToolUse:   recorder.py pre
  PostToolUse:  recorder.py post
  SessionStart: recorder.py session_start
  Stop:         recorder.py session_end

pre  — POST permission_request; if PANEL_GATE=1 and tool is risky, poll
       until a decision is recorded, exit 0 (allow) or 2 (deny).
post — POST resource_access, exit 0.
session_start/session_end — manage a root run keyed by session_id.

Env:
  PANEL_URL  default http://127.0.0.1:7780
  PANEL_GATE default 0 (advisory: never block)
  PANEL_GATE_TOOLS default Bash,Write,Edit
  PANEL_GATE_TIMEOUT_S default 30
"""
import json
import os
import sys
import time
import urllib.request

PANEL = os.environ.get("PANEL_URL", "http://127.0.0.1:7780")
GATE = os.environ.get("PANEL_GATE", "0") == "1"
GATE_TOOLS = set(os.environ.get("PANEL_GATE_TOOLS", "Bash,Write,Edit").split(","))
GATE_TIMEOUT = int(os.environ.get("PANEL_GATE_TIMEOUT_S", "30"))
STATE = "/tmp/panel-recorder"
os.makedirs(STATE, exist_ok=True)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{PANEL}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def find_or_create_entity(kind, name, cfg):
    rows = call("GET", f"/api/entities?kind={kind}")
    for r in rows:
        if r["name"] == name:
            return r["id"]
    new = call("POST", "/api/entities", {"kind": kind, "name": name, "config": cfg})
    return new["id"]


def session_run(session_id):
    f = f"{STATE}/session-{session_id}"
    if os.path.exists(f):
        return int(open(f).read())
    eid = find_or_create_entity("plugin", "claude-code",
                                {"source": "recorder", "description": "live Claude Code"})
    r = call("POST", f"/api/entities/{eid}/run", {"input": {"session_id": session_id}})
    rid = r["run_id"]
    open(f, "w").write(str(rid))
    return rid


def cmd_session_start():
    payload = json.load(sys.stdin)
    session_run(payload.get("session_id", "unknown"))
    sys.exit(0)


def cmd_session_end():
    payload = json.load(sys.stdin)
    f = f"{STATE}/session-{payload.get('session_id', 'unknown')}"
    if os.path.exists(f):
        os.unlink(f)
    sys.exit(0)


def cmd_post():
    payload = json.load(sys.stdin)
    rid = session_run(payload.get("session_id", "unknown"))
    call("POST", f"/api/runs/{rid}/events", {
        "kind": "resource_access",
        "payload": {"tool": payload.get("tool_name"),
                    "input": payload.get("tool_input", {}),
                    "allowed": True},
    })
    sys.exit(0)


def cmd_pre():
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name", "?")
    rid = session_run(payload.get("session_id", "unknown"))
    decision = "pending" if (GATE and tool in GATE_TOOLS) else "allow_session"
    ev = call("POST", f"/api/runs/{rid}/events", {
        "kind": "permission_request",
        "payload": {"type": tool, "target": str(payload.get("tool_input", {}))[:200],
                    "decision": decision},
    })
    if decision != "pending":
        sys.exit(0)
    evid = ev["event_id"]
    deadline = time.time() + GATE_TIMEOUT
    while time.time() < deadline:
        events = call("GET", f"/api/events?run_id={rid}")
        for e in events:
            if e["id"] == evid:
                d = json.loads(e["payload"]).get("decision")
                if d and d != "pending":
                    if d.startswith("allow"):
                        sys.exit(0)
                    print(f"panel denied {tool}", file=sys.stderr)
                    sys.exit(2)
        time.sleep(0.5)
    sys.exit(0)


CMDS = {"pre": cmd_pre, "post": cmd_post,
        "session_start": cmd_session_start, "session_end": cmd_session_end}

if __name__ == "__main__":
    CMDS[sys.argv[1]]()
