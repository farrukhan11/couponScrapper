import csv, sys, re
from collections import Counter
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

rows = list(csv.DictReader(open('results_us.csv', encoding='utf-8')))
print('total codes:', len(rows))
print('by method:', dict(Counter(r['method'] for r in rows)))
print('by via:', dict(Counter(r['via'] for r in rows)))
print()
# junk heuristics
junk_pat = re.compile(r'^(?=.*\d)[A-Z0-9]{8,}$')
long_alpha = [r for r in rows if re.match(r'^[A-Z]{7,}\d{3,}$', r['code'])]
measure = [r for r in rows if re.search(r'\d+-(DAY|STAR|SECOND|PX|K)$', r['code'])]
print('username-pattern junk:', len(long_alpha), '| measure junk:', len(measure))
print()
print('--- sample text-mined (200) ---')
tm = [r['code'] for r in rows if r['method'] == 'text']
print(tm[:60])
print()
for b in ['Shop LC|shoplc.com', 'Mileseey|mileseeygolf.com']:
    br = [r for r in rows if r['brand'] == b]
    print('===', b, len(br), 'codes | methods:', dict(Counter(r['method'] for r in br)))
    print('   ', sorted(set(r['code'] for r in br))[:40])
