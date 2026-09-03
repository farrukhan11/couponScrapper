@echo off
echo =======================================================
echo 🚀 LAUNCHING CHROME WITH REMOTE DEBUGGING FOR SCRAPER
echo =======================================================
echo.
echo Please wait... Google Chrome will open now.
echo Do not close this command window.
echo.
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --profile-directory="Default"
