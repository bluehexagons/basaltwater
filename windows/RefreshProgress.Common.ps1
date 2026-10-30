<# Native, read-only refresh progress. No installers or desktop actions. #>
function New-NativeRefreshProgressWindow {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $form = New-Object Windows.Forms.Form
    $form.Text = 'Basaltwater refresh'
    $form.Size = New-Object Drawing.Size(900, 620)
    $form.MinimumSize = New-Object Drawing.Size(620, 420)
    $form.StartPosition = 'CenterScreen'
    $form.AutoScaleDimensions = New-Object Drawing.SizeF(96, 96)
    $form.AutoScaleMode = 'Dpi'
    $form.Font = New-Object Drawing.Font('Segoe UI', 10)
    $layout = New-Object Windows.Forms.TableLayoutPanel
    $layout.Dock = 'Fill'; $layout.Padding = New-Object Windows.Forms.Padding(16)
    $layout.ColumnCount = 1; $layout.RowCount = 6
    foreach ($height in @(36, 58, 24, 0, 48, 38)) {
        $style = New-Object Windows.Forms.RowStyle
        $style.SizeType = if ($height) { 'Absolute' } else { 'Percent' }
        $style.Height = if ($height) { $height } else { 100 }
        $null = $layout.RowStyles.Add($style)
    }
    $status = New-Object Windows.Forms.Label
    $status.Dock = 'Fill'; $status.Font = New-Object Drawing.Font('Segoe UI', 15, [Drawing.FontStyle]::Bold)
    $status.AccessibleName = 'Refresh status'; $status.Text = 'Starting refresh'
    $summary = New-Object Windows.Forms.Label
    $summary.Dock = 'Fill'; $summary.Text = 'Waiting for setup output...'
    $progress = New-Object Windows.Forms.Label
    $progress.Dock = 'Fill'; $progress.Text = 'Elapsed: 0s'
    $output = New-Object Windows.Forms.TextBox
    $output.Dock = 'Fill'; $output.Multiline = $true; $output.ReadOnly = $true
    $output.ScrollBars = 'Vertical'; $output.WordWrap = $true
    $output.Font = New-Object Drawing.Font('Consolas', 10)
    $output.AccessibleName = 'Setup output'
    $advice = New-Object Windows.Forms.Label
    $advice.Dock = 'Fill'
    $advice.Text = 'Closing this window leaves setup running. The result stays here until you close it.'
    $close = New-Object Windows.Forms.Button
    $close.Text = 'Close'; $close.Dock = 'Right'; $close.Width = 110
    $close.Add_Click({ $form.Close() }.GetNewClosure())
    foreach ($control in @($status, $summary, $progress, $output, $advice, $close)) { $layout.Controls.Add($control) }
    $form.Controls.Add($layout)
    $form.CancelButton = $close
    return [pscustomobject]@{ Form = $form; Status = $status; Summary = $summary; Progress = $progress;
        Output = $output; Advice = $advice; LogLength = [long]-1 }
}

function Update-NativeRefreshProgressWindow($Window, [string]$Id) {
    $receipt = Get-NativeRefreshStatus $Id
    $Window.Status.Text = 'Refresh: ' + $receipt.Status
    $Window.Summary.Text = Get-NativeRefreshSummary $receipt
    $Window.Progress.Text = "Elapsed: $(Get-NativeRefreshElapsed $receipt)s"
    $log = Join-Path (Get-NativeRefreshDirectory $Id) 'setup.log'
    Assert-NativeSkillPath $log
    if (Test-Path -LiteralPath $log) {
        $length = (Get-Item -LiteralPath $log).Length
        if ($Window.LogLength -ne $length) {
            # Bound UI work and keep vendor output literal; no terminal escapes
            # or rich-text markup are interpreted. The full log remains on disk.
            $tail = (Get-Content -LiteralPath $log -Tail 100 -Encoding UTF8) -join [Environment]::NewLine
            if ($tail.Length -gt 65536) { $tail = $tail.Substring($tail.Length - 65536) }
            $Window.Output.Text = $tail
            $Window.Output.SelectionStart = $Window.Output.TextLength
            $Window.Output.ScrollToCaret()
            $Window.LogLength = $length
        }
    }
    $Window.Advice.Text = "Log: $log`r`nClose leaves setup running. Reopen: basaltw refresh --progress $Id"
    if ($receipt.LogTruncated) { $Window.Summary.Text += ' Output exceeded the 16 MiB retained log limit.' }
    return $receipt.Status -notin @('queued','running')
}

function Show-NativeRefreshProgress([string]$Id) {
    $null = Read-NativeRefreshReceipt $Id
    $window = New-NativeRefreshProgressWindow
    $timer = New-Object Windows.Forms.Timer
    $timer.Interval = 1000
    $update = {
        try {
            if (Update-NativeRefreshProgressWindow $window $Id) { $timer.Stop() }
        } catch {
            $window.Status.Text = 'Progress unavailable'
            $window.Summary.Text = "Could not read refresh status. Setup may still be running. Use basaltw refresh --status $Id and --logs $Id."
        }
    }.GetNewClosure()
    $timer.Add_Tick($update)
    $window.Form.Add_Shown({ $timer.Start(); & $update }.GetNewClosure())
    try { $null = $window.Form.ShowDialog() }
    finally { $timer.Stop(); $timer.Dispose(); $window.Form.Dispose() }
}
