import csv, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
rows = list(csv.DictReader(open('results_us.csv', encoding='utf-8')))

for brand, probes, wanted in [
    ('Shop LC|shoplc.com', ['HELLO20', 'GOLD20', 'DEAL20', 'SHOP25', 'EXTRA25', 'SHOPSAVE', 'SALE20', 'SAVE25', 'BEST15'], 'couponreals'),
    ('Mileseey|mileseeygolf.com', ['SAMHELINGMIN', 'BREAKINGEIGHTY', 'GSONICBP15X', 'JESSPLAYSGOLF10', 'GOLFLOVER'], 'simplycodes'),
]:
    brand_rows = [r for r in rows if r['brand'] == brand]
    codes = {r['code'] for r in brand_rows}
    print('=' * 60)
    print(brand, '->', len(brand_rows), 'codes')
    for p in probes:
        hit = [r for r in brand_rows if r['code'] == p]
        print(f"  {p:<18} {'✅' if hit else '❌ MISSING'}", hit[0]['source_url'][:60] if hit else '')
    print('-- couponreals/simplycodes se hissa:', sum(1 for r in brand_rows if wanted in r['source_url']))
    print('-- top source domains:')
    doms = {}
    for r in brand_rows:
        d = r['source_url'].split('/')[2] if '://' in r['source_url'] else '?'
        doms[d] = doms.get(d, 0) + 1
    for d, n in sorted(doms.items(), key=lambda x: -x[1])[:6]:
        print(f'   {d:<34} {n}')
