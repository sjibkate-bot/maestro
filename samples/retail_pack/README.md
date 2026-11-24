# Retail / grocery demo pack: "Saffron Trail Foods"

**Saffron Trail Foods** is a fictional importer and distributor of South Asian groceries in North America, with a home brand of the same name. It's styled on that kind of business (an importer that buys from India, Sri Lanka, Vietnam and Mexico and supplies grocery stores from its own distribution centres), but every company, brand, store, item and number is invented. The cities, states and countries are real, so the geopolitical, lane and hazard analysis has something real to work on.

Rebuild the files at any time. Dates are relative to the build day, so rebuild the day before the demo:

```bash
python samples/retail_pack/build_retail_pack.py
```

The files are written to `samples/retail_pack/out/`.

## The company
| Distribution centre | Location | Serves | Buys in |
|---|---|---|---|
| DC Edison | Edison, New Jersey, USA | North-east (largest) | USD |
| DC Elk Grove Village | Elk Grove Village, Illinois, USA | Midwest | USD |
| DC Houston | Houston, Texas, USA | South | USD |
| DC Fremont | Fremont, California, USA | West Coast | USD |
| DC Mississauga | Mississauga, Ontario, Canada | Canada | CAD |

19 items: spices, basmati and jasmine rice, dals, ghee, snacks, coconut milk, tea, cashews, frozen okra and shrimp, mango pulp, limes, spice mixes and atta flour. The forecast has a **festival peak** (Diwali / holiday season) in weeks 3 to 5 for spices, rice, ghee, snacks, cashews and mango pulp.

**25 suppliers in 6 countries**: 18 tier-1 plus 7 sub-tier (farms, paddy, milk, packaging).

| Risk | Supplier (city, state, country) | What the demo shows |
|---|---|---|
| Home-brand chilli from one processor | Guntur Chilli Traders (Guntur, Andhra Pradesh, IN) | Rated Critical: 72% on time, sole source, a container **held at Port Newark for sampling**, register R-01/R-02 |
| Cumin crop and repacking | Unjha Seed Traders (Unjha, Gujarat, IN) → Garden State Co-Packers (Secaucus, NJ) | Sub-tier rated Critical; 4.5 days of cumin cover; price-spike alert |
| Basmati export policy | Karnal Basmati Mills (Karnal, Haryana, IN) ← Kaithal Paddy Growers | Top revenue line; export-curb scenario and alert |
| Red Sea routing | Every India, Sri Lanka and Pakistan supplier | +14 days round the Cape; the scenario doubles the items short |
| Sri Lanka power and currency | Ceylon Coconut Products (Kurunegala), Kandy Tea Estates, Galle Cinnamon Growers ← Colombo Tin Can | Sri Lanka crisis scenario |
| Vietnam typhoon | Can Tho Rice Export, Ca Mau Seafood ← An Giang Paddy Co-op | Frozen shrimp goes short |
| Mexico fresh and frozen | Celaya Frozen Vegetables (Celaya, Guanajuato, MX), Sinaloa Mango Pulp (Culiacán), Veracruz Lime Farms | 3 days of limes and 5 of okra; US tariff scenario (cost only) |
| Pakistan banking and port delays | Karachi Halal Foods (Karachi, Sindh, PK) | Highest country risk (78) |
| Low-risk domestic base | Garden State Co-Packers, Panhandle Flour Milling (Amarillo, TX) | Shows what "Low" looks like |

## Files
| # | File | Loads as | Notes |
|---|---|---|---|
| 01 | `01_Supplier_Master.csv` | supplier | City, state/province, country, tier, sole source, spend, buffer and recovery days. **Two Mapping Studio decisions** (below). |
| 02 | `02_Item_Master.csv` | product | 19 items, by case or bag |
| 03 | `03_Supply_Network_Links.csv` | supply link | Supplier → DC, or sub-tier → tier-1, with share of supply and lane |
| 04 | `04_DC_Inventory.xlsx` | inventory | On hand, allocated and reorder point by DC (column `Warehouse`) |
| 05 | `05_Purchase_Orders.csv` | purchase order | 9 months received (on-time history) + 18 weeks of open orders |
| 06 | `06_Demand_Forecast.csv` | forecast | 8 weeks by item and DC, with the festival peak |
| 07 | `07_Country_Risk.csv` | country risk | India, Sri Lanka, Vietnam, Mexico, Pakistan, US, Canada |
| 08 | `08_Transport_Lanes.csv` | transport lane | Red Sea / Suez, Panama Canal, Trans-Pacific, US–Mexico border, domestic |
| 09 | `09_Risk_Register.csv` | risk register | 8 risks |
| 10 | `10_Supply_Alerts.csv` | alert | 8 open alerts |
| 11 | `11_Disruption_Scenarios.csv` | scenario | 7 what-if scenarios |
| 12 | `12_BOM_Festival_Hamper.csv` | BOM line | Festival gift hamper → pantry, spice and snack kits → items |
| 20–22 | Customer stores, store sales (POS), promotions | retail pack | **Optional**: shows "any industry" |
| 30 | `30_Import_Entries.csv` | (unknown) | **Optional**: register a new entity from the file (customs entries with admissibility status and hold reasons) |

## Demo script (about 20 minutes)

**Before you start:** run *Configuration → Loaded data → Delete all data*. In **Admin → Industry & modules**, leave every module on *Auto*: a grocery distributor sees no aircraft screens.

1. **Load (Configuration → Load files).** Enter your name, select files **01 to 12** together, keep *Review mappings…* ticked, and click **Load**. 11 tables are **Ready**; the supplier master needs **2 decisions**.
2. **Mapping Studio (supplier master).** **Accept** *Site City* as location. For *State / Province* (suggested as status, which is wrong) click **Extension** and name it `state_province`.
3. **Publish.** Back on the batch, click **Validate, govern and publish**: about 1,975 records, all grade A/B, nothing quarantined.
4. **Supply → Overview:**
   * 4 Critical and 12 High suppliers, almost all on the Red Sea route.
   * Items about to run out: limes (3 days), cumin (4.5), okra (5), shrimp (8), chilli (9).
5. **Supply network.** Click **Garden State Co-Packers**: the home-brand cumin is repacked in New Jersey, but the seed comes from one market town in Gujarat.
6. **Supplier risk → Guntur Chilli Traders.** Walk through the rating: 72% on time, 55-day lead time, sole source, the port hold alert and register R-01/R-02. Click **Plan** to add a mitigation (for example, qualify a second chilli processor in Karnataka).
7. **What-if:**
   * **Red Sea / Suez closure:** items short go from 4 to 8, plus an 18% cost uplift on about 140 M of affected spend.
   * **Failed monsoon:** hits only the chilli, pulse and cumin suppliers. The screen says which words matched.
   * **Basmati export curbs:** the top revenue line goes short, about 1.2 M at risk.
   * **US East & Gulf Coast port strike:** nothing short today, 3 items short after three weeks.
   * **US tariff on Mexican produce:** no shortage, about 6.5 M extra cost.
   * Record a decision (**Accept / Modify / Override / Defer**).
8. **Governance:** four products with data (Inventory, Supplier Performance, Work Packages & BOM for the hamper, Risk Register); the empty ones are hidden.

### Optional: "any industry" (5 minutes)
9. Load files **20, 21, 22 and 30** together. All four are held as **not recognised**.
10. In **Admin → Industry & modules**, enable **Retail**. Governance now uses retail names (for example *Store & DC Logistics*).
11. Load 20, 21 and 22 again: they're now customer stores, store sales and promotions.
12. On `30_Import_Entries.csv`, use `GET /api/objects/{id}/propose-entity` and `POST /api/objects/{id}/register-entity` (see `docs/FRONTEND_API.md`). A new **import_entries** entity is created; publish.
