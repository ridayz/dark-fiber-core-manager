@echo off
rem Jalan pintas: commit semua perubahan lalu push ke GitHub.
rem Pemakaian: scripts\sync.cmd -m "pesan commit"
setlocal
node "%~dp0sync.mjs" %*
