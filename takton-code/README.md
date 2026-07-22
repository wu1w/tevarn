# Takton Code

Repo-native coding agent aligned with **OpenCode / Claude Code / Grok Build** interaction model.

Independent process. Desktop is **entry-only**; full backend power via `/api/bridge/v1/*`.

## 模型配置（浅层，对标 openclaw config）

```bash
takton-code models              # 一眼看当前 + 全部预设
takton-code models set local     # 一键 LOCAL
takton-code models set desktop  # 跟桌面端同一模型
takton-code models list         # 拉 /v1/models
takton-code models test         # PONG 冒烟
takton-code models doctor       # 全面体检
takton-code setup               # 交互向导
takton-code setup --preset local

# 自定义
takton-code models use -u http://host:8088/v1 -m my-model -k sk-xxx

# 会话内
/model
/model local
/model set url=http://x/v1 model=foo
```

配置落盘：`~/.takton-code/settings.json` + 同步 `config.toml`（可手改）。

```bash
cd takton-code
uv pip install -e ".[dev]"
```

## Run

```bash
# Fullscreen TUI (default)
takton-code --path ~/src/foo

# Force Desktop bridge (or auto-detect :8090/:8000)
takton-code --bridge

# Local LLM only
takton-code --local

# Headless
takton-code -p "Add rate limit" --path . --output json

# Autoloop: plan → build → test → fix (bounded)
takton-code -p "Add rate limit" --path . --autoloop --yes-build --autoloop-max-fix 3

# Grok-style inspect
takton-code inspect --path .
takton-code bridge-check
```

### Autoloop (A) & Checkpoints (B) — Claude-parity+

Aligned with local Claude Code 2.1.x (`file-history/<session>/<hash>@vN`, `/rewind` scopes, EscEsc) and **deeper**:

| Area | Claude | Takton |
|------|--------|--------|
| Disk backups | `~/.claude/file-history/` | `~/.takton-code/file-history/<session>/` |
| Versions | `@v1` `@v2` | content-hash `@vN` + DB version |
| Snapshot leaf | per messageId | user leaf + turn edit point |
| Rewind scopes | code / conversation / both | same + `preview` dry-run |
| Diff stats | yes | `/rewind <id> preview` |
| Export bundle | no | `FileHistory.export_point` |
| Autoloop | multi-turn ad hoc | explicit phases + doom-loop + plan file + verify |

```bash
# Autoloop CI-friendly
takton-code -p "Add rate limit" --path . --autoloop --yes-build --autoloop-max-fix 3 --output json

# Session
/checkpoint risky-refactor
/checkpoints
/rewind chk_xxx preview
/rewind chk_xxx scope=both
/autoloop fix flaky tests --yes
# Esc Esc → last code rewind
```

Settings (`agent.*`): `autoloop`, `autoloop_max_fix`, `autoloop_auto_approve`, `file_checkpointing`.

### Auto-mode rules (local TOML)

```bash
takton-code auto-rules --init
takton-code auto-rules --path .
# session: /auto-rules | /auto-rules reload
```

- User: `~/.takton-code/auto_rules.toml`
- Project overlay: `.takton/auto_rules.toml`
- Env: `TAKTON_CODE_AUTO_RULES`
- Enable: `--permission-mode auto`

Rewind side panel shows Δ restore/delete + file list + **unified diff snippets**
(disk → checkpoint, green/red in TUI) after EscEsc / Ctrl+R.

**Partial rewind** (beyond Claude):
- TUI: after picking checkpoint, file multi-select (Space / a / n)
- Slash: `/rewind chk_xxx files=src/a.py,src/b.py`
- Focus patch: `]` / `[` or `/patch next|prev|<path>`

**Unrewind / hunks** (beyond Claude):
- `/unrewind` or Ctrl+Shift+Z — undo last rewind (redo stack under `file-history/<session>/redo.jsonl`)
- `/redo-list` — show stack
- `/hunk list` — list hunks of focused file
- `/hunk apply 0,2` or `/hunk apply all` — selective hunk apply (also pushes redo)

**Hunk workbench** (mouse + keyboard):
- Toolbar **Hunks** / key `H` (NORMAL) / `/hunk` / after Rewind **Preview**
- Left: files · Middle: checkboxes · Right: colored hunk preview
- Apply this file / Apply all selected · j/k preview · ←/→ files · Space toggle

**Client UX (P2 experience)**:
- **Vim keys** (`ui.vim_keys=true`): Esc=NORMAL · i=INSERT · j/k · **10j 计数** · gg/G · **`/` 搜索高亮** · n/N · yy · R · H · `:` palette
- **Ctrl+K** command palette (filter + mouse)
- Badge shows `NORMAL 10 · /error 2/5 · chat`

Env:

| Var | Meaning |
|-----|---------|
| `TAKTON_CODE_BASE_URL` | OpenAI-compatible LLM |
| `TAKTON_CODE_MODEL` | Model id |
| `TAKTON_CODE_BRIDGE_URL` | e.g. `http://127.0.0.1:8090/api` |
| `TAKTON_CODE_BRIDGE_ENABLED` | `true` |
| `TAKTON_CODE_HOME` | State dir (default `~/.takton-code`) |

## Worktree (Grok-style)

Isolated coding on a linked branch under `.takton/worktrees/<name>`:

```bash
# create + enter worktree for this session
takton-code -w feat-login --path ~/src/foo
takton-code -w              # auto name tkc-YYYYMMDD-...
takton-code -w feat --ref main

# manage
takton-code worktree list --path .
takton-code worktree add hotfix --ref HEAD
takton-code worktree show hotfix
takton-code worktree rm hotfix --force --delete-branch
takton-code worktree gc

# in-session
/worktree list
/worktree status
```

Session meta records worktree name/path/branch; tools run inside the worktree root.

| Mode | Permission |
|------|------------|
| `build` | read/write + shell + tests |
| `plan` | read-only → markdown plan → `/approve` |
| `always` | same writes as build (Grok always-approve) |
| `ask` | read-only Q&A |
| `explore` | read-only search (Claude Explore style) |

**Tab / Shift+Tab** cycles `build → plan → always`.

## TUI keys

| Key | Action |
|-----|--------|
| Tab | Cycle mode (build→plan→always) |
| Ctrl+C | Stop turn |
| Ctrl+; | Show prompt queue |
| Esc Esc | Undo last turn file changes |
| Ctrl+O | Diff side panel |
| `/` | Slash palette (side panel filter) |
| plain text while running | **Steer only** (no auto-queue) |
| `/enqueue msg` while running | Queue next turn |
| `@path` | Inject file contents into prompt |

## Slash (selected)

`/plan /build /always /ask /explore /approve /reject /diff /undo /revert /test /check /compress /status /usage /inspect /continue /stop /enqueue /queue /todo /title /fork /export /sessions /model /worktree`

## Headless

```bash
takton-code -p "fix bug" --path . --output json
takton-code -p "add tests" --check --output streaming-json
takton-code -p "…" --mode always --bridge
```

## Desktop ecosystem

Skills / MCP / desktop tools / RAG **come from Takton Desktop bridge** (`--bridge` or auto-detect `:8090`). No second local skill/MCP stack.

Bridge contract: `docs/DESKTOP_BRIDGE.md`  
Competitor audit: `docs/COMPETITOR_LOCAL_AUDIT.md`

## Smoke

```bash
# LOCAL llama.cpp + optional live bridge
export TAKTON_CODE_BASE_URL=http://192.168.5.32:8088/v1
export TAKTON_CODE_MODEL=local-model.gguf
python smoke/smoke_full.py
```

## Architecture

```
takton-code (TUI/CLI)
    │  auto-detect or --bridge
    ▼
Takton Desktop Backend  /api/bridge/v1/{health,models,chat,skills,tools,mcp,rag}
    │
    └── same LLM / skills / tools / MCP / RAG as desktop chat
```
