$ErrorActionPreference = 'Stop'

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$projectRoot = $PSScriptRoot
$venvDir = Join-Path $projectRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

function Get-Python311 {
  $candidates = @(
    (Join-Path $env:LocalAppData 'Programs\Python\Python311\python.exe'),
    (Join-Path $env:ProgramFiles 'Python311\python.exe')
  )

  foreach ($candidate in $candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }

  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    return 'py -3.11'
  }

  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($python) {
    return $python.Source
  }

  return $null
}

function Ensure-Python311 {
  $python = Get-Python311
  if ($python) {
    return $python
  }

  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if (-not $winget) {
    throw 'Python was not found (recommended: Python 3.11). winget was not found either, so automatic installation is unavailable.'
  }

  winget install -e --id Python.Python.3.11 --source winget --silent --accept-package-agreements --accept-source-agreements | Out-Host

  $python = Get-Python311
  if (-not $python) {
    throw 'Python 3.11 was installed via winget, but python.exe was not found. Please restart the terminal and try again.'
  }

  return $python
}

function New-Venv {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Python
  )

  if (Test-Path $venvPython) {
    return
  }

  if ($Python -eq 'py -3.11') {
    & py -3.11 -m venv --copies $venvDir
  } else {
    & $Python -m venv --copies $venvDir
  }

  & $venvPython -m ensurepip --upgrade
}

function Remove-Venv {
  if (Test-Path $venvDir) {
    Remove-Item -LiteralPath $venvDir -Recurse -Force
  }
}

function Ensure-VenvPip {
  if (-not (Test-Path $venvPython)) {
    return $false
  }
  $pipDir = Join-Path $venvDir 'Lib\site-packages\pip'
  return (Test-Path $pipDir)
}

function Install-Requirements {
  & $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')
  if ($LASTEXITCODE -ne 0) {
    throw 'pip install -r requirements.txt failed.'
  }
}

function Check-SystemDeps {
  $pdftoppm = Get-Command pdftoppm -ErrorAction SilentlyContinue
  if (-not $pdftoppm) {
    Write-Warning 'pdftoppm (Poppler) was not found. pdf2image may fail to convert scanned PDFs to images, so PaddleOCR may not run for image-based PDFs.'
  }
}

$python311 = Ensure-Python311
New-Venv -Python $python311
if (-not (Ensure-VenvPip)) {
  Remove-Venv
  New-Venv -Python $python311
  if (-not (Ensure-VenvPip)) {
    throw 'pip is not available in the virtual environment.'
  }
}
Install-Requirements
Check-SystemDeps

Write-Host ''
Write-Host 'Setup completed.'
Write-Host ('- venv: ' + $venvDir)
Write-Host ('- example: ' + (Join-Path $venvDir 'Scripts\python.exe') + ' src\pdf_extractor.py <pdf-or-folder> --output-dir temp')
