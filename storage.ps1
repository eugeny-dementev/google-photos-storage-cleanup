param([Parameter(ValueFromRemainingArguments=$true)][string[]]$StorageArgs)
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$oldHttp=$env:HTTP_PROXY; $oldHttps=$env:HTTPS_PROXY
try {
  $network=Join-Path $PSScriptRoot 'gpsync-data/network.json'
  $settings=Get-Content -Raw -LiteralPath (Join-Path $env:APPDATA 'Code/User/settings.json') | ConvertFrom-Json
  $proxy=$settings.'http.proxy'
  if(-not $proxy){throw 'VS Code http.proxy is missing; cleanup will not connect directly.'}
  $config=Get-Content -Raw -LiteralPath $network|ConvertFrom-Json
  $config.proxy=$proxy
  $config|ConvertTo-Json|Set-Content -LiteralPath $network -Encoding utf8NoBOM
  $env:HTTP_PROXY=$proxy; $env:HTTPS_PROXY=$proxy
  & "$PSScriptRoot/.venv/Scripts/python.exe" -m gpsync storage @StorageArgs
  exit $LASTEXITCODE
} finally {$env:HTTP_PROXY=$oldHttp; $env:HTTPS_PROXY=$oldHttps}
