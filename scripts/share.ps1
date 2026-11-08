param(
  [int]$Port = 4174,
  [string]$Profiles = "lin-shu-demo,another-demo"
)

# 一条命令把本机服务暴露到公网，并刷新二维码。
# 前提：本机服务已在运行（例如 .\scripts\serve.ps1），cloudflared.exe 已下载到 LOCALAPPDATA。

$projectRoot = Split-Path -Parent $PSScriptRoot
$cloudflared = Join-Path $env:LOCALAPPDATA "cloudflared\cloudflared.exe"
if (-not (Test-Path $cloudflared)) {
  throw "未找到 cloudflared.exe，请先下载：https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe 放到 $env:LOCALAPPDATA\cloudflared\"
}

$log = Join-Path $projectRoot "out\tunnel.log"
if (Test-Path $log) { Remove-Item $log -Force }
$proc = Start-Process -FilePath $cloudflared -ArgumentList "tunnel","--url","http://localhost:$Port","--no-autoupdate","--logfile",$log -PassThru -WindowStyle Hidden

$url = $null
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 2
  if (Test-Path $log) {
    $m = Select-String -LiteralPath $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -AllMatches | Select-Object -First 1
    if ($m) { $url = $m.Matches[0].Value; break }
  }
}
if (-not $url) {
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  throw "隧道启动失败，请查看 $log"
}

# 读取访问口令
$envFile = Join-Path $projectRoot "server\.env"
$code = ""
if (Test-Path $envFile) {
  $line = Get-Content -LiteralPath $envFile -Encoding UTF8 | Where-Object { $_ -match "^ACCESS_CODE=" } | Select-Object -First 1
  if ($line) { $code = ($line -split "=", 2)[1].Trim() }
}

$qrDir = Join-Path $projectRoot "out\onsite"
if (Test-Path $qrDir) {
  Get-ChildItem -LiteralPath $qrDir -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in ".png", ".txt", ".json" } | Remove-Item -Force -ErrorAction SilentlyContinue
}
$args = @()
if ($code) { $args = @("--access-code", $code) }
foreach ($id in ($Profiles -split ",")) {
  $id = $id.Trim()
  if (-not $id) { continue }
  & py -3 (Join-Path $projectRoot "scripts\generate_links.py") --profile $id --base-url ($url + "/web/") --out $qrDir @args | Out-Null
}

Write-Host ""
Write-Host ("公网地址：" + $url + "/web/")
if ($code) { Write-Host "访问口令：已开启（二维码里已包含）" } else { Write-Host "访问口令：未设置，任何拿到链接的人都能使用" -ForegroundColor Yellow }
Write-Host ("二维码目录：" + $qrDir)
Write-Host ("隧道日志：" + $log)
Write-Host "关闭本窗口或停止该进程后，公网地址立即失效；每次重启地址都会变。" -ForegroundColor Yellow
Write-Host ""
Write-Host "按 Ctrl+C 停止隧道"
Wait-Process -Id $proc.Id
