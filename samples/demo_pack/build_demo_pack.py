"""Build the Maestro-SC customer demo data pack.

    python samples/demo_pack/build_demo_pack.py

Inputs (customer files, kept unchanged):
    samples/maestro/source/LS_B-52_Fixer_Chart_Data_20300909.xlsx
    samples/maestro/source/LS_B-52_Fixer_Chart_Data_20300916.xlsx
    samples/maestro/Dashboard_Supporting_Data.xlsx

Outputs (samples/demo_pack/out/):
    01/02  cleaned weekly Fixer snapshots         -> Docks, Gates, Machine (depot)
    03     fleet health aligned to the same tails -> Fleet, Alerts
    04     OC-ALC weapon-system summary + issues  -> ALC production & issues (governed)
    05-09  B-52 PDM parts supply data              -> Supply analysis
    90     deliberately flawed fault entries       -> governance quarantine demo
    Cleaning_Log.csv  every change to customer data, with reason
    README.md         what each file is, load order, demo script, open questions

Customer data is changed only where it is wrong or ambiguous, and every change is
logged. Anything added for the demo is marked SYNTHETIC in the log and the README.
Supply data is dated relative to the day the pack is built, so overdue lines,
stock-outs and the live risk feeds work on the demo day; depot data keeps the
customer's notional 2030 dates.
"""
import csv
import random
import re
from copy import copy
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "samples" / "maestro" / "source"
OUT = Path(__file__).resolve().parent / "out"
random.seed(52)
LOG: list[dict] = []
GATES = ["G1 Q", "PreDock", "G2 Q", "Inspection", "G3 Q", "Repair", "G4 Q", "Restore", "G5 Q", "Post Dock"]
IN_DOCK = {"Inspection", "Repair", "Restore"}   # the customer's sheets mark docks under these gate columns


def log(file, sheet, cell, before, after, reason, kind="fix"):
    LOG.append({"file": file, "sheet": sheet, "cell": cell, "before": "" if before is None else str(before),
                "after": "" if after is None else str(after), "kind": kind, "reason": reason})


def d(v):
    return v.date() if isinstance(v, datetime) else v


# ---------------------------------------------------------------- 01/02 Fixer snapshots

TAIL_FIXES = {"64-4003": "60-4003", "61-3547": "61-3457", "61-5912": "61-5812", "60-00127": "60-0127"}
# One aircraft per dock (six docks, per the Machine Info note "hangar, 6 docks"). Demo assumption:
# the source marks six aircraft "Dock 5". Pre Dock / Post Dock work is off-dock.
DOCKS = {"61-3457": "Dock 4", "60-7890": "Dock 5", "60-7531": "Dock 1", "61-4657": "Dock 2",
         "61-2847": "Dock 3", "60-2861": "Dock 6"}


def clean_fixer(src: Path, out: Path) -> dict:
    fn = out.name
    wb = openpyxl.load_workbook(src, data_only=True)   # freeze formulas to their values
    for name in ("Summary", "G3 summary"):
        if name in wb.sheetnames:
            del wb[name]
            log(fn, name, "(sheet)", "present", "removed",
                "Working sheet derived from the tail tabs; it contradicts them (other start dates, its own 'today'). "
                "The tail tabs are the source of truth.")
    mi = wb["Machine Info"]
    cleared = 0
    for row in mi.iter_rows():
        for c in row:
            if c.value is not None and (c.row > 5 or c.column > 10):
                c.value = None
                cleared += 1
    log(fn, "Machine Info", "rows 6+, columns K+", f"{cleared} cells", "cleared",
        "Notes, a copy of one tail tab and a Gate 3 table sat beside the throughput table; kept only the table (A1:J5).")

    sch = wb["FY30 Schedule"]
    goals = []
    for r in range(1, sch.max_row + 1):
        a = sch.cell(r, 1)
        if isinstance(a.value, str) and a.value.strip() in TAIL_FIXES:
            log(fn, "FY30 Schedule", a.coordinate, a.value, TAIL_FIXES[a.value.strip()], "Tail number typo (no such tail tab)")
            a.value = TAIL_FIXES[a.value.strip()]
        for col in (7, 8, 9):                                  # Quarterly Goals block G:I
            v = sch.cell(r, col).value
            if v is not None:
                if col == 7 and isinstance(v, str) and v.startswith("Q") and len(v) <= 3:
                    goals.append((v, sch.cell(r, 8).value, sch.cell(r, 9).value))
                sch.cell(r, col).value = None
    log(fn, "FY30 Schedule", "G1:I6", "Quarterly Goals table", "moved to 04_OC-ALC_Weapon_System_Summary.xlsx",
        "A second table beside the schedule breaks it as a table; produced vs goal belongs to the ALC summary.")
    sch["E1"].value = "Notes"
    sch["F1"].value = "Reference Tail"
    log(fn, "FY30 Schedule", "E1, F1", "(no header)", "Notes / Reference Tail",
        "Unlabelled columns. Column F matches the tails on the scanned status board: assumed to be an alias. CONFIRM.",
        kind="question")

    due = {str(sch.cell(r, 1).value).strip(): sch.cell(r, 3).value for r in range(2, sch.max_row + 1) if sch.cell(r, 1).value}
    tails = {}
    for ws in wb.worksheets:
        if not re.match(r"^\d{2}-\d{4}$", ws.title):
            continue
        t = ws.title
        if str(ws["D1"].value).strip() != t:
            log(fn, t, "D1", ws["D1"].value, t, "Tail number in the header differs from the tab name")
            ws["D1"].value = t
        amrep, sched_due = ws["B5"].value, due.get(t)
        if isinstance(amrep, datetime) and isinstance(sched_due, datetime) and amrep != sched_due \
                and abs((amrep - sched_due).days) in range(360, 370):
            log(fn, t, "B5", d(amrep), d(sched_due), "AMREP year typo: differs from the FY30 Schedule due date by one year")
            ws["B5"].value = sched_due
        hdr = next(r for r in range(1, 15) if ws.cell(r, 2).value == "G1 Q")
        start_r, proj_r, act_r = hdr + 1, hdr + 2, hdr + 3
        if ws["B1"].value is None and isinstance(ws["B2"].value, datetime):
            ws["B1"].value = ws["B2"].value - timedelta(days=1)
            log(fn, t, "B1", None, d(ws["B1"].value), "Arrival date blank; set to start date - 1 day (the pattern on every other tab)",
                kind="derived")
        if ws["B2"].value is None:
            first = next((ws.cell(start_r, c).value for c in range(2, 12) if isinstance(ws.cell(start_r, c).value, datetime)), None)
            if first:
                ws["B2"].value = first
                log(fn, t, "B2", None, d(first), "Start date blank; set to the first gate start", kind="derived")
        for c in range(2, 12):
            s, f = ws.cell(start_r, c).value, ws.cell(act_r, c).value
            if isinstance(s, datetime) and isinstance(f, datetime) and f < s:
                log(fn, t, ws.cell(act_r, c).coordinate, d(f), d(s), "Finish before start (0-day queue); finish set to start")
                ws.cell(act_r, c).value = s
        # docks: one row above the gate header
        dock_r = hdr - 1
        want = DOCKS.get(t)
        for c in range(2, 12):
            cell = ws.cell(dock_r, c)
            gate = ws.cell(hdr, c).value
            new = want if (want and gate in IN_DOCK) else None
            if cell.value != new:
                if cell.value is not None or new is not None:
                    log(fn, t, cell.coordinate, cell.value, new,
                        "One aircraft per dock (source shows six aircraft in 'Dock 5'). DEMO ASSUMPTION - confirm real docks.",
                        kind="synthetic")
                cell.value = new
        tails[t] = {"tab_color": ws.sheet_properties.tabColor.rgb if ws.sheet_properties.tabColor else None,
                    "as_of": d(ws["K1"].value)}
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return {"tails": tails, "goals": goals, "schedule": [(sch.cell(r, 1).value, d(sch.cell(r, 2).value), d(sch.cell(r, 3).value),
                                                          d(sch.cell(r, 4).value)) for r in range(2, sch.max_row + 1) if sch.cell(r, 1).value]}


# ---------------------------------------------------------------- 03 fleet health

SUBSYS_KEYWORDS = [("hydraulic", "Hydraulics"), ("engine", "Propulsion"), ("oil", "Propulsion"), ("bus", "Electrical"),
                   ("electric", "Electrical"), ("radar", "Avionics"), ("avionic", "Avionics"), ("fuel", "Fuel"),
                   ("struct", "Airframe"), ("crack", "Airframe")]


def table(ws, first_header):
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    # the data dictionary at the top repeats column names: the real header is the LAST matching row
    h = max(i for i, r in enumerate(rows) if r and r[0] == first_header)
    head = [c for c in rows[h] if c is not None]
    out = []
    for r in rows[h + 1:]:
        if r[0] is None:
            break
        out.append(dict(zip(head, r[:len(head)])))
    return out


def fleet_health(tails: list[str], as_of: date, fn: str) -> dict:
    src = openpyxl.load_workbook(ROOT / "samples" / "maestro" / "Dashboard_Supporting_Data.xlsx", data_only=True)
    status = table(src["Fleet Status"], "Tail_Number")
    tele = table(src["Telemetry"], "Timestamp")
    faults = table(src["Fault Registry"], "Tail_Number")
    sh_rows = [list(r) for r in src["Subsystem Health"].iter_rows(values_only=True)]
    h = max(i for i, r in enumerate(sh_rows) if r and r[0] == "Tail_Number")
    subs = [{"Tail_Number": r[0], "Subsystem_Name": r[1], "Health_Score": r[2]} for r in sh_rows[h + 1:] if r[0]]
    crit = {(r[4], r[5]): r[7] for r in sh_rows[h + 1:] if len(r) > 7 and r[4]}   # right-hand table holds the counts
    # faults: same codes/texts/technicians, re-dated into the demo window
    shift = datetime.combine(as_of, datetime.min.time()) - timedelta(days=4) - max(f["Date_Logged"] for f in faults)
    fault_rows = []
    for f in faults:
        nd = f["Date_Logged"] + shift
        log(fn, "Fault Registry", f"{f['Tail_Number']} {f['Fault_Code']}", d(f["Date_Logged"]), d(nd),
            "Re-dated into the demo window (source 2026 dates vs 2030 depot data); order and gaps kept", kind="fix")
        sub = next((s for k, s in SUBSYS_KEYWORDS if k in f["Description"].lower()), "Airframe")
        fault_rows.append({"Tail_Number": f["Tail_Number"], "Date_Logged": nd, "Fault_Code": f["Fault_Code"],
                           "Severity": f["Severity"], "Subsystem": sub, "Description": f["Description"],
                           "Technician_Assigned": f["Technician_Assigned"], "Status": "Open"})
    # critical counts in Subsystem Health need matching critical faults
    have = {}
    for f in fault_rows:
        if f["Severity"] == "Critical":
            have[(f["Tail_Number"], f["Subsystem"])] = have.get((f["Tail_Number"], f["Subsystem"]), 0) + 1
    texts = {"Propulsion": "Engine #{n} vibration above limit", "Hydraulics": "Hydraulic pressure drop - System {n}",
             "Electrical": "Generator {n} intermittent", "Avionics": "Nav unit {n} BIT failure", "Fuel": "Fuel quantity probe {n} fault",
             "Airframe": "Corrosion found - station {n}"}
    for (t, s), n in crit.items():
        missing = int(n or 0) - have.get((t, s), 0)
        for k in range(max(0, missing)):
            row = {"Tail_Number": t, "Date_Logged": datetime.combine(as_of, datetime.min.time()) - timedelta(days=2 + k),
                   "Fault_Code": f"{s[:3].upper()}{100 + k}", "Severity": "Critical", "Subsystem": s,
                   "Description": texts[s].format(n=k + 1), "Technician_Assigned": "Unassigned", "Status": "Open"}
            fault_rows.append(row)
            log(fn, "Fault Registry", f"{t} {row['Fault_Code']}", None, row["Description"],
                f"Subsystem Health shows {n} critical fault(s) on {t} {s}; registry had none. Added so both agree.", kind="synthetic")

    open_by_tail = {}
    for f in fault_rows:
        open_by_tail[f["Tail_Number"]] = open_by_tail.get(f["Tail_Number"], 0) + 1
    fleet_rows = []
    for r in status:
        mc = float(r["MC_Rate_Current"])
        st = "Up" if mc >= 80 else "Degraded" if mc >= 60 else "Down"
        if st != r["Status"]:
            log(fn, "Fleet Status", f"{r['Tail_Number']} Status", r["Status"], st,
                "Status set from the MC-rate bands in the customer's own Health Score table (>=80% flight-worthy)")
        alerts = r["Alert_Count"]   # customer's count of active flags/squawks (the registry lists only some of them)
        fleet_rows.append({"Tail_Number": r["Tail_Number"], "Status": st, "MC_Rate_Current": mc, "MTBM_Actual": r["MTBM_Actual"],
                           "MTBM_Target": r["MTBM_Target"], "Alert_Count": alerts, "As_Of": as_of})
    sub_rows = [{"Tail_Number": t, "Subsystem_Name": s, "Health_Score": hs,
                 "Critical_Fault_Count": sum(1 for f in fault_rows if f["Tail_Number"] == t and f["Subsystem"] == s and f["Severity"] == "Critical")}
                for t, s, hs in [(r["Tail_Number"], r["Subsystem_Name"], r["Health_Score"]) for r in subs]]
    # telemetry: same readings moved to the demo date; plus a hydraulic series for the tail with the hydraulic leak
    t0 = min(x["Timestamp"] for x in tele)
    base = datetime.combine(as_of, datetime.min.time()) + timedelta(hours=8)
    tele_rows = [{"Timestamp": base + (x["Timestamp"] - t0), "Tail_Number": x["Tail_Number"], "Sensor_ID": x["Sensor_ID"],
                  "Value": x["Value"], "Threshold_High": x["Threshold_High"], "Threshold_Low": x["Threshold_Low"]} for x in tele]
    log(fn, "Telemetry", "Timestamp", d(t0), base.date(), "Moved from 2024 to the demo date; values unchanged")
    leak = next((f["Tail_Number"] for f in fault_rows if "hydraulic leak" in f["Description"].lower()), None)
    if leak:
        for i in range(24):
            v = round(3010 - (i ** 1.6) * 6 + random.uniform(-25, 25), 1)
            tele_rows.append({"Timestamp": base + timedelta(minutes=5 * i), "Tail_Number": leak, "Sensor_ID": "Hyd_Press_System_A",
                              "Value": v, "Threshold_High": 3200, "Threshold_Low": 2800})
        log(fn, "Telemetry", f"{leak} Hyd_Press_System_A", None, "24 readings falling below 2,800 psi",
            "Shows the open 'Hydraulic Leak' fault in telemetry (alert demo)", kind="synthetic")
    return {"Fleet Status": fleet_rows, "Subsystem Health": sub_rows, "Telemetry": sorted(tele_rows, key=lambda x: (x["Tail_Number"], x["Sensor_ID"], x["Timestamp"])),
            "Fault Registry": sorted(fault_rows, key=lambda x: x["Date_Logged"], reverse=True)}


# ---------------------------------------------------------------- 04 ALC summary

def fy_quarter(dt: date) -> str:
    fy = dt.year + 1 if dt.month >= 10 else dt.year
    q = {10: 1, 11: 1, 12: 1, 1: 2, 2: 2, 3: 2, 4: 3, 5: 3, 6: 3, 7: 4, 8: 4, 9: 4}[dt.month]
    return f"FY{str(fy)[2:]} Q{q}"


def alc_summary(fixer: dict, as_of: date, fn: str) -> dict:
    # B-52 from the customer's own schedule; other OC-ALC lines from the "OC-ALC Aircraft Summary" slide (a/o 31 May 2030),
    # with Q4 filled in for the demo date (SYNTHETIC).
    sold = [(t, due, s) for t, ind, due, s in fixer["schedule"] if isinstance(s, date) and date(2029, 10, 1) <= s <= as_of]
    b52 = {}
    for t, due, s in sold:
        q = fy_quarter(s)
        b52.setdefault(q, [0, 0])
        b52[q][0] += 1
        b52[q][1] += 1 if (isinstance(due, date) and s <= due) else 0
    goals = {f"FY30 {q}": g for q, _, g in fixer["goals"]}
    slide = {  # weapon system: [(produced, goal) Q1..Q4], on-time %, hours act/target, yield act/target
        "B-1": ([(3, 3), (4, 4), (3, 4), (4, 5)], 100.0, (180000, 195000), (0.8237, 0.85)),
        "E-3": ([(1, 1), (1, 2), (1, 1), (1, 2)], 66.0, (110000, 130000), (0.8237, 0.85)),
        "E-6": ([(0, 0), (1, 1), (0, 0), (1, 1)], 100.0, (250000, 260000), (0.8237, 0.85)),
        "KC-135": ([(12, 12), (15, 15), (17, 20), (21, 25)], 100.0, (545000, 550000), (0.8237, 0.85)),
        "KC-46": ([(2, 2), (2, 2), (2, 3), (3, 3)], 100.0, (100000, 95000), (0.8237, 0.85)),
    }
    rows = []
    for ws_, (qs, ot, hrs, yld) in slide.items():
        for i, (p, g) in enumerate(qs, 1):
            rows.append({"Organization": "OC-ALC", "Weapon System": ws_, "Period": f"FY30 Q{i}", "Produced": p, "Goal": g,
                         "On Time %": ot, "Hours Actual": round(hrs[0] / 4), "Hours Target": round(hrs[1] / 4),
                         "Yield Actual": yld[0], "Yield Target": yld[1], "As Of": as_of})
    log(fn, "Production Summary", "OC-ALC lines except B-52", "slide 'OC-ALC Aircraft Summary a/o 31 May 2030'",
        "FY30 Q1-Q4 rows", "Q1-Q3 from the slide; Q4 (shown as 'x' on the slide) filled for the demo date", kind="synthetic")
    for i in range(1, 5):
        q = f"FY30 Q{i}"
        p, on = b52.get(q, [0, 0])
        rows.append({"Organization": "OC-ALC", "Weapon System": "B-52", "Period": q, "Produced": p, "Goal": goals.get(q),
                     "On Time %": round(100 * on / p, 1) if p else None, "Hours Actual": 48750, "Hours Target": 51750,
                     "Yield Actual": 0.8237, "Yield Target": 0.85, "As Of": as_of})
    log(fn, "Production Summary", "B-52 rows", "", "from FY30 Schedule (sold dates, due dates) + Quarterly Goals",
        "Produced and on-time computed from the customer's schedule; hours/yield from the slide", kind="derived")
    other = {"OO-ALC": ["F-16", "F-22", "F-35", "A-10", "C-130"], "WR-ALC": ["C-5", "C-17", "C-130", "F-15"]}
    for org, systems in other.items():
        for w in systems:
            g = random.randint(6, 30)
            p = g - random.choice([0, 0, 1, 2])
            rows.append({"Organization": org, "Weapon System": w, "Period": "FY30", "Produced": p, "Goal": g,
                         "On Time %": round(random.uniform(88, 100), 1), "Hours Actual": None, "Hours Target": None,
                         "Yield Actual": None, "Yield Target": None, "As Of": as_of})
    log(fn, "Production Summary", "OO-ALC, WR-ALC rows", "", "FY30 totals",
        "Weapon systems from the AFSC view slide; numbers invented for the demo", kind="synthetic")
    # issues: the coloured cells of the AFSC view slide (organization x weapon system)
    issues = [("76 MXW OKC-ALC", "E-6", "Critical", "Critical backlog - engine parts", 18),
              ("76 MXW OKC-ALC", "KC-46", "Issue", "PDM slip - material", 7),
              ("448 SCMW", "E-3", "Critical", "Critical backlog - components", 14),
              ("448 SCMW", "E-6", "Critical", "Critical backlog - components", 12),
              ("DLA", "B-52", "Critical", "Critical backlog - components (nose cowls, BL55 splice plates)", 16),
              ("DLA", "E-6", "Critical", "Critical supply backlog - components", 10),
              ("DLA", "KC-135", "Issue", "Backlog - minor issues", 4),
              ("Engineering Disposition", "B-1", "Stable", "Minor issues", 0),
              ("Engineering Disposition", "KC-135", "Issue", "Minor issues - 202 dispositions pending", 3),
              ("Engineering Disposition", "KC-46", "Issue", "Minor issues", 2),
              ("Engineering Disposition", "B-52", "Issue", "202 answers pending: RH ASM fitting, BL55 splice plate #2", 5),
              ("MajCom", "B-1", "Stable", "Minor issues", 0),
              ("MajCom", "B-52", "Issue", "Minor issues - FCF scheduling", 2)]
    issue_rows = [{"Issue ID": f"ISS-{i + 1:03d}", "Organization": o, "Weapon System": w, "Status": s, "Title": t,
                   "Impact Days": n, "Owner": {"DLA": "DLA Aviation", "448 SCMW": "448 SCMW/Supply",
                                               "Engineering Disposition": "OC-ALC Engineering", "MajCom": "AFGSC A4"}.get(o, "76 MXW Production"),
                   "Date Logged": as_of - timedelta(days=random.randint(3, 40)), "Notes": ""} for i, (o, w, s, t, n) in enumerate(issues)]
    log(fn, "Issue Register", "all rows", "AFSC view slide (coloured cells)", f"{len(issue_rows)} issues",
        "Status and text from the slide; impact days and dates invented; B-52 rows tied to the tail notes", kind="synthetic")
    return {"Production Summary": rows, "Issue Register": issue_rows}


# ---------------------------------------------------------------- 05-09 supply (B-52 PDM parts)

SUPPLIERS = [  # name, tier, supplies_to, country, commodity, sole source, annual spend, lead days, on-time propensity
    ("448 SCMW Propulsion", 1, "", "United States", "TF33 engines (overhauled)", "yes", 14_500_000, 45, 0.55),
    ("DLA Aviation", 1, "", "United States", "Consumables and spares", "no", 3_200_000, 20, 0.80),
    ("Prairie Aerostructures", 1, "", "United States", "Nose cowls and fairings", "yes", 4_100_000, 60, 0.45),
    ("Red River Precision", 1, "", "United States", "Splice plates and fittings", "no", 2_600_000, 35, 0.60),
    ("Cimarron Composites", 1, "", "United States", "Flap track and panels", "no", 1_900_000, 30, 0.85),
    ("Keystone Hydraulics", 1, "", "United States", "Hydraulic pumps and actuators", "no", 2_200_000, 40, 0.75),
    ("Sooner Avionics Repair", 1, "", "United States", "Radar and nav LRUs", "yes", 1_700_000, 50, 0.70),
    ("Great Plains Harness", 1, "", "Mexico", "Wiring harnesses", "no", 900_000, 25, 0.90),
    ("Vistula Alloys", 2, "Red River Precision", "Poland", "Aluminium forgings", "yes", 0, 70, None),
    ("Penang Microsystems", 2, "Sooner Avionics Repair", "Malaysia", "Radar receiver modules", "yes", 0, 90, None),
    ("Rhine Sealants", 2, "Cimarron Composites", "Germany", "Sealants and primers", "no", 0, 30, None),
]
ITEMS = [  # part, description, supplier(s), unit cost, on hand, reorder point, daily use, site
    ("B52-ENG-TF33", "TF33-P-103 engine, overhauled", ["448 SCMW Propulsion"], 1_250_000, 3, 8, 0.25, "OC-ALC Engine Pool"),
    ("B52-NC-LH01", "Nose cowl, LH", ["Prairie Aerostructures"], 86_000, 1, 4, 0.12, "Tinker Supply"),
    ("B52-NC-RH01", "Nose cowl, RH", ["Prairie Aerostructures"], 86_000, 2, 4, 0.12, "Tinker Supply"),
    ("B52-SPL-BL55", "BL55 splice plate", ["Red River Precision"], 14_500, 6, 10, 0.5, "Tinker Supply"),
    ("B52-FIT-ASM-R", "ASM fitting, RH", ["Red River Precision", "DLA Aviation"], 9_800, 9, 6, 0.3, "Tinker Supply"),
    ("B52-FIT-ASM-L", "ASM fitting, LH", ["Red River Precision", "DLA Aviation"], 9_800, 7, 6, 0.3, "Tinker Supply"),
    ("B52-STR-CW100", "Center wing stringer", ["Red River Precision"], 22_000, 12, 8, 0.4, "Tinker Supply"),
    ("B52-FLP-TRK", "Flap track assembly", ["Cimarron Composites"], 31_000, 5, 3, 0.15, "Tinker Supply"),
    ("B52-PNL-ACC", "Access panel set", ["Cimarron Composites", "DLA Aviation"], 4_200, 40, 20, 1.2, "Tinker Supply"),
    ("B52-HYD-PMP", "Hydraulic pump, engine driven", ["Keystone Hydraulics"], 48_000, 4, 4, 0.2, "Tinker Supply"),
    ("B52-HYD-ACT-BD", "Bomb door actuator", ["Keystone Hydraulics"], 27_000, 3, 2, 0.1, "Tinker Supply"),
    ("B52-RDR-RCV", "Radar receiver LRU", ["Sooner Avionics Repair"], 155_000, 2, 3, 0.08, "Tinker Supply"),
    ("B52-NAV-INU", "Inertial nav unit", ["Sooner Avionics Repair"], 210_000, 2, 2, 0.05, "Tinker Supply"),
    ("B52-CPC-01", "Cabin pressure controller", ["Sooner Avionics Repair", "DLA Aviation"], 38_000, 5, 3, 0.15, "Tinker Supply"),
    ("B52-HRN-W2", "Wing wiring harness", ["Great Plains Harness"], 6_400, 25, 12, 0.8, "Tinker Supply"),
    ("B52-FST-KIT", "Fastener kit, structural", ["DLA Aviation"], 450, 300, 150, 9.0, "Tinker Supply"),
    ("B52-SEAL-PR", "Sealant and primer kit", ["DLA Aviation"], 320, 180, 120, 6.0, "Tinker Supply"),
    ("B52-OIL-XMTR", "Oil pressure transmitter", ["DLA Aviation"], 2_900, 14, 8, 0.4, "Tinker Supply"),
    ("B52-GEN-CTR", "Generator control unit (Bus 2)", ["Sooner Avionics Repair", "DLA Aviation"], 41_000, 3, 3, 0.1, "Tinker Supply"),
]
DOCK_CUSTOMERS = ["565 AMXS Dock 1", "565 AMXS Dock 2", "565 AMXS Dock 3", "565 AMXS Dock 4", "565 AMXS Dock 5",
                  "565 AMXS Dock 6", "Post Dock / FCF"]


def supply(today: date) -> dict:
    sup_rows = [{"Supplier": n, "Tier": t, "Supplies To": st, "Country": c, "Commodity": com, "Sole Source": ss,
                 "Annual Spend": sp, "Lead Time": lt} for n, t, st, c, com, ss, sp, lt, _ in SUPPLIERS]
    prop = {s[0]: s[8] for s in SUPPLIERS}
    lead = {s[0]: s[7] for s in SUPPLIERS}
    item_rows = [{"Part Number": p, "Description": desc, "Primary Supplier": sups[0], "Unit Cost": cost, "UoM": "EA"}
                 for p, desc, sups, cost, *_ in ITEMS]
    inv_rows = [{"Part Number": p, "Description": desc, "Warehouse": site, "Qty On Hand": oh, "Reorder Point": rop,
                 "Unit Cost": cost, "UoM": "EA"} for p, desc, sups, cost, oh, rop, use, site in ITEMS]
    po_rows, n = [], 4500
    for p, desc, sups, cost, oh, rop, use, site in ITEMS:
        for k in range(random.randint(3, 7)):
            sup = sups[k % len(sups)]
            od = today - timedelta(days=random.randint(5, 330))
            promised = od + timedelta(days=lead[sup])
            late = 0 if random.random() < (prop[sup] or 0.8) else random.choice([3, 6, 10, 15, 25])
            recv = promised + timedelta(days=late)
            n += 1
            qty = max(1, round(use * random.uniform(20, 60))) if cost < 50_000 else random.randint(1, 3)
            po_rows.append({"PO Number": f"FA8100-{n}", "Vendor": sup, "Material": p, "Description": desc, "Order Qty": qty,
                            "Net Price": cost, "Currency": "USD", "PO Date": od, "Delivery Date": promised,
                            "Received On": recv if recv <= today else None, "Plant": site})
    dem_rows = []
    for i in range(1, 181):
        p, desc, sups, cost, oh, rop, use, site = random.choice(ITEMS)
        dem_rows.append({"Work Order": f"WO-{30000 + i}", "Requesting Unit": random.choice(DOCK_CUSTOMERS), "Part Number": p,
                         "Qty": max(1, round(use * random.uniform(3, 9))),
                         "Request Date": today - timedelta(days=random.randint(1, 60))})
    return {"suppliers": sup_rows, "items": item_rows, "inventory": inv_rows, "pos": sorted(po_rows, key=lambda r: r["PO Date"]),
            "demand": sorted(dem_rows, key=lambda r: r["Request Date"])}


# ---------------------------------------------------------------- writers

def write_xlsx(path: Path, sheets: dict[str, list[dict]]):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name[:31])
        if not rows:
            continue
        cols = list(rows[0].keys())
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c) for c in cols])
        for i, c in enumerate(cols, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(12, min(45, len(str(c)) + 4))
            ws.cell(1, i).font = openpyxl.styles.Font(bold=True)
            if isinstance(rows[0].get(c), (date, datetime)):
                for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                    row[0].number_format = "yyyy-mm-dd hh:mm" if isinstance(rows[0][c], datetime) and rows[0][c].hour else "yyyy-mm-dd"
        ws.freeze_panes = "A2"
    wb.save(path)


def write_csv(path: Path, rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (v.isoformat() if isinstance(v, (date, datetime)) else ("" if v is None else v)) for k, v in r.items()})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.iterdir():
        f.unlink()
    today = date.today()
    f1 = clean_fixer(SRC / "LS_B-52_Fixer_Chart_Data_20300909.xlsx", OUT / "01_B-52_Fixer_2030-09-09.xlsx")
    f2 = clean_fixer(SRC / "LS_B-52_Fixer_Chart_Data_20300916.xlsx", OUT / "02_B-52_Fixer_2030-09-16.xlsx")
    as_of = next(iter(f2["tails"].values()))["as_of"]
    fh = fleet_health(sorted(f2["tails"]), as_of, "03_Fleet_Health_2030-09-16.xlsx")
    write_xlsx(OUT / "03_Fleet_Health_2030-09-16.xlsx", fh)
    alc = alc_summary(f2, as_of, "04_OC-ALC_Weapon_System_Summary.xlsx")
    write_xlsx(OUT / "04_OC-ALC_Weapon_System_Summary.xlsx", alc)
    sp = supply(today)
    write_csv(OUT / "05_Supplier_Master.csv", sp["suppliers"])
    write_csv(OUT / "06_Item_Master.csv", sp["items"])
    write_xlsx(OUT / "07_Inventory_Tinker.xlsx", {"Stock": sp["inventory"]})
    write_csv(OUT / "08_Purchase_Orders.csv", sp["pos"])
    write_csv(OUT / "09_Parts_Demand_Work_Orders.csv", sp["demand"])
    log("05-09 supply files", "", "", "", f"dated relative to {today.isoformat()}",
        "Fictional suppliers (no real companies) around the parts named in the tail notes: engines, nose cowls, "
        "BL55 splice plates, ASM fittings. Government sources (448 SCMW, DLA) as named in the AFSC slide.", kind="synthetic")
    bad = [
        {"Tail_Number": "B52-7531", "Date_Logged": as_of.isoformat(), "Fault_Code": "F077", "Severity": "Major",
         "Description": "Fuel leak - aft body tank", "Technician_Assigned": "SSgt Ortiz"},
        {"Tail_Number": "60-7890", "Date_Logged": as_of.isoformat(), "Fault_Code": "H210", "Severity": "Urgent",
         "Description": "Hydraulic line chafing", "Technician_Assigned": "TSgt Reyes"},
        {"Tail_Number": "", "Date_Logged": as_of.isoformat(), "Fault_Code": "E311", "Severity": "Critical",
         "Description": "Engine #2 EGT exceedance", "Technician_Assigned": "MSgt Kim"},
        {"Tail_Number": "61-2847", "Date_Logged": "2030-13-02", "Fault_Code": "A055", "Severity": "Major",
         "Description": "Antenna damaged", "Technician_Assigned": "SrA Patel"},
    ]
    write_csv(OUT / "90_Governance_Demo_Bad_Fault_Entries.csv", bad)
    log("90_Governance_Demo_Bad_Fault_Entries.csv", "", "", "", "4 rows",
        "Deliberate errors to show quarantine: bad tail format, invalid severity, missing tail, impossible date", kind="synthetic")
    write_csv(OUT / "Cleaning_Log.csv", LOG)
    print(f"wrote {len(list(OUT.iterdir()))} files to {OUT}; {len(LOG)} log entries")


if __name__ == "__main__":
    main()
