"""
SUMMARY — flat results.csv se per-brand summary banata hai.

Input (results.csv):  brand, code, method, via, source_url
Output (summary.csv): brand, total_codes, sites, methods, codes

Usage:
  python summary.py results.csv summary.csv
  python summary.py                # default: results.csv -> summary.csv
"""
import csv
import os
import sys

SUMMARY_FIELDS = ["brand", "total_codes", "sites", "methods", "codes"]


def build_summary(results_path="results.csv", out_path="summary.csv"):
    """{brand: {...}} banao aur CSV likho. Return: summary rows list."""
    if not os.path.exists(results_path):
        return []

    by_brand = {}
    with open(results_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
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

    rows = []
    for brand, b in by_brand.items():
        rows.append({
            "brand": brand,
            "total_codes": len(b["codes"]),
            "sites": ", ".join(sorted(b["sites"])),
            "methods": ", ".join(sorted(b["methods"])),
            "codes": ", ".join(b["codes"]),
        })
    rows.sort(key=lambda r: (-r["total_codes"], r["brand"]))

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rp = args[0] if len(args) > 0 else "results.csv"
    op = args[1] if len(args) > 1 else "summary.csv"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = build_summary(rp, op)
    total = sum(r["total_codes"] for r in rows)
    print(f"✅ {len(rows)} brands, {total} codes -> {op}")
