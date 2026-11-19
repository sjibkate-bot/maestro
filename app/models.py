"""Canonical data model.

Every source (Excel, PDF, ERP, IoT...) is normalised into *records*. A record
has a `record_type` (an entity from ENTITIES below), the entity's canonical
fields, and `attributes` holding any source column that didn't map. MongoDB
stores this without schema changes, so adding an entity is a dict entry here.

Entities cover classic supply chain (inventory, POs, shipments...) and the
Maestro-SC maintenance / depot domain (aircraft status, gate events,
subsystem health, telemetry, faults, production schedule).
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

N, D, S = "number", "date", "string"

# entity -> {field: (kind, description)}
ENTITIES: dict[str, dict[str, tuple[str, str]]] = {
    # ---------------- supply chain ----------------
    "inventory": {
        "sku": (S, "Item / material / part number"), "description": (S, "Item description"),
        "location": (S, "Warehouse, plant, site"), "quantity": (N, "Quantity on hand"),
        "unit": (S, "Unit of measure"), "unit_cost": (N, "Cost per unit"),
        "total_value": (N, "Stock value"), "reorder_point": (N, "Reorder point / safety stock"),
        "lead_time_days": (N, "Replenishment lead time in days"),
    },
    "purchase_order": {
        "order_id": (S, "PO number"), "sku": (S, "Item / part number"), "description": (S, "Line text"),
        "supplier": (S, "Supplier / vendor"), "quantity": (N, "Ordered quantity"),
        "unit_cost": (N, "Unit price"), "total_value": (N, "Line amount"), "currency": (S, "Currency"),
        "order_date": (D, "PO date"), "promised_date": (D, "Promised / due / expected date"),
        "delivered_date": (D, "Actual receipt date"), "status": (S, "Line status"),
        "location": (S, "Receiving site"), "lead_time_days": (N, "Lead time in days"),
    },
    "sales_order": {
        "order_id": (S, "Sales order number"), "customer": (S, "Customer"), "sku": (S, "Item"),
        "quantity": (N, "Ordered quantity"), "total_value": (N, "Line amount"),
        "order_date": (D, "Order date"), "promised_date": (D, "Requested date"),
        "delivered_date": (D, "Shipped / delivered date"), "status": (S, "Status"),
    },
    "shipment": {
        "shipment_id": (S, "Shipment / tracking / BOL number"), "carrier": (S, "Carrier"),
        "origin": (S, "Ship-from"), "destination": (S, "Ship-to"), "status": (S, "Status"),
        "order_id": (S, "Related order"), "sku": (S, "Item"), "quantity": (N, "Quantity"),
        "promised_date": (D, "ETA / promised date"), "delivered_date": (D, "Actual arrival"),
    },
    "supplier": {
        "supplier": (S, "Supplier name / id"), "location": (S, "Site / city"),
        "country": (S, "Country of the supplying site"), "tier": (N, "Supply tier: 1 = direct supplier, 2+ = sub-tier"),
        "supplies_to": (S, "For a sub-tier supplier: the supplier it delivers to"),
        "commodity": (S, "What it supplies (category / commodity)"),
        "sole_source": (S, "yes if it is the only qualified source for what it supplies"),
        "annual_spend": (N, "Annual spend with the supplier"),
        "lead_time_days": (N, "Standard lead time"), "status": (S, "Approval status"),
    },
    "product": {
        "sku": (S, "Item / part number"), "description": (S, "Description"),
        "unit_cost": (N, "Cost"), "unit": (S, "UoM"), "supplier": (S, "Primary supplier"),
        "lead_time_days": (N, "Lead time"),
    },
    "sensor_reading": {
        "asset_id": (S, "Asset the sensor is on: tail number, machine, truck, reefer"),
        "device_id": (S, "Sensor / device id"), "metric": (S, "Measured quantity"),
        "value": (N, "Reading"), "threshold_high": (N, "Upper limit"),
        "threshold_low": (N, "Lower limit"), "timestamp": (D, "Reading time"),
        "location": (S, "Site"),
    },
    # ---------------- maintenance / depot (Maestro-SC) ----------------
    "asset_status": {
        "asset_id": (S, "Tail number / serial / machine id"), "status": (S, "Up / Down / Degraded"),
        "mc_rate": (N, "Mission-capable (availability) rate, percent"),
        "mtbm_actual": (N, "Mean time between maintenance, actual (hours)"),
        "mtbm_target": (N, "MTBM target (hours)"), "alert_count": (N, "Active alerts / squawks"),
        "location": (S, "Base / site"),
    },
    "subsystem_health": {
        "asset_id": (S, "Tail number / asset"), "subsystem": (S, "Subsystem name"),
        "health_score": (N, "Health score 0-100"), "critical_fault_count": (N, "Critical faults"),
    },
    "fault": {
        "asset_id": (S, "Tail number / asset"), "date_logged": (D, "When logged"),
        "fault_code": (S, "Fault code / JCN"), "severity": (S, "Critical / Major / Minor"),
        "description": (S, "Fault text"), "technician": (S, "Assigned technician"),
        "status": (S, "Open / closed"),
    },
    "production_schedule": {
        "asset_id": (S, "Tail number / unit"), "induction_date": (D, "Induction / start date"),
        "due_date": (D, "Contract due date (AMREP)"), "sold_date": (D, "Sold / delivered date"),
        "notes": (S, "Notes"),
    },
    "gate_event": {
        "asset_id": (S, "Tail number"), "gate": (S, "Gate / phase / queue name"),
        "gate_seq": (N, "Order of the gate in the flow"), "is_queue": (S, "true for a queue gate"),
        "dock": (S, "Dock / bay"), "start_date": (D, "Gate start"),
        "projected_finish": (D, "Projected finish"), "actual_finish": (D, "Actual finish"),
        "actual_days": (N, "Days in gate"), "goal_days": (N, "Required days for gate"),
    },
    "asset_flow": {
        "asset_id": (S, "Tail number"), "status": (S, "in_work or produced"),
        "arrival_date": (D, "Arrival date"), "start_date": (D, "Start date"),
        "projected_finish": (D, "Projected finish"), "actual_finish": (D, "Actual finish"),
        "amrep_date": (D, "Contract (AMREP) date"), "total_flow_days": (N, "Total flow days"),
        "total_queue_days": (N, "Total queue days"), "amrep_days": (N, "Flow days allowed by contract"),
        "in_dock_flow_days": (N, "In-dock flow days"), "in_dock_goal_days": (N, "In-dock goal days"),
        "current_milestone": (S, "Current milestone"), "location": (S, "Home base"),
        "as_of": (D, "Status date of the source"),
    },
    "gate_plan": {
        "gate": (S, "Gate name"), "gate_number": (N, "Gate number"), "wip_plan": (N, "Planned WIP"),
        "required_days": (N, "Required calendar days"),
    },
    "production_summary": {
        "organization": (S, "ALC / wing / group the line belongs to, e.g. OC-ALC"),
        "weapon_system": (S, "Weapon system / aircraft type, e.g. B-52"), "period": (S, "Fiscal period, e.g. FY30 Q3"),
        "produced": (N, "Aircraft produced (delivered) in the period"), "goal": (N, "Aircraft planned for the period"),
        "on_time_pct": (N, "Share delivered on or before the contract date, percent"),
        "hours_actual": (N, "Production hours actual"), "hours_target": (N, "Production hours target"),
        "yield_actual": (N, "Yield actual (0-1)"), "yield_target": (N, "Yield target (0-1)"),
        "as_of": (D, "Status date"),
    },
    "issue": {
        "issue_id": (S, "Issue identifier"), "organization": (S, "Organization raising or owning the issue (DLA, 448 SCMW...)"),
        "weapon_system": (S, "Weapon system affected"), "status": (S, "Critical / Issue / Stable"),
        "title": (S, "Short description"), "impact_days": (N, "Schedule impact in days"),
        "owner": (S, "Person or office working it"), "date_logged": (D, "When raised"), "notes": (S, "Detail"),
    },
    "bom_line": {
        "parent": (S, "Parent assembly part number (the end item for top-level lines)"),
        "child": (S, "Component part number"), "description": (S, "Component description"),
        "quantity": (N, "Quantity per parent"), "level": (N, "Indenture level (1 = directly under the end item)"),
        "end_item": (S, "End item / weapon system, e.g. B-52H"),
    },
    "work_task": {
        "asset_id": (S, "Tail number"), "gate": (S, "Gate / phase"), "skill": (S, "Skill or shop code"),
        "work_package": (S, "Work package"), "task": (S, "Task / operation"), "task_id": (S, "Task identifier (JCN, operation)"),
        "planned_start_day": (N, "Planned start, day of the gate (1 = first day)"), "planned_days": (N, "Planned duration in days"),
        "hours_planned": (N, "Planned hours"), "hours_actual": (N, "Actual hours charged"),
        "status": (S, "Not started / In work / Done / Blocked"), "found_work": (S, "yes if added after inspection (found work)"),
        "part_number": (S, "Part the task waits on, if any"), "closed_date": (D, "Closure date"),
    },
    "risk_signal": {
        "signal_id": (S, "Identifier of the signal in its source feed"), "source": (S, "Feed that produced it"),
        "category": (S, "sanctions, hazard, stability or unrest"), "country": (S, "ISO 3166 alpha-3 country code"),
        "country_name": (S, "Country name"), "supplier": (S, "Supplier the signal concerns, if any"),
        "risk_score": (N, "0-100, higher is worse"), "title": (S, "Short description"), "url": (S, "Link to the source"),
        "event_date": (D, "When the event happened or the value was published"),
    },
    "other": {},
}

RecordType = Enum("RecordType", {k: k for k in ENTITIES}, type=str)

ALL_FIELDS: dict[str, tuple[str, str]] = {}
for _fields in ENTITIES.values():
    for _f, _spec in _fields.items():
        ALL_FIELDS.setdefault(_f, _spec)
NUMERIC_FIELDS = {f for f, (k, _) in ALL_FIELDS.items() if k == N}
DATE_FIELDS = {f for f, (k, _) in ALL_FIELDS.items() if k == D}
COMMON_FIELDS = {"record_type", "source_id", "source_type", "attributes", "ingested_at", "batch_id"}


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Schemas Claude fills via structured outputs ----------

class ColumnMapping(BaseModel):
    source_column: str
    canonical_field: Optional[str] = Field(
        description="A field of the chosen record type, or null if no good match")


class MappingResult(BaseModel):
    record_type: str
    mappings: list[ColumnMapping]
    notes: str


class SheetTable(BaseModel):
    header_row: int = Field(description="0-based row index of the column headers")
    first_col: int = Field(description="0-based index of the table's first column")
    last_col: int = Field(description="0-based index of the table's last column (inclusive)")


class SheetLayout(BaseModel):
    kind: str = Field(description="'table', 'form' (labelled values / report layout) or 'empty'")
    tables: list[SheetTable]
    notes: str


class ExtractedRecord(BaseModel):
    """A line item pulled out of an unstructured document (PDF, email, text)."""
    record_type: str
    sku: Optional[str]
    description: Optional[str]
    location: Optional[str]
    quantity: Optional[float]
    unit: Optional[str]
    unit_cost: Optional[float]
    total_value: Optional[float]
    currency: Optional[str]
    supplier: Optional[str]
    customer: Optional[str]
    order_id: Optional[str]
    shipment_id: Optional[str]
    status: Optional[str]
    order_date: Optional[str] = Field(description="ISO 8601 date, or null")
    promised_date: Optional[str] = Field(description="ISO 8601 date, or null")
    delivered_date: Optional[str] = Field(description="ISO 8601 date, or null")
    origin: Optional[str]
    destination: Optional[str]


class BoardAsset(BaseModel):
    """One aircraft / asset on a status board (whiteboard, at-a-glance chart)."""
    asset_id: str
    location: Optional[str] = Field(description="Dock, bay or area, e.g. 'Dock 4', 'Pre Dock'")
    current_gate: Optional[str]
    day_in_gate: Optional[float]
    gate_goal_days: Optional[float]
    flow_day: Optional[float]
    flow_goal_days: Optional[float]
    queue_days: Optional[float]
    amrep_date: Optional[str] = Field(description="ISO 8601 date, or null")
    next_big_thing: list[str]
    known_repairs: list[str]
    notes: list[str]


class DocumentExtraction(BaseModel):
    document_kind: str = Field(description="e.g. purchase order, invoice, BOL, status board")
    records: list[ExtractedRecord]
    assets: list[BoardAsset] = Field(description="Assets on a maintenance / production status board; empty otherwise")


class AnalystAnswer(BaseModel):
    answer: str
    key_findings: list[str]
    recommended_actions: list[str]
    evidence: list[str] = Field(default_factory=list, description="Specific data points the answer rests on")
    confidence: Optional[float] = Field(default=None, description="0-1: how well the data supports the recommendation")
