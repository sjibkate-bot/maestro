# Car manufacturing demo pack: "Auremont Automotive"

**Auremont Automotive** is a fictional premium carmaker, styled on a German premium OEM. Every company, supplier, part and number here is invented. The cities, states and countries are real, so the geopolitical and hazard analysis has something real to work on.

Rebuild the files at any time. Dates are relative to the build day, so rebuild the day before the demo:

```bash
python samples/auto_pack/build_auto_pack.py
```

The files are written to `samples/auto_pack/out/`.

## The company
| Plant | Location | Builds | Buys in |
|---|---|---|---|
| Plant Halle | Halle (Saale), Saxony-Anhalt, Germany | AX3-EV, AX5-PHEV | EUR |
| Plant Greenville | Greenville, South Carolina, USA | AX3-EV, AX5-PHEV | USD |
| Plant San Luis Potosí | San Luis Potosí, Mexico | AX5-PHEV | USD |

**25 suppliers in 17 countries**: 19 tier-1, plus 6 sub-tier, down to raw materials. The risks built in:

| Risk | Supplier (city, state, country) | What the demo shows |
|---|---|---|
| Taiwan chip single source | Hsinchu Microcontrollers (Hsinchu, TW) ← Kaohsiung Wafer Foundry | 5 days of MCU cover, live-fire alert, register R-01, Taiwan Strait scenario |
| Battery modules from Korea via Suez | Hanseong Battery Cells (Cheongju, Chungcheongbuk-do, KR) ← lithium (Antofagasta, CL), cobalt (Lubumbashi, Haut-Katanga, CD), graphite and copper foil (CN) | Sub-tier map, cobalt compliance, Red Sea and Cheongju-fire scenarios |
| Rare-earth magnets from China | Jiangxi Rare Earth Magnetics (Ganzhou, Jiangxi, CN) | Export-licence alert, China export-control scenario |
| Radar in an earthquake zone | Kyushu Sensor Works (Kumamoto, JP) | Hazard index 72, earthquake alert, Kyushu scenario |
| Mexico harness and castings | Bajío Wire Harness (León, Guanajuato, MX), Monterrey Castings (Monterrey, Nuevo León, MX) | 6 days of harness cover, strike-vote alert, US tariff scenario (cost impact) |
| Russian sub-tier | Volga Alloys (Samara, Samara Oblast, RU) → Silesia Brake Systems (Katowice, PL) | Sanctions-exposure alert, rated Critical |
| Red Sea forgings | Chennai Forgings (Chennai, Tamil Nadu, IN) | 69% on time, Suez lane |
| Low-risk European base | Stuttgart, Dresden, Duisburg, Jena, Bergamo, Valladolid, Győr | Shows what "Low" looks like |

## Files
| # | File | Loads as | Notes |
|---|---|---|---|
| 01 | `01_Supplier_Master.csv` | supplier | City, state/province, country, tier, sole source, spend, buffer and recovery days. **Two Mapping Studio decisions** (below). |
| 02 | `02_Item_Master.csv` | product | 19 parts |
| 03 | `03_Supply_Network_Links.csv` | supply link | Supplier → plant or sub-tier → tier-1, with share of supply and shipping lane |
| 04 | `04_Plant_Inventory.xlsx` | inventory | On hand, allocated and reorder point by plant |
| 05 | `05_Purchase_Orders.csv` | purchase order | 9 months received (on-time history) + 18 weeks of open orders; EUR at Halle, USD in the Americas |
| 06 | `06_Demand_Forecast.csv` | forecast | 8 weeks by part and plant |
| 07 | `07_Country_Risk.csv` | country risk | Political and hazard index for the 17 countries |
| 08 | `08_Transport_Lanes.csv` | transport lane | Taiwan Strait, Red Sea / Suez, Malacca, US–Mexico… |
| 09 | `09_Risk_Register.csv` | risk register | 9 risks |
| 10 | `10_Supply_Alerts.csv` | alert | 8 open alerts |
| 11 | `11_Disruption_Scenarios.csv` | scenario | 6 what-if scenarios |
| 12 | `12_BOM_AX3-EV.csv` | BOM line | Vehicle → system → part |
| 20–22 | Body-shop work orders (SAP headers), plant machines, quality inspections | manufacturing pack | **Optional**: shows "any industry" |
| 30 | `30_Dealer_Order_Bank.csv` | (unknown) | **Optional**: register a new entity from the file |

## Demo script (about 20 minutes)

**Before you start:** run *Configuration → Loaded data → Delete all data*, then *Configuration → Risk feeds → Refresh now* (needs internet; optional).

1. **Load (Configuration → Load files).** Enter your name, select files **01 to 12** together, keep *Review mappings…* ticked, and click **Load**. One batch is created: 11 tables are **Ready** and the supplier master needs **2 decisions**.
2. **Mapping Studio (supplier master)**:
   * **Site City** is suggested as *location*. **Accept** it.
   * **State / Province** is suggested as *status*, which is wrong. Click **Extension** and name it `state_province`, so the state is kept on every supplier record without changing the common model.
   * Point out the profile column (types, % empty, samples) and the decision log. Optionally click **Save & activate profile**: the next supplier file with these columns maps by itself.
3. **Publish.** Go back to the batch and click **Validate, govern and publish**. All five stages run, with about 1,130 records at grade A/B. Certification is left to the data owner (*Governance → Certify*).
4. **Supply → Overview:**
   * Risk index "Guarded"; 2 Critical and 8 High suppliers.
   * Volga Alloys (Russian sub-tier) and Katanga Cobalt lead the list.
   * Seats (3 days, just-in-time), MCUs (5) and harnesses (6) are about to run out.
   * Spend by country.
5. **Supply network.** Click **Hanseong Battery Cells**: lithium from Chile, cobalt from the Democratic Republic of the Congo, and graphite and foil from China feed the Korean battery plant, which feeds Halle and Greenville. Point out the single points of failure.
6. **Supplier risk → Hsinchu Microcontrollers.** The rating is likelihood × impact, built from:
   * on-time 67%;
   * a 72-day lead time;
   * Taiwan's risk index;
   * the live-fire alert and register entry R-01;
   * 5 days of cover.

   Then show the recommended actions and click **Plan** to create a mitigation.
7. **What-if:**
   * **Taiwan Strait blockade:** MCUs go short within the 45-day horizon. The screen lists which suppliers it hits and why.
   * **Red Sea / Suez closure:** battery modules go short, plus a 15% cost uplift on the affected spend.
   * **US tariff on Mexican-origin parts:** no shortage, but about 67.5 M of extra cost.
   * **No scenario, PO delay 14 days:** 11 parts go short, with value at risk shown separately in **EUR and USD** (never summed across currencies).
   * Record a decision (**Accept / Modify / Override / Defer**). Modify and override need a rationale.
8. **Governance:** the Supplier Performance and Inventory Position products, their trust scores, lineage back to the CSV row, and **Certify**.

### Optional: "any industry" (5 minutes)
9. Load files **20, 21, 22 and 30** together. All four are held as **not recognised**: the system won't force body-shop work orders into purchase orders.
10. Enable the manufacturing pack. There's no button for it yet, so open **`/docs` → POST `/api/packs/{pid}/enable`**, use `pid = manufacturing` and body `{"user": "SJ"}`.
11. Load 20, 21 and 22 again. They're now recognised as work orders, machines and quality inspections, and SAP column names such as `AUFNR` and `GAMNG` map automatically.
12. On `30_Dealer_Order_Bank.csv`, use `GET /api/objects/{id}/propose-entity`, then `POST /api/objects/{id}/register-entity`. This is described in `docs/FRONTEND_API.md`; the UI buttons for it are with the front-end team. A new **dealer_order_bank** entity is created from the file, then publish.

## Things to say
* Everything shown is computed from the loaded files. Change a supplier's country, cover or on-time history, reload, and the rating changes with an explanation.
* Value at risk is the **part value** of the shortage. Lost vehicles are not modelled.
* Tier-2 and tier-3 suppliers have no purchase orders from Auremont. Their risk reaches the plants through the supply-network links.
