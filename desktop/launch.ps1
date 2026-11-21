$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$url = 'http://127.0.0.1:5000'
$logDir = Join-Path $root 'logs'

Add-Type @'
using System;
using System.Text;
using System.Threading;
using System.Runtime.InteropServices;

public static class JarvisWin {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr hWnd, int index);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);

    public static IntPtr Find(uint procId) {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr hWnd, IntPtr lParam) {
            uint pid;
            GetWindowThreadProcessId(hWnd, out pid);
            if (pid == procId && IsWindowVisible(hWnd)) {
                StringBuilder title = new StringBuilder(256);
                GetWindowText(hWnd, title, 256);
                if (title.ToString() == "J.A.R.V.I.S.") {
                    found = hWnd;
                    return false;
                }
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static bool IsFullscreen(IntPtr hWnd) {
        return (GetWindowLong(hWnd, -16) & 0x00C00000) == 0;
    }

    public static bool EnsureFullscreen(IntPtr hWnd) {
        ShowWindow(hWnd, 9);
        SetForegroundWindow(hWnd);
        Thread.Sleep(250);
        if (GetForegroundWindow() != hWnd) return false;
        if (IsFullscreen(hWnd)) return true;
        keybd_event(0x7A, 0, 0, UIntPtr.Zero);
        keybd_event(0x7A, 0, 2, UIntPtr.Zero);
        Thread.Sleep(900);
        return IsFullscreen(hWnd);
    }
}
'@

$existing = Get-Process chrome -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -eq 'J.A.R.V.I.S.' }
if ($existing) {
    foreach ($proc in $existing) {
        $handle = [JarvisWin]::Find([uint32]$proc.Id)
        if ($handle -ne [IntPtr]::Zero) {
            [void][JarvisWin]::EnsureFullscreen($handle)
            break
        }
    }
    exit 0
}

function Test-Jarvis {
    try {
        $request = [System.Net.HttpWebRequest]::Create("$url/api/status")
        $request.Proxy = $null
        $request.Timeout = 2000
        $request.ReadWriteTimeout = 2000
        $response = $request.GetResponse()
        $ok = [int]$response.StatusCode -lt 500
        $response.Close()
        return $ok
    } catch {
        return $false
    }
}

if (-not (Test-Jarvis)) {
    $server = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match '(^|[\s"])server\.py([\s"]|$)' } |
        Select-Object -First 1
    if (-not $server) {
        $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
        if (-not (Test-Path $python)) {
            $found = Get-Command python.exe -ErrorAction SilentlyContinue
            $python = if ($found) { $found.Source } else { 'python.exe' }
        }
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        $outLog = Join-Path $logDir 'desktop-app.out.log'
        $errLog = Join-Path $logDir 'desktop-app.err.log'
        Start-Process -FilePath $python `
            -ArgumentList (Join-Path $root 'server.py') `
            -WorkingDirectory $root `
            -WindowStyle Hidden `
            -RedirectStandardOutput $outLog `
            -RedirectStandardError $errLog
    }
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline -and -not (Test-Jarvis)) {
        Start-Sleep -Milliseconds 400
    }
    if (-not (Test-Jarvis)) {
        $hint = "J.A.R.V.I.S. could not start its server.`n`nCheck $errLog"
        (New-Object -ComObject WScript.Shell).Popup($hint, 20, 'J.A.R.V.I.S.', 48) | Out-Null
        exit 1
    }
}

$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $chrome) {
    foreach ($key in @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'
    )) {
        $item = Get-Item -Path $key -ErrorAction SilentlyContinue
        if ($item) {
            $value = $item.GetValue('')
            if ($value -and (Test-Path $value)) { $chrome = $value; break }
        }
    }
}

if (-not $chrome) {
    $hint = 'Google Chrome was not found. Install Chrome or edit launch.ps1 to point at another Chromium browser.'
    (New-Object -ComObject WScript.Shell).Popup($hint, 20, 'J.A.R.V.I.S.', 48) | Out-Null
    exit 1
}

$profileDir = Join-Path $env:LOCALAPPDATA 'JarvisDesktop\profile'
New-Item -ItemType Directory -Path $profileDir -Force | Out-Null

$appProc = Start-Process -FilePath $chrome -PassThru -ArgumentList @(
    "--app=$url/",
    '--start-fullscreen',
    "--user-data-dir=`"$profileDir`"",
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-mode',
    '--use-fake-ui-for-media-stream',
    '--disable-features=Translate,MediaRouter'
)

$handle = [IntPtr]::Zero
$deadline = (Get-Date).AddSeconds(25)
while ((Get-Date) -lt $deadline -and $handle -eq [IntPtr]::Zero) {
    Start-Sleep -Milliseconds 250
    if ($appProc) { $handle = [JarvisWin]::Find([uint32]$appProc.Id) }
}

if ($handle -ne [IntPtr]::Zero) {
    Start-Sleep -Milliseconds 1200
    $settle = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $settle) {
        if ([JarvisWin]::IsFullscreen($handle)) {
            Start-Sleep -Milliseconds 500
        } elseif (-not [JarvisWin]::EnsureFullscreen($handle)) {
            Start-Sleep -Milliseconds 500
        }
    }
}
