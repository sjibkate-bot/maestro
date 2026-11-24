"""Build the retail / grocery demo pack: a fictional North American importer and distributor of
South Asian groceries ("Saffron Trail Foods", with a home brand of the same name). It is styled on
that kind of business but contains no real company data. It runs five distribution centres in the
US and Canada and buys mainly from India, Sri Lanka, Vietnam and Mexico.

    python samples/retail_pack/build_retail_pack.py

Dates are relative to the day you build, so rebuild the day before a demo.
Everything here is synthetic. Supplier, brand and store names are invented; cities, states and
countries are real.
"""
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pack_builder import TODAY, build_core, write_csv  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"

SITES = {  # code: (name, city, state, country, currency)
    "DC-NJ": ("DC Edison", "Edison", "New Jersey", "US", "USD"),
    "DC-IL": ("DC Elk Grove Village", "Elk Grove Village", "Illinois", "US", "USD"),
    "DC-TX": ("DC Houston", "Houston", "Texas", "US", "USD"),
    "DC-CA": ("DC Fremont", "Fremont", "California", "US", "USD"),
    "DC-ON": ("DC Mississauga", "Mississauga", "Ontario", "CA", "CAD"),
}

# id, name, city, state/province, country, tier, supplies_to, commodity, sole, spend (USD m), lead days,
# buffer days, recovery days, critical, on-time rate, lane to the DCs
SUPPLIERS = [
    ("GCT", "Guntur Chilli Traders", "Guntur", "Andhra Pradesh", "IN", 1, None, "Red chilli powder", "yes", 9, 55, 18, 120, "yes", .72, "suez-red-sea"),
    ("KSE", "Kerala Spice Exporters", "Kochi", "Kerala", "IN", 1, None, "Black pepper & cardamom", "no", 14, 55, 20, 90, "yes", .86, "suez-red-sea"),
    ("KBM", "Karnal Basmati Mills", "Karnal", "Haryana", "IN", 1, None, "Basmati rice", "yes", 38, 50, 25, 90, "yes", .88, "suez-red-sea"),
    ("IPD", "Indore Pulses & Dals", "Indore", "Madhya Pradesh", "IN", 1, None, "Toor & chana dal", "no", 22, 52, 20, 60, "yes", .83, "suez-red-sea"),
    ("RSF", "Rajkot Snack Foods", "Rajkot", "Gujarat", "IN", 1, None, "Khakhra & namkeen snacks", "no", 12, 48, 15, 45, "no", .90, "suez-red-sea"),
    ("ADG", "Anand Dairy Ghee", "Anand", "Gujarat", "IN", 1, None, "Cow ghee", "yes", 18, 50, 20, 90, "yes", .92, "suez-red-sea"),
    ("CCP", "Ceylon Coconut Products", "Kurunegala", "North Western Province", "LK", 1, None, "Coconut milk", "yes", 11, 45, 15, 75, "no", .84, "suez-red-sea"),
    ("KTE", "Kandy Tea Estates", "Kandy", "Central Province", "LK", 1, None, "Ceylon black tea", "no", 7, 45, 30, 60, "no", .93, "suez-red-sea"),
    ("GCG", "Galle Cinnamon Growers", "Galle", "Southern Province", "LK", 1, None, "Ceylon cinnamon", "yes", 4, 45, 30, 120, "no", .89, "suez-red-sea"),
    ("DLP", "Dak Lak Pepper & Cashew", "Buon Ma Thuot", "Dak Lak", "VN", 1, None, "Cashew kernels", "no", 9, 40, 20, 60, "no", .91, "pacific"),
    ("MRE", "Can Tho Rice Export", "Can Tho", "Can Tho", "VN", 1, None, "Jasmine rice", "no", 16, 42, 20, 45, "no", .90, "panama"),
    ("CMS", "Ca Mau Seafood", "Ca Mau", "Ca Mau", "VN", 1, None, "Frozen seafood (shrimp)", "no", 10, 38, 10, 45, "no", .87, "pacific"),
    ("SMP", "Sinaloa Mango Pulp", "Culiacán", "Sinaloa", "MX", 1, None, "Mango pulp", "no", 8, 12, 10, 30, "no", .92, "us-mx"),
    ("CFV", "Celaya Frozen Vegetables", "Celaya", "Guanajuato", "MX", 1, None, "Frozen okra", "yes", 13, 10, 5, 45, "yes", .76, "us-mx"),
    ("VLF", "Veracruz Lime Farms", "Martínez de la Torre", "Veracruz", "MX", 1, None, "Fresh limes", "no", 5, 6, 4, 21, "no", .85, "us-mx"),
    ("KHF", "Karachi Halal Foods", "Karachi", "Sindh", "PK", 1, None, "Biryani & spice mixes", "no", 6, 55, 20, 60, "no", .80, "suez-red-sea"),
    ("GSC", "Garden State Co-Packers", "Secaucus", "New Jersey", "US", 1, None, "Cumin repacking (home brand)", "yes", 15, 7, 10, 30, "yes", .96, "domestic"),
    ("TXF", "Panhandle Flour Milling", "Amarillo", "Texas", "US", 1, None, "Chapati atta flour", "no", 20, 7, 14, 21, "no", .97, "domestic"),
    # sub-tier: no purchase orders from the distributor; reached through the supply-network links
    ("UJS", "Unjha Seed Traders", "Unjha", "Gujarat", "IN", 2, "Garden State Co-Packers", "Cumin seed", "yes", 0, 60, 20, 120, "yes", None, "suez-red-sea"),
    ("KCF", "Kota Coriander Farmers", "Kota", "Rajasthan", "IN", 2, "Garden State Co-Packers", "Coriander seed", "no", 0, 45, 30, 40, "no", None, "suez-red-sea"),
    ("HPG", "Kaithal Paddy Growers", "Kaithal", "Haryana", "IN", 2, "Karnal Basmati Mills", "Basmati paddy", "no", 0, 30, 30, 180, "yes", None, "domestic"),
    ("PFP", "Pune Flexible Packaging", "Pune", "Maharashtra", "IN", 2, "Rajkot Snack Foods", "Laminated snack pouches", "yes", 0, 20, 10, 45, "no", None, "domestic"),
    ("CTC", "Colombo Tin Can", "Colombo", "Western Province", "LK", 2, "Ceylon Coconut Products", "Tinplate cans", "yes", 0, 30, 15, 60, "no", None, "domestic"),
    ("AGP", "An Giang Paddy Co-op", "Long Xuyen", "An Giang", "VN", 2, "Can Tho Rice Export", "Jasmine paddy", "no", 0, 20, 20, 90, "no", None, "domestic"),
    ("KMC", "Kheda Milk Cooperative", "Nadiad", "Gujarat", "IN", 2, "Anand Dairy Ghee", "Cream & butterfat", "no", 0, 5, 7, 30, "no", None, "domestic"),
]

# item, description, supplier id, unit price (per case/bag), uom, lead days, daily demand by DC, days of cover, open-order days
PARTS = [
    ("SPC-CHL-400", "Chilli powder 400 g, case of 20", "GCT", 38.0, "CS", 55, {"DC-NJ": 40, "DC-IL": 25, "DC-TX": 20, "DC-CA": 18, "DC-ON": 15}, 12, 25),
    ("SPC-PEP-200", "Black pepper whole 200 g, case of 24", "KSE", 52.0, "CS", 55, {"DC-NJ": 18, "DC-IL": 10, "DC-CA": 12}, 25, 22),
    ("SPC-CRD-100", "Green cardamom 100 g, case of 24", "KSE", 96.0, "CS", 55, {"DC-NJ": 10, "DC-ON": 6}, 30, 20),
    ("SPC-JRA-400", "Cumin seed 400 g (repacked), case of 20", "GSC", 44.0, "CS", 7, {"DC-NJ": 35, "DC-IL": 20, "DC-TX": 15, "DC-CA": 15, "DC-ON": 10}, 6, 25),
    ("RIC-BAS-10", "Basmati rice 10 lb, case of 4", "KBM", 58.0, "CS", 50, {"DC-NJ": 90, "DC-IL": 55, "DC-TX": 50, "DC-CA": 45, "DC-ON": 40}, 16, 25),
    ("RIC-JAS-20", "Jasmine rice 20 lb bag", "MRE", 21.0, "BAG", 42, {"DC-TX": 40, "DC-CA": 50}, 25, 22),
    ("DAL-TOOR-4", "Toor dal 4 lb, case of 10", "IPD", 34.0, "CS", 52, {"DC-NJ": 45, "DC-IL": 30, "DC-TX": 25, "DC-CA": 25, "DC-ON": 20}, 18, 25),
    ("SNK-KHK-200", "Methi khakhra 200 g, case of 30", "RSF", 27.0, "CS", 48, {"DC-NJ": 30, "DC-IL": 15, "DC-CA": 12}, 20, 22),
    ("GHE-COW-32", "Pure cow ghee 32 oz, case of 12", "ADG", 118.0, "CS", 50, {"DC-NJ": 25, "DC-IL": 15, "DC-TX": 12, "DC-CA": 14, "DC-ON": 10}, 14, 25),
    ("CCN-MLK-400", "Coconut milk 400 ml, case of 24", "CCP", 19.0, "CS", 45, {"DC-NJ": 30, "DC-TX": 20, "DC-CA": 25, "DC-ON": 15}, 10, 25),
    ("TEA-BLK-450", "Ceylon black tea 450 g, case of 12", "KTE", 48.0, "CS", 45, {"DC-NJ": 12, "DC-ON": 10}, 35, 15),
    ("SPC-CIN-100", "Ceylon cinnamon sticks 100 g, case of 24", "GCG", 62.0, "CS", 45, {"DC-NJ": 6, "DC-CA": 5}, 40, 15),
    ("NUT-CSH-1K", "Cashews W320 1 kg, case of 10", "DLP", 145.0, "CS", 40, {"DC-NJ": 10, "DC-IL": 8, "DC-CA": 12}, 18, 22),
    ("FRZ-SHR-2", "Frozen shrimp 2 lb, case of 10", "CMS", 88.0, "CS", 38, {"DC-TX": 15, "DC-CA": 18}, 8, 22),
    ("MNG-PLP-850", "Mango pulp 850 g, case of 24", "SMP", 36.0, "CS", 12, {"DC-NJ": 25, "DC-IL": 15, "DC-TX": 20, "DC-ON": 12}, 14, 24),
    ("FRZ-OKR-300", "Frozen cut okra 300 g, case of 24", "CFV", 28.0, "CS", 10, {"DC-NJ": 35, "DC-IL": 22, "DC-TX": 25, "DC-CA": 20}, 5, 26),
    ("FRS-LIM-40", "Fresh limes, 40 lb carton", "VLF", 32.0, "CTN", 6, {"DC-NJ": 20, "DC-IL": 12, "DC-TX": 15}, 3, 28),
    ("MIX-BIR-50", "Biryani masala mix 50 g, case of 48", "KHF", 30.0, "CS", 55, {"DC-NJ": 12, "DC-IL": 8, "DC-TX": 8, "DC-ON": 8}, 20, 20),
    ("FLR-ATA-20", "Chapati atta 20 lb bag", "TXF", 17.0, "BAG", 7, {"DC-NJ": 60, "DC-IL": 40, "DC-TX": 45, "DC-CA": 35}, 10, 25),
]

LANES = [("suez-red-sea", "Red Sea / Suez", 82), ("panama", "Panama Canal", 48), ("pacific", "Trans-Pacific", 30),
         ("us-mx", "US–Mexico border", 40), ("domestic", "Domestic road / rail", 6)]

COUNTRIES = [("IN", "India", 45, 50), ("LK", "Sri Lanka", 62, 40), ("VN", "Vietnam", 38, 55), ("MX", "Mexico", 55, 35),
             ("PK", "Pakistan", 78, 60), ("US", "United States", 22, 30), ("CA", "Canada", 10, 20)]

SCENARIOS = [
    ("S-RED", "Red Sea / Suez closure", "Attacks force sailings from India, Sri Lanka and Pakistan to the US East Coast round the Cape: +14 days and fewer slots.", 60, 0.35, 18, 30, None, "suez-red-sea"),
    ("S-MON", "Failed monsoon", "A weak monsoon and heatwave cut chilli, pulse and cumin crops by about 40%.", 120, 0.4, 30, 15, "IN", None),
    ("S-RICE", "Basmati export curbs", "Government curbs cut basmati shipments by half for four months.", 120, 0.5, 35, 12, "IN", None),
    ("S-PORT", "US East & Gulf Coast port strike", "Dockworkers strike for three weeks; containers on the Red Sea / Suez and Panama Canal routes wait offshore.", 21, 0.8, 10, 10, None, None),
    ("S-MX", "US tariff on Mexican produce", "25% tariff on Mexican-origin food imports.", 0, 0.0, 25, 20, "MX", None),
    ("S-LK", "Sri Lanka fuel and currency crisis", "Fuel shortages and power cuts halve factory output for two months.", 60, 0.5, 15, 8, "LK", None),
    ("S-VN", "Southern Vietnam typhoon", "A typhoon floods the southern provinces; seafood and jasmine processing stops for a month.", 30, 0.9, 5, 12, "VN", None),
]

REGISTER = [
    ("R-01", "GCT", 4, 4, "Single source for home-brand chilli powder", "All chilli powder from one Guntur processor; 12 days of cover.", "Category Manager – Spices", "Open"),
    ("R-02", "GCT", 3, 5, "Pesticide residue and import holds", "Two chilli lots sampled at the port in the last year; one held.", "Food Safety & Compliance", "Open"),
    ("R-03", "KBM", 3, 5, "Basmati export policy", "Export curbs possible after a poor harvest; basmati is the top revenue line.", "Category Manager – Rice", "Open"),
    ("R-04", "CFV", 3, 4, "Frozen okra from one plant", "One frozen-vegetable plant in Celaya; 5 days of cover.", "Category Manager – Frozen", "Mitigating"),
    ("R-05", "CCP", 3, 3, "Sri Lanka power and currency", "Power cuts in 2022 stopped coconut milk output for weeks.", "Purchasing – Sri Lanka", "Open"),
    ("R-06", "UJS", 4, 3, "Cumin crop and price swings", "Cumin prices doubled after a poor Gujarat harvest.", "Category Manager – Spices", "Open"),
    ("R-07", "KHF", 4, 2, "Pakistan: port and banking delays", "Letters of credit and Karachi port delays add 2–3 weeks.", "Purchasing – Pakistan", "Open"),
    ("R-08", "ADG", 2, 4, "Ghee labelling for Canada", "Bilingual labelling change due next quarter.", "Regulatory", "Open"),
]

ALERTS = [
    ("A-401", 20, "Import status feed", "critical", "GCT", "Container of chilli powder held at Port Newark for sampling (pesticide residue).", 3, "escalated"),
    ("A-402", 60, "Carrier EDI", "high", "KBM", "Liner reroutes India–US East Coast sailings via the Cape: +14 days on 6 bookings.", 2, "new"),
    ("A-403", 95, "News / OSINT", "high", "KBM", "Reports that export curbs on rice may follow a poor monsoon.", 2, "new"),
    ("A-404", 180, "Supplier portal", "high", "CFV", "Celaya plant reports a compressor failure; shipments delayed 4 days.", 2, "new"),
    ("A-405", 300, "Weather service", "medium", "CMS", "Tropical storm forecast to cross southern Vietnam this week.", 1, "new"),
    ("A-406", 420, "Port authority", "medium", "CCP", "Colombo port congestion: 3-day berthing delay.", 1, "acknowledged"),
    ("A-407", 45, "Union notice", "high", "KSE", "Dockworkers' union sets a strike deadline for East and Gulf Coast ports; 4 pepper containers due at Newark.", 2, "new"),
    ("A-408", 15, "Price feed", "low", "UJS", "Cumin price up 18% week on week at the Unjha market.", 1, "new"),
]

BOM = [  # a festival gift hamper: the kit the DCs assemble for the holiday season
    ("HAMPER-FEST", "KIT-PANTRY", "Pantry set", 1, 1), ("HAMPER-FEST", "KIT-SPICE", "Spice box", 1, 1),
    ("HAMPER-FEST", "KIT-SNACK", "Snacks & nuts", 1, 1),
    ("KIT-PANTRY", "RIC-BAS-10", "Basmati rice 10 lb", 1, 2), ("KIT-PANTRY", "GHE-COW-32", "Cow ghee 32 oz", 1, 2),
    ("KIT-PANTRY", "DAL-TOOR-4", "Toor dal 4 lb", 1, 2), ("KIT-PANTRY", "MNG-PLP-850", "Mango pulp 850 g", 2, 2),
    ("KIT-SPICE", "SPC-CHL-400", "Chilli powder 400 g", 1, 2), ("KIT-SPICE", "SPC-JRA-400", "Cumin seed 400 g", 1, 2),
    ("KIT-SPICE", "SPC-CRD-100", "Green cardamom 100 g", 1, 2), ("KIT-SPICE", "SPC-CIN-100", "Ceylon cinnamon 100 g", 1, 2),
    ("KIT-SPICE", "TEA-BLK-450", "Ceylon black tea 450 g", 1, 2),
    ("KIT-SNACK", "SNK-KHK-200", "Methi khakhra 200 g", 2, 2), ("KIT-SNACK", "NUT-CSH-1K", "Cashews W320 1 kg", 0.5, 2),
]

PACK = {"SITES": SITES, "SUPPLIERS": SUPPLIERS, "PARTS": PARTS, "LANES": LANES, "COUNTRIES": COUNTRIES,
        "SCENARIOS": SCENARIOS, "REGISTER": REGISTER, "ALERTS": ALERTS, "BOM": BOM, "END_ITEM": "HAMPER-FEST",
        "site_col": "Warehouse", "item_col": "Item Code", "inventory_file": "04_DC_Inventory.xlsx",
        "bom_file": "12_BOM_Festival_Hamper.csv", "po_prefix": "PO7",
        # festival peak (Diwali / holiday season) in forecast weeks 3 to 5
        "peak": {"weeks": [3, 4, 5], "factor": 1.8,
                 "skus": {"SPC-CHL-400", "SPC-JRA-400", "SPC-CRD-100", "RIC-BAS-10", "GHE-COW-32", "SNK-KHK-200", "NUT-CSH-1K", "MNG-PLP-850"}}}

STORES = [  # customer grocery stores the DCs deliver to (invented names)
    ("1001", "Lotus Grocers Edison", "New Jersey", "US", "Supermarket", "2009-04-01", "DC-NJ"),
    ("1002", "Lotus Grocers Jersey City", "New Jersey", "US", "Supermarket", "2014-09-15", "DC-NJ"),
    ("1003", "Chandni Market Queens", "New York", "US", "Neighbourhood", "2011-06-10", "DC-NJ"),
    ("2001", "Tulsi Fresh Devon Ave", "Illinois", "US", "Neighbourhood", "2006-03-20", "DC-IL"),
    ("2002", "Tulsi Fresh Naperville", "Illinois", "US", "Supermarket", "2018-11-02", "DC-IL"),
    ("3001", "Masala Mart Sugar Land", "Texas", "US", "Supermarket", "2012-08-08", "DC-TX"),
    ("3002", "Masala Mart Irving", "Texas", "US", "Supermarket", "2016-02-14", "DC-TX"),
    ("4001", "Spice Corner Fremont", "California", "US", "Supermarket", "2008-05-05", "DC-CA"),
    ("4002", "Spice Corner Sunnyvale", "California", "US", "Neighbourhood", "2019-07-21", "DC-CA"),
    ("5001", "Chandni Market Brampton", "Ontario", "CA", "Supermarket", "2013-10-01", "DC-ON"),
]


def main():
    build_core(OUT, PACK)
    random.seed(7)
    price = {p[0]: p[3] for p in PARTS}
    sold = ["SPC-CHL-400", "RIC-BAS-10", "DAL-TOOR-4", "GHE-COW-32", "FRZ-OKR-300", "MNG-PLP-850", "SNK-KHK-200", "FLR-ATA-20"]
    promos = [("PR-DIWALI-1", "GHE-COW-32", "", 3, 18, 15), ("PR-DIWALI-2", "SNK-KHK-200", "", 3, 18, 20),
              ("PR-RICE-10", "RIC-BAS-10", "1001", -10, 4, 10), ("PR-MANGO", "MNG-PLP-850", "3001", -5, 9, 12)]
    on_promo = lambda sku, store, day: any(p[1] == sku and (not p[2] or p[2] == store) and
                                           TODAY + timedelta(days=p[3]) <= day <= TODAY + timedelta(days=p[4]) for p in promos)

    # ---- optional: the retail industry pack (enable it first) and an unknown file to register
    write_csv(OUT / "20_Customer_Stores.csv", [{"Store No": s[0], "Store Name": s[1], "District": s[2], "Country Code": s[3],
                                                "Format": s[4], "Opened": s[5], "Served From": s[6]} for s in STORES])
    sales = []
    for s in STORES:
        cur = "CAD" if s[3] == "CA" else "USD"
        for d in range(14, 0, -1):
            day = TODAY - timedelta(days=d)
            for sku in sold:
                u = random.randint(2, 12) * (2 if day.weekday() >= 5 else 1) * (1.6 if on_promo(sku, s[0], day) else 1)
                sales.append({"Store No": s[0], "Item": sku, "Business Date": day.isoformat(), "Units Sold": round(u),
                              "Net Sales": round(u * price[sku] / 10 * 1.35, 2), "Curr": cur,
                              "On Promo": "Y" if on_promo(sku, s[0], day) else "N"})
    write_csv(OUT / "21_Store_Sales_POS.csv", sales)
    write_csv(OUT / "22_Promotions.csv", [{"Promotion ID": p[0], "Item": p[1], "Store": p[2], "Start Date": (TODAY + timedelta(days=p[3])).isoformat(),
                                           "End Date": (TODAY + timedelta(days=p[4])).isoformat(), "Discount": p[5]} for p in promos])
    entries = []
    for i, (sid, sku, port, status, reason) in enumerate([
            ("GCT", "SPC-CHL-400", "Newark, NJ", "Held for sampling", "Pesticide residue screening"),
            ("KBM", "RIC-BAS-10", "Newark, NJ", "Released", ""), ("KSE", "SPC-PEP-200", "Newark, NJ", "Released", ""),
            ("CCP", "CCN-MLK-400", "Houston, TX", "Released", ""), ("DLP", "NUT-CSH-1K", "Oakland, CA", "Released", ""),
            ("CMS", "FRZ-SHR-2", "Oakland, CA", "Document review", "Country-of-origin labelling"),
            ("ADG", "GHE-COW-32", "Newark, NJ", "Released", ""), ("KHF", "MIX-BIR-50", "Newark, NJ", "Refused", "Undeclared colour additive"),
            ("SMP", "MNG-PLP-850", "Laredo, TX", "Released", ""), ("GCG", "SPC-CIN-100", "Newark, NJ", "Held for sampling", "Salmonella screening")]):
        entries.append({"Entry Number": f"EN-{TODAY.year}-{4100 + i}", "Port Of Entry": port, "Container": f"MSKU{7300000 + i * 137}",
                        "Item Code": sku, "Shipper": next(x[1] for x in SUPPLIERS if x[0] == sid),
                        "Country Of Origin": next(x[4] for x in SUPPLIERS if x[0] == sid), "Admissibility Status": status, "Hold Reason": reason,
                        "Entry Date": (TODAY - timedelta(days=random.randint(1, 20))).isoformat()})
    write_csv(OUT / "30_Import_Entries.csv", entries)
    print(f"wrote {len(list(OUT.iterdir()))} files to {OUT}")


if __name__ == "__main__":
    main()
