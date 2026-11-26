@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
title splitmedia
cd /d "%~dp0"

rem ---------------------------------------------------------------- 1. sudah siap?
rem Venv bisa "ada" tetapi rusak (folder disalin ke komputer lain tanpa Python-nya).
rem Jalankan uji singkat: venv harus bisa mengimpor aplikasi dengan benar.
if exist "%~dp0.venv\Scripts\python.exe" if exist "%~dp0splitmedia\.setup\ready.json" (
    "%~dp0.venv\Scripts\python.exe" -c "import splitmedia.app" >nul 2>&1
    if not errorlevel 1 (
        "%~dp0.venv\Scripts\python.exe" -m splitmedia %*
        set "CODE=!errorlevel!"
        if not "!CODE!"=="0" (
            echo.
            echo   Aplikasi berhenti dengan kode !CODE!. Tekan tombol apa saja untuk menutup.
            pause >nul
        )
        exit /b !CODE!
    )
    echo.
    echo   Lingkungan aplikasi tidak cocok dengan komputer ini.
    echo   Menyiapkan ulang secara otomatis ^(kurang dari 1 menit^)...
    rmdir /s /q "%~dp0.venv" >nul 2>&1
    del /q "%~dp0splitmedia\.setup\ready.json" >nul 2>&1
)

rem ------------------------------------------------- 2. cari python untuk installer
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>&1 && set "PY=python"
)
if defined PY (
    !PY! -c "import sys" >nul 2>&1 || set "PY="
)

rem ------------------------------------ 3. python ada? tetap cek versi minimal 3.9
if defined PY (
    !PY! -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
    if errorlevel 1 set "PY="
)

rem ----------------------------- 4. belum ada python: unduh lewat PowerShell (system)
if not defined PY (
    set "BOOTSTRAP=%~dp0installer\bootstrap.py"
    if not exist "!BOOTSTRAP!" (
        echo.
        echo   Berkas installer tidak ditemukan: installer\bootstrap.py
        echo   Pastikan seluruh folder aplikasi tersalin utuh.
        pause
        exit /b 2
    )
    echo.
    echo   Python belum terdeteksi. Menyiapkan lewat Windows PowerShell...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\fetch-python.ps1"
    if errorlevel 1 (
        echo.
        echo   Penyiapan otomatis gagal. Baca pesan di atas.
        pause
        exit /b 3
    )
    where py >nul 2>&1 && set "PY=py -3"
)

if not defined PY (
    echo.
    echo   Python tetap tidak ditemukan setelah penyiapan.
    echo   Buka https://www.python.org/downloads/ lalu jalankan run.bat lagi.
    pause
    exit /b 4
)

rem ------------------------------------------------------- 5. jalankan installer
!PY! "%~dp0installer\bootstrap.py" %*
set "CODE=!errorlevel!"
if not "!CODE!"=="0" (
    echo.
    echo   Penyiapan belum selesai ^(kode !CODE!^).
    echo   Jalankan run.bat lagi untuk melanjutkan.
    pause
    exit /b !CODE!
)

rem --------------------------------------------------- 6. buka aplikasi setelah siap
if exist "%~dp0.venv\Scripts\python.exe" (
    cls
    "%~dp0.venv\Scripts\python.exe" -m splitmedia
    set "CODE=!errorlevel!"
    if not "!CODE!"=="0" (
        echo.
        echo   Aplikasi berhenti dengan kode !CODE!.
        pause >nul
    )
    exit /b !CODE!
)

endlocal
