"""Build the pharma demo pack: a fictional maker of generic tablets, sterile injectables and one
biologic ("Veridia Pharmaceuticals"). It has no real company data. Plants in New Jersey
(oral solids), Puerto Rico (sterile injectables) and Ireland (biologic fill-finish) buy active
ingredients (APIs) from India, Italy and China, glass and components from Europe, cell-culture
media from Singapore and drug substance from a Korean contract manufacturer.

    python samples/pharma_pack/build_pharma_pack.py

Dates are relative to the day you build, so rebuild the day before a demo.
Everything here is synthetic. Supplier names are invented; cities, states and countries are real.
"""
import random
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pack_builder import TODAY, build_core, write_csv  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"

SITES = {  # code: (name, city, state, country, currency)
    "PLT-NJ": ("Plant Piscataway", "Piscataway", "New Jersey", "US", "USD"),
    "PLT-PR": ("Plant Juncos", "Juncos", "Puerto Rico", "PR", "USD"),
    "PLT-IE": ("Plant Ringaskiddy", "Ringaskiddy", "County Cork", "IE", "EUR"),
}

# id, name, city, state/province, country, tier, supplies_to, commodity, sole, spend (USD m), lead days,
# buffer days, recovery days, critical, on-time rate, lane to the plants
SUPPLIERS = [
    ("HAL", "Hyderabad API Labs", "Hyderabad", "Telangana", "IN", 1, None, "Metformin API", "no", 28, 30, 30, 120, "yes", .85, "india-us-air"),
    ("VFC", "Vizag Fine Chemicals", "Visakhapatnam", "Andhra Pradesh", "IN", 1, None, "Amoxicillin API", "yes", 22, 30, 20, 180, "yes", .74, "india-us-air"),
    ("BFL", "Baddi Formulations", "Baddi", "Himachal Pradesh", "IN", 1, None, "Contract-made atorvastatin tablets", "yes", 40, 35, 25, 120, "yes", .76, "india-us-air"),
    ("GCL", "Bharuch Cellulose", "Bharuch", "Gujarat", "IN", 1, None, "Microcrystalline cellulose", "no", 6, 55, 40, 60, "no", .88, "suez-red-sea"),
    ("BCS", "Brianza Chemical Synthesis", "Monza", "Lombardy", "IT", 1, None, "Paclitaxel API", "yes", 45, 30, 45, 180, "yes", .95, "transatlantic"),
    ("AEX", "Aargau Excipients", "Aarau", "Aargau", "CH", 1, None, "Lactose & magnesium stearate", "no", 9, 21, 40, 45, "no", .97, "transatlantic"),
    ("HHB", "Hubei Heparin Biochem", "Wuhan", "Hubei", "CN", 1, None, "Heparin sodium API", "yes", 34, 45, 20, 180, "yes", .82, "trans-pacific"),
    ("SSF", "Suzhou Sterile Filters", "Suzhou", "Jiangsu", "CN", 1, None, "Sterile filter capsules", "no", 11, 40, 25, 90, "yes", .87, "trans-pacific"),
    ("MPG", "Mainz Pharma Glass", "Mainz", "Rhineland-Palatinate", "DE", 1, None, "Type I glass vials", "yes", 18, 35, 15, 150, "yes", .90, "transatlantic"),
    ("GSS", "Galway Stopper & Seal", "Galway", "Connacht", "IE", 1, None, "Rubber stoppers & seals", "no", 7, 14, 20, 60, "no", .95, "eu-road"),
    ("SBM", "Tuas Bioprocess Media", "Tuas", "West Region", "SG", 1, None, "Cell-culture media", "yes", 26, 28, 30, 120, "yes", .93, "asia-eu-air"),
    ("ICD", "Incheon Biologics CDMO", "Incheon", "Incheon", "KR", 1, None, "Monoclonal antibody drug substance", "yes", 120, 45, 40, 240, "yes", .91, "asia-eu-air"),
    ("CBF", "Carolina Blister Foil", "Greensboro", "North Carolina", "US", 1, None, "Blister lidding foil", "no", 8, 10, 15, 30, "no", .96, "domestic"),
    ("PRC", "Caguas Labels & Cartons", "Caguas", "Puerto Rico", "PR", 1, None, "Labels & cartons", "no", 3, 7, 10, 21, "no", .94, "domestic"),
    ("MXC", "Monterrey Cold Chain Packaging", "Monterrey", "Nuevo León", "MX", 1, None, "Insulated shippers", "no", 5, 10, 15, 30, "no", .89, "us-mx"),
    # sub-tier: no purchase orders from the company; reached through the supply-network links
    ("ZPI", "Taizhou Pharma Intermediates", "Taizhou", "Zhejiang", "CN", 2, "Hyderabad API Labs", "Metformin key starting material", "yes", 0, 40, 25, 120, "yes", None, "china-india-sea"),
    ("SPG", "Jining Penicillin Works", "Jining", "Shandong", "CN", 2, "Vizag Fine Chemicals", "6-APA penicillin intermediate", "yes", 0, 40, 25, 150, "yes", None, "china-india-sea"),
    ("SPM", "Chengdu Porcine Mucosa", "Chengdu", "Sichuan", "CN", 2, "Hubei Heparin Biochem", "Crude heparin", "no", 0, 20, 20, 180, "yes", None, "domestic"),
    ("JSS", "Nantong Specialty Solvents", "Nantong", "Jiangsu", "CN", 2, "Brianza Chemical Synthesis", "Process solvents", "no", 0, 45, 30, 40, "no", None, "suez-red-sea"),
    ("YTB", "Yunnan Taxus Biomass", "Kunming", "Yunnan", "CN", 3, "Brianza Chemical Synthesis", "Yew biomass", "yes", 0, 90, 30, 365, "yes", None, "suez-red-sea"),
    ("MGT", "Mitterteich Glass Tubing", "Mitterteich", "Bavaria", "DE", 2, "Mainz Pharma Glass", "Borosilicate glass tubing", "yes", 0, 14, 20, 120, "yes", None, "eu-road"),
]

# item, description, supplier id, unit price, uom, lead days, daily use by plant, days of cover, open-order days
PARTS = [
    ("API-MET", "Metformin HCl API", "HAL", 9.5, "KG", 30, {"PLT-NJ": 800}, 25, 25),
    ("API-AMX", "Amoxicillin trihydrate API", "VFC", 38.0, "KG", 30, {"PLT-NJ": 220}, 12, 25),
    ("TAB-ATV-20", "Atorvastatin 20 mg tablets (1,000)", "BFL", 14.0, "K", 35, {"PLT-NJ": 350}, 14, 25),
    ("EXC-MCC", "Microcrystalline cellulose", "GCL", 4.1, "KG", 55, {"PLT-NJ": 900}, 20, 22),
    ("API-PAC", "Paclitaxel API", "BCS", 180.0, "G", 30, {"PLT-PR": 60}, 45, 20),
    ("EXC-LAC", "Lactose monohydrate", "AEX", 3.2, "KG", 21, {"PLT-NJ": 600}, 30, 20),
    ("API-HEP", "Heparin sodium API (million units)", "HHB", 95.0, "MU", 45, {"PLT-PR": 30}, 18, 24),
    ("FLT-022", "0.22 µm sterile filter capsule", "SSF", 95.0, "EA", 40, {"PLT-PR": 40, "PLT-IE": 30}, 18, 22),
    ("VIAL-10R", "Type I glass vial 10 ml", "MPG", 0.42, "EA", 35, {"PLT-PR": 40000, "PLT-IE": 25000}, 12, 25),
    ("STOP-20", "20 mm stopper with flip-off seal", "GSS", 0.09, "EA", 14, {"PLT-PR": 40000, "PLT-IE": 25000}, 20, 22),
    ("MED-CHO", "Cell-culture media, 1,000 L batch", "SBM", 8200.0, "EA", 28, {"PLT-IE": 2}, 20, 20),
    ("DS-MAB", "Monoclonal antibody drug substance", "ICD", 14000.0, "G", 45, {"PLT-IE": 50}, 30, 22),
    ("BLS-FOIL", "Blister lidding foil, roll", "CBF", 210.0, "ROLL", 10, {"PLT-NJ": 12}, 20, 20),
    ("LBL-CTN", "Labels & cartons (1,000)", "PRC", 55.0, "K", 7, {"PLT-PR": 45}, 8, 24),
    ("SHP-COLD", "Insulated shipper with gel packs", "MXC", 18.0, "EA", 10, {"PLT-PR": 300, "PLT-NJ": 100}, 12, 24),
]

LANES = [("india-us-air", "India–US air freight", 35), ("china-india-sea", "China–India sea freight", 50),
         ("trans-pacific", "Trans-Pacific", 30), ("transatlantic", "Transatlantic", 15), ("asia-eu-air", "Asia–Europe air freight", 25),
         ("suez-red-sea", "Red Sea / Suez", 82), ("eu-road", "EU road network", 8), ("us-mx", "US–Mexico border", 40),
         ("domestic", "Domestic road / rail", 6)]

COUNTRIES = [("IN", "India", 45, 50), ("CN", "China", 70, 40), ("IT", "Italy", 20, 25), ("CH", "Switzerland", 8, 10),
             ("DE", "Germany", 12, 10), ("IE", "Ireland", 10, 10), ("US", "United States", 22, 30), ("PR", "Puerto Rico", 35, 80),
             ("SG", "Singapore", 10, 10), ("KR", "South Korea", 30, 28), ("MX", "Mexico", 55, 35)]

SCENARIOS = [
    ("S-KSM", "Key starting material shortage", "Lockdowns in China stop key starting materials; makers of metformin and amoxicillin API cut output by 60% for two months.", 60, 0.6, 20, 10, "IN", None),
    ("S-HUR", "Category 4 hurricane in Puerto Rico", "Power and road outages stop Juncos and its island suppliers for five weeks.", 35, 0.8, 10, 8, "PR", None),
    ("S-ASF", "African swine fever outbreak", "Pig herd losses cut crude heparin supply by half for three months.", 90, 0.5, 40, 10, "CN", None),
    ("S-VIAL", "Glass vial shortage", "A vaccine demand surge takes borosilicate tubing and Type I vial capacity for three months.", 90, 0.4, 30, 8, "DE", None),
    ("S-FDA", "FDA import alert on an API site", "Warning letter and import alert stop amoxicillin API for at least two months.", 60, 1.0, 0, 6, "IN", None),
    ("S-AIR", "Air cargo capacity crunch", "Freighter capacity on India–US air freight falls by a third and rates double for six weeks.", 45, 0.2, 8, 15, None, "india-us-air"),
]

REGISTER = [
    ("R-01", "VFC", 4, 5, "Sole source for amoxicillin API", "One API site in Visakhapatnam; FDA inspection due; 12 days of cover.", "Procurement – APIs", "Open"),
    ("R-02", "ZPI", 4, 4, "China dependency for key starting materials", "Both Indian API suppliers rely on Chinese starting materials.", "Procurement – APIs", "Open"),
    ("R-03", "HHB", 3, 5, "Heparin crude supply and adulteration", "Crude heparin from porcine mucosa; contamination history; swine fever exposure.", "Quality Assurance", "Open"),
    ("R-04", "MPG", 3, 5, "One qualified vial supplier", "Type I vials qualified from one plant; second-source validation 9 months away.", "Sterile Operations", "Mitigating"),
    ("R-05", "ICD", 2, 5, "Drug substance from one CDMO", "All drug substance for the biologic from one Incheon facility.", "Biologics Supply", "Open"),
    ("R-06", "BFL", 4, 3, "Contract manufacturer data-integrity findings", "Last audit: 3 data-integrity observations; reinspection pending.", "Quality Assurance", "Open"),
    ("R-07", "PRC", 3, 4, "Hurricane exposure in Puerto Rico", "Labels, cartons and the Juncos plant are all on the island.", "Site Operations – Juncos", "Open"),
    ("R-08", "SBM", 2, 4, "Cold-chain media shipments", "Media ships at 2–8 °C by air through Frankfurt.", "Biologics Supply", "Open"),
]

ALERTS = [
    ("A-501", 30, "FDA data dashboard", "critical", "VFC", "FDA Form 483 issued to the amoxicillin API site after inspection (7 observations).", 3, "escalated"),
    ("A-502", 70, "Supplier portal", "high", "ZPI", "Starting-material supplier reports a shutdown for an environmental inspection in Taizhou.", 2, "new"),
    ("A-503", 110, "Weather service", "high", "PRC", "Tropical storm watch for Puerto Rico; landfall possible in 72 hours.", 2, "new"),
    ("A-504", 200, "Cold-chain monitoring", "high", "SBM", "Media shipment SBM-2291 logged 46 minutes above 8 °C at the Frankfurt hub.", 2, "new"),
    ("A-505", 260, "News / OSINT", "medium", "SPM", "African swine fever cases reported in two Sichuan counties.", 1, "new"),
    ("A-506", 400, "Drug shortage list", "medium", "HAL", "Metformin extended-release listed as in shortage; customers increase orders.", 1, "acknowledged"),
    ("A-507", 20, "Carrier EDI", "medium", "HAL", "Freighter cancelled Hyderabad–Newark; API rebooked on the next flight (+3 days).", 1, "new"),
    ("A-508", 15, "Supplier quality", "low", "GSS", "Stopper lot release delayed one day for retesting.", 1, "new"),
]

BOM = [  # paclitaxel injection, 300 mg / 50 ml vial, made in Juncos
    ("PAC-INJ-300", "BULK-PAC", "Compounded bulk solution", 1, 1), ("PAC-INJ-300", "FILL-PAC", "Filled & sealed vial", 1, 1),
    ("PAC-INJ-300", "PACK-PAC", "Labelled & cartoned pack", 1, 1),
    ("BULK-PAC", "API-PAC", "Paclitaxel API (g)", 0.3, 2), ("BULK-PAC", "FLT-022", "Sterile filter capsule", 0.002, 2),
    ("FILL-PAC", "VIAL-10R", "Type I glass vial", 1, 2), ("FILL-PAC", "STOP-20", "Stopper & seal", 1, 2),
    ("PACK-PAC", "LBL-CTN", "Label & carton (per 1,000)", 0.001, 2), ("PACK-PAC", "SHP-COLD", "Insulated shipper (per 24)", 0.042, 2),
]

PACK = {"SITES": SITES, "SUPPLIERS": SUPPLIERS, "PARTS": PARTS, "LANES": LANES, "COUNTRIES": COUNTRIES,
        "SCENARIOS": SCENARIOS, "REGISTER": REGISTER, "ALERTS": ALERTS, "BOM": BOM, "END_ITEM": "PAC-INJ-300",
        "site_col": "Plant", "item_col": "Material", "inventory_file": "04_Plant_Inventory.xlsx",
        "bom_file": "12_BOM_Paclitaxel_Injection.csv", "po_prefix": "45"}


def main():
    build_core(OUT, PACK)
    random.seed(11)
    # ---- optional: the life sciences industry pack (enable it first) and an unknown file to register
    lots = []
    for i, (sku, plant, status) in enumerate([
            ("PAC-INJ-300", "PLT-PR", "Released"), ("PAC-INJ-300", "PLT-PR", "Released"), ("PAC-INJ-300", "PLT-PR", "Quarantined"),
            ("HEP-INJ-5K", "PLT-PR", "Released"), ("HEP-INJ-5K", "PLT-PR", "On hold"), ("MET-500-B100", "PLT-NJ", "Released"),
            ("MET-500-B100", "PLT-NJ", "Released"), ("AMX-500-C30", "PLT-NJ", "Released"), ("AMX-500-C30", "PLT-NJ", "Rejected"),
            ("ATV-20-B90", "PLT-NJ", "Released"), ("MAB-100-VL", "PLT-IE", "Released"), ("MAB-100-VL", "PLT-IE", "Quarantined")]):
        made = TODAY - timedelta(days=random.randint(20, 400))
        life = 365 * (3 if sku.startswith(("MET", "ATV")) else 2)
        lots.append({"Batch No": f"VP{made:%y}{1000 + i}", "NDC": sku, "Mfg Date": made.isoformat(),
                     "Expiry": (made + timedelta(days=life)).isoformat(), "Qty": random.choice([12000, 24000, 48000, 5000]),
                     "QA Status": status, "Plant": plant})
    lots[6]["Expiry"] = (TODAY + timedelta(days=40)).isoformat()      # one lot close to expiry
    write_csv(OUT / "20_Batch_Lots.csv", lots)
    write_csv(OUT / "21_Cold_Chain_Excursions.csv", [
        {"Alarm ID": f"TX-{9100 + i}", "Batch": lot, "AWB": awb, "Min Temp": lo, "Max Temp": hi, "Minutes Out": mins,
         "Event Time": (TODAY - timedelta(days=d)).isoformat(), "Disposition": disp}
        for i, (lot, awb, lo, hi, mins, d, disp) in enumerate([
            (lots[10]["Batch No"], "180-55512231", 2.1, 11.4, 46, 2, "Open"), (lots[11]["Batch No"], "180-55512298", 1.4, 9.2, 18, 9, "Assessed"),
            (lots[0]["Batch No"], "014-77120045", 3.0, 26.5, 95, 15, "Closed"), ("", "SBM-2291", 2.6, 8.9, 46, 1, "Open")])])
    write_csv(OUT / "22_Supplier_Audits.csv", [
        {"Audit No": f"AUD-{TODAY.year}-{i + 1:03d}", "Auditee": name, "Audit Date": (TODAY - timedelta(days=d)).isoformat(),
         "Outcome": res, "Observations": obs}
        for i, (name, d, res, obs) in enumerate([
            ("Vizag Fine Chemicals", 25, "Conditional", 7), ("Baddi Formulations", 70, "Conditional", 3), ("Hyderabad API Labs", 160, "Approved", 1),
            ("Hubei Heparin Biochem", 210, "Approved", 2), ("Mainz Pharma Glass", 120, "Approved", 0), ("Incheon Biologics CDMO", 300, "Approved", 1),
            ("Brianza Chemical Synthesis", 90, "Approved", 0), ("Taizhou Pharma Intermediates", 400, "Rejected", 9)])])
    write_csv(OUT / "30_Serialization_Events.csv", [
        {"Serial Number": f"{random.randint(10**11, 10**12 - 1)}", "GTIN": gtin, "Lot": lot, "Event Type": ev,
         "Event Timestamp UTC": (datetime.combine(TODAY, time(8)) - timedelta(hours=h)).isoformat(timespec="minutes"), "Trading Partner": tp}
        for gtin, lot, ev, h, tp in [
            ("00312345678906", lots[0]["Batch No"], "Commission", 300, "Veridia Juncos"), ("00312345678906", lots[0]["Batch No"], "Ship", 200, "Northeast Wholesale Drug"),
            ("00312345678906", lots[1]["Batch No"], "Commission", 180, "Veridia Juncos"), ("00312345678906", lots[1]["Batch No"], "Ship", 90, "Gulf Coast Pharma Distributors"),
            ("00312345678920", lots[5]["Batch No"], "Commission", 400, "Veridia Piscataway"), ("00312345678920", lots[5]["Batch No"], "Ship", 250, "Northeast Wholesale Drug"),
            ("00312345678920", lots[5]["Batch No"], "Receive", 230, "Northeast Wholesale Drug"), ("00312345678937", lots[8]["Batch No"], "Decommission", 60, "Veridia Piscataway")]])
    print(f"wrote {len(list(OUT.iterdir()))} files to {OUT}")


if __name__ == "__main__":
    main()
