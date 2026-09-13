import re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

h = open('logs/diag_https_simplycodes_com_store_mileseeygolf_com.html', encoding='utf-8').read()
print('simplycodes len:', len(h))
for pat in ['competitor', 'Competitor', 'similar stores', 'Similar Stores', 'More Mileseey']:
    i = h.find(pat)
    print(' ', pat, '->', i, ('(%.1f%%)' % (i / len(h) * 100)) if i >= 0 else '')

codes = re.findall(r'data-code="([^"]+)"', h)
print('data-code count:', len(codes), '| unique:', len(set(codes)))
print('sample:', sorted(set(codes))[:25])

# data-code attrs ka position distribution
positions = sorted(m.start() for m in re.finditer(r'data-code="', h))
print('first data-code pos:', positions[0] if positions else None,
      '| last:', positions[-1] if positions else None,
      '| pct first/last: %.1f%% / %.1f%%' % (positions[0] / len(h) * 100, positions[-1] / len(h) * 100) if positions else '')

print()
c = open('logs/diag_https_couponreals_com_store_shoplc.html', encoding='utf-8').read()
m = re.search(r'<title[^>]*>(.*?)</title>', c, re.S | re.I)
print('couponreals title:', m.group(1).strip()[:100] if m else '?')
print('len:', len(c), '| has shoplc:', 'shoplc' in c.lower(),
      '| HELLO20:', 'HELLO20' in c, '| GOLD20:', 'GOLD20' in c)
print('has cloudflare/challenge:', any(x in c.lower() for x in ['just a moment', 'cloudflare', 'captcha', 'incapsula']))
# visible text sample
text = re.sub(r'<script\b.*?</script>', ' ', c, flags=re.S | re.I)
text = re.sub(r'<style\b.*?</style>', ' ', text, flags=re.S | re.I)
text = re.sub(r'<[^>]+>', ' ', text)
text = re.sub(r'\s+', ' ', text).strip()
print('visible text (400):', text[:400])
