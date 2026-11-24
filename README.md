# Supply Chain AI Data Hub

Pulls supply-chain data from **any source** (Excel, CSV, JSON, PDF, emails, ERP, REST APIs, IoT), uses **AI (Claude)** to understand and normalise it, stores it in **MongoDB** (NoSQL), and turns it into supply-chain analytics plus plain-language Q&A.

It follows the same idea as Praxie (AI supply-chain workspace on connected data) and Kinaxis Maestro (data fabric → intelligence engine → user interface), as an MVP you can run and extend.

Everything runs in one application, **Maestro-SC** (http://localhost:8000/maestro):

* **Configuration** is the only place data enters: file upload, API/ERP pull and IoT. After each load it shows which module the data feeds. **Loaded data** lists every source and deletes one source, or everything. You must type the source name (or `DELETE ALL`) and your name. Deletion removes the source from Bronze, Silver, Gold and quarantine, withdraws certification of the affected Gold products, makes any older version of the same records current again, and is recorded in the audit log.
* **Modules** show the governed data: **Supply analysis** (http://localhost:8000/supply), **Docks / Gates / Machine** (depot flow) and **Fleet** (health and faults).
* **Governance** shows quality, quarantine and lineage for everything that was loaded.

![gate graph](docs-maestro-gates.png)

## Any industry: entity registry, industry packs, generic data API

The data model is data, not code. **Front-end developers: start with [docs/FRONTEND_API.md](docs/FRONTEND_API.md).**

* **Core entities** cover supply chain, MRO / depot and supply risk. Any other kind of data is *registered* as an entity: fields, types, key, required fields, allowed values, ranges and column synonyms. Registering one needs no code change and no restart, and an entity can be versioned: fields can be added, but types can't change once it has data.
* **Unknown files stay unknown.** A file is classified as an entity only when it covers that entity's required fields. Otherwise Mapping Studio offers **register a new entity from this source**: it drafts fields, types and a key from the columns, a person edits and approves the draft, and the file maps 1:1. The next file with the same columns maps by itself.
* **Industry packs** (`app/packs/*.json`) are ready-made entity sets to switch on, with ERP-style synonyms (e.g. SAP `AUFNR`, `GAMNG`). Shipped: **manufacturing** (work order, machine, quality inspection), **retail** (store, store sales, promotion) and **life sciences** (batch / lot, temperature excursion, supplier audit). To add a pack, add a JSON file.
* **Screens follow the data.** AMXG, Docks / Gates / Machine and Fleet appear only when their kind of data is loaded, so a car maker doesn't see aircraft screens. An administrator can switch them on or off, and enable industry packs, in **Admin → Industry & modules**.
* **Governance follows the registry.** Every registered domain gets its own governance domain and data product. Required fields, keys, allowed values and ranges become quality rules for that entity only, so one industry's codes never quarantine another's data.
* **One data API for every entity:** `GET /api/entities`, `/api/entities/{e}`, `/records` (filters, sort, paging, Gold by default), `/summary` (per-field profile) and `/aggregate` (grouped metrics and time buckets for charts).

Try it with `samples/industry/`. Load `retail_store_sales.csv` and `hospital_bed_occupancy.csv` together: both are held as unknown. Then enable the retail pack (`POST /api/packs/retail/enable`) and register the hospital file from Mapping Studio.

## Loading data: batches and Mapping Studio

**Configuration → Load files**: select several files at once. They load as one *ingestion batch*, and every table (CSV, sheet, JSON list) becomes a *source object*:

1. **Discover and classify.** Each column is profiled (type, % empty, % distinct, samples), and the object is classified to a Maestro entity, with a confidence score and alternatives. Suggestions are always fields of that entity, so a column can never be mapped to a field of another domain.
2. **Mapping Studio.** For each column choose **Accept** (a Maestro field of the entity), **Ignore** (kept only in the original file, Bronze) or **Extension** (kept on the record under a key you name). Every decision records who made it, and the decision log shows the history. Columns with no match and no decision are kept as attributes.
3. **Save & activate profile.** The next file with the same columns is mapped by the profile and publishes straight away. A file whose columns changed is held as **drift**: the earlier decisions are kept for the columns that remain, the active profile is not touched, and saving creates version 2.
4. **Validate, govern and publish.** Objects whose required fields are mapped are published. The rest are held with the reason, not dropped. Each run records five real stages:
   - **VALIDATE**: mapping checks.
   - **GOVERN**: the trust scores and grades.
   - **CERTIFY**: lists the affected data products. Certification stays a named owner's decision.
   - **PUBLISH**: writes the records.
   - **RISK**: refreshes supply risk.

   Runs are kept whether they succeed, partly succeed or fail.

Untick *Review mappings…* to publish without review (the old behaviour). Tracker templates (PDM tail sheets, machine info) and AI-read documents are published directly and appear in the batch as such.

![mapping studio](docs-mapping-studio.png)

`samples/risk_center/` holds the 11 Risk Center CSVs. Load them together to see the flow end to end. Three objects need a decision (`risk_title`, `purchase_order`, `product_or_material`), and the other eight are ready.

## Data governance (after ingest)

Open **http://localhost:8000/governance**. It follows the DFSP IP Data Governance design: Bronze → Silver → Gold, a Data Trust Score, quarantine, certified Gold products and an AI decision gate.

![governance](docs-governance.png)

| Layer / step | What happens |
|---|---|
| **Bronze** (raw evidence) | Every uploaded file is kept byte-for-byte with its SHA-256. Every sheet or table is kept as received, with a payload hash and a row hash on each record. Measure, never fix. |
| **Gate 1** (technical checks) | Parsed by a known reader and the schema maps to a Maestro entity. Otherwise the rows stay in Bronze only and count as *blocked at ingest*. A malware scan is **not** configured. |
| **Silver** (trusted and scored) | Cleaned and conformed. Duplicate copies are removed; for snapshots, the newest version is kept and older ones go to history. Records are matched to the fleet master and tagged with domain and sensitivity (CUI). Every record gets a **Data Trust Score** from six dimensions: completeness 20, validity 20, timeliness 20, consistency 20, uniqueness 10, provenance 10. That gives grade **A ≥ 95 · B ≥ 85 · C ≥ 70 · D**. |
| **Quarantine** | Grade D or a hard rule (missing identity key, code not in the approved list, bad tail number, negative quantity, rate outside 0–100) sends the record to quarantine. It gets a reason code, the domain's steward and an SLA. Records are never edited in place: when the source sends a corrected version, the item resolves itself. |
| **Gold** (certified products) | Built-in products for process flow and gates, production schedule, asset health, inventory, supplier performance, logistics, production summary, work packages and BOM, and supply risk, plus one per registered domain. Only products with data loaded are shown. Their names and owners are neutral; an **industry pack** gives them that industry's names (for example, **Aviation MRO** names them Depot Flow & Gate Performance, Fleet Health and ALC Production). Each is built only from A/B records. **Gate 2:** product grade ≥ B *and* the data owner certifies. New data after certification shows **RECERTIFY**. The trust label travels with the data; for example, gate graphs carry it. **Maestro modules and supply analytics read Gold only.** |
| **AI decision gate** | Data grade × model confidence gives *recommend*, *recommend with caveat*, *advisory* or *withhold*. Accept, Modify and Override are recorded; an override requires a reason. Without an API key, recommendations are withheld. |
| **Monitor loop** | Records graded A/B (target ≥ 95%), quarantine aging (target < 5 days), acceptance and override rates, and drift alerts (newest load of a sheet vs earlier loads). |
| **Audit & balance** | Control totals per batch (received = blocked + silver + quarantine + duplicate + history + replaced) and an audit log of every governance action. |

**Drill-down:** every number on the governance page opens the records behind it. Facets let you narrow by entity, layer, grade, issue and source. Clicking a record shows its six dimension scores, each issue (a hard rule or a deduction), its quarantine status, every version of it, and its lineage: Bronze file (SHA-256) → batch and Gate 1 result → the exact source row → Silver → Gold, or why it was excluded. Bronze batches open to show the content exactly as received. In Maestro, tail numbers on dock cards and gate-graph bars open that aircraft's records. A module with no Gold data explains why: nothing loaded, quarantined, grade C, or blocked at Gate 1.

Weights, grade thresholds, SLA, drift threshold and roles are editable under **01 Plan & govern**. They stay proposals until a governance council ratifies them, and changing them re-scores everything and is audited. Actions are recorded against the name in **Acting as**. There is no login yet, so this is not access control.

## Supply analysis (risk & resilience)

Open **http://localhost:8000/supply**. It's a light-theme screen computed only from governed Gold supply data: supplier master, purchase orders, inventory, item master and customer orders. The samples are in `samples/` (`supplier_master.csv`, `sales_orders.csv`, `inventory.xlsx`, `purchase_orders.csv`).

![supply analysis](docs-supply.png)

| View | What it shows |
|---|---|
| Overview | Supply risk index (spend-weighted, 0–100), critical/high suppliers, spend at risk, single points of failure, items under 14 days of cover, data confidence; plus what needs attention, the rating mix and spend by country |
| Supply network | Sub-tier → tier 1 → items → sites → customers. Click a node to trace what it depends on and what it feeds. Sole-sourced items and sub-tiers are marked as single points of failure |
| Supplier risk | Scorecard per supplier: rating = **likelihood × impact** (1–5 each), built in four shown steps. Likelihood = 50% delivery + 20% lead time + 30% country concentration, over the factors with data. Impact = 40% spend share + 60% continuity (sole sourcing × days of cover). A sub-tier inherits the impact of the supplier it feeds, and missing inputs widen the ± range. Shows recommended actions, plans and items, plus "Ask why" (AI, through the decision gate) |
| Risk matrix | 5×5 likelihood × impact grid and the risk register |
| Stock coverage | Days of cover per item (demand from customer orders; purchase orders as a proxy), run-out date, next receipt, and a safety-stock what-if: suggested reorder point = daily demand × (lead time + buffer days) |
| Delivery | On-time trend by month and late or overdue lines |
| Mitigation | Supplier segmentation (supply risk × business impact, split at the median: strategic, bottleneck, leverage, non-critical) with a strategy per segment. A 4T plan board (Treat / Transfer / Tolerate / Terminate) moves plans through Proposed → Approved → In progress → Done; every move is audited |

The weights and thresholds are starting proposals. They are documented in `app/supply_risk.py` so they can be agreed and tuned.

### External risk feeds (free, daily)

The app pulls these public sources once a day, at `RISK_FEEDS_HOUR` UTC (default 02:00), and at start-up if the last run is over a day old. **Configuration → Risk feeds** shows each feed's status (OK, PARTIAL or ERROR), lets you switch feeds on and off, and has **Refresh now**. Feeds are governed like any other source (Bronze copy with hash → `risk_signal` records → Gold) and appear under **Supply analysis → Risk signals**.

| Feed | Source | Used for | What leaves your network |
|---|---|---|---|
| OFAC SDN list | US Treasury | possible sanctions matches (entities only, matched locally) | nothing |
| UK Sanctions List | UK FCDO | possible sanctions matches (entities only, matched locally) | nothing |
| GDACS alerts | UN OCHA / EU JRC | orange and red disaster alerts (droughts and wildfires weighted down) | nothing |
| USGS earthquakes | US Geological Survey | magnitude 5+ in the last 7 days | nothing |
| Political stability | World Bank WGI (`GOV_WGI_PV.EST`) | country stability (no data for Taiwan) | nothing |
| Unrest news | GDELT | 3-day news volume vs the usual level, per supplier country | country names only |

They feed a 4th likelihood factor, **geopolitical & hazard** = max(serious hazard, 60% stability + 40% unrest). The likelihood weights become 40% delivery, 15% lead time, 20% concentration and 25% geopolitical & hazard. A possible sanctions match sets the factor to 100 and adds "verify" as the first action. Matches are name-only, so confirm identity before acting.

Limits:
* Hazards are matched by **country**, because supplier site coordinates are not loaded. Only serious events (score ≥ 60) change ratings; smaller ones are only shown.
* GDELT rate-limits heavily. Countries it refuses are reported, and the feed shows PARTIAL.
* Hosts to allow in a restricted network: `www.treasury.gov`, `sanctionslistservice.ofac.treas.gov`, OFAC's download redirect to `*.s3.us-gov-west-1.amazonaws.com`, `sanctionslist.fcdo.gov.uk`, `www.gdacs.org`, `earthquake.usgs.gov`, `api.worldbank.org`, `api.gdeltproject.org`.
* Set `RISK_FEEDS_ENABLED=false` where the app has no internet access.

### Risk Center data and what-if

The supply model also reads these entities when they are loaded:

| Data | What it changes |
|---|---|
| Supplier network (`supplier`: code, node type, buffer days, time to recover, critical) | Vendor codes on POs resolve to the master. A recovery time longer than the buffer raises continuity impact. |
| Supply relationships (`supply_link`: upstream, downstream, share, lane) | Network edges, sub-tier "supplies to", and the lanes each supplier ships on. |
| Country risk, transport lanes | Fill in where the public feeds leave gaps, and add lane risk to the geopolitical factor. |
| Risk register, alerts | Open items on a node raise likelihood and appear in the scorecard's signals. |
| Forecast, inventory allocation | Demand comes from the forecast, and stock counts only what is not allocated. |
| Scenarios | **Supply → What-if** compares the baseline with the scenario over the same horizon. |

What-if inputs: PO delay, demand change, inventory loss, or a geopolitical scenario. A scenario's scope comes from its own country/lane fields, or else from lane, country and material names found in its text. The evidence is listed with the result.

Results:
- Value at risk is grouped by currency and never summed across currencies.
- A cost-uplift scenario shows its extra cost.

Each result can be accepted, modified, overridden or deferred. Modify and override need a rationale, and every decision goes in the audit log.

![what-if](docs-supply-whatif.png)

## Maestro-SC module (depot / maintenance)

Open **http://localhost:8000/maestro#config** (Configuration) and load the files in `samples/maestro/`.

![gate graph](docs-maestro-gates.png)

| View | What it shows | Built from |
|---|---|---|
| Modules | The 14 Maestro components. Built ones are marked LIVE, the rest DESIGN | – |
| Docks (AMX_03) | One card per aircraft in work: current gate, Day x/goal, Flow x/AMREP days, In-dock x/goal, Queue, milestones, next big thing, notes, AMREP risk | PDM tail sheets |
| Gates | Gate graph per *FIXER DATA TO GRAPHS* process: last X aircraft (default 6) + aircraft in the gate (actual + remaining), rolling last-X average, requirement, and the WIP plan vs actual inset | tail sheets + Machine Info |
| Machine | Last-X average days per gate and queue vs requirement, last-X flow and in-dock days, last aircraft produced | same |
| Fleet | MC rate with FMC/PMC/Degraded/NMC band, MTBM vs target, subsystem health, telemetry against limits, fault registry | Dashboard supporting data |
| Alerts | Critical and warning alerts from the fault registry, telemetry breaches, AMREP risk and gate overruns | all |

### AMXG specific modules

Open **http://localhost:8000/maestro#amxg** (or **AMXG** in the top bar).

![AMXG hub](docs-amxg-hub.png)

| Card | Screen | Built from |
|---|---|---|
| AMX_01 Event management | One feed of fault-registry entries, telemetry breaches, AMREP and gate risks, the issue register, blocked work and supply shortages. Filter by state, source and level; acknowledge, assign, close or reopen (each action asks for a name and goes in the audit log) | all depot, fleet and supply data |
| AMX_02 Product specific timelines | Gantt of every aircraft through the gates: queues vs gates, gates over goal, AMREP date, projected/actual finish, an as-of line. Filters All / In work / Sold-delivered / Incoming | tail sheets + FY schedule |
| AMX_03 Dock overview | The Docks board | tail sheets |
| AMX_04 Tiered dashboards | Drill AFSC → ALC → weapon system → B-52 PDM line → dock → aircraft. Produced vs goal by quarter, attainment, on-time, hours, yield, open issues at each level; the PDM line adds WIP-over-plan and slow gates | ALC summary + issue register + tail sheets |
| AMX_05 Aircraft status report | Aircraft × gate grid (days / goal, in gate now, over goal), AMREP margin, health band, blocked work; a detail panel per aircraft | tail sheets + fleet + work tasks |
| AMX_06 Machine health | The Fleet view. It reads any `asset_status` / `subsystem_health` / `fault` / telemetry data, so shop equipment loaded the same way appears here too | dashboard data |
| Concept: Dockboard icicle | Floor → dock → aircraft → gate → skill → work package → task, width = planned hours, click to zoom. Lenses: status, schedule against the script, priority, parts, hours charged vs planned, skill backlog | work tasks |
| Concept: BOM icicle | End item → system → assembly → piece part. Lenses: availability (worst-of rollup), supplier lead time, extended cost, supplier on-time conformance. Blocked-only filter and single-aircraft zoom (only the parts its open work needs) | BOM + supply data + work tasks |
| Concept: Gate script | The gate's days across the top, task rows grouped by skill code, today and goal lines. **+ Found work** splices a task into a work package and pushes overlapping not-started tasks after it; **Close** completes a task. Both appear in the PMSS mirror, which only records what would be sent: PMSS is not connected | work tasks |

Work tasks (`work_task`: tail, gate, skill, work package, task, planned start day and days, hours, status, found work, part) and BOM lines (`bom_line`: parent, child, quantity, level, end item) are new entity types and are governed like everything else. Acknowledgements, closures and found work added on screen are stored beside the data (`amxg_overlay`) and never change the governed records; **Delete all data** clears them too.

![gate script](docs-amxg-gate-script.png)

How the workbooks are read:
* **Tail sheets** are recognised by their labels (`G1 Q … Post Dock`, `# of Actual Days`), not by fixed cells, so inserted rows don't break them. Green and blue tabs are kept as `tab_color`, and in-work vs produced comes from the actual finish date. A queue left open on the sheet counts as finished once a later gate has started (on or before the sheet's "Today's Date"). A mismatch between the sheet name and cell D1 is flagged as a data check.
* **Sheets with a data dictionary above the table** (Dashboard supporting data): the real header row is detected and the dictionary is passed to the AI as context. Side-by-side copies of a table are merged into one.
* **Scanned status boards** (PDF/photo): Claude reads them visually into per-aircraft records. Without an API key they are skipped rather than turned into junk rows.

## Architecture

```
 Sources                         Ingestion + AI                       Storage (MongoDB)        Use
 ───────                         ─────────────                        ────────────────         ───
 Excel / CSV / JSON  ─┐                                               raw       (untouched    Dashboard (web/)
 PDF / scans / email ─┤  connectors/  ──►  AI mapping & extraction ──► payloads, lineage)     KPIs & risk lists
 ERP (OData / REST)  ─┤  files.py          (app/ai/claude.py)         records   (canonical    "Ask AI" Q&A
 REST APIs           ─┤  remote.py         cached per table layout     schema, flexible       REST API
 IoT (HTTP / MQTT)   ─┘                                               attributes)
                                                                      mappings  (AI cache)
                                                                      sources
```

**How the AI reads the data**

| Data shape | What the AI does | Cost |
|---|---|---|
| Tables (Excel sheets, CSV, ERP/API JSON, IoT payloads) | Claude sees the headers plus 8 sample rows. It works out the record type (inventory, PO, shipment...) and maps each column to the canonical schema, including ERP technical names like `MATNR`, `EBELN` and `LABST`. The mapping is **cached by column layout**, so a nightly file with the same layout never calls the AI again. | one small call per new layout |
| Unstructured (PDFs incl. scanned, emails, text) | Claude reads the whole document and extracts one record per line item. | one call per document |
| Questions ("which suppliers put SKUs at risk?") | KPIs, risk lists and samples go to Claude, which answers with findings and actions. | one call per question |

Without an API key the app still works. A rule-based header matcher (with SAP synonyms) and PDF table extraction stand in for the AI.

**Canonical model** (`app/models.py`): `inventory`, `purchase_order`, `sales_order`, `shipment`, `supplier`, `product`, `sensor_reading`. Common fields (sku, quantity, supplier, dates...) are indexed. Anything else from the source is kept in `attributes`, so new sources never need a schema migration. That is the main reason to use a NoSQL store here.

**Analytics** (`app/analytics.py`): inventory value, SKUs below reorder point, stockouts, supplier on-time rate, lead time and spend, late PO lines, delayed shipments, demand by SKU, and IoT anomalies (z-score per device and metric, e.g. cold-chain temperature excursions).

## Run it

With Docker (MongoDB + app). Put these in a `.env` file next to `docker-compose.yml`; it is git-ignored, so never commit it:
```
ANTHROPIC_API_KEY=sk-ant-...
MAESTRO_ADMIN_USER=admin
MAESTRO_ADMIN_PASSWORD=choose-a-long-passcode-1
```
```bash
docker compose up --build
```

Or locally with an in-memory database:
```bash
pip install -r requirements.txt
MONGO_URI=mongomock:// MAESTRO_ADMIN_PASSWORD=choose-a-long-passcode-1 uvicorn app.main:app --reload
```
Open http://localhost:8000 and sign in as the administrator. Load the demo packs in **Configuration**.

Tests: `pytest -q`

**On Google Cloud:** [docs/DEPLOY_GCP.md](docs/DEPLOY_GCP.md) is a step-by-step guide to running it on one VM with HTTPS, sign-in and the secrets kept in Secret Manager (files in `deploy/gcp/`).

## Sign-in and admin console (M_12)

Every page and API needs a signed-in operator.

* **Sign-in** (`/login`): operator ID and passcode, with "Remember username".
  * Every attempt, success or failure, and every sign-out goes into the sign-in log.
  * Five failed attempts lock the account for 15 minutes.
  * Sessions last 12 hours (`SESSION_HOURS`). They are kept in an HttpOnly cookie, and only a hash of the token is stored.
* **First administrator.** It's created when there are no users, from `MAESTRO_ADMIN_USER` / `MAESTRO_ADMIN_PASSWORD`. Without a passcode, a random one is printed once in the app log, and the admin is asked to change it at first sign-in.
* **Request access** (`/request-access`): username, email, department and passcode (at least 10 characters, with letters and digits). The account stays *pending* until an administrator approves it and chooses its groups, or rejects it.
* **Reset passcode** (`/reset-passcode`):
  1. The user asks for a reset. The answer is the same whether or not the account exists.
  2. An administrator checks the request and issues a one-time code (valid 24 hours, used once), then gives it to the user.
  3. The user sets a new passcode with it. Their other sessions are signed out.

  The app sends no email.
* **Admin console** (`/maestro#admin`, administrators only) has five tabs:
  * access requests;
  * users: role, groups, disable/enable, reset code;
  * groups and the modules each group sees;
  * passcode resets;
  * the sign-in log.

  Built-in groups: Administrators, Supply analysts, Depot / AMXG, Data stewards, Viewers.
* **Module access is enforced on the server.** A page or API outside the user's modules returns 403, or redirects back to Modules. Menus and module cards hide or mark what the user can't open.
* **Audit log.** Every entry records the signed-in account (`account`) next to any name typed into a form.
* **Over HTTPS**, set `COOKIE_SECURE=true`. `AUTH_ENABLED=false` switches sign-in off; use it only for local experiments and tests.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/ingest/file` | Upload Excel / CSV / JSON / PDF / TXT / EML (`source`, optional `record_type`) |
| `POST /api/ingest/api` | Pull a REST API or ERP OData endpoint. Follows `@odata.nextLink`, `d.__next` and `Link` paging. Supports basic auth or headers. |
| `POST /api/ingest/iot/{source}` | Devices or gateways push JSON readings |
| `POST /api/ingest/mqtt/start` / `stop/{source}` | Subscribe to an MQTT broker topic |
| `GET /api/analytics/overview` and `/api/analytics/{inventory,suppliers,shipments,demand,iot}` | KPIs |
| `POST /api/ask` | Natural-language question → answer, findings, actions |
| `GET /api/records`, `/api/sources`, `/api/mappings` | Browse data, sources and the AI's learned mappings |
| `POST /api/batches` (files[], user, mode=review/auto) · `GET /api/batches[/{id}]` · `POST /api/batches/{id}/publish` · `GET /api/batches/{id}/runs` | Ingestion batches and validate-govern-publish runs |
| `GET /api/objects/{id}` · `PATCH /api/objects/{id}/entity` · `PATCH /api/objects/{id}/fields` (column, action ACCEPTED/IGNORED/EXTENSION/RESET) · `POST /api/objects/{id}/accept-high-confidence` · `POST /api/objects/{id}/profile` · `GET /api/profiles` | Mapping Studio |
| `GET /api/supply/scenarios` · `POST /api/supply/whatif` · `GET/POST /api/supply/decisions` | Scenarios, what-if and decisions |
| `GET /api/amxg/hub`, `/events`, `/timelines?filter=`, `/tiered`, `/status`, `/dockboard`, `/bom?asset=`, `/script/{tail}?gate=` | AMXG screens |
| `POST /api/amxg/events/{id}`, `/api/amxg/script/{tail}/close`, `/api/amxg/script/{tail}/found-work` | Acknowledge / close events, close tasks, add found work (audited) |

ERP examples: SAP S/4HANA (`/sap/opu/odata/sap/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrderItem`, records path `d.results`), Dynamics 365 (`/data/PurchaseOrderLinesV2`), plus Oracle Fusion, NetSuite and Odoo REST endpoints.

## Configuration

| Variable | Default |
|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` (`mongomock://` = in-memory) |
| `ANTHROPIC_API_KEY` | – (unset → rule-based mode) |
| `AI_MODEL` | `claude-opus-5-5` |
| `AI_ENABLED` | `true` |

To use a different AI vendor, implement `map_columns`, `extract_document` and `answer` (see `app/ai/provider.py`) and return your class from `get_provider()`.

## Roadmap to production
- Auth / multi-tenant (an org id on every document), plus a secrets vault for connector credentials
- Scheduled pulls (store connector configs in `sources`, run them with Celery/APScheduler)
- Large volumes: move analytics into MongoDB aggregation pipelines and use time-series collections for IoT
- Human review screen to correct AI mappings (edit `mappings`, then re-process `raw`)
- Demand forecasting and what-if scenarios (the Maestro-style planning layer)
