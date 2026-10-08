param([string]$StatePath, [double]$Opacity = 0)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @"
using System.Windows.Forms;
public class QuietForm : Form {
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams {
        get { CreateParams p = base.CreateParams; p.ExStyle |= 0x08000000 | 0x00000080; return p; }   // NOACTIVATE | TOOLWINDOW
    }
}
"@
$script:st = @{ clicks = 0; text = ""; checked = $false }
function Save { ($script:st | ConvertTo-Json -Compress) | Set-Content -Path $StatePath -Encoding UTF8 }
$f = New-Object QuietForm
$f.Text = "pcsight-test"; $f.Width = 440; $f.Height = 260; $f.StartPosition = "Manual"; $f.Location = New-Object System.Drawing.Point(40, 40)
$f.ShowInTaskbar = $false; $f.Opacity = $Opacity
$f.Add_Shown({ $f.SendToBack() })
$l = New-Object System.Windows.Forms.Label; $l.Text = "Nombre"; $l.Left = 20; $l.Top = 20; $f.Controls.Add($l)
$t = New-Object System.Windows.Forms.TextBox; $t.AccessibleName = "Campo nombre"; $t.Left = 20; $t.Top = 45; $t.Width = 300
$t.Add_TextChanged({ $script:st.text = $t.Text; Save }); $f.Controls.Add($t)
$m = New-Object System.Windows.Forms.Label; $m.Text = "Clicks: 0"; $m.Left = 20; $m.Top = 170; $m.Width = 200; $f.Controls.Add($m)
$b = New-Object System.Windows.Forms.Button; $b.Text = "Pulsar"; $b.Left = 20; $b.Top = 85; $b.Width = 120
$b.Add_Click({ $script:st.clicks++; $m.Text = "Clicks: " + $script:st.clicks; Save }); $f.Controls.Add($b)
$c = New-Object System.Windows.Forms.CheckBox; $c.Text = "Activar opcion"; $c.Left = 20; $c.Top = 130; $c.Width = 200
$c.Add_CheckedChanged({ $script:st.checked = $c.Checked; Save }); $f.Controls.Add($c)
Save
[System.Windows.Forms.Application]::Run($f)
