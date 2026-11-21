# PC-Control test app: a WinForms window whose canvas draws on click. What it draws is NOT in the UI tree: only pixels can show it.
# Each click on the canvas is counted into the file given as first argument.
param([string]$Out = "$env:TEMP\pcc-canvas.txt", [string]$Title = "PCC Canvas Test")
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = $Title; $form.Width = 560; $form.Height = 380; $form.StartPosition = "Manual"; $form.Left = 140; $form.Top = 140
$canvas = New-Object System.Windows.Forms.Panel
$canvas.Left = 20; $canvas.Top = 20; $canvas.Width = 300; $canvas.Height = 280; $canvas.BackColor = [System.Drawing.Color]::White
$canvas.Name = "Lienzo"; $canvas.AccessibleName = "Lienzo"
$script:n = 0
$script:bmp = New-Object System.Drawing.Bitmap 300, 280
$g0 = [System.Drawing.Graphics]::FromImage($script:bmp); $g0.Clear([System.Drawing.Color]::White); $g0.Dispose()
$canvas.BackgroundImage = $script:bmp
$canvas.Add_MouseDown({
    param($s, $e)
    $script:n++
    $g = [System.Drawing.Graphics]::FromImage($script:bmp)
    $g.FillEllipse([System.Drawing.Brushes]::OrangeRed, $e.X - 25, $e.Y - 25, 50, 50)
    $g.Dispose()
    $canvas.Invalidate()
    Set-Content -Path $Out -Value $script:n -Encoding UTF8
})
$form.Controls.Add($canvas)
$label = New-Object System.Windows.Forms.Label
$label.Text = "Haz clic en el lienzo"; $label.Left = 340; $label.Top = 30; $label.Width = 190
$form.Controls.Add($label)
Set-Content -Path $Out -Value 0 -Encoding UTF8
[System.Windows.Forms.Application]::Run($form)
