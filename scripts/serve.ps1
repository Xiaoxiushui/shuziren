param(
  [int]$Port = 4174,
  [string]$BindAddress = "0.0.0.0",
  # 提供证书与私钥时以 https 启动；手机要用麦克风录音必须走 https。
  [string]$Cert = "",
  [string]$Key = ""
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$scheme = "http"
if ((Test-Path $Cert) -and (Test-Path $Key)) { $scheme = "https" }
elseif ($Cert -or $Key) { Write-Host "证书与私钥需要同时提供，本次按 http 启动。" -ForegroundColor Yellow }

$lanIp = $null
if ($BindAddress -eq "0.0.0.0") {
  $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
    Sort-Object RouteMetric |
    Select-Object -First 1
  if ($route) {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue |
      Select-Object -First 1).IPAddress
  }
}

# 默认生成二维码：每次启动都把当前地址的二维码刷到 out\onsite，省去手工生成步骤。
$qrDir = Join-Path $projectRoot "out\onsite"
# 只保留最新的一套二维码：每次启动先清空，避免新旧地址混在一起。
if (Test-Path $qrDir) {
  Get-ChildItem -LiteralPath $qrDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".png", ".txt", ".json" } |
    Remove-Item -Force -ErrorAction SilentlyContinue
}
$profiles = @("lin-shu-demo", "another-demo")
$accessCode = $env:ACCESS_CODE
if ($null -eq $accessCode) {
  $envFile = Join-Path $projectRoot "server\.env"
  if (Test-Path -LiteralPath $envFile) {
    $line = Get-Content -LiteralPath $envFile -Encoding UTF8 | Where-Object { $_ -match '^\s*ACCESS_CODE\s*=' } | Select-Object -First 1
    if ($line) { $accessCode = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'") }
  }
}
$qrArgs = @()
if ($accessCode) { $qrArgs = @("--access-code", $accessCode) }
if ($lanIp) {
  $baseUrl = $scheme + "://" + $lanIp + ":" + $Port + "/web/"
  foreach ($id in $profiles) {
    try {
      & py -3 (Join-Path $projectRoot "scripts\generate_links.py") --profile $id --base-url $baseUrl --out $qrDir @qrArgs 2>$null | Out-Null
    } catch {
      Write-Host "二维码生成失败，可忽略（不影响服务启动）。" -ForegroundColor Yellow
    }
  }
}

$localUrl = $scheme + "://localhost:" + $Port
Write-Host ""
Write-Host ("电脑访问：" + $localUrl + "/web/")
Write-Host ("人物列表：" + $localUrl + "/web/people.html")
if ($lanIp) {
  $phoneUrl = $scheme + "://" + $lanIp + ":" + $Port + "/web/people.html"
  Write-Host ("手机访问：" + $phoneUrl + "（手机和电脑连同一个 Wi-Fi）")
  Write-Host ("二维码：" + (Join-Path $qrDir "lin-shu-demo.png") + "  与  " + (Join-Path $qrDir "another-demo.png"))
  if ($scheme -eq "https") {
    Write-Host "手机首次打开会提示证书不受信任，点“继续访问”即可。" -ForegroundColor Yellow
  } else {
    Write-Host "提示：http 地址下手机浏览器会禁用麦克风，只能打字；要录音请加 -Cert 与 -Key 走 https。" -ForegroundColor Yellow
  }
}
Write-Host "按 Ctrl+C 停止服务"
Write-Host ""

$python = Get-Command py -ErrorAction SilentlyContinue
$extra = @()
if ($scheme -eq "https") { $extra = @("--cert", $Cert, "--key", $Key) }

if ($python) {
  & $python.Source -3 (Join-Path $projectRoot "server\server.py") --host $BindAddress --port $Port @extra
  exit $LASTEXITCODE
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  throw "Python 3 was not found. Install Python 3 or run server/server.py manually."
}

& $python.Source (Join-Path $projectRoot "server\server.py") --host $BindAddress --port $Port @extra
exit $LASTEXITCODE
