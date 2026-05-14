import json
import sqlite3
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).parent
DB = ROOT / "panel.db"
INDEX = ROOT / "index.html"
SCHEMA = ROOT / "schema.sql"


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    if DB.exists():
        return
    conn = db()
    conn.executescript(SCHEMA.read_text())
    seed(conn)
    conn.commit()
    conn.close()


def seed(conn):
    c = conn.execute
    gstack = c("INSERT INTO entity(kind,name,config) VALUES(?,?,?)",
               ("plugin", "gstack", json.dumps({"version":"0.4.1","author":"openonion","description":"Dev workflow toolkit","risk":"low","permissions":["read","write","shell","network"]}))).lastrowid
    memo = c("INSERT INTO entity(kind,name,config) VALUES(?,?,?)",
             ("plugin", "lossless-mem", json.dumps({"version":"0.1.0","author":"yfshuu","description":"Agent memory layer","risk":"medium","permissions":["read","write","db_read","db_write"]}))).lastrowid
    ship = c("INSERT INTO entity(kind,name,parent_id,config) VALUES(?,?,?,?)",
             ("skill","ship",gstack,json.dumps({"description":"Ship workflow","permissions":["shell","network"],"input_schema":{"type":"object","properties":{"branch":{"type":"string"}}},"output_schema":{"type":"object"}}))).lastrowid
    c("INSERT INTO entity(kind,name,parent_id,config) VALUES(?,?,?,?)",
      ("skill","review",gstack,json.dumps({"description":"PR review","permissions":["read","network"]})))
    c("INSERT INTO entity(kind,name,parent_id,config) VALUES(?,?,?,?)",
      ("skill","mem-recall",memo,json.dumps({"description":"Recall memory","permissions":["read","db_read"]})))
    c("INSERT INTO entity(kind,name,parent_id,config) VALUES(?,?,?,?)",
      ("hook","PostToolUse:Edit",gstack,json.dumps({"event":"PostToolUse","match":"Edit","mode":"advisory","priority":10,"permissions":["read"]})))
    c("INSERT INTO entity(kind,name,parent_id,config) VALUES(?,?,?,?)",
      ("hook","PreToolUse:Bash",gstack,json.dumps({"event":"PreToolUse","match":"Bash","mode":"blocking","priority":1,"permissions":["read","shell"]})))
    settings = [
        ("plugin_dir","~/.claude/plugins"),
        ("workspace_dir","~/coding"),
        ("max_concurrency",4),
        ("default_timeout_s",120),
        ("log_retention_days",30),
        ("artifact_dir","~/.panel/artifacts"),
        ("autoenable_new_plugin",False),
        ("confirm_high_risk_permission",True),
        ("allow_background_hooks",True),
        ("record_session",True),
        ("record_io",True),
        ("record_full_log",False),
        ("redaction_rules",["password","token","secret","api_key"]),
        ("developer_mode",False),
    ]
    for k, v in settings:
        c("INSERT INTO entity(kind,name,config) VALUES(?,?,?)", ("setting", k, json.dumps({"value": v})))
    root = c("INSERT INTO run(entity_id,trigger,status,input,started_at,ended_at) VALUES(?,?,?,?,datetime('now','-5 minutes'),datetime('now','-4 minutes'))",
             (ship,"user","success",json.dumps({"branch":"main"}))).lastrowid
    c("INSERT INTO event(run_id,kind,level,payload) VALUES(?,?,?,?)", (root,"log","info",json.dumps({"msg":"ship started"})))
    c("INSERT INTO event(run_id,kind,payload) VALUES(?,?,?)", (root,"permission_request",json.dumps({"type":"shell","target":"git push","decision":"allow_session"})))
    c("INSERT INTO event(run_id,kind,payload) VALUES(?,?,?)", (root,"resource_access",json.dumps({"kind":"shell","cmd":"git push","allowed":True})))
    c("INSERT INTO event(run_id,kind,payload) VALUES(?,?,?)", (root,"resource_access",json.dumps({"kind":"file_read","path":"CHANGELOG.md","allowed":True})))
    c("INSERT INTO event(run_id,kind,payload) VALUES(?,?,?)", (root,"resource_access",json.dumps({"kind":"network","host":"api.github.com","allowed":True})))
    c("INSERT INTO event(run_id,kind,payload) VALUES(?,?,?)", (root,"artifact",json.dumps({"type":"diff","name":"changes.diff","size":2104,"preview":"--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,3 @@\n-old\n+new"})))
    c("INSERT INTO event(run_id,kind,level,payload) VALUES(?,?,?,?)", (root,"log","info",json.dumps({"msg":"PR opened"})))
    c("INSERT INTO run(entity_id,trigger,status,input,error,started_at,ended_at) VALUES(?,?,?,?,?,datetime('now','-2 minutes'),datetime('now','-1 minutes'))",
      (ship,"user","failed",json.dumps({"branch":"feat"}),"tests failed"))
    waiting = c("INSERT INTO run(entity_id,trigger,status,input,started_at) VALUES(?,?,?,?,datetime('now','-10 seconds'))",
                (ship,"user","running",json.dumps({"branch":"experiment"}))).lastrowid
    c("INSERT INTO event(run_id,kind,payload) VALUES(?,?,?)",
      (waiting,"permission_request",json.dumps({"type":"network","target":"https://api.openai.com","decision":"pending"})))


def j(handler, code, body):
    data = json.dumps(body, default=str).encode()
    handler.send_response(code)
    handler.send_header("Content-Type","application/json")
    handler.send_header("Content-Length",str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def read_body(handler):
    n = int(handler.headers.get("Content-Length","0"))
    return json.loads(handler.rfile.read(n) or "{}")


def list_entities(handler, qs):
    conn = db()
    sql = "SELECT * FROM entity"
    where, args = [], []
    if "kind" in qs:
        where.append("kind=?"); args.append(qs["kind"][0])
    if "parent_id" in qs:
        where.append("parent_id=?"); args.append(qs["parent_id"][0])
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY kind, parent_id, name"
    rows = [dict(r) for r in conn.execute(sql, args)]
    conn.close()
    j(handler, 200, rows)


def create_entity(handler):
    body = read_body(handler)
    conn = db()
    rid = conn.execute(
        "INSERT INTO entity(kind,name,parent_id,enabled,config) VALUES(?,?,?,?,?)",
        (body["kind"], body["name"], body.get("parent_id"), body.get("enabled", 1), json.dumps(body.get("config", {})))
    ).lastrowid
    conn.commit()
    row = dict(conn.execute("SELECT * FROM entity WHERE id=?", (rid,)).fetchone())
    conn.close()
    j(handler, 201, row)


def patch_entity(handler, eid):
    body = read_body(handler)
    fields, args = [], []
    if "enabled" in body:
        fields.append("enabled=?"); args.append(int(body["enabled"]))
    if "config" in body:
        fields.append("config=?"); args.append(json.dumps(body["config"]))
    if "name" in body:
        fields.append("name=?"); args.append(body["name"])
    if not fields:
        return j(handler, 400, {"error":"no fields"})
    args.append(eid)
    conn = db()
    conn.execute(f"UPDATE entity SET {','.join(fields)} WHERE id=?", args)
    conn.commit()
    row = dict(conn.execute("SELECT * FROM entity WHERE id=?", (eid,)).fetchone())
    conn.close()
    j(handler, 200, row)


def delete_entity(handler, eid):
    import adapter
    conn = db()
    row = conn.execute("SELECT * FROM entity WHERE id=?", (eid,)).fetchone()
    if row and row["kind"] == "hook":
        cfg = json.loads(row["config"])
        if cfg.get("scope") == "user" and cfg.get("event") and cfg.get("command"):
            adapter.remove_user_hook(cfg["event"], cfg.get("matcher", ""), cfg["command"])
    conn.execute("DELETE FROM entity WHERE id=? OR parent_id=?", (eid, eid))
    conn.commit()
    conn.close()
    j(handler, 200, {"deleted": eid})


def bulk_entities(handler):
    body = read_body(handler)
    ids = body["ids"]
    enabled = int(body["enabled"])
    conn = db()
    conn.executemany("UPDATE entity SET enabled=? WHERE id=?", [(enabled, i) for i in ids])
    conn.commit()
    conn.close()
    j(handler, 200, {"updated": len(ids), "enabled": enabled})


def list_runs(handler, qs):
    conn = db()
    sql = "SELECT r.*, e.name AS entity_name, e.kind AS entity_kind, e.parent_id AS plugin_id FROM run r JOIN entity e ON e.id=r.entity_id"
    where, args = [], []
    if "session" in qs:
        where.append("r.parent_run_id IS NULL")
    if "entity_id" in qs:
        where.append("r.entity_id=?"); args.append(qs["entity_id"][0])
    if "plugin_id" in qs:
        where.append("(e.id=? OR e.parent_id=?)"); args.extend([qs["plugin_id"][0], qs["plugin_id"][0]])
    if "status" in qs:
        where.append("r.status=?"); args.append(qs["status"][0])
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY r.started_at DESC LIMIT 200"
    rows = [dict(r) for r in conn.execute(sql, args)]
    conn.close()
    j(handler, 200, rows)


def get_run(handler, rid):
    conn = db()
    run = conn.execute("SELECT * FROM run WHERE id=?", (rid,)).fetchone()
    if not run:
        conn.close(); return j(handler, 404, {"error":"not found"})
    events = [dict(r) for r in conn.execute("SELECT * FROM event WHERE run_id=? ORDER BY id", (rid,))]
    children = [dict(r) for r in conn.execute(
        "SELECT r.id,r.entity_id,r.status,r.started_at,e.name AS entity_name FROM run r JOIN entity e ON e.id=r.entity_id WHERE r.parent_run_id=?",
        (rid,))]
    conn.close()
    j(handler, 200, {"run": dict(run), "events": events, "children": children})


def manual_run(handler, eid):
    body = read_body(handler)
    conn = db()
    rid = conn.execute(
        "INSERT INTO run(entity_id,trigger,status,input) VALUES(?,?,?,?)",
        (eid, "user", "running", json.dumps(body.get("input", {})))
    ).lastrowid
    conn.execute("UPDATE run SET status='success', ended_at=datetime('now'), output=? WHERE id=?",
                 (json.dumps({"demo": True}), rid))
    conn.execute("INSERT INTO event(run_id,kind,level,payload) VALUES(?,?,?,?)",
                 (rid, "log", "info", json.dumps({"msg": f"manual test run {rid}"})))
    conn.commit()
    conn.close()
    j(handler, 201, {"run_id": rid})


def cancel_run(handler, rid):
    conn = db()
    conn.execute("UPDATE run SET status='cancelled', ended_at=datetime('now') WHERE id=? AND status IN ('running','pending')", (rid,))
    conn.commit()
    conn.close()
    j(handler, 200, {"cancelled": rid})


def replay_run(handler, rid):
    conn = db()
    src = conn.execute("SELECT * FROM run WHERE id=?", (rid,)).fetchone()
    if not src:
        conn.close(); return j(handler, 404, {"error":"not found"})
    new_id = conn.execute(
        "INSERT INTO run(entity_id,trigger,status,input,parent_run_id) VALUES(?,?,?,?,?)",
        (src["entity_id"], "replay", "running", src["input"], src["id"])
    ).lastrowid
    conn.execute("INSERT INTO event(run_id,kind,level,payload) VALUES(?,?,?,?)",
                 (new_id, "log", "info", json.dumps({"msg": f"replay of {rid}"})))
    conn.commit()
    conn.close()
    j(handler, 201, {"replay_id": new_id, "source": rid})


def list_events(handler, qs):
    conn = db()
    sql = "SELECT ev.*, r.entity_id, e.name AS entity_name, e.kind AS entity_kind FROM event ev JOIN run r ON r.id=ev.run_id JOIN entity e ON e.id=r.entity_id"
    where, args = [], []
    for col, key in (("ev.kind","kind"),("ev.level","level"),("ev.run_id","run_id"),("r.entity_id","entity_id")):
        if key in qs:
            where.append(f"{col}=?"); args.append(qs[key][0])
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ev.id DESC LIMIT 500"
    rows = [dict(r) for r in conn.execute(sql, args)]
    conn.close()
    if qs.get("format", [""])[0] == "ndjson":
        body = "\n".join(json.dumps(r, default=str) for r in rows).encode()
        handler.send_response(200)
        handler.send_header("Content-Type","application/x-ndjson")
        handler.send_header("Content-Disposition","attachment; filename=audit.ndjson")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return
    j(handler, 200, rows)


def add_event(handler, rid):
    body = read_body(handler)
    conn = db()
    eid = conn.execute(
        "INSERT INTO event(run_id, kind, level, payload) VALUES(?,?,?,?)",
        (rid, body["kind"], body.get("level"), json.dumps(body["payload"]))
    ).lastrowid
    conn.commit()
    conn.close()
    j(handler, 201, {"event_id": eid})


def import_real(handler):
    import adapter
    conn = db()
    adapter.import_into(conn)
    n = conn.execute("SELECT COUNT(*) FROM entity WHERE json_extract(config,'$.source')='real'").fetchone()[0]
    conn.close()
    j(handler, 200, {"imported": n})


def list_sessions(handler):
    import adapter
    j(handler, 200, adapter.scan_sessions())


def get_session(handler, qs):
    import adapter
    path = qs.get("path", [""])[0]
    if not path:
        return j(handler, 400, {"error": "path required"})
    d = adapter.read_session(path)
    if d is None:
        return j(handler, 404, {"error": "not found"})
    j(handler, 200, d)


def list_memory(handler):
    import adapter
    j(handler, 200, adapter.scan_memory())


def get_memory(handler, qs):
    import adapter
    path = qs.get("path", [""])[0]
    if not adapter.is_allowed_memory_path(path):
        return j(handler, 403, {"error": "path not allowed"})
    p = Path(path)
    if not p.exists():
        return j(handler, 404, {"error": "not found"})
    j(handler, 200, adapter.read_memory(path))


def put_memory(handler, qs):
    import adapter
    path = qs.get("path", [""])[0]
    if not adapter.is_allowed_memory_path(path):
        return j(handler, 403, {"error": "path not allowed"})
    body = read_body(handler)
    j(handler, 200, adapter.write_memory(path, body["content"]))


def create_user_hook(handler):
    import adapter
    body = read_body(handler)
    event = body["event"]
    matcher = body.get("matcher", "") or ""
    if body.get("script_body"):
        cmd = adapter.save_inline_hook_script(event, matcher, body["script_body"])
    else:
        cmd = body["command"]
    adapter.add_user_hook(event, matcher, cmd)
    conn = db()
    name = f"{event}:{matcher or '*'}"
    cfg = {"source": "real", "scope": "user", "event": event,
           "matcher": matcher or "*", "command": cmd, "mode": "blocking"}
    rid = conn.execute(
        "INSERT INTO entity(kind,name,config) VALUES('hook',?,?)",
        (name, json.dumps(cfg))).lastrowid
    conn.commit()
    conn.close()
    j(handler, 201, {"id": rid, "command": cmd, "settings_backup": str(adapter.CLAUDE_HOME / "settings.json.panel.bak")})


def delete_user_hook(handler, eid):
    import adapter
    conn = db()
    row = conn.execute("SELECT * FROM entity WHERE id=? AND kind='hook'", (eid,)).fetchone()
    if not row:
        conn.close()
        return j(handler, 404, {"error": "not found"})
    cfg = json.loads(row["config"])
    if cfg.get("scope") == "user" and cfg.get("event") and cfg.get("command"):
        adapter.remove_user_hook(cfg["event"], cfg.get("matcher", ""), cfg["command"])
    conn.execute("DELETE FROM entity WHERE id=?", (eid,))
    conn.commit()
    conn.close()
    j(handler, 200, {"deleted": eid})


def delete_event(handler, evid):
    conn = db()
    conn.execute("DELETE FROM event WHERE id=?", (evid,))
    conn.commit()
    conn.close()
    j(handler, 200, {"deleted": evid})


def purge_logs(handler):
    body = read_body(handler)
    days = int(body.get("older_than_days", 30))
    conn = db()
    n = conn.execute("DELETE FROM event WHERE kind='log' AND created_at < datetime('now', ?)", (f"-{days} days",)).rowcount
    conn.commit()
    conn.close()
    j(handler, 200, {"deleted": n})


def decide_permission(handler, evid):
    body = read_body(handler)
    decision = body["decision"]
    conn = db()
    ev = conn.execute("SELECT * FROM event WHERE id=?", (evid,)).fetchone()
    if not ev or ev["kind"] != "permission_request":
        conn.close(); return j(handler, 404, {"error":"not a permission_request"})
    payload = json.loads(ev["payload"])
    payload["decision"] = decision
    conn.execute("UPDATE event SET payload=? WHERE id=?", (json.dumps(payload), evid))
    if decision.startswith("allow"):
        run = conn.execute("SELECT entity_id FROM run WHERE id=?", (ev["run_id"],)).fetchone()
        conn.execute(
            "INSERT INTO entity(kind,name,parent_id,config) VALUES(?,?,?,?)",
            ("permission_grant", f"{payload['type']}:{payload['target']}", run["entity_id"],
             json.dumps({"scope": decision, "target": payload["target"], "type": payload["type"]})))
    conn.commit()
    conn.close()
    j(handler, 200, {"event_id": evid, "decision": decision})


def get_artifact(handler, evid):
    conn = db()
    ev = conn.execute("SELECT * FROM event WHERE id=? AND kind='artifact'", (evid,)).fetchone()
    conn.close()
    if not ev:
        return j(handler, 404, {"error":"not found"})
    payload = json.loads(ev["payload"])
    body = (payload.get("preview") or json.dumps(payload, indent=2)).encode()
    handler.send_response(200)
    handler.send_header("Content-Type","application/octet-stream")
    handler.send_header("Content-Disposition", f"attachment; filename={payload.get('name', f'artifact-{evid}')}")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def get_settings(handler):
    conn = db()
    rows = list(conn.execute("SELECT id,name,config FROM entity WHERE kind='setting' ORDER BY name"))
    conn.close()
    j(handler, 200, [{"id":r["id"], "key":r["name"], "value": json.loads(r["config"])["value"]} for r in rows])


def patch_setting(handler, sid):
    body = read_body(handler)
    conn = db()
    conn.execute("UPDATE entity SET config=? WHERE id=? AND kind='setting'",
                 (json.dumps({"value": body["value"]}), sid))
    conn.commit()
    conn.close()
    j(handler, 200, {"updated": sid})


def stream(handler):
    handler.send_response(200)
    handler.send_header("Content-Type","text/event-stream")
    handler.send_header("Cache-Control","no-cache")
    handler.end_headers()
    last_run, last_event = 0, 0
    while True:
        conn = db()
        runs = list(conn.execute("SELECT r.*, e.name AS entity_name, e.kind AS entity_kind FROM run r JOIN entity e ON e.id=r.entity_id WHERE r.id>? ORDER BY r.id", (last_run,)))
        events = list(conn.execute("SELECT ev.*, e.name AS entity_name, e.kind AS entity_kind FROM event ev JOIN run r ON r.id=ev.run_id JOIN entity e ON e.id=r.entity_id WHERE ev.id>? ORDER BY ev.id", (last_event,)))
        conn.close()
        for r in runs:
            last_run = r["id"]
            handler.wfile.write(f"event: run\ndata: {json.dumps(dict(r), default=str)}\n\n".encode())
        for e in events:
            last_event = e["id"]
            handler.wfile.write(f"event: event\ndata: {json.dumps(dict(e), default=str)}\n\n".encode())
        handler.wfile.flush()
        time.sleep(1)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        u = urlparse(self.path); qs = parse_qs(u.query); p = u.path
        if p in ("/", "/index.html"):
            body = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type","text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/api/entities":          list_entities(self, qs)
        elif p == "/api/runs":              list_runs(self, qs)
        elif p.startswith("/api/runs/"):    get_run(self, int(p.rsplit("/",1)[1]))
        elif p == "/api/events":            list_events(self, qs)
        elif p.startswith("/api/artifacts/"): get_artifact(self, int(p.rsplit("/",1)[1]))
        elif p == "/api/settings":          get_settings(self)
        elif p == "/api/sessions":          list_sessions(self)
        elif p.startswith("/api/sessions/"): get_session(self, qs)
        elif p == "/api/memory":            list_memory(self)
        elif p == "/api/memory/file":       get_memory(self, qs)
        elif p == "/api/stream":            stream(self)
        else: j(self, 404, {"error":"not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/entities":            create_entity(self)
        elif p == "/api/entities/bulk":     bulk_entities(self)
        elif p.startswith("/api/entities/") and p.endswith("/run"):
            manual_run(self, int(p.split("/")[3]))
        elif p.startswith("/api/runs/") and p.endswith("/cancel"):
            cancel_run(self, int(p.split("/")[3]))
        elif p.startswith("/api/runs/") and p.endswith("/replay"):
            replay_run(self, int(p.split("/")[3]))
        elif p.startswith("/api/events/") and p.endswith("/decide"):
            decide_permission(self, int(p.split("/")[3]))
        elif p.startswith("/api/runs/") and p.endswith("/events"):
            add_event(self, int(p.split("/")[3]))
        elif p == "/api/logs/purge":        purge_logs(self)
        elif p == "/api/import":            import_real(self)
        elif p == "/api/hooks":             create_user_hook(self)
        else: j(self, 404, {"error":"not found"})

    def do_PATCH(self):
        p = urlparse(self.path).path
        if p.startswith("/api/entities/"):  patch_entity(self, int(p.rsplit("/",1)[1]))
        elif p.startswith("/api/settings/"): patch_setting(self, int(p.rsplit("/",1)[1]))
        else: j(self, 404, {"error":"not found"})

    def do_PUT(self):
        u = urlparse(self.path); qs = parse_qs(u.query)
        if u.path == "/api/memory/file":
            put_memory(self, qs)
        else: j(self, 404, {"error":"not found"})

    def do_DELETE(self):
        p = urlparse(self.path).path
        if p.startswith("/api/entities/"):  delete_entity(self, int(p.rsplit("/",1)[1]))
        elif p.startswith("/api/events/"):  delete_event(self, int(p.rsplit("/",1)[1]))
        else: j(self, 404, {"error":"not found"})


if __name__ == "__main__":
    init_db()
    print("panel on http://127.0.0.1:7780")
    ThreadingHTTPServer(("127.0.0.1", 7780), H).serve_forever()
