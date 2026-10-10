param([string]$StatePath)
# Second test app: a real Windows menu with shortcuts, a drop-down, a long list, a long text and a scrolled panel.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @"
using System.Windows.Forms;
public class QuietForm2 : Form {
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams { get { CreateParams p = base.CreateParams; p.ExStyle |= 0x08000000 | 0x00000080; return p; } }
}
"@
$script:st = @{ saved = 0; newdoc = 0; color = ""; deep = 0; pick = "" }
function Save { ($script:st | ConvertTo-Json -Compress) | Set-Content -Path $StatePath -Encoding UTF8 }
$f = New-Object QuietForm2
$f.Text = "pc-control-test2"; $f.Width = 520; $f.Height = 420; $f.StartPosition = "Manual"; $f.Location = New-Object System.Drawing.Point(60, 60)
# a classic Windows menu (HMENU): "Save\tCtrl+S" and "New\tCtrl+Shift+N"
$mm = New-Object System.Windows.Forms.MainMenu
$file = $mm.MenuItems.Add("&File")
$sv = New-Object System.Windows.Forms.MenuItem("&Save"); $sv.Shortcut = [System.Windows.Forms.Shortcut]::CtrlS
$sv.Add_Click({ $script:st.saved++; Save }); [void]$file.MenuItems.Add($sv)
$nw = New-Object System.Windows.Forms.MenuItem("&New"); $nw.Shortcut = [System.Windows.Forms.Shortcut]::CtrlShiftN
$nw.Add_Click({ $script:st.newdoc++; Save }); [void]$file.MenuItems.Add($nw)
$pr = New-Object System.Windows.Forms.MenuItem("&Print"); $pr.Shortcut = [System.Windows.Forms.Shortcut]::CtrlP; $pr.Enabled = $false
[void]$file.MenuItems.Add($pr)
$f.Menu = $mm
$cb = New-Object System.Windows.Forms.ComboBox; $cb.DropDownStyle = "DropDownList"; $cb.AccessibleName = "Color"
[void]$cb.Items.AddRange(@("Red", "Green", "Blue", "Dark blue")); $cb.Left = 10; $cb.Top = 10; $cb.Width = 150
$cb.Add_SelectedIndexChanged({ $script:st.color = [string]$cb.SelectedItem; Save }); $f.Controls.Add($cb)
$lb = New-Object System.Windows.Forms.ListBox; $lb.AccessibleName = "Cities"; $lb.Left = 10; $lb.Top = 45; $lb.Width = 150; $lb.Height = 120
for ($i = 1; $i -le 80; $i++) { [void]$lb.Items.Add("City $i") }
$lb.Add_SelectedIndexChanged({ $script:st.pick = [string]$lb.SelectedItem; Save }); $f.Controls.Add($lb)
$tb = New-Object System.Windows.Forms.TextBox; $tb.Multiline = $true; $tb.ScrollBars = "Vertical"; $tb.AccessibleName = "Long text"
$tb.Left = 175; $tb.Top = 10; $tb.Width = 310; $tb.Height = 155
$tb.Text = (1..120 | ForEach-Object { "Line $_ of the long text" }) -join "`r`n"
$tb.Text += "`r`nTHE END MARKER"
$f.Controls.Add($tb)
$pn = New-Object System.Windows.Forms.Panel; $pn.AutoScroll = $true; $pn.Left = 10; $pn.Top = 180; $pn.Width = 475; $pn.Height = 150; $pn.BorderStyle = "FixedSingle"
$pn.AccessibleName = "Scrolled panel"
for ($i = 0; $i -lt 12; $i++) { $lab = New-Object System.Windows.Forms.Label; $lab.Text = "Filler row $i"; $lab.Top = 5 + 40 * $i; $lab.Left = 5; $pn.Controls.Add($lab) }
$deep = New-Object System.Windows.Forms.Button; $deep.Text = "Deep button"; $deep.Top = 5 + 40 * 12; $deep.Left = 5; $deep.Width = 120
$deep.Add_Click({ $script:st.deep++; Save }); $pn.Controls.Add($deep)
$f.Controls.Add($pn)
Save
[System.Windows.Forms.Application]::Run($f)
