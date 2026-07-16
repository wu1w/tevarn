# Takton Evolution Engine (TEE) v0.1.1 — HAEE-inspired

Installed in desktop `resources/backend/evolution/`.

## Phases
- **P1** `from_tasks` / `from_cron` — task & cron outcomes create evolution assets
- **P2** Structured SKILL.md proposals + G6 structure gate + dedupe
- **P3** Tool playbook drafts (`kind=tool`, default draft-only)
- **P4** Auto-observe clusters + curator archive

## Enable
Env or API:
```
TAKTON_EVOLUTION_ENABLED=1
TAKTON_EVOLUTION_MODE=on_failure
TAKTON_EVOLUTION_FROM_CRON=1
TAKTON_EVOLUTION_AUTO_OBSERVE=1
```

API:
- `POST /api/evolution/enable` `{"enabled": true}`
- `POST /api/evolution/from_task`
- `POST /api/evolution/curator/run`
- `GET /api/evolution/version`
- `GET /api/evolution/assets`
