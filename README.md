# claude-code-dashboard

**English** · [中文](README.zh.md)

A local control panel for Claude Code — see your plugins, skills, hooks, sessions, and memory in one place. Edit `CLAUDE.md` files inline. Add hooks that write back into `~/.claude/settings.json`. Stream live tool activity over SSE.

~1800 lines, pure Python stdlib + SQLite + vanilla JS. No Node, no React, no pip install. Just `python3 server.py`.

---

## What it does

Claude Code keeps everything on disk under `~/.claude/`: installed plugins, your skills, the hook config, every session transcript, and the auto-memory it accumulates. This dashboard reads all of it, renders it in a real UI, and lets you edit the pieces you can safely edit.

**7 tabs:**

| Tab | What you see |
| --- | --- |
| **Plugins** | Every plugin from `~/.claude/plugins/installed_plugins.json` with manifest, version, author, declared permissions. Toggle enabled/disabled. Install a new one. |
| **Skills** | Every skill — user-scope (`~/.claude/skills/*/SKILL.md`) and plugin-scope. Description parsed from YAML frontmatter (block scalars included). Test-run any skill from the UI. |
| **Hooks** | Every hook from `~/.claude/settings.json`. **+ Add hook** writes a new entry back to `settings.json` (with `.panel.bak`) and drops an inline script into `~/.claude/panel-hooks/`. |
| **Sessions** | Every Claude Code conversation, read straight from `~/.claude/projects/<encoded>/<uuid>.jsonl`. Grouped by recency: **Active / Today / This week / Older**. Click one to see the transcript. |
| **Memory** | All `CLAUDE.md` files plus per-project auto-memory (`~/.claude/projects/<path>/memory/*.md`). Edit in a textarea, click Save, it writes back to disk. Whitelist-guarded. |
| **Live** | SSE stream of run/event activity from the panel database. Pending permission requests get inline allow/deny buttons. |
| **Settings** | 14 panel-wide preferences (retention, redaction rules, etc.) stored as `entity(kind='setting')`. |

## Why this exists

You're already running Claude Code. The data is already on disk. You probably want to:

- **Audit** which sessions exist on your machine (`/exit` doesn't delete them — the `.jsonl` stays forever)
- **Edit `CLAUDE.md`** without opening an editor in another window
- **Tweak auto-memory** the agent built up about you
- **Add a hook** without hand-editing JSON and worrying about a stray comma
- **Watch tool calls live** while another Claude session is running

That's the whole product.

## Quick start

```bash
git clone git@github.com:yyy900/claude-code-dashboard.git
cd claude-code-dashboard
python3 server.py
```

Open <http://127.0.0.1:7780>. Default port: 7780.

The first run creates `panel.db` next to the script and seeds a few demo entities so the UI isn't empty. Click **Import from ~/.claude** in the Plugins / Skills / Hooks tab to pull your actual data.

To reset: `rm panel.db && python3 server.py`.

If something else is on port 7780 (e.g. an old server you forgot to kill):

```bash
lsof -i :7780      # find PID
kill <pid>
```

## Architecture

Three tables. One database. Everything else is a query.

```
entity (kind=plugin | skill | hook | setting | permission_grant)
  id · name · parent_id · enabled · config(JSON) · created_at

run (a session = root run with parent_run_id=NULL; child runs nest)
  id · parent_run_id · entity_id · trigger · status · input · output · error · started_at · ended_at

event (logs, permission requests, resource access, artifacts — all here, differ by kind)
  id · run_id · kind · level · payload(JSON) · created_at
```

The 12 capability modules people usually spec for a control plane (Plugin / Skill / Hook / Session / Run / Permission / Resource audit / Logs / Artifacts / Settings / Live status / Lifecycle) all collapse onto these three tables. `permission_grant` is just an `entity`. `setting` is just an `entity`. A "session" is just a `run` with no parent. Audit views are `SELECT * FROM event WHERE kind=?` with filters.

## Files

```
server.py     ~480 lines  — HTTP server, 18 endpoints, SSE stream
adapter.py    ~370 lines  — read ~/.claude (plugins, skills, hooks, sessions, memory)
                            and safely write hooks/memory back
recorder.py   ~125 lines  — Claude Code hook wrapper that posts to the panel
index.html    ~810 lines  — 7-tab UI, no build step
schema.sql       37 lines — three tables, five indexes
```

## Wire it to your real Claude Code

By default the panel is a viewer. To capture live tool calls into the `run` and `event` tables, drop `recorder.py` into your Claude Code hook config:

```jsonc
// ~/.claude/settings.json
"hooks": {
  "SessionStart": [{ "hooks": [{ "type":"command",
    "command":"/path/to/claude-code-dashboard/recorder.py session_start" }] }],
  "PreToolUse":   [{ "matcher":"*", "hooks":[{ "type":"command",
    "command":"/path/to/claude-code-dashboard/recorder.py pre" }] }],
  "PostToolUse":  [{ "matcher":"*", "hooks":[{ "type":"command",
    "command":"/path/to/claude-code-dashboard/recorder.py post" }] }],
  "Stop":         [{ "hooks":[{ "type":"command",
    "command":"/path/to/claude-code-dashboard/recorder.py session_end" }] }]
}
```

Then start the panel server before launching Claude Code. Every tool call gets logged.

**Permission gating:** set `PANEL_GATE=1` and `PreToolUse` will block until you click allow/deny in the Live tab.

```bash
PANEL_GATE=1 PANEL_GATE_TOOLS=Bash,Write,Edit claude
```

## Adding a hook from the panel

Hooks tab → **+ Add hook**. Pick the event (8 of them — see below), optionally a matcher, paste a bash script, click save. The panel:

1. Writes the script to `~/.claude/panel-hooks/<event>-<matcher>-<ts>.sh`, `chmod +x`
2. Appends `{type:"command", command:"<that path>"}` to `~/.claude/settings.json` under the matching event
3. Copies the previous `settings.json` to `settings.json.panel.bak` before writing

Next event fires it — no Claude Code restart.

### Claude Code hook events (what each one is for)

| Event | When | Useful for |
| --- | --- | --- |
| `UserPromptSubmit` | Before Claude sees your prompt | stdout is **prepended to the prompt**; exit 2 cancels the prompt |
| `PreToolUse` | Before any tool call | exit 2 **blocks** the call (with `matcher:"Bash"` you can refuse `rm -rf` etc.) |
| `PostToolUse` | After a tool returns | log, notify, auto-format (`PostToolUse` + `matcher:"Edit"` → `ruff format`) |
| `SubagentStop` | Task-spawned subagent finished | record sub-agent outcomes |
| `SessionStart` | Session opens | initialize state, open a run |
| `Stop` | Session ends (`/exit`) | flush / mark run complete |
| `Notification` | Claude waits on user input | desktop notify |
| `PreCompact` | Before context compression | archive transcript before it shrinks |

> Want to gate sub-agent (Task-tool) calls? Use `PreToolUse` with `matcher:"Task"`. There is no separate `PreAgent` event.

## Editing memory

Memory tab → pick a file on the left → edit in the textarea → **Save**. Writes are restricted to `~/.claude/**/*.md` and `~/coding/**/*.md`; anything else returns HTTP 403.

Three groups:

- **User-level** — `~/.claude/CLAUDE.md` (instructions Claude reads every session)
- **Project** — every `CLAUDE.md` under your workspace (`~/coding/**/CLAUDE.md` by default)
- **Auto-memory** — `~/.claude/projects/<path>/memory/*.md` (per-project memory the agent built; survives `/exit`)

## On `/exit` and persistence

`/exit` doesn't delete anything.

- Session transcripts stay at `~/.claude/projects/<path>/<uuid>.jsonl`
- Auto-memory stays at `~/.claude/projects/<path>/memory/*.md` and is **reloaded on the next session in that same directory**
- Panel's own `panel.db` survives too

To actually clear, delete the files manually.

## Endpoints

The server exposes a small REST surface (full list in `server.py`):

```
GET    /api/entities?kind=plugin|skill|hook|setting|permission_grant
POST   /api/entities                         install new
PATCH  /api/entities/<id>                    toggle/edit
DELETE /api/entities/<id>                    remove (auto-syncs settings.json for user hooks)
POST   /api/entities/bulk                    {ids, enabled}

GET    /api/runs?session=1&status=...&entity_id=...
GET    /api/runs/<id>                        run + events + child runs
POST   /api/entities/<id>/run                manual test run
POST   /api/runs/<id>/cancel | /replay
POST   /api/runs/<id>/events                 used by recorder.py

GET    /api/events?kind=...&level=...&format=ndjson
POST   /api/events/<id>/decide               {decision: allow_once|allow_session|allow_long|deny}
DELETE /api/events/<id>
POST   /api/logs/purge                       {older_than_days}

POST   /api/import                           rescan ~/.claude
POST   /api/hooks                            add user hook (writes settings.json)

GET    /api/sessions                         list Claude Code sessions
GET    /api/sessions/x?path=...              transcript

GET    /api/memory                           list CLAUDE.md + auto-memory
GET    /api/memory/file?path=...
PUT    /api/memory/file?path=...

GET    /api/settings                         panel preferences
PATCH  /api/settings/<id>

GET    /api/stream                           SSE: run + event
```

## Security

- **Memory write whitelist**: writes are constrained to `~/.claude/**` and `~/coding/**` `.md` files. Other paths return 403.
- **Hook writes** to `settings.json` are atomic (tmp file + rename) and back up the prior version to `settings.json.panel.bak`.
- **No auth.** This is a localhost tool. Don't expose 7780 to the network.
- **Recorder permission gate** only blocks when `PANEL_GATE=1`. Default is advisory.

## Roadmap (things I haven't built)

- Reading per-session token / cost from jsonl (the data is there, just not parsed)
- Permission grants persistence across sessions (currently stored but not re-injected into PreToolUse decisions)
- Diff-based memory edits with undo
- Multi-machine view (right now this is single-host only)

## Why three primitives instead of twelve

Because data dominates. Spec out enough "modules" for any control plane and you find they're projections of the same `entity` / `run` / `event` triple. Adding a real feature here usually means one new SQL filter, not a new table.

## License

MIT.
