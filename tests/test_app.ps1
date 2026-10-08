param([string]$StatePath)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$script:st = @{ clicks = 0; text = ""; checked = $false }
function Save { ($script:st | ConvertTo-Json -Compress) | Set-Content -Path $StatePath -Encoding UTF8 }
$f = New-Object System.Windows.Forms.Form
$f.Text = "pcsight-test"; $f.Width = 440; $f.Height = 260; $f.StartPosition = "Manual"; $f.Location = New-Object System.Drawing.Point(200, 200)
$l = New-Object System.Windows.Forms.Label; $l.Text = "Nombre"; $l.Left = 20; $l.Top = 20; $f.Controls.Add($l)
$t = New-Object System.Windows.Forms.TextBox; $t.Name = "campo"; $t.AccessibleName = "Campo nombre"; $t.Left = 20; $t.Top = 45; $t.Width = 300
$t.Add_TextChanged({ $script:st.text = $t.Text; Save }); $f.Controls.Add($t)
$b = New-Object System.Windows.Forms.Button; $b.Text = "Pulsar"; $b.Left = 20; $b.Top = 85; $b.Width = 120
$b.Add_Click({ $script:st.clicks++; Save }); $f.Controls.Add($b)
$c = New-Object System.Windows.Forms.CheckBox; $c.Text = "Activar opcion"; $c.Left = 20; $c.Top = 130; $c.Width = 200
$c.Add_CheckedChanged({ $script:st.checked = $c.Checked; Save }); $f.Controls.Add($c)
Save
[System.Windows.Forms.Application]::Run($f)
