# FE/BE Capability Gap Report

- BE routes: 261
- FE API paths: 179
- FE→BE missing: 2
- BE→FE missing (excl internal): 60

## FE calls without matching BE

- `/evolution/assets/{id}/{enabled`
- `/knowledge/rebuild-index{id}`

## BE without FE (grouped)

### audit
- `GET` `/audit/logs`

### cluster
- `DELETE,POST` `/cluster/execute`
- `WEBSOCKET` `/cluster/ws/{id}`

### context
- `POST` `/context/compact`
- `GET` `/context/engine-status`

### cron
- `GET` `/cron/{id}/logs`

### cron-hooks
- `POST` `/cron-hooks`
- `GET` `/cron-hooks/cron-job/{id}/with-hooks`
- `GET` `/cron-hooks/{id}/logs`

### desktop
- `POST` `/desktop/execute`
- `POST` `/desktop/operation`
- `DELETE,POST` `/desktop/permission`
- `GET` `/desktop/screenshot`
- `GET` `/desktop/shots/{id}`
- `WEBSOCKET` `/desktop/stream`

### entities
- `GET,POST` `/entities`
- `DELETE,GET,PUT` `/entities/search`
- `DELETE,GET,PUT` `/entities/{id}`
- `POST` `/entities/{id}/merge/{id}`

### evolution
- `POST` `/evolution/assets/{id}/disable`
- `POST` `/evolution/assets/{id}/enable`
- `GET` `/evolution/clusters`
- `POST` `/evolution/curator/run`
- `POST` `/evolution/drafts/{id}/apply`
- `POST` `/evolution/drafts/{id}/reject`
- `POST` `/evolution/from_task`
- `GET` `/evolution/tasks`
- `GET` `/evolution/version`

### files
- `GET` `/files/download`
- `GET` `/files/info`
- `POST` `/files/open`
- `GET` `/files/resolve`

### kernel
- `GET` `/kernel/approval-rules`
- `POST` `/kernel/identities/{id}/memory/{id}/supersede`
- `GET,POST` `/kernel/inbox`
- `GET` `/kernel/processes/{id}`

### knowledge
- `POST` `/knowledge/rebuild-index`

### mcp
- `GET` `/mcp/store/{id}/{id}`

### memory
- `GET` `/memory/graph/nodes`
- `GET` `/memory/graph/nodes/{id}`

### packages
- `GET` `/packages/detail/{id}`
- `GET` `/packages/export/{id}`
- `POST` `/packages/install-url`
- `PUT` `/packages/session`
- `GET` `/packages/session/{id}`

### runs
- `GET` `/runs/session/{id}`
- `GET` `/runs/{id}`

### sessions
- `POST` `/sessions/{id}/resume`

### store
- `GET` `/store/injection-preview`

### subagents
- `GET,POST` `/subagents`
- `GET` `/subagents/{id}/resolve-model`

### tools
- `GET` `/tools/schema/active`

### traces
- `GET` `/traces/session/{id}`
- `GET` `/traces/session/{id}/latest`
- `DELETE,GET` `/traces/{id}`

### webhooks
- `GET,POST` `/webhooks`
- `GET` `/webhooks/{id}/logs`

### workflow-templates
- `GET,POST` `/workflow-templates`

### workflows
- `GET` `/workflows/{id}/executions`

### workspace
- `GET` `/workspace`
