@echo off
rem Buka Dark Fiber Core Manager di browser.
rem - server sudah jalan di port ini  -> langsung buka browser
rem - belum jalan                     -> nyalakan server (tanpa jendela konsol), tunggu siap, buka browser
rem Dipanggil oleh "Dark Fiber Core Manager.bat" di Desktop.
setlocal
set "PORT=4174"
set "URL=http://localhost:%PORT%"
set "ROOT=%~dp0.."
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

pushd "%ROOT%" 2>nul
if errorlevel 1 (
  echo Folder project tidak ditemukan: %ROOT%
  exit /b 1
)

curl.exe -s -o nul --max-time 2 --fail "%URL%/api/health" >nul 2>nul
if not errorlevel 1 goto open

set "PYW=%PYTHONW%"
if not defined PYW if exist "D:\tools\python312\pythonw.exe" set "PYW=D:\tools\python312\pythonw.exe"
if not defined PYW set "PYW=pythonw"

start "" "%PYW%" "src\server\app.py"

rem Tunggu sampai /api/health menjawab (maks ~40 detik). ping dipakai sebagai delay
rem karena "timeout" gagal saat jendela disembunyikan / stdin dialihkan.
set /a tries=0
:wait
ping -n 2 127.0.0.1 >nul 2>nul
curl.exe -s -o nul --max-time 2 --fail "%URL%/api/health" >nul 2>nul
if not errorlevel 1 goto open
set /a tries+=1
if %tries% lss 40 goto wait

echo Server belum menjawab di %URL% - membuka browser apa adanya.
echo Untuk melihat error, jalankan run.cmd di folder project.

:open
start "" "%URL%"
popd
exit /b 0
