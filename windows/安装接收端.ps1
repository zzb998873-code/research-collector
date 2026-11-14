[CmdletBinding()]
param(
    [string]$ListenAddress,
    [int]$Port = 8789,
    [switch]$InstallModels,
    [switch]$SkipInstall,
    [switch]$ConfigureFirewall
)

$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$state = Join-Path $project '.runtime'
$venv = Join-Path $project '.venv'
$program = Join-Path $PSScriptRoot 'collector.py'
$researchPython = (python -c 'import sys; print(sys._base_executable)').Trim()
$researchPythonWindowless = Join-Path (Split-Path -Parent $researchPython) 'pythonw.exe'
$site = Join-Path $venv 'Lib/site-packages'

if (-not $ListenAddress) {
    $physical = Get-NetAdapter -Physical | Where-Object Status -EQ 'Up' | Select-Object -ExpandProperty ifIndex
    $candidates = Get-NetIPConfiguration | Where-Object {
        $_.InterfaceIndex -in $physical -and $_.IPv4DefaultGateway -and $_.IPv4Address
    }
    $chosen = $candidates | Where-Object InterfaceAlias -Match 'Wi-Fi|WLAN' | Select-Object -First 1
    if (-not $chosen) { $chosen = $candidates | Select-Object -First 1 }
    if (-not $chosen) { throw '没有找到局域网 IPv4，请使用 -ListenAddress 指定电脑的 Wi-Fi 地址。' }
    $ListenAddress = @($chosen.IPv4Address)[0].IPAddress
}

if (-not $SkipInstall) {
    $requirements = if ($InstallModels) { 'requirements-models.txt' } else { 'requirements.txt' }
    & $researchPython -m pip install --disable-pip-version-check --target $site -r (Join-Path $PSScriptRoot $requirements)
    if ($LASTEXITCODE -ne 0) { throw '无法安装 Python 依赖。' }
}

& $researchPython -X utf8 $program --state $state setup --host $ListenAddress --port $Port
if ($LASTEXITCODE -ne 0) { throw '接收端配置失败。' }

if ($ConfigureFirewall) {
    $name = 'ResearchCollectorInbound'
    $rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
    if (-not $rule) {
        $firewall = @{
            Name = $name; DisplayName = '科研资料收集（本地子网）'; Direction = 'Inbound'
            Action = 'Allow'; Protocol = 'TCP'; LocalPort = $Port; LocalAddress = $ListenAddress
            RemoteAddress = 'LocalSubnet'; Profile = 'Any'
        }
        New-NetFirewallRule @firewall | Out-Null
    } else {
        $rule | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -LocalPort $Port | Out-Null
        $rule | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -LocalAddress $ListenAddress -RemoteAddress LocalSubnet | Out-Null
    }
}

$startup = [Environment]::GetFolderPath('Startup')
if (-not $startup) { throw '无法读取当前用户的启动目录。' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $startup 'Research Collector.lnk'))
$shortcut.TargetPath = $researchPythonWindowless
$shortcut.Arguments = '-X utf8 "{0}" --state "{1}" serve' -f $program, $state
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = '科研资料局域网接收与 Obsidian 入库'
$shortcut.Save()

$listener = Get-NetTCPConnection -LocalAddress $ListenAddress -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    Start-Process -FilePath $researchPythonWindowless -ArgumentList $shortcut.Arguments -WindowStyle Hidden
}
