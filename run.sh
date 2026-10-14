#!/usr/bin/env bash
# Coupon Scraper Web UI — production start (Ubuntu)
set -e
cd "$(dirname "$0")"
[ -d venv ] && source venv/bin/activate
exec gunicorn -w 1 --threads 8 -b "0.0.0.0:${PORT:-8000}" web_ui:app
