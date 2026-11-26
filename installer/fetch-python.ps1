# Fallback: dipakai run.bat hanya jika Python belum terpasang sama sekali.
# Memakai PowerShell bawaan Windows agar tidak perlu installer tambahan.
# Memasang Python untuk pengguna saat ini (tanpa akses administrator).

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # progres digambar oleh bootstrap.py

$root = Split-Path -Parent $PSScriptRoot
$version = '3.12.10'
$work = Join-Path $root 'splitmedia\.setup'
$cache = Join-Path $work 'cache'
$setup = Join-Path $cache "python-$version-amd64.exe"
$url = "https://www.python.org/ftp/python/$version/python-$version-amd64.exe"

function Write-Head($text) {
    Write-Host ""
    Write-Host "  $text" -ForegroundColor Cyan
}
function Write-Step($text) {
    Write-Host "  >  $text"
}
function Write-Good($text) {
    Write-Host "  OK  $text" -ForegroundColor Green
}
function Write-Warn($text) {
    Write-Host "  !  $text" -ForegroundColor Yellow
}

New-Item -ItemType Directory -Force -Path $cache | Out-Null

Write-Head "splitmedia - menyiapkan Python $version"
Write-Warn "Python belum terpasang. Langkah ini terjadi sekali saja."
Write-Host ""
Write-Host "  Installer akan:"
Write-Host "    1. Mengunduh Python $version dari python.org (~26 MB)"
Write-Host "    2. Memasangnya untuk pengguna ini (tanpa administrator)"
Write-Host ""

try {
    if ((Test-Path $setup) -and ((Get-Item $setup).Length -gt 20MB)) {
        Write-Good "Installer Python sudah diunduh sebelumnya"
    }
    else {
        Write-Step "Mengunduh installer Python..."
        # BITS memberi progres nyata dan bisa melanjutkan unduhan terputus
        try {
            Start-BitsTransfer -Source $url -Destination $setup `
                -Description 'splitmedia: mengunduh Python' -ErrorAction Stop
        }
        catch {
            Write-Warn "BITS tidak tersedia, memakai unduhan biasa..."
            $client = New-Object System.Net.WebClient
            $client.Headers.Add('User-Agent', 'splitmedia-setup/1.0')
            $client.DownloadFile($url, $setup)
        }
        Write-Good "Installer Python terunduh"
    }

    Write-Step "Memasang Python (jendela progres muncul sebentar)..."
    $log = Join-Path $work ("python-install-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
    $args = @(
        '/quiet',
        'InstallAllUsers=0',
        'PrependPath=1',
        'Include_test=0',
        'Include_launcher=1',
        'Include_pip=1',
        "Log=$log"
    )
    $proc = Start-Process -FilePath $setup -ArgumentList $args -Wait -PassThru
    if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
        throw "Installer Python keluar dengan kode $($proc.ExitCode). Log: $log"
    }
    Write-Good "Python terpasang"
}
catch {
    Write-Host ""
    Write-Host "  GAGAL: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Cara manual:"
    Write-Host "    1. Buka https://www.python.org/downloads/"
    Write-Host "    2. Unduh dan jalankan installer Python"
    Write-Host "    3. Centang 'Add Python to PATH'"
    Write-Host "    4. Jalankan run.bat lagi"
    exit 1
}

# Pastikan launcher/path terlihat oleh proses lain: refresh PATH sesi ini
$env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path', 'User')

# Beri waktu installer selesai menulis registry
for ($i = 0; $i -lt 30; $i++) {
    $ok = $false
    try {
        $candidates = @()
        $candidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
        $candidates += 'C:\Program Files\Python312\python.exe'
        foreach ($c in $candidates) { if (Test-Path $c) { $ok = $true } }
        if (-not $ok) {
            $py = Get-Command py -ErrorAction SilentlyContinue
            if ($py) { $ok = $true }
        }
        if (-not $ok) {
            $p = Get-Command python -ErrorAction SilentlyContinue
            if ($p) { $ok = $true }
        }
    }
    catch { $ok = $false }
    if ($ok) { break }
    Start-Sleep -Seconds 2
}

exit 0
