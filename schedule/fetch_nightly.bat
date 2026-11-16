@echo off
REM Wrapper the VBI-FetchNightly scheduled task calls (nightly, ~02:30).
REM Fetches one staggered shard of brand signals into the cache. Tees to a log.
setlocal
pushd "%~dp0\.."
set "VBI_ROOT=%CD%"
if not exist "%VBI_ROOT%\logs" mkdir "%VBI_ROOT%\logs"
set "STAMP=%DATE:/=-%_%TIME::=-%"
set "LOG=%VBI_ROOT%\logs\nightly_%STAMP: =0%.log"
set "PY=%VBI_ROOT%\.venv\Scripts\python.exe"
REM Force UTF-8 so printing accented brand names (Hermès, Façonnable…) can't crash
REM the run under the scheduler's legacy cp1252 console (see force_utf8_stdio).
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "%PY%" (
  echo [task] venv python not found at %PY%>>"%LOG%"
  popd & exit /b 9
)

echo [task] %DATE% %TIME% VGR Brand Search Interest nightly fetch>>"%LOG%"
"%PY%" -m vgr_brand_index.fetch_nightly %* 1>>"%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
>>"%LOG%" echo [task] finished rc=%RC%
popd
endlocal & exit /b %RC%
