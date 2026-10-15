# Windows 等价复现链（本机无 make）：.\tasks.ps1 dev / verify / demo / serve / test ...
#
# 也可以直接双击根目录的：启动演示.cmd  跑检查.cmd（它们是纯 ASCII 薄壳，转调本脚本）
#
# ！！本文件必须保存为「UTF-8 带 BOM」！！
#   Windows PowerShell 5.1 在没有 BOM 时按系统 ANSI(GBK) 解析 .ps1，
#   下面的中文会被拆成乱码并报 "Unexpected token '{'"。
#   编辑后请执行（本文件被 .gitattributes 标为 -text，git 不会动它的字节）：
#     python tools\fix_ps1_bom.py
param(
  [Parameter(Position = 0)][string]$Target = "help",
  [string]$Community = "caoyang-demo",
  [int]$Port = 8000,
  [int]$K = 2
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
Set-Location $PSScriptRoot

function Free-Port([int]$p) {
  $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    Write-Host "[*] 端口 $p 被进程 $($c.OwningProcess) 占用，先结束它"
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
  }
  if ($conns) { Start-Sleep -Milliseconds 800 }
}

function Wait-Health([int]$p, [int]$Seconds = 90) {
  for ($i = 0; $i -lt $Seconds * 2; $i++) {
    try {
      $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$p/api/health" -TimeoutSec 2
      if ($r.StatusCode -eq 200) { return $true }
    } catch { }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

switch ($Target) {
  "help" {
    Write-Host ""
    Write-Host "  .\tasks.ps1 dev      启动服务并自动打开浏览器（日常用这个）"
    Write-Host "  .\tasks.ps1 verify   跑一遍全部离线自检（规范 + 单测 + API + 演示 + 打包）"
    Write-Host "  .\tasks.ps1 demo     生成数据 → 体检 → 报告"
    Write-Host "  .\tasks.ps1 demoall  把全部社区的报告 + 风圈图 + 对比页都重生成"
    Write-Host "  .\tasks.ps1 serve    只启动服务（不开浏览器）"
    Write-Host "  .\tasks.ps1 test     引擎单元测试"
    Write-Host "  .\tasks.ps1 lint     代码规范自检（行长 / 编码 / 行尾，含 tasks.ps1 的 BOM）"
    Write-Host "  .\tasks.ps1 tour     一键演示逐步取景截图（out/tour/stepNN.png，录制前用）"
    Write-Host "  .\tasks.ps1 tourcheck 一键演示逐步自检（无头浏览器实跑 12 步，需先 serve）"
    Write-Host "  .\tasks.ps1 check    端到端 API 自检（需先 serve）"
    Write-Host "  .\tasks.ps1 pack     重新打交付包并自检"
    Write-Host "  .\tasks.ps1 ak       校验百度地图 AK（需网络）"
    Write-Host ""
    Write-Host "  参数：-Port 8000   -Community caoyang-real   -K 2"
    Write-Host ""
  }
  "dev" {
    Free-Port $Port
    Write-Host "[*] http://127.0.0.1:$Port   （关掉本窗口 / Ctrl+C 即停止）"
    python tools\serve.py --port $Port --warm --open
  }
  "serve" {
    Free-Port $Port
    python tools\serve.py --port $Port --warm
  }
  "verify" {
    $fail = 0
    # 自检另起一个端口，绝不动你可能正在用的 8000（一次疏忽就会把演示服务杀掉）
    $vp = if ($Port -eq 8000) { 8123 } else { $Port }
    Write-Host "== [1/5] 代码规范自检 ==" -ForegroundColor Cyan
    python tools\lint_style.py
    if ($LASTEXITCODE -ne 0) { $fail = 1 }

    Write-Host "`n== [2/5] 引擎单元测试 ==" -ForegroundColor Cyan
    python -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { $fail = 1 }

    Write-Host "`n== [3/5] 端到端 API 自检（端口 $vp）==" -ForegroundColor Cyan
    Free-Port $vp
    $srv = Start-Process -PassThru -WindowStyle Hidden python `
      -ArgumentList "tools\serve.py", "--port", $vp, "--warm"
    if (Wait-Health $vp) {
      python tools\check_api.py "http://127.0.0.1:$vp"
      if ($LASTEXITCODE -ne 0) { $fail = 1 }

      Write-Host "`n== [4/5] 一键演示逐步自检（无头浏览器实跑 12 步）==" -ForegroundColor Cyan
      # 需要本机有 Edge/Chrome；没有就跳过（返回 2 = 环境不具备，不算失败）
      python tools\tour_shots.py --check --url "http://127.0.0.1:$vp/?community=caoyang-real"
      if ($LASTEXITCODE -eq 1) { $fail = 1 }
      elseif ($LASTEXITCODE -eq 2) { Write-Host "  [i] 没找到 Edge/Chrome，跳过演示自检" }
    } else {
      Write-Host "  [X] 服务没起来，跳过 API 自检" -ForegroundColor Red
      $fail = 1
    }
    Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue
    Free-Port $vp

    Write-Host "`n== [5/5] 交付包自检 ==" -ForegroundColor Cyan
    if (Test-Path "dist\*.zip") {
      python tools\verify_package.py
      if ($LASTEXITCODE -ne 0) { $fail = 1 }
    } else {
      Write-Host "  [i] dist 里还没有包，跳过（python tools\make_package.py 可产出）"
    }

    if ($fail -eq 0) { Write-Host "`n全部通过 ✔" -ForegroundColor Green }
    else { Write-Host "`n有失败项 ✘（往上翻日志）" -ForegroundColor Red; exit 1 }
  }
  "demo"    { python tools\build_demo.py $Community --k $K }
  "demoall" {
    # 顺序有讲究：report.md/html 会内嵌风圈（focus_iso.json）与动线（day_route.json）的结论，
    # 这两份数据由 route_map.py 产出 —— 必须先跑 route_map，再生成报告，否则报告里的风圈
    # 结论会滞后一轮（图是新的、文字是旧的）。
    python tools\route_map.py caoyang-real | Out-Null
    foreach ($c in @("caoyang-real", "caoyang-demo", "custom-078ed4",
                     "custom-590cc5", "custom-ca018d", "custom-2af78a")) {
      Write-Host "== $c ==" -ForegroundColor Cyan
      python tools\build_demo.py $c | Out-Null
      if ($LASTEXITCODE -ne 0) { Write-Host "  [X] $c 失败" -ForegroundColor Red }
    }
    python tools\compare.py | Out-Null
    Write-Host "报告 / 风圈图 / 跨社区对比页已刷新" -ForegroundColor Green
  }
  "test"    { python -m unittest discover -s tests -v }
  "tour"    { python tools\tour_shots.py --url "http://127.0.0.1:$Port/?community=$Community" }
  "tourcheck" { python tools\tour_shots.py --check --url "http://127.0.0.1:$Port/?community=$Community" }
  "check"   { python tools\check_api.py "http://127.0.0.1:$Port" }
  "pack"    { python tools\make_package.py; python tools\verify_package.py }
  "ak"      { python tools\check_baidu_ak.py }
  "lint"    { python tools\lint_style.py }
  default   { throw ("未知目标：$Target（可用：dev / verify / demo / demoall / serve / test / " +
                     "tour / tourcheck / check / pack / lint / ak / help）") }
}
