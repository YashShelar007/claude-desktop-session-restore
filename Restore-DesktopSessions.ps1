<#
.SYNOPSIS
  Rebuild the Claude Desktop "Code" session index from CLI transcripts.

.DESCRIPTION
  Claude Desktop lists Code sessions from per-session pointer records at
      <index-root>/<orgUuid>/<accountUuid>/local_<uuid>.json
  Each record points at a CLI transcript through its "cliSessionId" field. The
  app only writes records for sessions IT creates, so CLI sessions -- and any
  session tree migrated from another machine -- stay invisible in the picker
  even though every transcript is intact on disk.

  This script regenerates the missing records.

  Unlike other tools in this space it does NOT hardcode the record schema. It
  clones a record the app itself wrote on THIS machine and overrides only the
  fields it can derive, so fields added by an app update are carried through
  verbatim instead of being silently dropped.

.PARAMETER Apply
  Actually write records. Without it the script runs read-only (dry run).

.PARAMETER CwdPrefix
  Only index transcripts whose recorded cwd starts with this string. Useful
  after a migration where only some paths are valid on this machine.

.PARAMETER Limit
  Index at most N sessions, most-recently-active first. Good for a first run.

.PARAMETER IndexDir
  Override index auto-detection. Point at the folder CONTAINING the
  <orgUuid>/<accountUuid> pair.

.PARAMETER ProjectsRoot
  Override the transcript root (default: ~/.claude/projects).

.PARAMETER IncludeDeleted
  Re-index sessions the user deleted in the app UI. Off by default: the app
  records deletions as tombstone files and honouring them stops this tool from
  resurrecting sessions somebody deliberately removed.

.PARAMETER MinIdleMinutes
  Skip transcripts modified within this many minutes, on the assumption they
  belong to a session that is still running (default 2). A live session is
  owned by the process writing it.

.PARAMETER NoBackup
  Skip the pre-write backup. Not recommended.

.EXAMPLE
  .\Restore-DesktopSessions.ps1
  Dry run -- report what would be indexed, write nothing.

.EXAMPLE
  .\Restore-DesktopSessions.ps1 -Limit 5 -Apply
  Write the 5 most recent, confirm they open in the app, then re-run without -Limit.

.NOTES
  The on-disk format is undocumented and version-unstable. Everything here was
  established by observation against a live install. See SCHEMA.md.
#>
[CmdletBinding()]
param(
  [switch] $Apply,
  [string] $CwdPrefix,
  [int]    $Limit = 0,
  [string] $IndexDir,
  [string] $ProjectsRoot,
  [switch] $IncludeDeleted,
  [int]    $MinIdleMinutes = 2,
  [switch] $NoBackup
)

$ErrorActionPreference = 'Stop'

# No BOM: the app's JSON parser rejects one outright.
$UTF8 = New-Object System.Text.UTF8Encoding($false)

# Fields derived per session. Everything else is inherited from the reference
# record -- that inheritance is what makes this survive app updates.
$DerivedFields = @(
  'sessionId', 'cliSessionId', 'cwd', 'originCwd',
  'createdAt', 'lastActivityAt', 'lastFocusedAt',
  'title', 'titleSource', 'completedTurns'
)

# Per-session runtime state that must NOT be inherited. Reset only if the
# reference record actually carries the field.
$ResetFields = [ordered]@{
  remoteMcpServersConfig   = @()
  alwaysAllowedReasons     = @()
  sessionPermissionUpdates = @()
  spawnSeed                = @{}
  isArchived               = $false
}

function Write-Step { param($m) Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Warn { param($m) Write-Host "  ! $m"  -ForegroundColor Yellow }
function Write-Ok   { param($m) Write-Host "  + $m"  -ForegroundColor Green }

# --------------------------------------------------------------- path probing

function Resolve-IndexRoot {
  param([string]$Override)

  if ($Override) {
    if (-not (Test-Path $Override)) { throw "IndexDir not found: $Override" }
    return (Resolve-Path $Override).Path
  }

  $candidates = @()

  # Windows Store / MSIX install: the app is packaged, so its %APPDATA% writes
  # are redirected into the package container. Probe this BEFORE the plain path.
  if ($env:LOCALAPPDATA) {
    $pkgRoot = Join-Path $env:LOCALAPPDATA 'Packages'
    if (Test-Path $pkgRoot) {
      Get-ChildItem $pkgRoot -Filter 'Claude_*' -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $candidates += (Join-Path $_.FullName 'LocalCache\Roaming\Claude\claude-code-sessions')
      }
    }
  }
  if ($env:APPDATA) { $candidates += (Join-Path $env:APPDATA 'Claude\claude-code-sessions') }
  if ($env:XDG_CONFIG_HOME) { $candidates += (Join-Path $env:XDG_CONFIG_HOME 'Claude/claude-code-sessions') }
  if ($env:HOME) {
    $candidates += (Join-Path $env:HOME 'Library/Application Support/Claude/claude-code-sessions')
    $candidates += (Join-Path $env:HOME '.config/Claude/claude-code-sessions')
  }

  $found = @($candidates | Where-Object { $_ -and (Test-Path $_) })
  if (-not $found) {
    throw ("No Claude Desktop session index found. Probed:" + [Environment]::NewLine +
           ($candidates -join [Environment]::NewLine) + [Environment]::NewLine + [Environment]::NewLine +
           "If the app has never run here, open it, start one Code session, send a message," + [Environment]::NewLine +
           "let it finish, then re-run. This script needs one app-written record to copy.")
  }
  return $found[0]
}

function Resolve-AccountDir {
  param([string]$IndexRoot)

  # Layout: <root>/<orgUuid>/<accountUuid>/local_*.json
  $pairs = @()
  Get-ChildItem $IndexRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $org = $_
    Get-ChildItem $org.FullName -Directory -ErrorAction SilentlyContinue | ForEach-Object {
      $pairs += [pscustomobject]@{
        Path    = $_.FullName
        Org     = $org.Name
        Account = $_.Name
        Records = @(Get-ChildItem $_.FullName -Filter 'local_*.json' -File -ErrorAction SilentlyContinue).Count
      }
    }
  }

  if (-not $pairs) {
    throw "No <orgUuid>/<accountUuid> folder pair under $IndexRoot. Start a session in the app first."
  }
  if ($pairs.Count -gt 1) {
    Write-Warn 'Multiple account folders found; using the one with the most records:'
    $pairs | ForEach-Object { Write-Host "      $($_.Org)\$($_.Account)  ($($_.Records) records)" }
  }
  return ($pairs | Sort-Object Records -Descending | Select-Object -First 1)
}

# ----------------------------------------------------------- transcript parse

function Read-Transcript {
  param([string]$Path)

  $firstTs = $null; $lastTs = $null; $cwd = $null
  $customTitle = $null; $aiTitle = $null; $firstUserMsg = $null
  $userCount = 0; $hasMainChain = $false

  # Explicit UTF8: PowerShell 5.1's Get-Content defaults to the ANSI codepage
  # and will silently mojibake any non-ASCII title.
  foreach ($line in [System.IO.File]::ReadLines($Path, [System.Text.Encoding]::UTF8)) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    try { $o = $line | ConvertFrom-Json } catch { continue }

    if ($o.PSObject.Properties.Name -contains 'isSidechain' -and $o.isSidechain -eq $false) { $hasMainChain = $true }
    if ($o.type -eq 'custom-title' -and $o.customTitle) { $customTitle = $o.customTitle }
    if ($o.type -eq 'ai-title'     -and $o.aiTitle)     { $aiTitle     = $o.aiTitle }
    if ($o.timestamp) {
      if (-not $firstTs) { $firstTs = $o.timestamp }
      $lastTs = $o.timestamp
    }
    if (-not $cwd -and $o.cwd) { $cwd = $o.cwd }

    if ($o.type -eq 'user') {
      $userCount++
      if (-not $firstUserMsg -and $o.isSidechain -eq $false) {
        $c = $o.message.content
        $txt = $null
        if ($c -is [string]) { $txt = $c }
        elseif ($c) { foreach ($b in $c) { if ($b.type -eq 'text' -and $b.text) { $txt = $b.text; break } } }
        if ($txt) {
          $t = $txt.Trim()
          # Skip harness scaffolding so the title is the human's actual words.
          if ($t -notmatch '^<(local-command|command-name|command-message|command-args|system-reminder|user-prompt-submit)' -and
              $t -notmatch '^Caveat: The messages below were generated' -and
              $t -notmatch '^\[Request interrupted') {
            $firstUserMsg = $t
          }
        }
      }
    }
  }

  [pscustomobject]@{
    FirstTs      = $firstTs
    LastTs       = $lastTs
    Cwd          = $cwd
    CustomTitle  = $customTitle
    AiTitle      = $aiTitle
    FirstUserMsg = $firstUserMsg
    UserCount    = $userCount
    HasMainChain = $hasMainChain
  }
}

function ConvertTo-EpochMs {
  param([string]$Iso)
  if (-not $Iso) { return $null }
  ([DateTimeOffset]::Parse(
      $Iso,
      [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal
  )).ToUnixTimeMilliseconds()
}

function Get-SessionTitle {
  param($Parsed)
  # custom-title (the user's own /rename) beats the model's ai-title, which
  # beats raw first-message text. The app's own /desktop import drops
  # customTitle entirely -- see anthropics/claude-code#83051.
  if ($Parsed.CustomTitle) { return @{ Title = $Parsed.CustomTitle; Source = 'custom' } }
  if ($Parsed.AiTitle)     { return @{ Title = $Parsed.AiTitle;     Source = 'auto'   } }
  if ($Parsed.FirstUserMsg) {
    $s = ($Parsed.FirstUserMsg -replace '\s+', ' ').Trim()
    if ($s.Length -gt 60) { $s = $s.Substring(0, 60).TrimEnd() }
    return @{ Title = $s; Source = 'auto' }
  }
  return @{ Title = 'Untitled session'; Source = 'auto' }
}

# ----------------------------------------------------------------------- main

Write-Step 'Locating the Claude Desktop session index'
$indexRoot = Resolve-IndexRoot -Override $IndexDir
$acct      = Resolve-AccountDir -IndexRoot $indexRoot
Write-Ok "index:   $($acct.Path)"
Write-Ok "org=$($acct.Org)  account=$($acct.Account)"

$manifestPath = Join-Path $acct.Path '.restore-manifest.json'
$authored = @{}
if (Test-Path $manifestPath) {
  try {
    (Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json) | ForEach-Object { $authored[$_] = $true }
  } catch { }
}

Write-Step 'Selecting a reference record written by the app itself'
$existing      = @{}
$refCandidates = @()
foreach ($f in Get-ChildItem $acct.Path -Filter 'local_*.json' -File -ErrorAction SilentlyContinue) {
  try { $r = Get-Content $f.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { continue }
  if ($r.cliSessionId) { $existing[$r.cliSessionId] = $f.Name }
  if (-not $authored.ContainsKey($r.sessionId)) { $refCandidates += $r }
}

if (-not $refCandidates) {
  throw ("No app-written record to model the schema on." + [Environment]::NewLine + [Environment]::NewLine +
         "Open Claude Desktop, start a Code session in a real folder, send one message," + [Environment]::NewLine +
         "let it finish, quit, then re-run. This tool deliberately refuses to invent a" + [Environment]::NewLine +
         "schema: the format is undocumented and changes between app versions.")
}

# Richest record wins -- most likely to carry the full current field set.
$ref = $refCandidates |
  Sort-Object @{ Expression = { @($_.PSObject.Properties).Count } } -Descending |
  Select-Object -First 1

$refFields = @($ref.PSObject.Properties.Name)
Write-Ok "reference carries $($refFields.Count) fields"
$inherited = @($refFields | Where-Object { $DerivedFields -notcontains $_ -and -not $ResetFields.Contains($_) })
if ($inherited) {
  Write-Host "      inheriting verbatim: $($inherited -join ', ')" -ForegroundColor DarkGray
}

# Deleting a session in the UI leaves a tombstone PAIR in the index folder:
# "deleted_<desktop sessionId>" and "deleted_<cliSessionId>", each containing a
# deletion epoch-ms. Without honouring these, a restore run silently resurrects
# every session the user has ever deleted.
$tombstoned = @{}
foreach ($t in Get-ChildItem $acct.Path -Filter 'deleted_*' -File -ErrorAction SilentlyContinue) {
  $tombstoned[$t.Name.Substring(8)] = $true
}
if ($tombstoned.Count) {
  Write-Ok "$($tombstoned.Count) tombstone(s) found; deleted sessions will be left alone"
}

Write-Step 'Scanning transcripts'
if (-not $ProjectsRoot) { $ProjectsRoot = Join-Path $HOME '.claude\projects' }
if (-not (Test-Path $ProjectsRoot)) { throw "Transcript root not found: $ProjectsRoot" }

# Only <project>/<uuid>.jsonl is a real session. Anything deeper is a subagent
# transcript or a workflow journal; indexing those floods the picker.
$files = Get-ChildItem $ProjectsRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  Get-ChildItem $_.FullName -Filter '*.jsonl' -File -ErrorAction SilentlyContinue
}

$records = @()
$skips   = @{}
function Add-Skip {
  param($Reason)
  if ($skips.ContainsKey($Reason)) { $skips[$Reason]++ } else { $skips[$Reason] = 1 }
}

foreach ($f in $files) {
  $stem = [IO.Path]::GetFileNameWithoutExtension($f.Name)

  if ($f.Name -like 'agent-*')      { Add-Skip 'subagent transcript (agent-*)'; continue }
  if ($existing.ContainsKey($stem)) { Add-Skip 'already indexed';               continue }
  if (-not $IncludeDeleted -and $tombstoned.ContainsKey($stem)) {
    Add-Skip 'deleted in the app (tombstoned)'; continue
  }
  if ($MinIdleMinutes -gt 0 -and $f.LastWriteTime -gt (Get-Date).AddMinutes(-$MinIdleMinutes)) {
    Add-Skip "still active (modified < $MinIdleMinutes min ago)"; continue
  }

  $p = Read-Transcript -Path $f.FullName
  if (-not $p.HasMainChain) { Add-Skip 'no main chain (sidechain-only)'; continue }
  if (-not $p.Cwd)          { Add-Skip 'no cwd recorded';                continue }
  if (-not $p.FirstTs)      { Add-Skip 'no timestamps';                  continue }
  if ($CwdPrefix -and -not $p.Cwd.StartsWith($CwdPrefix)) { Add-Skip 'cwd outside -CwdPrefix'; continue }

  $t = Get-SessionTitle -Parsed $p

  # Clone the reference, then override. Field order follows the reference so the
  # result is shaped like something the app wrote.
  $rec = [ordered]@{}
  foreach ($prop in $ref.PSObject.Properties) {
    if ($ResetFields.Contains($prop.Name)) { $rec[$prop.Name] = $ResetFields[$prop.Name] }
    else                                   { $rec[$prop.Name] = $prop.Value }
  }
  $rec['sessionId']      = 'local_' + [guid]::NewGuid().ToString()
  $rec['cliSessionId']   = $stem
  $rec['cwd']            = $p.Cwd
  $rec['originCwd']      = $p.Cwd
  $rec['createdAt']      = (ConvertTo-EpochMs $p.FirstTs)
  $rec['lastActivityAt'] = (ConvertTo-EpochMs $p.LastTs)
  $rec['lastFocusedAt']  = (ConvertTo-EpochMs $p.LastTs)
  $rec['title']          = $t.Title
  $rec['titleSource']    = $t.Source
  $rec['completedTurns'] = $p.UserCount

  $records += , [pscustomobject]$rec
}

$records = @($records) | Sort-Object -Property @{ Expression = { [int64]$_.lastActivityAt } } -Descending
if ($Limit -gt 0) { $records = @($records) | Select-Object -First $Limit }

Write-Host ''
Write-Host ('{0,-3} {1,-17} {2,-46} {3}' -f '#', 'LAST ACTIVE', 'TITLE', 'CWD')
$i = 0
foreach ($r in $records) {
  $i++
  $when = [DateTimeOffset]::FromUnixTimeMilliseconds($r.lastActivityAt).ToLocalTime().ToString('yyyy-MM-dd HH:mm')
  $ttl  = if ($r.title.Length -gt 44) { $r.title.Substring(0, 44) } else { $r.title }
  Write-Host ('{0,-3} {1,-17} {2,-46} {3}' -f $i, $when, $ttl, $r.cwd)
}
Write-Host ''

if ($skips.Count) {
  Write-Step 'Skipped'
  $skips.GetEnumerator() | Sort-Object Value -Descending | ForEach-Object {
    Write-Host ('  {0,5}  {1}' -f $_.Value, $_.Key)
  }
  Write-Host ''
}

if (-not $Apply) {
  Write-Warn "DRY RUN -- $($records.Count) record(s) would be written. Re-run with -Apply."
  return
}
if (-not $records) { Write-Ok 'Nothing to do.'; return }

if (-not $NoBackup) {
  Write-Step 'Backing up the index'
  $stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
  $backup = Join-Path (Split-Path $indexRoot -Parent) "claude-code-sessions_backup_$stamp"
  Copy-Item $indexRoot $backup -Recurse
  Write-Ok "backup: $backup"
  Write-Host "      restore: Remove-Item '$indexRoot' -Recurse -Force; Copy-Item '$backup' '$indexRoot' -Recurse" -ForegroundColor DarkGray
}

Write-Step "Writing $($records.Count) record(s)"
foreach ($r in $records) {
  $path = Join-Path $acct.Path ($r.sessionId + '.json')
  [IO.File]::WriteAllText($path, ($r | ConvertTo-Json -Depth 20 -Compress), $UTF8)
  $authored[$r.sessionId] = $true
}
[IO.File]::WriteAllText($manifestPath, (@($authored.Keys) | ConvertTo-Json -Depth 3), $UTF8)

Write-Ok 'Done. Restart Claude Desktop and open the Code session picker.'
