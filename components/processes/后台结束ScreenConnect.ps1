# 每 30 秒关闭指定的非必要后台组件；由当前用户登录自启。
$processNames = @(
    'ScreenConnect.WindowsClient',
    'platform-communicator-tray',
    'LenovoVantage-(SmartScenarioAddin)',
    'ElabsSSPSystemDaemon',
    'MessagingPlugin',
    'aha_doctor',
    'BASupSrvcCnfg'
)
while ($true) {
    Get-Process -Name $processNames -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 30
}
