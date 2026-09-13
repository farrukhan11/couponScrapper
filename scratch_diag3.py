import csv, re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
rows = [r for r in csv.DictReader(open('results_us.csv', encoding='utf-8'))
        if r['brand'].startswith('Shop LC')]
print('Shop LC codes (%d):' % len(rows))
for r in rows:
    print('   %-14s %-12s %s' % (r['code'], r['method'], r['source_url'][:55]))
