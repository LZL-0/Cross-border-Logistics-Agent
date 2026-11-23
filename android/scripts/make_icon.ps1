# 生成 App 图标：深墨蓝底 + 青色集装箱 + 定位点（与项目视觉一致）
Add-Type -AssemblyName System.Drawing

$outDir = Join-Path $PSScriptRoot "..\app\res\drawable"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

function New-Icon([int]$size, [string]$path) {
  $bmp = New-Object System.Drawing.Bitmap $size, $size
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAlias
  $s = $size / 512.0

  # 圆角底
  $bg = [System.Drawing.Color]::FromArgb(11, 15, 20)
  $g.Clear($bg)
  $pathRound = New-Object System.Drawing.Drawing2D.GraphicsPath
  $r = 96 * $s
  $pathRound.AddArc(0, 0, $r * 2, $r * 2, 180, 90)
  $pathRound.AddArc($size - $r * 2, 0, $r * 2, $r * 2, 270, 90)
  $pathRound.AddArc($size - $r * 2, $size - $r * 2, $r * 2, $r * 2, 0, 90)
  $pathRound.AddArc(0, $size - $r * 2, $r * 2, $r * 2, 90, 90)
  $pathRound.CloseFigure()
  $g.FillPath((New-Object System.Drawing.SolidBrush $bg), $pathRound)

  # 速度线
  $lineBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(95, 227, 224))
  $lines = @(@(300, 96, 92, 8), @(280, 118, 130, 8), @(258, 140, 168, 8))
  foreach ($l in $lines) {
    $g.FillRectangle($lineBrush, $l[0] * $s, $l[1] * $s, $l[2] * $s, $l[3] * $s)
  }

  # 集装箱主体
  $body = [System.Drawing.Color]::FromArgb(31, 182, 201)
  $dark = [System.Drawing.Color]::FromArgb(12, 112, 128)
  $light = [System.Drawing.Color]::FromArgb(88, 214, 222)
  $x = 86 * $s; $y = 188 * $s; $w = 300 * $s; $h = 150 * $s
  $g.FillRectangle((New-Object System.Drawing.SolidBrush $body), $x, $y, $w, $h)

  # 立体感：顶部与侧边高光
  $g.FillRectangle((New-Object System.Drawing.SolidBrush $light), $x, $y, $w, 10 * $s)
  $g.FillRectangle((New-Object System.Drawing.SolidBrush $dark), $x, $y + $h - 12 * $s, $w, 12 * $s)

  # 竖向集装箱筋
  $ribPen = New-Object System.Drawing.Pen $dark, (3 * $s)
  for ($i = 1; $i -lt 9; $i++) {
    $xx = $x + ($w / 9.0) * $i
    $g.DrawLine($ribPen, $xx, $y + 12 * $s, $xx, $y + $h - 14 * $s)
  }
  $edgePen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(88, 214, 222)), (2 * $s)
  $g.DrawRectangle($edgePen, $x, $y, $w, $h)

  # 角件
  $corner = New-Object System.Drawing.SolidBrush $dark
  $leftX = $x - (6 * $s)
  $rightX = $x + $w - (10 * $s)
  $topY = $y - (6 * $s)
  $botY = $y + $h - (14 * $s)
  $cw = 16 * $s
  $ch = 20 * $s
  foreach ($cxx in @($leftX, $rightX)) {
    foreach ($cyy in @($topY, $botY)) {
      $g.FillRectangle($corner, $cxx, $cyy, $cw, $ch)
    }
  }

  # 定位点
  $pin = [System.Drawing.Color]::FromArgb(47, 214, 165)
  $px = 392 * $s; $py = 336 * $s; $pr = 30 * $s
  $g.FillEllipse((New-Object System.Drawing.SolidBrush $pin), $px - $pr, $py - $pr, $pr * 2, $pr * 2)
  $g.FillEllipse((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(11, 15, 20))), $px - 13 * $s, $py - 13 * $s, 26 * $s, 26 * $s)
  $g.FillEllipse((New-Object System.Drawing.SolidBrush $pin), $px - 7 * $s, $py - 7 * $s, 14 * $s, 14 * $s)
  $tri = New-Object System.Drawing.Drawing2D.GraphicsPath
  $pts = New-Object 'System.Drawing.PointF[]' 3
  $pts[0] = New-Object System.Drawing.PointF ([single]($px - 17 * $s)), ([single]($py + 16 * $s))
  $pts[1] = New-Object System.Drawing.PointF ([single]($px + 17 * $s)), ([single]($py + 16 * $s))
  $pts[2] = New-Object System.Drawing.PointF ([single]$px), ([single]($py + 56 * $s))
  $tri.AddPolygon($pts)
  $g.FillPath((New-Object System.Drawing.SolidBrush $pin), $tri)

  $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
  Write-Host "icon -> $path"
}

New-Icon 192 (Join-Path $outDir "appicon.png")
New-Icon 512 (Join-Path $PSScriptRoot "..\icon-512.png")
