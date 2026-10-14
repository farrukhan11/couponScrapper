# Deploy — Coupon Scraper Web UI (Contabo Ubuntu, IP:port)

Team ek browser se `http://<server-ip>:8000` khol kar brands scrape karegi.

## 1. System packages
```bash
sudo apt update
sudo apt install -y python3-venv python3-pip xvfb
```

## 2. Project + venv
```bash
# maan lo project /opt/couponScrapper mein hai
cd /opt/couponScrapper
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# headless Chrome + system deps
python -m playwright install --with-deps chromium
```

## 3. Config (.env)
```bash
cp .env.example .env
nano .env
```
Zaroori:
- `UI_PASSWORD=<lamba-random-password>`
- `UI_SECRET_KEY=<random-hex>`  (`openssl rand -hex 32`)
- `SCRAPER_HEADLESS=1`
- (agar system Chrome use karna ho) `CHROME_PATH=/usr/bin/google-chrome`

## 4. Firewall (port kholo)
```bash
sudo ufw allow 8000/tcp
```
Contabo panel → **Firewall** → `8000/tcp` inbound **bhi** allow karo.

## 5. systemd service
`couponscraper.service` mein `User=` aur `WorkingDirectory=` apne hisaab se edit karo, phir:
```bash
sudo cp couponscraper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now couponscraper
systemctl status couponscraper
journalctl -u couponscraper -f
```

## 5b. pm2 se run (systemd ke bajaye)
```bash
cd /var/www/couponScrapper
pm2 start /var/www/couponScrapper/venv/bin/gunicorn \
  --name couponscraper \
  --cwd /var/www/couponScrapper \
  --interpreter none \
  -- -w 1 --threads 8 -b 127.0.0.1:8000 web_ui:app
pm2 save
pm2 logs couponscraper
```
> `-b 127.0.0.1:8000` = sirf local; nginx proxy karega. Seedha `IP:8000` chahiye to `-b 0.0.0.0:8000` karo.
> pm2 restart: `pm2 restart couponscraper` · boot par auto-start: `pm2 startup` phir `pm2 save`.

## 5c. nginx reverse proxy (optional)
`/etc/nginx/sites-available/couponscraper`:
```nginx
server {
    listen 80;
    server_name coupons.tumharadomain.com;   # ya 81.17.100.61

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_read_timeout 3600s;    # lambi jobs
        proxy_buffering off;         # live log polling
    }
}
```
```bash
ln -s /etc/nginx/sites-available/couponscraper /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

## 6. Test
```bash
curl -I http://127.0.0.1:8000
```
Browser: `http://<server-ip>:8000` (ya nginx subdomain) → shared password se login.

## 7. Pehla index build (ZAROORI)
`data/stores_index.json` git mein commit nahi hota, isliye clone ke baad **ek dafa** index banao — warna scrape kuch nahi dega:
```bash
cd /var/www/couponScrapper && source venv/bin/activate
python sitemap_crawler.py                 # ~10-20 min
# ya UI ke "🔄 Refresh Store Index" button se
```

### 7b. Datacenter IP + Cloudflare (important)
Contabo ka server **datacenter IP** hai — Cloudflare wali sites (simplycodes, promopro,
tenereteam, groupon) browser fetch ko block kar deti hain, isliye server par index
**adhoora** ban sakta hai. Sabse reliable fix: **local machine (residential IP) par
index banao aur server par copy karo**:
```bash
# local machine se:
scp data/stores_index.json root@<server-ip>:/var/www/couponScrapper/data/stores_index.json
```
Yehi baat scraping par bhi lagti hai — server se kuch Cloudflare store-pages bhi block
ho sakte hain (headless + datacenter IP). Options:
- **Headful xvfb** (thoda behtar pass-rate): `SCRAPER_HEADLESS=0` + `xvfb-run` (dekho
  "Headless vs headful" section).
- **Firecrawl fallback**: `.env` mein `FIRECRAWL_API_KEY` set karo (residential proxy
  infra use karta hai, blocked sitemaps/pages ke liye).

## Manual run (service ke bina, test)
```bash
./run.sh          # gunicorn 0.0.0.0:8000
# ya dev:
python web_ui.py
```

## Headless vs headful (Cloudflare)
- Default `SCRAPER_HEADLESS=1` (VM par display nahi).
- Agar koi site headless par block kare, browser fallback ko **xvfb** ke through headful chala sakte ho:
  ```bash
  sudo apt install -y xvfb
  # systemd ExecStart se pehle:
  ExecStart=/usr/bin/xvfb-run -a /opt/couponScrapper/venv/bin/gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 web_ui:app
  ```
  Ya UI ka "Headless mode" checkbox off karke chalayo (xvfb maujood ho to).

## Stealth setup (Stage 1) — Cloudflare block kam karo (free)
`.env`:
```env
SCRAPER_HEADLESS=0          # headful (xvfb) — headless se kam detect
STEALTH_ENGINE=auto         # patchright -> playwright
USE_PERSISTENT_PROFILE=1    # cf_clearance cookies bache
BLOCK_MEDIA=1               # images/fonts block (bandwidth bachao)
CHROME_PATH=/usr/bin/google-chrome   # real Chrome (recommended)
```

Install (real Chrome + patchright + system libs):
```bash
wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor > /usr/share/keyrings/google.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google.list
apt update && apt install -y google-chrome-stable xvfb
source venv/bin/activate
python -m patchright install chromium
python -m playwright install-deps chromium
```

pm2 ko **xvfb** ke andar chalao (headful ke liye):
```bash
pm2 delete couponscraper
pm2 start /usr/bin/xvfb-run --name couponscraper --cwd /var/www/couponScrapper \
  --interpreter none -- -a /var/www/couponScrapper/venv/bin/gunicorn \
  -w 1 --threads 8 -b 0.0.0.0:8000 web_ui:app
pm2 save && pm2 logs couponscraper
```
> Bacha hua data: `chrome_profile/` mein cookies save hote hain — server par ye folder delete na karo.

## Proxy (Stage 2) — jab residential proxy le lo ($10 ~ 10GB)
`.env`:
```env
PROXY_URL=http://user:pass@proxy-host:port
```
`pm2 restart couponscraper`. Code khud browser fallback + crawl4ai par proxy lagata hai.
Bandwidth bachane ke liye proxy ko sirf blocked sites par chalaana behtar hai (agents selective mode add karenge).

## Security notes
- Abhi **HTTP** hai — password network par clear-text jata hai. Zaroor:
  - **lamba random** `UI_PASSWORD` rakho, aur
  - `ufw`/Contabo firewall se sirf **team ke IPs** ko 8000 allow karo (`ufw allow from <ip> to any port 8000`).
- Behtar (optional): aage chhota Caddy/nginx reverse proxy + TLS laga do, phir `https://` use karo.

## Disk / cleanup
Har run `jobs/<id>/` mein save hota hai (`results.csv`, `summary.csv`, `logs.txt`).
Purane jobs hataane ke liye:
```bash
find /opt/couponScrapper/jobs -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
```

## Update deploy
```bash
cd /opt/couponScrapper
git pull
source venv/bin/activate && pip install -r requirements.txt
sudo systemctl restart couponscraper
```
