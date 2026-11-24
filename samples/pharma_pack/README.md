# Pharma demo pack: "Veridia Pharmaceuticals"

**Veridia Pharmaceuticals** is a fictional maker of generic tablets, sterile injectables and one biologic. Every company, product, lot and number here is invented. The cities, states and countries are real, so the geopolitical, lane and hazard analysis has something real to work on.

Rebuild the files at any time. Dates are relative to the build day, so rebuild the day before the demo:

```bash
python samples/pharma_pack/build_pharma_pack.py
```

The files are written to `samples/pharma_pack/out/`.

## The company
| Plant | Location | Makes | Buys in |
|---|---|---|---|
| Plant Piscataway | Piscataway, New Jersey, USA | Oral solids: metformin, amoxicillin, packed atorvastatin | USD |
| Plant Juncos | Juncos, Puerto Rico | Sterile injectables: paclitaxel, heparin | USD |
| Plant Ringaskiddy | Ringaskiddy, County Cork, Ireland | Biologic (monoclonal antibody) fill-finish | EUR |

15 materials: active ingredients (APIs), excipients, contract-made tablets, glass vials, stoppers, sterile filters, cell-culture media, drug substance, blister foil, labels and insulated shippers.

**21 suppliers in 11 countries**: 15 tier-1 plus 6 sub-tier, down to starting materials and biomass.

| Risk | Supplier (city, state, country) | What the demo shows |
|---|---|---|
| China starting materials behind Indian APIs | Hyderabad API Labs (Hyderabad, Telangana, IN) ← Taizhou Pharma Intermediates (Taizhou, Zhejiang, CN); Vizag Fine Chemicals (Visakhapatnam, Andhra Pradesh, IN) ← Jining Penicillin Works (Jining, Shandong, CN) | Sub-tier rated Critical; starting-material shortage scenario; plant-shutdown alert |
| Sole-source amoxicillin API with an FDA 483 | Vizag Fine Chemicals | 74% on time, 12 days of cover, FDA 483 alert, supplier audit *Conditional* (7 observations), FDA import-alert scenario |
| Heparin from porcine mucosa | Hubei Heparin Biochem (Wuhan, Hubei, CN) ← Chengdu Porcine Mucosa (Chengdu, Sichuan, CN) | African swine fever alert and scenario; contamination history in the register |
| One qualified vial plant | Mainz Pharma Glass (Mainz, Rhineland-Palatinate, DE) ← Mitterteich Glass Tubing (Bavaria) | Vial-shortage scenario |
| Hurricane island | Caguas Labels & Cartons (Caguas, PR) and Plant Juncos | Hazard index 80, tropical-storm alert, hurricane scenario |
| Biologic from one CDMO, cold-chain media | Incheon Biologics CDMO (Incheon, KR), Tuas Bioprocess Media (Tuas, SG) | Media shipment above 8 °C at Frankfurt; register R-05/R-08 |
| Paclitaxel from yew biomass | Brianza Chemical Synthesis (Monza, Lombardy, IT) ← Yunnan Taxus Biomass (Kunming, Yunnan, CN) | A Low-risk tier-1 whose sub-tier is Critical |
| Contract tablets with data-integrity findings | Baddi Formulations (Baddi, Himachal Pradesh, IN) | 76% on time, audit *Conditional* |
| Low-risk base | Aargau Excipients (CH), Carolina Blister Foil (NC), Galway Stopper & Seal (IE) | Shows what "Low" looks like |

## Files
| # | File | Loads as | Notes |
|---|---|---|---|
| 01 | `01_Supplier_Master.csv` | supplier | **Two Mapping Studio decisions** (below) |
| 02 | `02_Item_Master.csv` | product | 15 materials (column `Material`) |
| 03 | `03_Supply_Network_Links.csv` | supply link | Supplier → plant, or sub-tier → tier-1, with lane |
| 04 | `04_Plant_Inventory.xlsx` | inventory | By plant |
| 05 | `05_Purchase_Orders.csv` | purchase order | 9 months received + 18 weeks open; EUR in Ireland, USD elsewhere |
| 06 | `06_Demand_Forecast.csv` | forecast | 8 weeks by material and plant |
| 07 | `07_Country_Risk.csv` | country risk | 11 countries, including Puerto Rico |
| 08 | `08_Transport_Lanes.csv` | transport lane | India–US air freight, China–India sea freight, Trans-Pacific, Transatlantic, Asia–Europe air, Red Sea / Suez… |
| 09 | `09_Risk_Register.csv` | risk register | 8 risks |
| 10 | `10_Supply_Alerts.csv` | alert | 8 open alerts |
| 11 | `11_Disruption_Scenarios.csv` | scenario | 6 what-if scenarios |
| 12 | `12_BOM_Paclitaxel_Injection.csv` | BOM line | Paclitaxel injection → bulk, fill, pack → materials |
| 20–22 | Batch lots (with expiry and QA status), cold-chain excursions, supplier audits | life sciences pack | **Optional**: shows "any industry" |
| 30 | `30_Serialization_Events.csv` | (unknown) | **Optional**: register a new entity (serial number, GTIN, lot, event) |

## Demo script (about 20 minutes)

**Before you start:** run *Configuration → Loaded data → Delete all data*.

1. **Load** files **01 to 12** together with *Review mappings…* ticked: 11 tables **Ready**, the supplier master needs **2 decisions**.
2. **Mapping Studio:** **Accept** *Site City* as location; make *State / Province* an **Extension** called `state_province`.
3. **Publish:** about 625 records, all grade A/B.
4. **Supply → Overview:** 2 Critical (both Chinese sub-tier) and 8 High suppliers. The risk sits below the tier-1 names most buyers know.
5. **Supply network.** Click **Hyderabad API Labs** and **Vizag Fine Chemicals**: both depend on one Chinese starting-material plant each.
6. **Supplier risk → Vizag Fine Chemicals:** sole source, 74% on time, the FDA 483 alert and register R-01. Click **Plan**: for example, qualify a second amoxicillin API site.
7. **What-if:**
   * **Key starting material shortage:** metformin and amoxicillin go short, about 330 K at risk.
   * **FDA import alert on an API site:** only Vizag is hit (the screen shows "materials named: amoxicillin").
   * **African swine fever outbreak:** heparin goes short.
   * **Glass vial shortage:** Type I vials go short, about 500 K at risk.
   * **Hurricane in Puerto Rico:** labels and cartons go short.
   * **No scenario, PO delay 30 days:** value at risk shown separately in **USD and EUR**.
8. **Governance:** four products with data. The empty depot and fleet products are hidden.

### Optional: "any industry" (5 minutes)
9. Load files **20, 21, 22 and 30** together. All four are held as **not recognised**.
10. In **Admin → Industry & modules**, enable **Life sciences**. Governance renames *Logistics* to *Cold-Chain Logistics & Shipments* and sets GxP owners.
11. Load 20, 21 and 22 again: they're now batch lots (one lot expires in 40 days; lots are Released, Quarantined, On hold and Rejected), temperature excursions and supplier audits.
12. On `30_Serialization_Events.csv`, use `propose-entity` / `register-entity` (see `docs/FRONTEND_API.md`) to create **serialization_events**, then publish.
