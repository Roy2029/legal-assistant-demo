## REMOVED Requirements

### Requirement: Manual rebuild of law library index

The system SHALL expose a "重建法律库" button in the knowledge-base management UI that triggers a background index rebuild via the `/api/kb/rebuild` and `/api/kb/rebuild/status` endpoints, spawning `scripts/run_rebuild_managed.py` → `scripts/rebuild_index_from_intermediate.py`.

**Reason**: The feature is non-functional and dangerous. It never completed successfully (no `rebuild.status.json`, no backup created, no backend requests logged), and if it ran it would move the entire Qdrant directory aside and rebuild only the public law corpus from the intermediate file — wiping the user knowledge base. It also perpetuated the corpus-tagging defect it was meant to fix.

**Migration**: No migration needed. The feature is removed entirely; the public index remains static at its current state (08-27 build). The corpus retrieval defect is fixed separately via `public-corpus-retrieval`.

#### Scenario: Rebuild button no longer present

- **WHEN** a user opens the knowledge-base management page
- **THEN** the "重建法律库" button is absent, and no rebuild state, status polling, or rebuild UI is rendered

#### Scenario: Rebuild endpoints removed

- **WHEN** a client calls `POST /api/kb/rebuild` or `GET /api/kb/rebuild/status`
- **THEN** the request is not routed to any handler (endpoint removed), and the backend exposes no rebuild endpoint

#### Scenario: Rebuild scripts removed

- **WHEN** a developer inspects the `scripts/` directory
- **THEN** `run_rebuild_managed.py`, `rebuild_index_from_intermediate.py`, and `rebuild_index_v2.py` are absent

#### Scenario: No dangling references to removed scripts

- **WHEN** a search is made for references to `rebuild_index` or `run_rebuild` in the codebase
- **THEN** no `server/` or `frontend/` code references the removed scripts, and the `/api/update/run` stub message does not name a deleted script
