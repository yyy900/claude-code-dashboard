# claude-code-dashboard

[English](README.md) · **中文**

Claude Code 的本地控制面板 — 一个页面看完 plugin / skill / hook / session / memory,直接编辑 `CLAUDE.md`,直接新增 hook 写回 `~/.claude/settings.json`,SSE 实时流看工具调用。

约 1800 行,纯 Python stdlib + SQLite + 原生 JS。没有 Node、没有 React、不用 pip install。一句 `python3 server.py` 跑起来。

---

## 它干什么

Claude Code 把所有东西都放磁盘 `~/.claude/`:已装的 plugin、你的 skill、hook 配置、每个 session 的对话记录、agent 自己积累的 auto-memory。这个面板把它们读出来、渲染成真正的 UI、把你能安全改的部分给你改。

**7 个 tab:**

| Tab | 看到什么 |
| --- | --- |
| **Plugins** | 从 `~/.claude/plugins/installed_plugins.json` 读到的所有 plugin,带 manifest、版本、作者、声明的权限。可启停。可装新的。 |
| **Skills** | 所有 skill — user 级 (`~/.claude/skills/*/SKILL.md`) 和 plugin 级。description 从 YAML frontmatter 解析(YAML `|` block scalar 也支持)。可以 UI 里直接 test run。 |
| **Hooks** | 从 `~/.claude/settings.json` 读到的所有 hook。**+ Add hook** 会把新条目写回 `settings.json`(带 `.panel.bak` 备份),inline 脚本落到 `~/.claude/panel-hooks/`。 |
| **Sessions** | 你所有的 Claude Code 对话,直接从 `~/.claude/projects/<encoded>/<uuid>.jsonl` 读。按时间分组:**Active / Today / This week / Older**。点开看 transcript。 |
| **Memory** | 所有 `CLAUDE.md` + 项目级 auto-memory(`~/.claude/projects/<path>/memory/*.md`)。textarea 改,点 Save 写回磁盘。白名单保护。 |
| **Live** | 面板数据库里 run/event 的 SSE 实时流。permission_request 状态为 pending 的会带 allow/deny 按钮。 |
| **Settings** | 面板自己的 14 个偏好(保留期、敏感词脱敏等),底层存成 `entity(kind='setting')`。 |

## 为什么写这个

你已经在用 Claude Code,数据就在磁盘上。你大概率想:

- **审计**机器上有哪些 session(`/exit` 不删 `.jsonl`,文件永远留着)
- **改 `CLAUDE.md`** 不用另开编辑器
- **调 auto-memory**(agent 自己写的那些关于你的备忘)
- **加 hook** 不用手动改 JSON 担心一个逗号写崩
- **看实时工具调用**,另一个 Claude session 在跑时

整个产品就这点事。

## 启动

```bash
git clone git@github.com:yyy900/claude-code-dashboard.git
cd claude-code-dashboard
python3 server.py
```

打开 <http://127.0.0.1:7780>。默认端口 7780。

首次运行会在脚本旁建 `panel.db` 并 seed 几条 demo 数据,免得 UI 是空的。点 Plugins / Skills / Hooks tab 顶部的 **Import from ~/.claude** 拉真实数据。

重置:`rm panel.db && python3 server.py`。

如果 7780 被别的占了(比如忘了杀的旧 server):

```bash
lsof -i :7780      # 找 PID
kill <pid>
```

## 架构

三张表。一个 DB。其余都是 query。

```
entity (kind=plugin | skill | hook | setting | permission_grant)
  id · name · parent_id · enabled · config(JSON) · created_at

run (session = parent_run_id 为 NULL 的根 run;子 run 嵌套)
  id · parent_run_id · entity_id · trigger · status · input · output · error · started_at · ended_at

event (日志、权限请求、资源访问、产物 —— 都在这,kind 区分)
  id · run_id · kind · level · payload(JSON) · created_at
```

控制面板常见的那 12 个能力模块(Plugin / Skill / Hook / Session / Run / 权限 / 资源审计 / 日志 / 产物 / 设置 / 实时 / 生命周期)全部塌缩在这三张表上。`permission_grant` 是 `entity`。`setting` 是 `entity`。"session" 是 `parent_run_id` 为 NULL 的 `run`。各种审计视图都是 `SELECT * FROM event WHERE kind=?` 加 filter。

## 文件

```
server.py     ~480 行  — HTTP 服务,18 个 endpoint,SSE 流
adapter.py    ~370 行  — 读 ~/.claude(plugins / skills / hooks / sessions / memory)
                          + 安全写回 hooks / memory
recorder.py   ~125 行  — Claude Code hook 包装,把事件 POST 到面板
index.html    ~810 行  — 7 tab UI,无构建步骤
schema.sql       37 行 — 三表 + 五索引
```

## 接到真实 Claude Code

默认面板只读。要把实时工具调用打进 `run`/`event` 表,把 `recorder.py` 配到 Claude Code 的 hook:

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

然后先启 panel server,再开 Claude Code。每次工具调用都会落库。

**权限阻塞模式:**`PANEL_GATE=1` 时 `PreToolUse` 会阻塞,等你在 Live tab 点 allow/deny。

```bash
PANEL_GATE=1 PANEL_GATE_TOOLS=Bash,Write,Edit claude
```

## 在面板里加 hook

Hooks tab → **+ Add hook**。选 event(下面列了 8 种)、可选 matcher、贴 bash 脚本、点保存。面板会:

1. 把脚本写到 `~/.claude/panel-hooks/<event>-<matcher>-<ts>.sh`,`chmod +x`
2. 在 `~/.claude/settings.json` 对应 event 段下追加 `{type:"command", command:"<上面那个路径>"}`
3. 写前把旧 `settings.json` 复制到 `settings.json.panel.bak`

下次该 event 触发就生效 — Claude Code 不用重启。

### Claude Code 的 hook event 都干啥

| Event | 时机 | 适合做什么 |
| --- | --- | --- |
| `UserPromptSubmit` | Claude 看到你 prompt 之前 | stdout **拼到 prompt 前**;exit 2 取消这次输入 |
| `PreToolUse` | 任何工具调用前 | exit 2 **阻断**调用(配 `matcher:"Bash"` 可以拦 `rm -rf` 之类) |
| `PostToolUse` | 工具返回后 | 记录、通知、自动格式化(`PostToolUse` + `matcher:"Edit"` → `ruff format`) |
| `SubagentStop` | Task 工具调的子 agent 结束 | 记录子 agent 输出 |
| `SessionStart` | 会话开始 | 初始化状态、开 run |
| `Stop` | 会话结束(`/exit`) | flush / 标记 run 完成 |
| `Notification` | Claude 等待你输入时 | 桌面通知 |
| `PreCompact` | 上下文压缩前 | 归档 transcript |

> 想拦子 agent (Task 工具)调用?用 `PreToolUse` + `matcher:"Task"`。没有单独的 `PreAgent` event。

## 编辑 memory

Memory tab → 左侧选文件 → textarea 改 → **Save**。写权限只允许 `~/.claude/**/*.md` 和 `~/coding/**/*.md`,其他路径返 HTTP 403。

三组:

- **User-level** — `~/.claude/CLAUDE.md`(Claude 每次 session 都会读的指令)
- **Project** — 工作区下的所有 `CLAUDE.md`(默认扫 `~/coding/**/CLAUDE.md`)
- **Auto-memory** — `~/.claude/projects/<path>/memory/*.md`(per project,agent 自己积累的,**`/exit` 后下次同目录开 session 会重新加载**)

## 关于 `/exit` 和持久化

`/exit` 什么都不删。

- Session transcript 留在 `~/.claude/projects/<path>/<uuid>.jsonl`
- Auto-memory 留在 `~/.claude/projects/<path>/memory/*.md`,**下次同目录开 session 会被重新加载**
- 面板自己的 `panel.db` 也留着

真要清,手动 `rm`。

## API endpoint

服务暴露一个小的 REST 接口(完整在 `server.py`):

```
GET    /api/entities?kind=plugin|skill|hook|setting|permission_grant
POST   /api/entities                         安装新的
PATCH  /api/entities/<id>                    启停 / 改配置
DELETE /api/entities/<id>                    删除(user hook 会顺手回写 settings.json)
POST   /api/entities/bulk                    {ids, enabled}

GET    /api/runs?session=1&status=...&entity_id=...
GET    /api/runs/<id>                        run + events + child runs
POST   /api/entities/<id>/run                手动 test run
POST   /api/runs/<id>/cancel | /replay
POST   /api/runs/<id>/events                 recorder.py 用

GET    /api/events?kind=...&level=...&format=ndjson
POST   /api/events/<id>/decide               {decision: allow_once|allow_session|allow_long|deny}
DELETE /api/events/<id>
POST   /api/logs/purge                       {older_than_days}

POST   /api/import                           重新扫 ~/.claude
POST   /api/hooks                            加 user hook(写 settings.json)

GET    /api/sessions                         列 Claude Code session
GET    /api/sessions/x?path=...              单个 transcript

GET    /api/memory                           列 CLAUDE.md + auto-memory
GET    /api/memory/file?path=...
PUT    /api/memory/file?path=...

GET    /api/settings                         面板设置
PATCH  /api/settings/<id>

GET    /api/stream                           SSE: run + event
```

## 安全

- **Memory 写白名单**:只允许写 `~/.claude/**` 和 `~/coding/**` 下的 `.md`。其他路径返 403。
- **Hook 写 `settings.json`** 是原子写(tmp + rename)+ `settings.json.panel.bak` 备份旧版本。
- **没有 auth。**这是 localhost 工具。**不要**把 7780 暴露到网络。
- **Recorder 权限阻塞**只在 `PANEL_GATE=1` 时生效。默认 advisory 不阻塞。

## Roadmap(还没做的)

- 从 jsonl 解析每个 session 的 token / cost(数据在,只是没解析)
- 把 permission_grant 持久化跨 session(目前存了但 PreToolUse 决策时没再注入)
- Memory 编辑带 diff 和 undo
- 多机视图(目前只支持单机)

## 为什么是三原语而不是十二个模块

因为 data dominates。任何"控制面板"列再多模块,最后都是同一份 `entity` / `run` / `event` 三元组的投影。这里加新功能通常是加一个 SQL filter,不是加一张表。

## License

MIT.
