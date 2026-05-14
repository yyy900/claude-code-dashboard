CREATE TABLE entity (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL,          -- plugin | skill | hook | setting | permission_grant
  name        TEXT NOT NULL,
  parent_id   INTEGER REFERENCES entity(id),
  enabled     INTEGER NOT NULL DEFAULT 1,
  config      TEXT NOT NULL DEFAULT '{}',
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE run (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_run_id INTEGER REFERENCES run(id),
  entity_id     INTEGER NOT NULL REFERENCES entity(id),
  trigger       TEXT NOT NULL,        -- user | hook | workflow
  status        TEXT NOT NULL,        -- pending | running | success | failed | cancelled | timeout
  input         TEXT,
  output        TEXT,
  error         TEXT,
  started_at    TEXT NOT NULL DEFAULT (datetime('now')),
  ended_at      TEXT
);

CREATE TABLE event (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      INTEGER NOT NULL REFERENCES run(id),
  kind        TEXT NOT NULL,          -- log | permission_request | resource_access | artifact
  level       TEXT,                   -- debug | info | warn | error  (logs only)
  payload     TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_entity_kind     ON entity(kind, parent_id);
CREATE INDEX idx_run_parent      ON run(parent_run_id);
CREATE INDEX idx_run_entity      ON run(entity_id, started_at DESC);
CREATE INDEX idx_event_run       ON event(run_id, id);
CREATE INDEX idx_event_kind      ON event(kind, created_at DESC);
