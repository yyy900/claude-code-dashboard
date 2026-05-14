"""Read real entities out of ~/.claude into panel's entity table.

Sources:
  ~/.claude/plugins/installed_plugins.json  → plugins
  <installPath>/.claude-plugin/plugin.json  → manifest
  <installPath>/skills/*/SKILL.md           → plugin-scoped skills
  ~/.claude/skills/*/SKILL.md               → user-scoped skills
  ~/.claude/settings.json `hooks`           → user-scoped hooks

Entities written here carry config.source='real'. Re-running import wipes
prior real entities (preserves seed demo + history).
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_HOME = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_HOME / "projects"
SETTINGS_PATH = CLAUDE_HOME / "settings.json"
PANEL_HOOKS_DIR = CLAUDE_HOME / "panel-hooks"
WORKSPACE_ROOTS = [Path.home() / "coding"]
ALLOWED_MEMORY_ROOTS = [CLAUDE_HOME, *WORKSPACE_ROOTS]


def read_user_settings():
    if not SETTINGS_PATH.exists():
        return {}
    return json.loads(SETTINGS_PATH.read_text())


def write_user_settings(data):
    if SETTINGS_PATH.exists():
        (CLAUDE_HOME / "settings.json.panel.bak").write_text(SETTINGS_PATH.read_text())
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(SETTINGS_PATH)


def save_inline_hook_script(event, matcher, body):
    PANEL_HOOKS_DIR.mkdir(exist_ok=True)
    name = f"{event}-{matcher or 'all'}-{int(time.time())}.sh"
    path = PANEL_HOOKS_DIR / name
    if not body.lstrip().startswith("#!"):
        body = "#!/usr/bin/env bash\nset -e\n\n" + body
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


def add_user_hook(event, matcher, command, hook_type="command"):
    data = read_user_settings()
    hooks = data.setdefault("hooks", {})
    matchers = hooks.setdefault(event, [])
    group = None
    for m in matchers:
        if m.get("matcher", "*") == (matcher or "*"):
            group = m
            break
    if group is None:
        group = {} if not matcher or matcher == "*" else {"matcher": matcher}
        matchers.append(group)
    group.setdefault("hooks", []).append({"type": hook_type, "command": command})
    write_user_settings(data)


def remove_user_hook(event, matcher, command):
    data = read_user_settings()
    matchers = data.get("hooks", {}).get(event, [])
    for m in matchers:
        if m.get("matcher", "*") == (matcher or "*"):
            m["hooks"] = [h for h in m.get("hooks", []) if h.get("command") != command]
            break
    matchers[:] = [m for m in matchers if m.get("hooks")]
    if not matchers and event in data.get("hooks", {}):
        del data["hooks"][event]
    write_user_settings(data)


def iso_mtime(p):
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()


def read_session_meta(path):
    p = Path(path)
    cwd, first_user, first_ts, last_ts = "", "", "", ""
    count = 0
    with open(p, errors="replace") as f:
        for line in f:
            count += 1
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = d.get("timestamp")
            if ts:
                if not first_ts:
                    first_ts = ts
                last_ts = ts
            if not cwd and d.get("cwd"):
                cwd = d["cwd"]
            if not first_user and d.get("type") == "user":
                m = d.get("message", {})
                c = m.get("content", "")
                if isinstance(c, list) and c:
                    c0 = c[0]
                    c = c0.get("text", str(c0)) if isinstance(c0, dict) else str(c0)
                first_user = str(c).strip()[:200]
    return {
        "id": p.stem,
        "path": str(p),
        "project_dir": p.parent.name,
        "cwd": cwd,
        "first_user": first_user,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "message_count": count,
        "size": p.stat().st_size,
        "mtime": iso_mtime(p),
    }


def scan_sessions():
    if not PROJECTS_DIR.exists():
        return []
    out = []
    for proj in PROJECTS_DIR.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            out.append(read_session_meta(f))
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def fmt_message(m):
    t = m.get("type")
    ts = m.get("timestamp", "")
    out = {"type": t, "time": ts}
    if t in ("user", "assistant"):
        msg = m.get("message", {})
        content = msg.get("content", "")
        tool_calls = []
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    ct = c.get("type")
                    if ct == "text":
                        parts.append(c.get("text", ""))
                    elif ct == "tool_use":
                        tool_calls.append(c.get("name"))
                        parts.append(f"⚙ tool_use: {c.get('name')}")
                    elif ct == "tool_result":
                        parts.append("← tool_result")
                    else:
                        parts.append(f"[{ct}]")
                else:
                    parts.append(str(c))
            content = "\n".join(parts)
        out["role"] = msg.get("role") or t
        out["text"] = str(content)[:1200]
        if tool_calls:
            out["tools"] = tool_calls
    elif t == "system":
        c = m.get("content", "")
        out["text"] = str(c)[:400]
    elif t == "attachment":
        a = m.get("attachment", {})
        out["text"] = f"[attachment: {a.get('type','?')}]"
    elif t == "file-history-snapshot":
        snap = m.get("snapshot", {})
        n = len(snap.get("trackedFileBackups", {}))
        out["text"] = f"file snapshot · {n} tracked files"
    elif t == "permission-mode":
        out["text"] = f"permission mode: {m.get('mode','?')}"
    else:
        out["text"] = f"[{t}]"
    return out


def read_session(path):
    p = Path(path)
    if not p.exists():
        return None
    msgs = []
    with open(p, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msgs.append(fmt_message(json.loads(line)))
            except json.JSONDecodeError:
                continue
    return {"meta": read_session_meta(p), "messages": msgs}


def is_allowed_memory_path(path):
    if not path:
        return False
    p = Path(path).expanduser().resolve()
    if p.suffix != ".md":
        return False
    for root in ALLOWED_MEMORY_ROOTS:
        try:
            p.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def scan_memory():
    items = []
    user_md = CLAUDE_HOME / "CLAUDE.md"
    if user_md.exists():
        items.append({"path": str(user_md), "kind": "user", "name": "~/.claude/CLAUDE.md",
                      "size": user_md.stat().st_size, "mtime": iso_mtime(user_md)})
    if PROJECTS_DIR.exists():
        for proj in sorted(PROJECTS_DIR.iterdir()):
            memdir = proj / "memory"
            if not memdir.is_dir():
                continue
            for mf in sorted(memdir.glob("*.md")):
                items.append({"path": str(mf), "kind": "auto",
                              "name": mf.name, "project": proj.name,
                              "size": mf.stat().st_size, "mtime": iso_mtime(mf)})
    for root in WORKSPACE_ROOTS:
        if not root.exists():
            continue
        for cm in root.rglob("CLAUDE.md"):
            if any(part.startswith(".") or part == "node_modules" for part in cm.relative_to(root).parts):
                continue
            items.append({"path": str(cm), "kind": "project",
                          "name": str(cm.relative_to(root.parent)),
                          "size": cm.stat().st_size, "mtime": iso_mtime(cm)})
    return items


def read_memory(path):
    p = Path(path)
    return {"path": str(p), "content": p.read_text(),
            "mtime": iso_mtime(p), "size": p.stat().st_size}


def write_memory(path, content):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"saved": str(p), "size": p.stat().st_size}


def parse_frontmatter(text):
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    lines = text[3:end].splitlines()
    out = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1; continue
        if ":" in line and not line[:1].isspace():
            k, v = line.split(":", 1)
            key = k.strip()
            v = v.strip()
            if v in ("|", "|-", "|+", ">", ">-", ">+"):
                buf = []
                i += 1
                while i < len(lines) and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
                    buf.append(lines[i].strip())
                    i += 1
                out[key] = " ".join(b for b in buf if b)
                continue
            out[key] = v
        i += 1
    return out


def scan_installed_plugins():
    f = CLAUDE_HOME / "plugins" / "installed_plugins.json"
    if not f.exists():
        return []
    data = json.loads(f.read_text())
    out = []
    for full_name, installs in data.get("plugins", {}).items():
        name, _, market = full_name.partition("@")
        for inst in installs:
            install_path = Path(inst["installPath"])
            manifest = {}
            mf = install_path / ".claude-plugin" / "plugin.json"
            if mf.exists():
                manifest = json.loads(mf.read_text())
            out.append({
                "name": name,
                "market": market,
                "version": inst.get("version"),
                "install_path": str(install_path),
                "installed_at": inst.get("installedAt"),
                "manifest": manifest,
            })
    return out


def scan_plugin_children(install_path):
    p = Path(install_path)
    skills, hooks = [], []
    sdir = p / "skills"
    if sdir.exists():
        for sd in sorted(sdir.iterdir()):
            if (sd / "SKILL.md").exists():
                skills.append((sd.name, parse_frontmatter((sd / "SKILL.md").read_text())))
    hjson = p / ".claude-plugin" / "hooks.json"
    if hjson.exists():
        for event, matchers in json.loads(hjson.read_text()).items():
            for m in matchers:
                hooks.append({"event": event, "config": m})
    return skills, hooks


def scan_user_skills():
    base = CLAUDE_HOME / "skills"
    if not base.exists():
        return []
    out = []
    for sd in sorted(base.iterdir()):
        f = sd / "SKILL.md"
        if f.exists():
            out.append((sd.name, parse_frontmatter(f.read_text())))
    return out


def scan_user_hooks():
    f = CLAUDE_HOME / "settings.json"
    if not f.exists():
        return []
    data = json.loads(f.read_text())
    out = []
    for event, matchers in data.get("hooks", {}).items():
        for m in matchers:
            matcher = m.get("matcher", "*")
            for h in m.get("hooks", []):
                out.append({"event": event, "matcher": matcher,
                            "command": h.get("command"), "type": h.get("type", "command")})
    return out


def import_into(conn):
    c = conn.execute
    c("DELETE FROM entity WHERE json_extract(config,'$.source') = 'real'")
    for p in scan_installed_plugins():
        m = p["manifest"]
        cfg = {
            "source": "real",
            "version": p["version"],
            "market": p["market"],
            "install_path": p["install_path"],
            "installed_at": p["installed_at"],
            "author": m.get("author"),
            "description": m.get("description"),
            "homepage": m.get("homepage"),
            "risk": "low",
        }
        pid = c("INSERT INTO entity(kind,name,config) VALUES('plugin',?,?)",
                (p["name"], json.dumps(cfg, default=str))).lastrowid
        skills, hooks = scan_plugin_children(p["install_path"])
        for sname, smeta in skills:
            scfg = {"source": "real",
                    "description": (smeta.get("description") or "")[:300],
                    "from_plugin": p["name"]}
            c("INSERT INTO entity(kind,name,parent_id,config) VALUES('skill',?,?,?)",
              (sname, pid, json.dumps(scfg)))
        for h in hooks:
            hcfg = {"source": "real", "event": h["event"], **h["config"]}
            hname = f"{h['event']}:{h['config'].get('matcher','*')}"
            c("INSERT INTO entity(kind,name,parent_id,config) VALUES('hook',?,?,?)",
              (hname, pid, json.dumps(hcfg)))
    for name, meta in scan_user_skills():
        cfg = {"source": "real",
               "description": (meta.get("description") or "")[:300],
               "scope": "user"}
        c("INSERT INTO entity(kind,name,config) VALUES('skill',?,?)",
          (name, json.dumps(cfg)))
    for h in scan_user_hooks():
        cfg = {"source": "real", "scope": "user", "event": h["event"],
               "matcher": h["matcher"], "command": h["command"], "mode": "blocking"}
        hname = f"{h['event']}:{h['matcher']}"
        c("INSERT INTO entity(kind,name,config) VALUES('hook',?,?)",
          (hname, json.dumps(cfg)))
    conn.commit()


if __name__ == "__main__":
    import sqlite3
    conn = sqlite3.connect("panel.db")
    import_into(conn)
    print("imported")
