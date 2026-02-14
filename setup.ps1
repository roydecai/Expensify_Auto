$ErrorActionPreference = 'Stop'

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$projectRoot = $PSScriptRoot
$localRoot = Join-Path $env:LocalAppData 'ExpensifyAuto'
$venvDir = Join-Path $localRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$venvPathFile = Join-Path $projectRoot '.venv_path'
$pythonPathFile = Join-Path $projectRoot '.python_path'

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

function Resolve-PythonPath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Python
  )
  if ($Python -eq 'py -3.11') {
    $resolved = & py -3.11 -c "import sys;print(sys.executable)"
    if ($LASTEXITCODE -ne 0 -or -not $resolved) {
      return $null
    }
    return $resolved.Trim()
  }
  if (-not (Test-Path $Python)) {
    return $null
  }
  return $Python
}

function Get-MissingModules {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Python,
    [Parameter(Mandatory = $true)]
    [string[]]$Modules
  )
  $modulesStr = $Modules -join ','
  $code = "import importlib.util;mods='$modulesStr'.split(',');missing=[m for m in mods if m and importlib.util.find_spec(m) is None];print(','.join(missing))"
  $result = & $Python -c $code
  if ($LASTEXITCODE -ne 0) {
    return @()
  }
  if (-not $result) {
    return @()
  }
  return $result.Trim().Split(',', [System.StringSplitOptions]::RemoveEmptyEntries)
}

function Map-ModuleToPackage {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Module
  )
  if ($Module -eq 'PIL') { return 'Pillow' }
  if ($Module -eq 'dotenv') { return 'python-dotenv' }
  return $Module
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
  $reqPath = Join-Path $projectRoot 'requirements.txt'
  $lines = Get-Content $reqPath
  if (-not $lines) {
    throw 'requirements.txt does not contain installable packages.'
  }

  # Configure pip to use Aliyun mirror for better connectivity in China
  Write-Host "Configuring pip to use Aliyun mirror..."
  & $venvPython -m pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
  & $venvPython -m pip config set install.trusted-host mirrors.aliyun.com

  $optional = @('paddleocr', 'paddlepaddle')
  $core = $lines | Where-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith('#')) { return $false }
    $keep = $true
    foreach ($opt in $optional) {
      if ($line -match ("^" + [Regex]::Escape($opt))) { $keep = $false }
    }
    $keep
  }

  Write-Host "Installing core requirements..."
  if ($core) {
      & $venvPython -m pip install $core
      if ($LASTEXITCODE -ne 0) {
        throw 'pip install core requirements failed.'
      }
  }

  $answer = Read-Host "Install optional packages (paddleocr/paddlepaddle)? (Y/N)"
  if ($answer -match '^(y|Y)') {
    $optionalLines = $lines | Where-Object {
      $line = $_.Trim()
      if (-not $line -or $line.StartsWith('#')) { return $false }
      $line -match '^paddleocr' -or $line -match '^paddlepaddle'
    }
    if ($optionalLines) {
      Write-Host "Installing optional requirements..."
      & $venvPython -m pip install $optionalLines
    }
  }
}

function Write-VenvPath {
  if (-not (Test-Path $localRoot)) {
    New-Item -ItemType Directory -Path $localRoot | Out-Null
  }
  # Use UTF-8 without BOM to ensure batch scripts can read it correctly even with chcp 65001
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($venvPathFile, $venvPython, $utf8NoBom)
}

function Write-PythonPath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Python
  )
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($pythonPathFile, $Python, $utf8NoBom)
}

function Install-Modules {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Python,
    [Parameter(Mandatory = $true)]
    [string[]]$Modules
  )
  $packages = @()
  foreach ($mod in $Modules) {
    $packages += (Map-ModuleToPackage -Module $mod)
  }
  & $Python -m pip install @packages
  return ($LASTEXITCODE -eq 0)
}

function Check-SystemDeps {
  $pdftoppm = Get-Command pdftoppm -ErrorAction SilentlyContinue
  if (-not $pdftoppm) {
    Write-Warning 'pdftoppm (Poppler) was not found. pdf2image may fail to convert scanned PDFs to images, so PaddleOCR may not run for image-based PDFs.'
  }
}

$python311 = Ensure-Python311
$resolvedPython = Resolve-PythonPath -Python $python311
$systemPython = $resolvedPython
$requiredModules = @('paddleocr', 'paddlepaddle', 'pdfplumber', 'pdf2image', 'PIL', 'dotenv', 'openai', 'ruff', 'mypy')
$smallModules = @('pdfplumber', 'pdf2image', 'PIL', 'dotenv', 'openai', 'ruff', 'mypy')
$largeModules = @('paddleocr', 'paddlepaddle')

$useVenv = $true
if ($systemPython) {
  Write-Host ("Local Python: " + $systemPython)
  $missing = Get-MissingModules -Python $systemPython -Modules $requiredModules
  if ($missing -and $missing.Count -gt 0) {
    Write-Host ("Missing modules: " + ($missing -join ', '))
  }
  if (-not $missing -or $missing.Count -eq 0) {
    Write-PythonPath -Python $systemPython
    $useVenv = $false
  } else {
    $missingLarge = $missing | Where-Object { $largeModules -contains $_ }
    $missingSmall = $missing | Where-Object { $smallModules -contains $_ }
    if ($missingLarge.Count -eq 0 -and $missingSmall.Count -gt 0) {
      $answer = Read-Host "Local Python missing modules: $($missingSmall -join ', '). Install to local Python? (Y/N)"
      if ($answer -match '^(y|Y)') {
        if (Install-Modules -Python $systemPython -Modules $missingSmall) {
          Write-PythonPath -Python $systemPython
          $useVenv = $false
        }
      }
    }
  }
}

if ($useVenv) {
  New-Venv -Python $python311
  if (-not (Ensure-VenvPip)) {
    Remove-Venv
    New-Venv -Python $python311
    if (-not (Ensure-VenvPip)) {
      throw 'pip is not available in the virtual environment.'
    }
  }
  Write-VenvPath
  Write-PythonPath -Python $venvPython
  Install-Requirements
}

Check-SystemDeps

Write-Host ''
Write-Host 'Setup completed.'
Write-Host ('- python: ' + (Get-Content $pythonPathFile))
Write-Host ('- venv: ' + $venvDir)
Write-Host ('- example: ' + (Get-Content $pythonPathFile) + ' src\pdf_extractor.py <pdf-or-folder> --output-dir temp')
