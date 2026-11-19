"""Spreadsheet reader for real-world workbooks.

Operational Excel files rarely start with a clean header in row 1. This module
handles, per sheet:

1. Known templates, parsed deterministically by their *labels* (not fixed
   cells, so inserted rows don't break them):
     - PDM tail sheet: arrival/start/AMREP block + gate matrix
       (G1 Q, PreDock ... Post Dock x Start / Projected / Actual / # days)
     - Machine info: per-gate WIP plan and required calendar days
2. Tables anywhere on the sheet: the AI (or a heuristic) finds the header row
   and column span, skipping titles and data-dictionary blocks, which are
   passed to the column-mapping step as context instead.
3. Forms with no known template: sent to the AI as a document.
"""
import io
import logging
import re
from datetime import datetime
from typing import Optional

import openpyxl

from ..ai.claude import AIError
from ..ai.provider import RuleBasedProvider, get_provider, heuristic_layout
from ..pipeline import build_record, ingest_document, ingest_rows, store, to_date, to_number

log = logging.getLogger(__name__)
TAIL_RE = re.compile(r"^\d{2}-\d{4,5}$")

# Canonical gate names, flow order, and gate number (queues share the number of the gate they feed).
GATES = [
    ("G1 Q", "Queue to Pre Dock", 1, True), ("PreDock", "Pre Dock", 1, False),
    ("G2 Q", "Queue to Inspection", 2, True), ("Inspection", "Inspection", 2, False),
    ("G3 Q", "Queue to Repair", 3, True), ("Repair", "Repair", 3, False),
    ("G4 Q", "Queue to Restore", 4, True), ("Restore", "Restore", 4, False),
    ("G5 Q", "Queue to Post Dock", 5, True), ("Post Dock", "Post Dock", 5, False),
]
_GATE_ALIASES = {
    "g1q": "G1 Q", "g2q": "G2 Q", "g3q": "G3 Q", "g4q": "G4 Q", "g5q": "G5 Q",
    "predock": "PreDock", "inspection": "Inspection", "inspect": "Inspection",
    "repair": "Repair", "restore": "Restore", "restoreops": "Restore", "postdock": "Post Dock",
}
GATE_INFO = {code: (name, seq, num, q) for seq, (code, name, num, q) in enumerate(GATES, 1)}


def norm(v) -> str:
    return re.sub(r"[^a-z0-9#]", "", str(v).lower()) if v is not None else ""


def gate_code(label) -> Optional[str]:
    return _GATE_ALIASES.get(norm(label).replace("#", ""))


def _filled(v) -> bool:
    return v is not None and not (isinstance(v, str) and not v.strip())


def load_grid(ws) -> list[list]:
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    while rows and not any(_filled(v) for v in rows[-1]):
        rows.pop()
    width = max((max((i for i, v in enumerate(r) if _filled(v)), default=-1) for r in rows), default=-1) + 1
    return [r[:width] for r in rows]


def _find_label(grid, *labels, col=0) -> Optional[int]:
    want = {norm(l) for l in labels}
    for i, row in enumerate(grid):
        if col < len(row) and norm(row[col]).rstrip(":") in want:
            return i
    return None


def _value_right_of(grid, *labels) -> object:
    r = _find_label(grid, *labels)
    if r is None:
        return None
    return next((v for v in grid[r][1:3] if _filled(v)), None)


# ---------------- template: PDM tail sheet ----------------

def is_tail_sheet(grid) -> bool:
    return _gate_header_row(grid) is not None and _find_label(grid, "# of Actual Days") is not None


def _gate_header_row(grid) -> Optional[int]:
    for i, row in enumerate(grid):
        if sum(1 for v in row if gate_code(v)) >= 4:
            return i
    return None


def parse_tail_sheet(grid, sheet_name: str, tab_color: Optional[str], source_id: str) -> list[dict]:
    hdr = _gate_header_row(grid)
    sheet_tail = sheet_name.strip()
    cell_tail = next((str(v).strip() for v in grid[0][2:8] if _filled(v) and re.match(r"^\d{2}-\d+$", str(v).strip())), None)
    asset = sheet_tail if TAIL_RE.match(sheet_tail) else (cell_tail or sheet_tail)
    as_of = None
    for r in grid[:3]:
        for j, v in enumerate(r):
            if norm(v) == "todaysdate" and j + 1 < len(r):
                as_of = to_date(r[j + 1])

    rows = {key: _find_label(grid, *labels) for key, labels in {
        "start": ("Start",), "projected": ("Projected Finish",), "actual": ("Actual Finish",),
        "days": ("# of Actual Days",), "goal": ("# of Goal Days",)}.items()}
    dock_row = grid[hdr - 1] if hdr > 0 else []

    def cell(key, c):
        r = rows.get(key)
        return grid[r][c] if r is not None and c < len(grid[r]) else None

    actual_finish = to_date(_value_right_of(grid, "Actual Finish Date"))
    flow_fields = {
        "asset_id": asset,
        "status": "produced" if actual_finish else "in_work",
        "arrival_date": _value_right_of(grid, "Arrival Date"),
        "start_date": _value_right_of(grid, "Start Date"),
        "projected_finish": _value_right_of(grid, "Projected Finish Date"),
        "actual_finish": actual_finish,
        "amrep_date": _value_right_of(grid, "AMREP"),
        "total_flow_days": _value_right_of(grid, "Total Flow Days"),
        "total_queue_days": _value_right_of(grid, "Total Queue Days"),
        "amrep_days": _value_right_of(grid, "AMREP Days"),
        "in_dock_flow_days": _value_right_of(grid, "In Dock Flow Days"),
        "in_dock_goal_days": _value_right_of(grid, "In Dock Goal Flow Dys", "In Dock Goal Flow Days"),
        "current_milestone": _value_right_of(grid, "Current Milestone"),
        "as_of": as_of,
    }
    # Free-text blocks: work package notes (col D) and notes under "Notes".
    wp = _find_label(grid, "Work Package Notes", col=3)
    wp_notes = [str(grid[i][3]).strip() for i in range(wp + 1, min(wp + 8, len(grid)))
                if wp is not None and len(grid[i]) > 3 and _filled(grid[i][3])] if wp is not None else []
    nr = _find_label(grid, "Notes")
    notes = [str(grid[i][1]).strip() for i in range(nr, len(grid))
             if nr is not None and len(grid[i]) > 1 and _filled(grid[i][1])] if nr is not None else []
    attrs = {"sheet": sheet_name, "tab_color": tab_color, "work_package_notes": wp_notes,
             "notes": notes, "tail_in_sheet": cell_tail}
    if cell_tail and cell_tail != asset:
        attrs["data_quality"] = f"Sheet name {asset} but cell D1 says {cell_tail}"
    if wp_notes:
        flow_fields["location"] = next((n for n in reversed(wp_notes) if n and n[0].isupper() and " " not in n), None)
    records = [build_record("asset_flow", source_id, "excel", flow_fields, attrs)]

    for c, label in enumerate(grid[hdr]):
        code = gate_code(label)
        if not code:
            continue
        name, seq, num, is_q = GATE_INFO[code]
        dock = dock_row[c] if c < len(dock_row) and _filled(dock_row[c]) else None
        records.append(build_record("gate_event", source_id, "excel", {
            "asset_id": asset, "gate": name, "gate_seq": seq, "is_queue": str(is_q).lower(),
            "dock": dock, "start_date": cell("start", c), "projected_finish": cell("projected", c),
            "actual_finish": cell("actual", c), "actual_days": cell("days", c),
            "goal_days": cell("goal", c)}, {"gate_number": num, "sheet": sheet_name}))
    return records


# ---------------- template: machine info (WIP plan) ----------------

def is_machine_info(grid) -> bool:
    return any(any(norm(v) == "wip" for v in r) for r in grid) and any(
        sum(1 for v in r if gate_code(v)) >= 3 for r in grid)


def parse_machine_info(grid, source_id: str) -> list[dict]:
    hdr = next(i for i, r in enumerate(grid) if sum(1 for v in r if gate_code(v)) >= 3)
    wip = next((r for r in grid if any(norm(v) == "wip" for v in r)), [])
    cal = next((r for r in grid if any("caldays" in norm(v) or "requiredcalendardays" in norm(v) for v in r)), [])
    out = []
    for c, label in enumerate(grid[hdr]):
        code = gate_code(label)
        if not code or GATE_INFO[code][3]:
            continue
        name, _, num, _ = GATE_INFO[code]
        out.append(build_record("gate_plan", source_id, "excel", {
            "gate": name, "gate_number": num,
            "wip_plan": wip[c] if c < len(wip) else None,
            "required_days": cal[c] if c < len(cal) else None}))
    extras = {str(grid[hdr][c]): grid[grid.index(wip)][c] for c in range(len(grid[hdr]))
              if wip and c < len(wip) and _filled(grid[hdr][c]) and not gate_code(grid[hdr][c])}
    if out:
        out[0]["attributes"]["machine"] = {k: to_number(v) for k, v in extras.items()}
        title = grid[0][0] if grid and grid[0] else None
        for r in out:
            r["attributes"]["title"] = title
    return out


# ---------------- generic tables ----------------

def _tables_from_layout(grid, layout) -> list[tuple[list[str], list[dict], str]]:
    out = []
    for t in layout.tables:
        if t.header_row >= len(grid):
            continue
        hdr = grid[t.header_row]
        cols = list(range(t.first_col, min(t.last_col, len(hdr) - 1) + 1))
        names, seen = [], set()
        for c in cols:
            n = str(hdr[c]).strip() if _filled(hdr[c]) else f"col{c + 1}"
            while n in seen:
                n += "_"
            seen.add(n)
            names.append(n)
        rows = []
        for r in grid[t.header_row + 1:]:
            vals = [r[c] if c < len(r) else None for c in cols]
            if not any(_filled(v) for v in vals):
                if rows:
                    break
                continue
            rows.append(dict(zip(names, vals)))
        context_rows = [" | ".join(str(v) for v in r if _filled(v)) for r in grid[:t.header_row]]
        out.append((names, rows, "\n".join(x for x in context_rows if x)[:3000]))
    return _merge_overlapping(out)


def _key(name: str) -> str:
    return norm(re.sub(r"\(.*?\)", "", name))


def _merge_overlapping(tables):
    """Side-by-side tables that share their leading key columns are one dataset
    (e.g. a summary copy with an extra column): merge the extra columns into the
    larger table by key instead of loading the rows twice."""
    tables = sorted(tables, key=lambda t: -len(t[1]))
    merged = []
    for names, rows, ctx in tables:
        keys = [_key(n) for n in names]
        target = next((m for m in merged if [_key(n) for n in m[0]][:2] == keys[:2]), None)
        if target is None:
            merged.append((list(names), [dict(r) for r in rows], ctx))
            continue
        t_names, t_rows, _ = target
        t_keys = {_key(n): n for n in t_names}
        extra = [n for n in names if _key(n) not in t_keys]
        index = {(r[t_names[0]], r[t_names[1]]): r for r in t_rows}
        for r in rows:
            hit = index.get((r[names[0]], r[names[1]]))
            if hit is not None:
                for n in extra:
                    hit[n] = r[n]
        t_names.extend(extra)
    return merged


def _layout(grid):
    provider = get_provider()
    clean = heuristic_layout(grid)
    # A sheet whose first row is a full header needs no AI call.
    if clean.tables and clean.tables[0].header_row == 0:
        return clean
    if isinstance(provider, RuleBasedProvider):
        return clean
    try:
        return provider.detect_layout([[_cellstr(v) for v in r] for r in grid])
    except AIError as e:
        log.warning("AI layout detection failed, using heuristic: %s", e)
        return clean


def _cellstr(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    return v


def _newer_snapshot_exists(asset: str, as_of: Optional[datetime]) -> Optional[datetime]:
    """Return the stored status date if the database already holds a newer
    snapshot of this aircraft (e.g. weekly Fixer workbooks uploaded out of order)."""
    if as_of is None:
        return None
    from ..db import get_db
    cur = get_db().records.find_one({"record_type": "asset_flow", "asset_id": asset}, {"as_of": 1})
    stored = cur.get("as_of") if cur else None
    if stored is not None:
        stored = stored.replace(tzinfo=None) if stored.tzinfo else stored
        if stored > as_of.replace(tzinfo=None):
            return stored
    return None


def _ingest_sheet(ws, grid, filename, source_id, record_type_hint) -> list[dict]:
    tc = ws.sheet_properties.tabColor
    tab_color = tc.rgb if tc is not None and isinstance(tc.rgb, str) else None
    meta = {"file": filename, "sheet": ws.title}

    if is_tail_sheet(grid):
        has_dates = any(isinstance(v, datetime) and v.year > 1900 for r in grid for v in r[1:3])
        if ws.title.strip().lower() == "template" or not has_dates:
            return [{**meta, "records": 0, "note": "empty template skipped"}]
        recs = parse_tail_sheet(grid, ws.title, tab_color, source_id)
        asset, as_of = recs[0]["asset_id"], recs[0].get("as_of")
        newer = _newer_snapshot_exists(asset, as_of)
        if newer:
            return [{**meta, "records": 0, "asset_id": asset,
                     "note": f"skipped: newer snapshot ({newer:%d %b %Y}) already loaded"}]
        return [store(recs, {"sheet": ws.title}, source_id, "excel",
                      {**meta, "record_type": "asset_flow+gate_event", "template": "pdm_tail_sheet",
                       "asset_id": asset},
                      replace_keys=[{"asset_id": asset, "record_type": {"$in": ["asset_flow", "gate_event"]}}])]
    if is_machine_info(grid):
        recs = parse_machine_info(grid, source_id)
        return [store(recs, {"sheet": ws.title}, source_id, "excel",
                      {**meta, "record_type": "gate_plan", "template": "machine_info"},
                      replace_keys=[{"record_type": "gate_plan"}])]

    layout = _layout(grid)
    out = []
    if layout.kind == "table" and layout.tables:
        for names, rows, ctx in _tables_from_layout(grid, layout):
            out.append(ingest_rows(rows, source_id=source_id, source_type="excel",
                                   record_type_hint=record_type_hint, context=ctx,
                                   meta={**meta, "header_row": layout.tables[0].header_row + 1}))
    elif layout.kind == "form":
        text = "\n".join(" | ".join(str(_cellstr(v)) for v in r if _filled(v)) for r in grid)
        out.append(ingest_document(source_id=source_id, source_type="excel",
                                   text=text, filename=f"{filename}#{ws.title}"))
    return out


def ingest_workbook(filename: str, data: bytes, source_id: str,
                    record_type_hint: Optional[str] = None) -> list[dict]:
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    results = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        grid = load_grid(ws)
        if not grid:
            continue
        try:
            results.extend(_ingest_sheet(ws, grid, filename, source_id, record_type_hint))
        except AIError as e:
            # One sheet needing the AI must not block the rest of the workbook.
            log.warning("Sheet %s in %s failed: %s", ws.title, filename, e)
            results.append({"file": filename, "sheet": ws.title, "records": 0, "error": str(e)})
    return results
