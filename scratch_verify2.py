import re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import page_scraper as ps

c = open('logs/diag_https_couponreals_com_store_shoplc.html', encoding='utf-8').read()
print('len:', len(c))
# competitor matches
for m in ps.COMPETITOR_PAT.finditer(c):
    print('  competitor match @', m.start(), '(%.0f%%)' % (m.start() / len(c) * 100),
          '->', repr(c[m.start():m.start() + 60]))
# data-code positions
pos = [m.start() for m in re.finditer(r'data-code=', c)]
print('data-code count:', len(pos))
for p in pos[:25]:
    print('   @%7d (%.0f%%) %s' % (p, p / len(c) * 100, c[p:p + 40].replace('\n', ' ')))
