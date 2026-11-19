"""AI provider selection.

`get_provider()` returns Claude when AI is enabled and credentials exist,
otherwise a rule-based fallback so the platform still works offline. To add
another model vendor, implement the same methods (map_columns, detect_layout,
extract_document, answer) and return it here.
"""
import os
import re
from functools import lru_cache

from .. import config
from ..models import (ENTITIES, AnalystAnswer, ColumnMapping, DocumentExtraction,
                      MappingResult, SheetLayout, SheetTable)

# Header synonyms (normalised: lowercase alphanumerics), incl. SAP technical names.
_SYNONYMS: dict[str, list[str]] = {
    "sku": ["sku", "item", "itemcode", "itemno", "material", "matnr", "partno",
            "partnumber", "product", "productcode", "article", "nsn"],
    "description": ["description", "desc", "itemname", "productname", "maktx", "text"],
    "location": ["location", "warehouse", "plant", "werks", "site", "dc", "lgort", "store", "base"],
    "quantity": ["qty", "quantity", "onhand", "qtyonhand", "stock", "menge", "labst", "units",
                 "orderedqty", "orderqty", "shippedqty"],
    "unit": ["uom", "unit", "meins", "unitofmeasure"],
    "unit_cost": ["unitcost", "unitprice", "price", "cost", "netpr", "netprice"],
    "total_value": ["total", "amount", "netvalue", "netwr", "linetotal", "totalvalue"],
    "currency": ["currency", "curr", "waers"],
    "reorder_point": ["reorderpoint", "rop", "safetystock", "minstock", "eisbe", "minqty"],
    "supplier": ["supplier", "vendor", "lifnr", "suppliername", "vendorname"],
    "customer": ["customer", "client", "kunnr", "customername", "soldto"],
    "order_id": ["po", "ponumber", "orderid", "ordernumber", "ebeln", "vbeln",
                 "invoice", "invoiceno", "documentno"],
    "shipment_id": ["shipmentid", "tracking", "trackingno", "awb", "bol", "consignment"],
    "carrier": ["carrier", "scac"],
    "status": ["status", "state", "statusicon"],
    "order_date": ["orderdate", "podate", "bedat", "documentdate", "createdon"],
    "promised_date": ["promiseddate", "expecteddate", "eta", "eindt", "requesteddate", "deliverydate"],
    "delivered_date": ["delivereddate", "receiptdate", "receiveddate", "receivedon", "receipt",
                       "grdate", "actualdate", "ata"],
    "lead_time_days": ["leadtime", "leadtimedays", "plifz"],
    "origin": ["origin", "from", "shipfrom"],
    "destination": ["destination", "to", "shipto"],
    "asset_id": ["tailnumber", "tail", "tailno", "serial", "serialnumber", "assetid", "asset",
                 "aircraft", "machine", "machineid", "equipment", "equnr", "vehicle"],
    "device_id": ["deviceid", "sensorid", "device", "sensor"],
    "metric": ["metric", "measurement", "parameter"],
    "value": ["value", "reading", "measurementvalue", "sensorvalue"],
    "threshold_high": ["thresholdhigh", "upperlimit", "highlimit", "max", "ucl"],
    "threshold_low": ["thresholdlow", "lowerlimit", "lowlimit", "min", "lcl"],
    "timestamp": ["timestamp", "time", "datetime", "ts", "eventtime"],
    "mc_rate": ["mcrate", "mcratecurrent", "missioncapablerate", "availability", "mc"],
    "mtbm_actual": ["mtbmactual", "mtbm"],
    "mtbm_target": ["mtbmtarget", "mtbmgoal"],
    "alert_count": ["alertcount", "alerts", "squawks"],
    "subsystem": ["subsystem", "subsystemname", "system", "wuc"],
    "health_score": ["healthscore", "health", "healthscorepct", "score"],
    "critical_fault_count": ["criticalfaultcount", "criticalfaults"],
    "date_logged": ["datelogged", "loggeddate", "datediscovered", "date"],
    "fault_code": ["faultcode", "jcn", "code", "discrepancycode"],
    "severity": ["severity", "priority", "criticality"],
    "technician": ["technicianassigned", "technician", "assignedto", "tech", "mechanic"],
    "induction_date": ["inductiondate", "induction", "inputdate", "startdate", "arrivaldate"],
    "due_date": ["duedate", "amrep", "amrepdate", "contractdate"],
    "sold_date": ["solddate", "sold", "outputdate"],
    "notes": ["notes", "remarks", "comment", "comments"],
}
_LOOKUP: dict[str, list[str]] = {}
for _field, _syns in _SYNONYMS.items():
    for _s in _syns:
        _LOOKUP.setdefault(_s, []).append(_field)


def _norm(col: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(col).lower())


def _map_for(entity: str, columns: list[str]) -> list[ColumnMapping]:
    fields = ENTITIES[entity]
    used: set[str] = set()
    out = []
    for c in columns:
        target = next((f for f in _LOOKUP.get(_norm(c), []) if f in fields and f not in used), None)
        if target:
            used.add(target)
        out.append(ColumnMapping(source_column=c, canonical_field=target))
    return out


class RuleBasedProvider:
    """Deterministic fallback used when no AI credentials are configured."""
    name = "rules"

    def map_columns(self, columns, sample_rows, context: str = "") -> MappingResult:
        best, best_score = "other", 0.0
        best_maps = [ColumnMapping(source_column=c, canonical_field=None) for c in columns]
        for entity, fields in ENTITIES.items():
            if not fields:
                continue
            maps = _map_for(entity, columns)
            hits = sum(1 for m in maps if m.canonical_field)
            # favour entities whose own fields are well covered, not just generic ones
            score = hits + hits / len(fields)
            if score > best_score:
                best, best_score, best_maps = entity, score, maps
        return MappingResult(record_type=best, mappings=best_maps,
                             notes="Rule-based header matching (AI disabled).")

    def detect_layout(self, grid: list[list]) -> SheetLayout:
        return heuristic_layout(grid)

    def extract_document(self, *, text=None, pdf_bytes=None, filename="") -> DocumentExtraction:
        return DocumentExtraction(document_kind="unknown (AI disabled)", records=[], assets=[])

    def answer(self, question, context) -> AnalystAnswer:
        k = context.get("kpis", {})
        findings = [f"{name}: {val}" for name, val in k.items()]
        return AnalystAnswer(
            answer="AI is disabled, so here are the computed KPIs. Set ANTHROPIC_API_KEY "
                   "to get natural-language answers.",
            key_findings=findings, recommended_actions=[])


# ---------------- sheet layout heuristics ----------------

def _filled(v) -> bool:
    return v is not None and not (isinstance(v, str) and not v.strip())


def heuristic_layout(grid: list[list]) -> SheetLayout:
    """Find the header row of the main table inside a messy sheet.

    A header row is all-text, has >= 2 cells, and is followed by the longest run
    of rows that fill most of its columns (so a data-dictionary block above the
    real table loses to the table). Side-by-side tables split by an empty column
    are returned separately.
    """
    best: tuple[int, int, list[tuple[int, int]]] = (0, -1, [])
    for r, row in enumerate(grid):
        cols = [c for c, v in enumerate(row) if _filled(v)]
        if len(cols) < 2 or not all(isinstance(row[c], str) for c in cols):
            continue
        spans, start = [], cols[0]
        for a, b in zip(cols, cols[1:] + [None]):
            if b is None or b != a + 1:
                spans.append((start, a))
                start = b
        spans = [sp for sp in spans if sp[1] > sp[0]]
        runs = []
        for a, b in spans:
            width, run = b - a + 1, 0
            for nxt in grid[r + 1:]:
                filled = sum(1 for c in range(a, b + 1) if c < len(nxt) and _filled(nxt[c]))
                if filled < max(1, (width + 1) // 2):
                    break
                run += 1
            runs.append(run)
        if runs and max(runs) > best[0]:
            best = (max(runs), r, [sp for sp, n in zip(spans, runs) if n > 0])
    run, header, spans = best
    if header < 0:
        has_data = any(any(_filled(v) for v in row) for row in grid)
        return SheetLayout(kind="form" if has_data else "empty", tables=[], notes="No table header found")
    tables = [SheetTable(header_row=header, first_col=a, last_col=b) for a, b in spans]
    return SheetLayout(kind="table", tables=tables, notes=f"Header at row {header}, {run} data rows")


def _has_credentials() -> bool:
    keys = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")
    if any(os.getenv(k) for k in keys):
        return True
    return os.path.isdir(os.path.expanduser("~/.config/anthropic"))


@lru_cache
def get_provider():
    if config.AI_ENABLED and _has_credentials():
        from .claude import ClaudeProvider
        return ClaudeProvider()
    return RuleBasedProvider()
