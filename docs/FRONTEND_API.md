# Maestro-SC backend API for front-end developers

The backend reads data from any industry, governs it, and serves it through the APIs below. The screens in `web/` are a reference only, so a new UI can replace them. Everything a new UI needs is here.

* Base URL: `http://localhost:8000` (Docker). Interactive reference with every endpoint and schema: **`/docs`** (OpenAPI).
* JSON everywhere. Dates are ISO 8601 strings. There is no authentication yet (see "Not in the API yet").
* Actions that change data take a `user` (name or role). It is written to the audit log, and the call is refused without it.
* Errors: `400` bad input, `404` not found, `409` or `422` refused by a rule. The body is `{"detail": "<human-readable reason>"}`, which you can show as is.

---

## 1. The data model is data: entities

The UI should never hard-code an industry. Ask the backend which entities exist and build menus, tables, forms and filters from the answer.

| Call | Returns |
|---|---|
| `GET /api/entities` | Every entity: `name, label, domain, origin (core / registered), pack, fields (count), records` |
| `GET /api/entities/{entity}` | One entity, described for building a UI (below) |
| `POST /api/entities` | Register an entity, or a new version of one. Body: `{definition, user}` |
| `DELETE /api/entities/{entity}?user=` | Retire a registered entity. Refused while it has records. |

`GET /api/entities/store_sales`:
```json
{"name": "store_sales", "label": "Store sales", "origin": "registered", "domain": "retail", "pack": "retail",
 "version": 1, "key": ["store_id", "sku", "sale_date"], "key_mode": "event", "records": 240,
 "fields": [
   {"name": "store_id", "type": "text", "description": "Store number", "required": true, "key": true,
    "allowed": null, "synonyms": ["store", "storeno", "storecode"]},
   {"name": "sale_date", "type": "date", "description": "Business day", "required": true, "key": true, "allowed": null, "synonyms": ["date", "businessdate"]},
   {"name": "units", "type": "number", "description": "Units sold", "required": true, "key": false, "allowed": null, "synonyms": ["qty", "unitssold"]}]}
```
* `type` is `text`, `number` or `date`. Use it to choose the input, the filter widget and the column alignment.
* `allowed` is a list of permitted values, or null. Render a drop-down when it is set.
* `domain` groups entities: supply, depot, fleet, risk, or a registered domain such as retail or healthcare. Use it for navigation.

Entity definition (for `POST /api/entities`):
```json
{"name": "work_order", "label": "Work order", "domain": "manufacturing", "key": ["work_order_id"], "key_mode": "snapshot",
 "freshness_field": "due_date", "freshness_days": 180,
 "fields": [{"name": "work_order_id", "type": "text", "required": true, "synonyms": ["aufnr", "wo"]},
            {"name": "status", "type": "text", "allowed": ["Open", "Released", "Closed"]},
            {"name": "quantity", "type": "number", "min": 0}]}
```
Rules the backend enforces:
* Names are snake_case, and core entity names can't be reused.
* A field name has one type across the model.
* While an entity has records, a field's type can't change and fields can't be removed. Adding fields is always allowed and creates version 2.

### Industry packs
| Call | |
|---|---|
| `GET /api/packs` | `manufacturing`, `retail`, `life_sciences`: their entities and whether each is enabled |
| `POST /api/packs/{id}/enable` | Registers the pack's entities. Body: `{user}`. Safe to repeat. |

Packs are JSON files in `app/packs/`. To add a new industry, add a file there. No code is needed.

---

## 2. Loading data: batches and Mapping Studio

| Call | |
|---|---|
| `POST /api/batches` (multipart) | `files` (repeat for several), `user`, `mode=review` (default) or `auto`, optional `name`, optional `record_type` to force an entity. Returns the batch (below). |
| `POST /api/batches/{id}/files` (multipart) | Add files to an existing batch (same mode and user). Use it to show progress: create the batch with the first file, then send the rest one at a time, and use `XMLHttpRequest.upload.onprogress` for bytes. This is what the reference UI does. |
| `GET /api/batches` | Recent batches with object counts by status |
| `GET /api/batches/{id}` | Batch, its source objects and its publish runs |
| `POST /api/batches/{id}/publish` | Validate, govern and publish everything that is ready. Body `{user}`. Returns the run. |
| `GET /api/batches/{id}/runs` | Every run, kept whatever the outcome |

A batch from two files (the retail pack is enabled; the hospital file matches nothing):
```json
{"_id": "4f170ccc…", "status": "MAPPING_REQUIRED", "rows": 270, "counts": {"READY": 1, "MAPPING_REQUIRED": 1},
 "objects": [
  {"_id": "0d04c215…", "name": "retail_store_sales.csv", "entity": "store_sales", "confidence": 85, "status": "READY", "gate": [], "rows": 240},
  {"_id": "a6551e84…", "name": "hospital_bed_occupancy.csv", "entity": "other", "confidence": 11, "status": "MAPPING_REQUIRED",
   "gate": ["Entity not recognised: choose one, or register a new entity from this source"], "rows": 30}]}
```
* Object `status` is one of `READY`, `MAPPING_REQUIRED`, `DRIFT` (columns changed since the saved profile) or `PUBLISHED`.
* `gate` lists, in plain words, what must happen before the object can be published. Show it as is.

### One source object (Mapping Studio)
| Call | |
|---|---|
| `GET /api/objects/{id}` | Fields with profile and decision, sample rows, entity fields, gate, decision log, profile |
| `PATCH /api/objects/{id}/entity` | `{entity, user}`. Choose another entity; suggestions are recalculated. |
| `PATCH /api/objects/{id}/fields` | `{column, action, canonical_field?, extension_key?, user}`. `action` is `ACCEPTED`, `IGNORED`, `EXTENSION` or `RESET`. |
| `POST /api/objects/{id}/accept-high-confidence` | `{user, minimum: 90}` |
| `POST /api/objects/{id}/profile` | `{user}`. Save and activate the mapping profile; the next file with these columns maps by itself. |
| `GET /api/objects/{id}/propose-entity?domain=&name=` | Drafts a new entity from an unrecognised source |
| `POST /api/objects/{id}/register-entity` | `{definition, user}`. Registers the (edited) draft and maps the source to it 1:1. |
| `GET /api/profiles` | All mapping profiles and versions |

Each field in `GET /api/objects/{id}`:
`{name, type, null_pct, unique_pct, samples[], canonical_field, confidence, status, why, candidates[{field, score, why}], extension_key, decided_by, decided_at}`.
* `status` values:
  * `AUTO`: matched with certainty.
  * `SUGGESTED` / `REVIEW`: a person must accept or reject. Publishing waits for these.
  * `UNMAPPED`: no match; kept as an attribute.
  * `ACCEPTED`, `IGNORED`, `EXTENSION`: decided by a person.
* `entity_fields` lists the entity's fields (`required`, `mapped`) for the drop-down.
* `alternatives` lists other likely entities with their confidence.

What each decision does to the published record:

| Decision | Effect |
|---|---|
| `ACCEPTED` / `AUTO` | The value goes into the Maestro field. |
| `IGNORED` | The value is not on the record; it stays only in the original file (Bronze). |
| `EXTENSION` | The value is kept as `ext.<extension_key>`. |
| `UNMAPPED` | The value is kept as `ext.<column name>`. |

Proposal for an unknown source (edit names, types and key, then send it to `register-entity`):
```json
{"name": "hospital_bed_occupancy", "domain": "healthcare", "key": [], "key_mode": "snapshot",
 "fields": [{"name": "ward_code", "type": "text", "required": true, "source_column": "Ward Code"},
            {"name": "census_date", "type": "date", "required": true, "source_column": "Census Date"},
            {"name": "beds_occupied", "type": "number", "required": true, "source_column": "Beds Occupied"}],
 "proposal_notes": ["Types come from the values; check numbers that are really codes…", "No column is distinct on every row: pick a key, or use key_mode event.", "…"]}
```

### Publish run
```json
{"status": "COMPLETED", "records": 168, "requested_by": "SJ",
 "stages": [{"stage": "VALIDATE", "status": "PASSED", "detail": "11 of 11 object(s) ready", "held": []},
            {"stage": "GOVERN", "status": "PASSED", "detail": "168 record(s) scored: 168 A", "grades": {"A": 168}},
            {"stage": "CERTIFY", "status": "PENDING_OWNER", "detail": "…", "products": [{"product": "…", "certification": "PENDING"}]},
            {"stage": "PUBLISH", "status": "PASSED", "detail": "…"},
            {"stage": "RISK", "status": "PASSED", "detail": "Supply risk refreshed: …"}]}
```
* Run `status` is `COMPLETED`, `PARTIAL` (some objects held) or `FAILED`.
* Stage `status` is `PASSED`, `PARTIAL`, `FAILED`, `SKIPPED`, `PENDING_OWNER` or `CERTIFIED`.

Older single-call loaders still work and publish directly: `POST /api/ingest/file`, `/api/ingest/api` (REST/ERP), `/api/ingest/iot/{source}` and `/api/ingest/mqtt/start`.

---

## 3. Reading data: one API for every entity

| Call | |
|---|---|
| `GET /api/entities/{entity}/records` | Filtered, sorted, paged rows |
| `GET /api/entities/{entity}/summary` | Per-field profile (filters, KPI tiles, empty states) |
| `GET /api/entities/{entity}/aggregate` | Grouped metric for charts |
| `GET /api/records/{id}` | One record with its trust score and lineage to the source row |

Common parameter: `layer=gold` (default: grade A/B, current, not quarantined, which is what dashboards should show), `silver` (everything current) or `all`.

### records
`GET /api/entities/store_sales/records?store_id=S014&units__gte=30&sort=-units&page=1&page_size=2`
```json
{"entity": "store_sales", "layer": "gold", "page": 1, "page_size": 2, "total": 42, "pages": 21,
 "rows": [{"store_id": "S014", "sku": "SKU-1001", "sale_date": "2026-10-29T00:00:00", "units": 60.0, "revenue": 227.34,
           "currency": "GBP", "promo_flag": "no",
           "_meta": {"id": "6aff16bd…", "source_id": "retail_store_sales.csv", "layer": "silver", "grade": "A",
                     "score": 100.0, "loaded_at": "2026-11-19T15:33:17", "batch_id": "b7ef…"}}]}
```
* Filters:
  * `field=value`
  * `field__op=value`, where `op` is `ne`, `gt`, `gte`, `lt`, `lte`, `in` (comma list), `contains` (case-insensitive) or `exists`.
  * Extension values: `ext.<key>=value`.
* `q`: free text over the text fields.
* `sort`: comma list; `-field` sorts descending.
* `fields`: comma list, to return fewer columns.
* `page_size`: at most 500.
* Unknown fields and operators return `400` with the reason.
* Each row has the entity's fields, then any `ext.*` values, then `_meta` (record id for drill-down, data grade and score).

### summary
`GET /api/entities/store_sales/summary`
```json
{"records": 240, "grades": {"A": 90, "B": 150},
 "fields": [{"field": "store_id", "type": "text", "filled_pct": 100.0, "distinct": 3, "top": [{"value": "S001", "count": 80}]},
            {"field": "sale_date", "type": "date", "filled_pct": 100.0, "min": "2026-10-01T00:00:00", "max": "2026-11-09T00:00:00"},
            {"field": "units", "type": "number", "filled_pct": 100.0, "min": 5.0, "max": 60.0, "avg": 31.8, "sum": 7631.0}],
 "extensions": [{"key": "Shift Lead", "filled_pct": 100.0}]}
```
Use `top` for drop-down filter options, number `min`/`max` for range sliders, and the date range for the date picker.

### aggregate
`GET /api/entities/store_sales/aggregate?metric=sum&field=units&group_by=store_id&date_field=sale_date&bucket=month`
```json
{"metric": "sum", "field": "units", "group_by": ["store_id"], "bucket": "month", "groups": 6,
 "rows": [{"store_id": "S001", "month": "2026-10", "value": 2152.0, "n": 62},
          {"store_id": "S002", "month": "2026-10", "value": 1740.0, "n": 62}]}
```
* `metric`: `count`, `sum`, `avg`, `min`, `max` or `distinct`. All except `count` need `field`, and `sum`/`avg` need a number field.
* `group_by`: comma list of fields or `ext.*`.
* `bucket`: `day`, `week`, `month`, `quarter` or `year`, with `date_field`.
* The same filters as `records` apply.
* Currency stays a group key and is never summed across currencies. Group by `currency` whenever you sum money.

---

## 4. Governance (all data-driven)

Domains and data products come from the entity registry. A registered domain (retail, healthcare…) gets its own data product automatically, so build governance screens from these lists, not from fixed tabs.

| Call | |
|---|---|
| `GET /api/governance/overview` | Bronze / Silver / Gold counts, KPIs, products, latest AI decision |
| `GET /api/governance/config` | Domains (with entities, owner, steward), required fields, keys, allowed values, weights, grade bands |
| `GET /api/governance/products` | Data products: score, grade, certification, freshness, records, quarantine |
| `POST /api/governance/products/{id}/certify` | Owner certifies a product (`{user, note}`) |
| `GET /api/governance/quarantine` · `POST /api/governance/quarantine/{id}/close` | Quarantine queue |
| `GET /api/governance/drill?product=&record_type=&layer=&grade=` · `GET /api/governance/record/{id}` | Drill-down and lineage |
| `GET /api/governance/batches` · `/batch/{id}` | How each load was read |
| `GET /api/governance/stage/{plan,filter,clean,enrich,audit,monitor}` | Data for the six governance steps |
| `GET /api/governance/decisions` · `POST /api/governance/decisions/{id}/{accept,modify,override,clear}` | AI decision gate |
| `POST /api/data/sources/{source}/delete` · `POST /api/data/delete-all` | Confirmed, audited deletion |

## 5. Module APIs (already built)
* **Supply risk:** `/api/supply/overview`, `suppliers`, `supplier?name=`, `network`, `coverage?buffer_days=`, `delivery`, `signals`, `plans`, `scenarios`, `whatif` (POST), `decisions`.
* **Depot / MRO:** `/api/maestro/docks`, `gates`, `gate/{gate}?last_n=`, `machine`, `fleet`, `alerts`.
* **AMXG:** `/api/amxg/hub`, `events`, `timelines`, `tiered`, `status`, `dockboard`, `bom`, `script/{tail}`.
* **Risk feeds:** `/api/feeds`, `/api/feeds/run-all`, `/api/feeds/{id}/run`.
* **Ask:** `POST /api/ask {question}` returns an answer with evidence and a decision-gate verdict.

## Not in the API yet
* **Authentication and roles.** Login, users, access requests and module permissions: every endpoint is open today, and `user` is self-declared.
* **Storage.** MongoDB only; no Postgres or BigQuery.
* **Incremental pulls.** "Since last pull" from ERPs and per-source schedules.
* **Saved views.** Front-end dashboards and filters are not stored server-side.
