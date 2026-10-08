param([string]$StatePath, [double]$Opacity = 1, [int]$X = 40, [switch]$Taskbar)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @"
using System.Windows.Forms;
public class QuietForm : Form {
    public static bool ToolWin = true;
    protected override bool ShowWithoutActivation { get { return true; } }
    [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool SetWindowPos(System.IntPtr h, System.IntPtr after, int x, int y, int cx, int cy, uint flags);
    // To the bottom of the z-order WITHOUT activating: Form.SendToBack() calls SetWindowPos without SWP_NOACTIVATE and ACTIVATES the window
    // (a foreground attempt on every run; if the foreground lock does not stop it, it steals focus).
    protected override void OnShown(System.EventArgs e) { SetWindowPos(Handle, new System.IntPtr(1), 0, 0, 0, 0, 0x0013); base.OnShown(e); }   // HWND_BOTTOM; NOSIZE|NOMOVE|NOACTIVATE
    protected override CreateParams CreateParams {
        get { CreateParams p = base.CreateParams; p.ExStyle |= 0x08000000; if (ToolWin) p.ExStyle |= 0x00000080; else p.ExStyle |= 0x00040000; return p; }   // NOACTIVATE | TOOLWINDOW (no button) or APPWINDOW (forces the button: NOACTIVATE removes it)
    }
}
"@
$script:st = @{ clicks = 0; text = ""; checked = $false; deletes = 0; secret = ""; anchored = 0; extra = 0; menu = 0; down = 0; up = 0; moves = 0; minx = 99999; maxx = -1; miny = 99999; maxy = -1; held = 0 }
function Save { ($script:st | ConvertTo-Json -Compress) | Set-Content -Path $StatePath -Encoding UTF8 }
[QuietForm]::ToolWin = (-not $Taskbar)
$f = New-Object QuietForm
$f.Text = "pc-control-test"; $f.Width = 440; $f.Height = 360; $f.StartPosition = "Manual"; $f.Location = New-Object System.Drawing.Point($X, 40)
$f.ShowInTaskbar = [bool]$Taskbar; $f.Opacity = $Opacity
if ($Taskbar) { $f.WindowState = 'Minimized' }
$l = New-Object System.Windows.Forms.Label; $l.Text = "Name"; $l.Left = 20; $l.Top = 20; $f.Controls.Add($l)
$t = New-Object System.Windows.Forms.TextBox; $t.AccessibleName = "Name field"; $t.Left = 20; $t.Top = 45; $t.Width = 300
$t.Add_TextChanged({ $script:st.text = $t.Text; Save }); $f.Controls.Add($t)
$m = New-Object System.Windows.Forms.Label; $m.Text = "Clicks: 0"; $m.Left = 20; $m.Top = 170; $m.Width = 200; $f.Controls.Add($m)
$b = New-Object System.Windows.Forms.Button; $b.Text = "Press"; $b.Left = 20; $b.Top = 85; $b.Width = 120
$b.Add_Click({ $script:st.clicks++; $m.Text = "Clicks: " + $script:st.clicks; Save }); $f.Controls.Add($b)
$c = New-Object System.Windows.Forms.CheckBox; $c.Text = "Enable option"; $c.Left = 20; $c.Top = 130; $c.Width = 200
$c.Add_CheckedChanged({ $script:st.checked = $c.Checked; Save }); $f.Controls.Add($c)
$d = New-Object System.Windows.Forms.Button; $d.Text = "Delete all"; $d.Left = 160; $d.Top = 85; $d.Width = 120
$d.Add_Click({ $script:st.deletes++; Save }); $f.Controls.Add($d)
$pw = New-Object System.Windows.Forms.TextBox; $pw.AccessibleName = "Secret key"; $pw.UseSystemPasswordChar = $true; $pw.Left = 20; $pw.Top = 195; $pw.Width = 200
$pw.Add_TextChanged({ $script:st.secret = $pw.Text; Save }); $f.Controls.Add($pw)
$an = New-Object System.Windows.Forms.Button; $an.Text = "Anchored"; $an.Left = 300; $an.Top = 150; $an.Width = 100
$an.Anchor = [System.Windows.Forms.AnchorStyles]::Bottom -bor [System.Windows.Forms.AnchorStyles]::Right
$an.Add_Click({ $script:st.anchored++; Save }); $f.Controls.Add($an)
$ex = New-Object System.Windows.Forms.Button; $ex.Text = "Extra"; $ex.Left = 160; $ex.Top = 125; $ex.Width = 100; $ex.Visible = $false
$ex.Add_Click({ $script:st.extra++; Save }); $f.Controls.Add($ex)
# a real Windows menu (ContextMenuStrip) opened by a button (by message, no mouse): to test menu entries
$cms = New-Object System.Windows.Forms.ContextMenuStrip
$mi1 = $cms.Items.Add("Copy"); $mi1.Add_Click({ $script:st.menu++; Save })
$mi2 = $cms.Items.Add("Synonyms"); $mi2.Enabled = $false
$mb = New-Object System.Windows.Forms.Button; $mb.Text = "Menu"; $mb.Left = 300; $mb.Top = 85; $mb.Width = 100
$mb.Add_Click({ $cms.Show($f, (New-Object System.Drawing.Point(10, 10))) }); $f.Controls.Add($mb)
# a canvas (like Paint's): records the drag it receives
$pad = New-Object System.Windows.Forms.Panel; $pad.Left = 20; $pad.Top = 230; $pad.Width = 380; $pad.Height = 90; $pad.BackColor = [System.Drawing.Color]::White
$pad.AccessibleName = "Canvas"; $pad.AccessibleRole = "Client"
$pad.Add_MouseDown({ param($s, $e) $script:st.down++; $script:st.held = 1; Save })
$pad.Add_MouseUp({ param($s, $e) $script:st.up++; $script:st.held = 0; Save })
$pad.Add_MouseMove({ param($s, $e) if ($script:st.held -eq 1) { $script:st.moves++; if ($e.X -lt $script:st.minx) { $script:st.minx = $e.X }; if ($e.X -gt $script:st.maxx) { $script:st.maxx = $e.X }; if ($e.Y -lt $script:st.miny) { $script:st.miny = $e.Y }; if ($e.Y -gt $script:st.maxy) { $script:st.maxy = $e.Y } } })
$pad.Add_MouseCaptureChanged({ Save })
$f.Controls.Add($pad)
$f.Add_Resize({ $ex.Visible = ($f.Width -ge 560) })
Save
[System.Windows.Forms.Application]::Run($f)
