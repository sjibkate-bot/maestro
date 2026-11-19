# Maestro-SC customer demo data pack

The files to load are in `out/`. Rebuild them at any time with:

```bash
python samples/demo_pack/build_demo_pack.py
```

Supply dates are set relative to the day you build, so rebuild the day before the demo.

## What is in it

| # | File | Feeds | Source |
|---|---|---|---|
| 01 | `01_B-52_Fixer_2030-09-09.xlsx` | Docks, Gates, Machine (earlier week) | Customer LS Fixer workbook, cleaned |
| 02 | `02_B-52_Fixer_2030-09-16.xlsx` | Docks, Gates, Machine (current week) | Customer LS Fixer workbook, cleaned |
| 03 | `03_Fleet_Health_2030-09-16.xlsx` | Fleet, Alerts | Customer dashboard data, aligned to the same tails and dates |
| 04 | `04_OC-ALC_Weapon_System_Summary.xlsx` | ALC production and issues (governed; AFSC view not built yet) | B-52 from the customer schedule; other lines from the AFSC and ALC slides, partly synthetic |
| 05 | `05_Supplier_Master.csv` | Supply analysis | Synthetic (fictional suppliers) |
| 06 | `06_Item_Master.csv` | Supply analysis | Synthetic, using parts named in the tail notes |
| 07 | `07_Inventory_Tinker.xlsx` | Supply analysis | Synthetic |
| 08 | `08_Purchase_Orders.csv` | Supply analysis | Synthetic |
| 09 | `09_Parts_Demand_Work_Orders.csv` | Supply analysis (demand) | Synthetic |
| 90 | `90_Governance_Demo_Bad_Fault_Entries.csv` | Governance quarantine | Deliberate errors; load during the demo |
| – | `Cleaning_Log.csv` | – | Every change to customer data, with the reason |

**Do not load** `samples/maestro/B-52_Fixer_Chart_Data.xlsx` (the 30 Sep workbook) with this pack. It is a different, older version of the tracker: its start dates and AMREP days differ from the LS series. Because it is dated later, it would replace the cleaned data.

## Load order (Configuration → Load files)

1. Start clean: **Configuration → Loaded data → Delete all data**, or `docker compose down -v` then `docker compose up`.
2. Load files 01 to 09 in order. 01 then 02 shows weekly history, and 02 becomes current.
3. **Configuration → Risk feeds → Refresh now** (needs internet; about 2 minutes, GDELT may be partial).
4. Keep file 90 for the governance part of the demo.

## Demo script (about 20 minutes)

1. **Configuration:** load the files and show where each sheet went ("→ Docks & Gates", "→ Fleet", "→ Supply").
2. **Governance → Overview:** everything is grade A and in Gold. Open **02 Filter & dedup**: the 09-09 and 09-16 tail sheets are versions of the same aircraft, and only the newer one is current.
3. **Maestro → Docks:** 10 aircraft in work as of 16 Sep 2030, one per dock. Open a tail to show its lineage back to the Excel cell.
4. **Gates → Repair:** last-6 average 61.8 against the 61-day requirement. WIP is **3 against a plan of 2.83**: the bottleneck from the Gate 3 analysis slide. Then **Post Dock**: 51.5 against 54.
5. **Fleet → 61-3457:** an open hydraulic-leak fault, and its hydraulic pressure trending below the 2,800 psi limit. **Alerts:** 7 critical.
6. **Supply analysis:** engines (448 SCMW) and nose cowls (Prairie Aerostructures) are High risk, both sole sources. LH nose cowls have about 4 days of cover, matching the tail note "Need 3 LH Nose Cowls". Open the supplier scorecard to show how the rating is built, then the network trace from sub-tier (Vistula Alloys) to dock.
7. **Risk signals:** live World Bank stability, USGS and GDACS data for the supplier countries, and sanctions screening with no supplier names sent out.
8. **Governance, live:** load file 90. Three records go to quarantine (missing tail, severity "Urgent", tail "B52-7531") with a steward and due date. The impossible date "2030-13-02" is graded B, not dropped. Show **Monitor loop** and the audit log.
9. **Ask Maestro** (if AI is approved): "Which aircraft are at risk of missing AMREP and what parts could delay them?"
10. **Configuration → Loaded data → Delete all data:** show the confirmed, audited clean-up.

## What was cleaned (customer files)

See `out/Cleaning_Log.csv` for every cell. In short:

* Removed the "Summary" and "G3 summary" working sheets: they contradict the tail tabs, with other start dates and their own "today".
* Machine Info: kept only the throughput table (notes and copied tables beside it removed).
* FY30 Schedule: fixed tail typos `64-4003` → 60-4003 and `61-3547` → 61-3457. Moved the Quarterly Goals table to file 04. Added headers to the unlabelled columns E and F.
* Tail tabs: fixed the header typo `60-00127`. Fixed AMREP one-year slips against the schedule (61-5812: 2030-04-29 → 2031-04-29). Filled 2 blank arrival dates (start − 1 day, the pattern on every other tab).
* Fleet data: dates moved from 2024/2026 into the demo window, values unchanged. Status set from the MC-rate bands in the customer's own Health Score table (60-1234 and 60-4003: "Degraded" at ≥ 95% → "Up").

## Synthetic additions (say so in the demo)

* **Dock assignments.** The source shows six aircraft in "Dock 5", so one aircraft per dock was assigned.
* **Three faults on 60-1234.** Subsystem Health counted critical faults that the registry did not list, so they were added.
* **A falling hydraulic-pressure series on 61-3457,** to show its leak fault in the telemetry.
* **OC-ALC Q4 numbers** (the slide shows "x"), **OO-ALC and WR-ALC totals, and the issue register's impact days and dates.**
* **All supply data:** fictional suppliers; parts named after the tail notes; government sources (448 SCMW, DLA) as named in the AFSC slide.

## Questions for the customer

1. Which docks are the in-work aircraft really in? The source marks six aircraft "Dock 5".
2. What is the unlabelled tail list (FY30 Schedule column F, and cell A25 on each tail tab)? It matches the tails on the scanned status board, so it looks like an alias.
3. Which tracker version is authoritative: the LS series (09/09, 09/16) or the 30 Sep workbook, whose dates and AMREP days differ?
4. Do the fleet alert counts include squawks that are not in the fault registry? (Kept as given.)
5. What are the real Q4 production numbers, hours and yield for the other OC-ALC weapon systems?
