<#
    One-shot setup for Windows. Safe to re-run.

        powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

    Installs uv and the Python dependencies, then generates a test vocal so
    the pipeline has something to run against.

    rubberband (the native time-stretch binary) is optional on Windows: when
    it is not on PATH the app falls back to librosa's phase vocoder, so stem
    generation works without it. Install it for higher-quality alignment.
#>

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "==> uv"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "    already installed ($(uv --version))"
} else {
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    # The installer adds uv to PATH for new shells; make it usable in this one.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    Write-Host "    installed. Open a new terminal if 'uv' is not found below."
}

Write-Host "==> rubberband (optional)"
if (Get-Command rubberband -ErrorAction SilentlyContinue) {
    Write-Host "    already installed"
} elseif (Get-Command choco -ErrorAction SilentlyContinue) {
    Write-Host "    installing via Chocolatey"
    choco install rubberband -y
} elseif (Get-Command scoop -ErrorAction SilentlyContinue) {
    Write-Host "    installing via Scoop"
    scoop install rubberband
} else {
    Write-Host "    !! not found. This is OK — the app uses a librosa fallback."
    Write-Host "       For better alignment, install a rubberband build from"
    Write-Host "       https://breakfastquay.com/rubberband/ and add it to PATH,"
    Write-Host "       or install Chocolatey/Scoop and re-run this script."
}

Write-Host "==> python dependencies"
uv sync

Write-Host "==> web frontend"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is required to build the web frontend. Install Node.js, then re-run setup."
}
npm --prefix web ci
npm --prefix web run build

Write-Host "==> test fixtures"
uv run python scripts/make_test_vocals.py

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "==> wrote .env - add STABILITY_API_KEY to enable the api backend"
}

Write-Host @"

Setup complete.

  Run the app:   uv run uvicorn backend.api:app --reload
                 http://127.0.0.1:8000

  Run the CLI:   uv run btg --input samples/fixtures/amin_100.wav --part bass

Both work right now on the ``mock`` backend - no model weights or API key needed.

To run the local Stable Audio 3 model on Windows (PyTorch, CPU on AMD):

  1. accept the licence at
     https://huggingface.co/stabilityai/stable-audio-3-small-music
  2. uv run hf auth login
  3. uv sync --extra local

CPU generation is slow but real. For best quality without a GPU, use the
``api`` backend instead (set STABILITY_API_KEY in .env).
"@
