# Implementation status — v8

| Feature | Status |
|---|---|
| Hotel catalog and feature registry (17) | Local SQLite demo and pilot data |
| Source records, revisions, conflicts | Implemented and tested |
| OSM coastline and beach routing | Connectors implemented; live network unavailable during build |
| Geo candidate review and refresh | Implemented and tested |
| Municipal construction / closure / festivals | Manual source submission, review workflow and date/distance matching implemented; no official city feed yet |
| Trip-date-specific search warnings | Implemented, no record => `unknown` |
| Wikidata building identity | Manual matching, approval and Action API metadata inspection; no fabricated historical use |
| Wikimedia geotag media | Commons API adapter, operator review and public-only-reviewed media |
| Hotelier answer extraction / confirmation | Implemented with guardrails |
| Telegram Mini App traveler interface | Functional web UI, not externally visually audited |
| Telegram hotelier authorization | HMAC signed initData and hotel binding implemented; external Telegram test pending |
| PostgreSQL/PostGIS | Migration and runtime prepared; external integration test skipped without server |
| Original GrekEv Go repository merge | Not performed; only reference Go adapter |
| Automatic real municipal source collection | Not implemented |
| Travel price / inventory / hotel booking | Not implemented |
| Independent quality labeling of 20 hotels | Not completed |

Run `python -m pytest -q` and `node --check web/app.js`. All public claims must identify offline-curated/demo/live provenance; `reported` does not mean an independent verification of an ongoing event.
