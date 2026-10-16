"""
SUMMARY — offers/results CSV se per-brand summary banata hai.

Agar input mein 'type' column ho (unified offers.csv):
  brand, couponse, deals, sale, shipping, newsletter, max_sale_off, shipping_amount
Warna (purani results.csv):
  brand, total_codes, sites, methods, codes

Usage:
  python summary.py offers.csv offers_summary.csv
  python summary.py                       # results.csv -> summary.csv
"""
import csv
import os
import sys

OFFER_FIELDS = ["brand", "coupons", "deals", "sale", "shipping",
                "newsletter", "max_sale_off", "shipping_amount"]
CODE_FIELDS = ["brand", "total_codes", "sites", "methods", "codes"]


def _num(v):
    try:
        return int(float(str(v).replace("%", "").strip()))
    except Exception:
        return None


def _summarize_offers(rows, out_path):
    by = {}
    for r in rows:
        brand = (r.get("brand") or "").strip()
        if not brand:
            continue
        b = by.setdefault(brand, {"coupons": 0, "deals": 0, "sale": 0,
                                  "shipping": 0, "newsletter": 0,
                                  "max_sale_off": 0, "shipping_amount": ""})
        t = (r.get("type") or "").strip().lower()
        key = {"coupon": "coupons", "deal": "deals"}.get(t, t)
        if key in b:
            b[key] += 1
        if t == "sale":
            pct = _num(r.get("percent_off"))
            if pct and pct > b["max_sale_off"]:
                b["max_sale_off"] = pct
        if t == "shipping" and r.get("amount"):
            b["shipping_amount"] = r["amount"]
    out = []
    for brand, b in by.items():
        out.append({"brand": brand, **b})
    out.sort(key=lambda x: (-(x["coupons"] + x["deals"] + x["sale"]), x["brand"]))
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OFFER_FIELDS)
        w.writeheader()
        w.writerows(out)
    return out


def _summarize_codes(rows, out_path):
    by_brand = {}
    for row in rows:
        brand = (row.get("brand") or "").strip()
        code = (row.get("code") or "").strip()
        if not brand or not code:
            continue
        b = by_brand.setdefault(brand, {"codes": [], "seen": set(),
                                        "sites": set(), "methods": set()})
        if code in b["seen"]:
            continue
        b["seen"].add(code)
        b["codes"].append(code)
        url = (row.get("source_url") or "").strip()
        if "://" in url:
            b["sites"].add(url.split("/")[2])
        if row.get("method"):
            b["methods"].add(row["method"])
    out = []
    for brand, b in by_brand.items():
        out.append({"brand": brand, "total_codes": len(b["codes"]),
                    "sites": ", ".join(sorted(b["sites"])),
                    "methods": ", ".join(sorted(b["methods"])),
                    "codes": ", ".join(b["codes"])})
    out.sort(key=lambda r: (-r["total_codes"], r["brand"]))
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CODE_FIELDS)
        w.writeheader()
        w.writerows(out)
    return out


def build_summary(in_path="results.csv", out_path="summary.csv"):
    if not os.path.exists(in_path):
        return []
    with open(in_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    if "type" in fields:
        return _summarize_offers(rows, out_path)
    return _summarize_codes(rows, out_path)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ip = args[0] if len(args) > 0 else "results.csv"
    op = args[1] if len(args) > 1 else "summary.csv"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = build_summary(ip, op)
    print(f"✅ {len(rows)} brands → {op}")
