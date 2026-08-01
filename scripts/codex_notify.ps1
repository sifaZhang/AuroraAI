$ErrorActionPreference = "Stop"

try {
    Add-Type -AssemblyName System.Windows.Forms

    [System.Media.SystemSounds]::Asterisk.Play()

    [System.Windows.Forms.MessageBox]::Show(
        "Codex 本轮任务已经结束，请检查结果。",
        "Codex",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    )
}
catch {
    Write-Host "通知脚本执行失败：" -ForegroundColor Red
    Write-Host $_.Exception.ToString() -ForegroundColor Red
    Read-Host "按回车关闭"
}
