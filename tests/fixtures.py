"""A tiny synthetic FS reference + QuickBooks list for tests (no customer data)."""
from app.reference import Reference


def make_ref() -> Reference:
    def item(no, desc, method="", size="", pack="", total=None, status="Ready", rm="", codes=()):
        return {"row": 0, "tpc_no": "", "item_no": no, "description": desc, "item_type": "Reg", "status": status,
                "cut_method": method, "cut_size": size, "other": "", "pack": pack, "total_lb": total,
                "rm1": rm, "rm1_code": "", "codes": [["Sysco imperial", c] for c in codes]}
    return Reference({"source_file": "test", "count": 6, "items": [
        item("TPC1", 'Onion Yellow Diced 3/8" 4/5lb', "Diced", '3/8"', "4/5lb", 20, rm="Onion Yellow", codes=["0010967"]),
        item("TPC2", 'Onion Yellow Diced 3/8" 1/5lb', "Diced", '3/8"', "1/5lb", 5, rm="Onion Yellow", codes=["6524029"]),
        item("TPC3", 'Carrot Diced 1/2" 1/5lb', "Diced", '1/2"', "1/5lb", 5, rm="Carrots"),
        item("TPC4", 'Carrot Diced 1/2" 4/5lb', "Diced", '1/2"', "4/5lb", 20, rm="Carrots"),
        item("TPC5", 'Celery Diced 1/4" 1/5lb', "Diced", '1/4"', "1/5lb", 5, rm="Celery", codes=["6523450"]),
        item("TPC6", 'Tomato Wedged 8 Cut 1/5lb', "Wedged", "8 Cut", "1/5lb", 5, rm="Tomato"),
    ]})


QB = [
    {"Item": "Sales IM:0010967", "Description": 'Onion Yellow Diced 3/8" 4/5lb', "Customer Item No": "0010967",
     "Case Weight": "20", "GTIN": "10074865916063", "Raw Material": "Onion Yellow"},
    {"Item": "Sales IM:6864110", "Description": 'Carrot Diced 1/2" 1/5lb', "Customer Item No": "6864110",
     "Case Weight": "5", "GTIN": "00074865931564", "Raw Material": "Carrot"},
    {"Item": "Sales IM:6523450", "Description": 'Celery Diced 1/4" 1/5lb', "Customer Item No": "6523450",
     "Case Weight": "5", "GTIN": "10074865935248", "Raw Material": "Celery"},
    {"Item": "Sales IM:6715163", "Description": "Tomato Wedged 8 Cut 1/5lb", "Customer Item No": "6715163",
     "Case Weight": "5", "GTIN": "10074865951507", "Raw Material": "Tomato"},
]
