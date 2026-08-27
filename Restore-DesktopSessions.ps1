<#
.SYNOPSIS
  Rebuild the Claude Desktop "Code" session index from CLI transcripts.

.DESCRIPTION
  Claude Desktop lists Code sessions from per-session pointer records at
      <index-root>/<accountUuid>/<orgUuid>/local_<uuid>.json
  Each record points at a CLI transcript through its "cliSessionId" field. The
  app only writes records for sessions IT creates, so CLI sessions -- and any
  session tree migrated from another machine -- stay invisible in the picker
  even though every transcript is intact on disk.

  This script regenerates the missing records.

  Unlike other tools in this space it does NOT hardcode the record schema. The
  record's field set is conditional on what a session did -- worktree fields
  only for worktree sessions, pr* only where a PR was opened, enabledMcpTools
  only where remote MCP servers were configured -- so no single record is a
  valid template. This reads every record the app wrote on THIS machine, keeps
  the fields present in nearly all of them (the structural core), and takes
  values from the most recent one. Fields added by an app update are carried
  through automatically; per-session state is left behind.

.PARAMETER Apply
  Actually write records. Without it the script runs read-only (dry run).

.PARAMETER CwdPrefix
  Only index transcripts whose recorded cwd starts with this string. Useful
  after a migration where only some paths are valid on this machine.

.PARAMETER Limit
  Index at most N sessions, most-recently-active first. Good for a first run.

.PARAMETER IndexDir
  Override index auto-detection. Point at the folder CONTAINING the
  <accountUuid>/<orgUuid> pair.

.PARAMETER Account
  Write into this account's folder instead of the one with the most records.
  Records only appear in the picker for the account the app is signed in as.

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
  [string] $Account,
  [string] $ProjectsRoot,
  [switch] $IncludeDeleted,
  [int]    $MinIdleMinutes = 2,
  [double] $CoreThreshold = 0.9,
  [switch] $NoBackup
)

$ErrorActionPreference = 'Stop'

# No BOM: the app's JSON parser rejects one outright.
$UTF8 = New-Object System.Text.UTF8Encoding($false)

# Fields derived per session. Everything else is inherited from the structural
# core -- that inheritance is what makes this survive app updates.
$DerivedFields = @(
  'sessionId', 'cliSessionId', 'cwd', 'originCwd',
  'createdAt', 'lastActivityAt', 'lastFocusedAt',
  'title', 'titleSource', 'completedTurns'
)

# Fields that are dangerous rather than merely wrong to inherit. The presence
# threshold already excludes these on a machine with many app-written records;
# this is the backstop for a machine that has only one, where conditionality is
# invisible. transcriptUnavailable is the worst of them: inherit it and every
# restored session is marked broken on arrival.
$NeverInherit = @(
  'transcriptUnavailable', 'error', 'errorAt',
  'forkedFromSessionId', 'spawnedFrom', 'dispatchParentOrigin',
  'prNumber', 'prUrl', 'prRepository', 'prState', 'prs',
  'branch', 'sourceBranch', 'writtenBranches',
  'worktreeName', 'worktreePath',
  'promptSuggestion', 'chromeTabGroupId', 'color',
  'enabledMcpTools'
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

function Get-AccountSignals {
  # Which account authored the transcripts, and which one is the app showing?
  # Two different questions, two different files, and they can disagree --
  # which is exactly the confusing case this warns about.
  param([string]$IndexRoot)

  $cliAccount = $null; $appAccount = $null
  $claudeJson = Join-Path $HOME '.claude.json'
  if (Test-Path $claudeJson) {
    try {
      $oa = (Get-Content $claudeJson -Raw -Encoding UTF8 | ConvertFrom-Json).oauthAccount
      if ($oa) { $cliAccount = $oa.accountUuid }
    } catch { }
  }
  $cfg = Join-Path (Split-Path $IndexRoot -Parent) 'config.json'
  if (Test-Path $cfg) {
    try { $appAccount = (Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json).lastKnownAccountUuid } catch { }
  }
  [pscustomobject]@{ CliAccount = $cliAccount; AppAccount = $appAccount }
}

function Write-AccountMismatch {
  # Two failure modes that look like bugs but are account scoping.
  param([string]$Account, $Signals)

  if ($Signals.AppAccount -and $Signals.AppAccount -ne $Account) {
    Write-Warn 'The Desktop app is signed in as a DIFFERENT account:'
    Write-Host "      writing into : $Account"
    Write-Host "      app shows    : $($Signals.AppAccount)"
    Write-Host '      Records written here are correct but will not appear in the'
    Write-Host '      picker until you sign in as the first account. Use -Account'
    Write-Host '      to target the signed-in one instead.'
  }
  if ($Signals.CliAccount -and $Signals.CliAccount -ne $Account) {
    Write-Warn 'The transcripts were authored by a DIFFERENT account:'
    Write-Host "      writing into : $Account"
    Write-Host "      authored by  : $($Signals.CliAccount)"
    Write-Host '      Conversation history will restore in full -- it is read from'
    Write-Host '      the local transcript. Artifacts published in these sessions'
    Write-Host '      are server-side and account-scoped, so they will show as'
    Write-Host '      unavailable. Nothing on disk can change that.'
  }
}

function Resolve-AccountDir {
  param([string]$IndexRoot, [string]$WantAccount)

  # Layout: <root>/<accountUuid>/<orgUuid>/local_*.json -- account FIRST.
  # Confirmed on macOS three ways: ~/.claude.json oauthAccount, config.json
  # lastKnownAccountUuid, and the app's own telemetry blobs. See SCHEMA.md.
  $pairs = @()
  Get-ChildItem $IndexRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $account = $_
    Get-ChildItem $account.FullName -Directory -ErrorAction SilentlyContinue | ForEach-Object {
      $pairs += [pscustomobject]@{
        Path    = $_.FullName
        Account = $account.Name
        Org     = $_.Name
        Records = @(Get-ChildItem $_.FullName -Filter 'local_*.json' -File -ErrorAction SilentlyContinue).Count
      }
    }
  }

  if (-not $pairs) {
    throw "No <accountUuid>/<orgUuid> folder pair under $IndexRoot. Start a session in the app first."
  }
  if ($WantAccount) {
    $match = @($pairs | Where-Object { $_.Account -eq $WantAccount })
    if (-not $match) {
      throw ("No folder for account $WantAccount under $IndexRoot." + [Environment]::NewLine +
             "Found: " + (($pairs | Select-Object -ExpandProperty Account -Unique) -join ', '))
    }
    return ($match | Sort-Object Records -Descending | Select-Object -First 1)
  }
  if ($pairs.Count -gt 1) {
    Write-Warn 'Multiple account/org folders found; using the one with the most records:'
    $pairs | ForEach-Object { Write-Host "      $($_.Account)\$($_.Org)  ($($_.Records) records)" }
  }
  return ($pairs | Sort-Object Records -Descending | Select-Object -First 1)
}

# ----------------------------------------------------------- transcript parse

# A worktree session runs in <repo>/.claude/worktrees/<name>; the app records the
# repo root as originCwd and the worktree as cwd. 39 of 69 observed records have
# originCwd != cwd, almost all of them this shape. Deriving originCwd = cwd took
# this from 59/62 correct down to 23/62.
function Get-OriginCwd {
  param([string]$Cwd)
  if ($Cwd -match '^(.*)[\\/]\.claude[\\/]worktrees[\\/][^\\/]+') { return $Matches[1] }
  return $Cwd
}

function Read-Transcript {
  param([string]$Path, [string]$Stem)

  $firstTs = $null; $lastTs = $null; $cwd = $null
  $ownFirstTs = $null; $ownCwd = $null
  $customTitle = $null; $aiTitle = $null; $firstUserMsg = $null
  $turns = 0; $hasMainChain = $false

  # Explicit UTF8: PowerShell 5.1's Get-Content defaults to the ANSI codepage
  # and will silently mojibake any non-ASCII title.
  foreach ($line in [System.IO.File]::ReadLines($Path, [System.Text.Encoding]::UTF8)) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    try { $o = $line | ConvertFrom-Json } catch { continue }

    if ($o.PSObject.Properties.Name -contains 'isSidechain' -and $o.isSidechain -eq $false) { $hasMainChain = $true }
    if ($o.type -eq 'custom-title' -and $o.customTitle) { $customTitle = $o.customTitle }
    if ($o.type -eq 'ai-title'     -and $o.aiTitle)     { $aiTitle     = $o.aiTitle }

    # A resumed session's transcript carries the parent's lines forward. They
    # keep the parent's sessionId and the app never counted them -- scoping to
    # our own lines is what keeps createdAt off the parent's start date (one
    # observed session was 19 days out) and completedTurns off its turn count.
    $own = ($o.sessionId -eq $Stem)

    if ($o.timestamp) {
      if (-not $firstTs) { $firstTs = $o.timestamp }
      $lastTs = $o.timestamp
      if ($own -and -not $ownFirstTs) { $ownFirstTs = $o.timestamp }
    }
    if ($o.cwd) {
      if (-not $cwd) { $cwd = $o.cwd }
      if ($own -and -not $ownCwd) { $ownCwd = $o.cwd }
    }

    if ($o.type -ne 'user') { continue }
    if ($o.isSidechain) { continue }

    # tool_result lines are the harness feeding results back, not turns.
    $c = $o.message.content
    $isToolResult = $false
    if ($c -isnot [string] -and $c) {
      foreach ($b in $c) { if ($b.type -eq 'tool_result') { $isToolResult = $true; break } }
    }
    if ($isToolResult) { continue }

    $txt = $null
    if ($c -is [string]) { $txt = $c }
    elseif ($c) { foreach ($b in $c) { if ($b.type -eq 'text' -and $b.text) { $txt = $b.text; break } } }
    $t = if ($txt) { $txt.Trim() } else { '' }

    # completedTurns == human turns belonging to THIS session. isMeta lines are
    # harness bookkeeping; "[Request interrupted...]" is a synthetic user line.
    # Slash-command scaffolding DOES count as a turn -- excluding it drops the
    # exact-match rate from 41/61 to 33/61. See SCHEMA.md.
    if ($own -and -not $o.isMeta -and -not $t.StartsWith('[Request interrupted')) {
      $turns++
    }

    if (-not $firstUserMsg -and $t) {
      # Skip harness scaffolding so the title is the human's actual words.
      if ($t -notmatch '^<(local-command|command-name|command-message|command-args|system-reminder|user-prompt-submit)' -and
          $t -notmatch '^Caveat: The messages below were generated' -and
          $t -notmatch '^\[Request interrupted') {
        $firstUserMsg = $t
      }
    }
  }

  $useCwd = if ($ownCwd) { $ownCwd } else { $cwd }
  [pscustomobject]@{
    FirstTs      = if ($ownFirstTs) { $ownFirstTs } else { $firstTs }
    LastTs       = $lastTs
    Cwd          = $useCwd
    OriginCwd    = (Get-OriginCwd $useCwd)
    CustomTitle  = $customTitle
    AiTitle      = $aiTitle
    FirstUserMsg = $firstUserMsg
    Turns        = $turns
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
  #
  # titleSource is the app's own enum and takes only 'user' or 'auto' -- across
  # 69 observed records, all 24 with titleSource 'user' have a title identical
  # to the transcript's custom-title. 'custom' is a value the app never writes.
  if ($Parsed.CustomTitle) { return @{ Title = $Parsed.CustomTitle; Source = 'user' } }
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
$acct      = Resolve-AccountDir -IndexRoot $indexRoot -WantAccount $Account
Write-Ok "index:   $($acct.Path)"
Write-Ok "account=$($acct.Account)  org=$($acct.Org)"

Write-AccountMismatch -Account $acct.Account -Signals (Get-AccountSignals -IndexRoot $indexRoot)

$manifestPath = Join-Path $acct.Path '.restore-manifest.json'
$authored = @{}
if (Test-Path $manifestPath) {
  try {
    (Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json) | ForEach-Object { $authored[$_] = $true }
  } catch { }
}

Write-Step 'Modelling the schema on records the app wrote here'
$existing   = @{}
$appWritten = @()
foreach ($f in Get-ChildItem $acct.Path -Filter 'local_*.json' -File -ErrorAction SilentlyContinue) {
  try { $r = Get-Content $f.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { continue }
  if ($r.cliSessionId) { $existing[$r.cliSessionId] = $f.Name }
  if (-not $authored.ContainsKey($r.sessionId)) { $appWritten += $r }
}

if (-not $appWritten) {
  throw ("No app-written record to model the schema on." + [Environment]::NewLine + [Environment]::NewLine +
         "Open Claude Desktop, start a Code session in a real folder, send one message," + [Environment]::NewLine +
         "let it finish, quit, then re-run. This tool deliberately refuses to invent a" + [Environment]::NewLine +
         "schema: the format is undocumented and the field set varies per session.")
}

# The field set is CONDITIONAL on what a session did: worktree fields only for
# worktree sessions, pr* only where a PR was opened, enabledMcpTools only where
# remote MCP servers were configured. Cloning any single record therefore stamps
# its circumstances onto every restored session -- the richest record on the
# machine this was validated against carries prNumber 109, a worktree path and a
# stale promptSuggestion. Keep only what is near-universal.
#
# With one app-written record the threshold degenerates to "every field in that
# record", which is the old behaviour. It gets strictly better as the app writes
# more records.
$presence = @{}
foreach ($r in $appWritten) {
  foreach ($name in $r.PSObject.Properties.Name) {
    if ($presence.ContainsKey($name)) { $presence[$name]++ } else { $presence[$name] = 1 }
  }
}
$n = $appWritten.Count
$keep = @($presence.Keys | Where-Object {
  ($presence[$_] / $n) -ge $CoreThreshold -and $NeverInherit -notcontains $_
})

# Most recently active first, so "latest value" means what it says.
$ordered = @($appWritten | Sort-Object @{ Expression = {
  if ($_.lastActivityAt) { [int64]$_.lastActivityAt } else { [int64]0 } } } -Descending)

$core = [ordered]@{}
foreach ($prop in $ordered[0].PSObject.Properties) {
  if ($keep -contains $prop.Name) { $core[$prop.Name] = $prop.Value }
}
foreach ($r in $ordered) {
  foreach ($prop in $r.PSObject.Properties) {
    if (($keep -contains $prop.Name) -and -not $core.Contains($prop.Name)) { $core[$prop.Name] = $prop.Value }
  }
}

Write-Ok "$n app-written record(s); $($presence.Count) distinct field(s) seen"
Write-Ok "structural core: $($core.Count) field(s) present in >=$([int]($CoreThreshold*100))% of them"
$dropped = @($presence.Keys | Where-Object { -not $core.Contains($_) -and $DerivedFields -notcontains $_ } | Sort-Object)
if ($dropped) {
  Write-Host "      conditional/per-session, not inherited: $($dropped -join ', ')" -ForegroundColor DarkGray
}
$inherited = @($core.Keys | Where-Object { $DerivedFields -notcontains $_ -and -not $ResetFields.Contains($_) })
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

  $p = Read-Transcript -Path $f.FullName -Stem $stem
  if (-not $p.HasMainChain) { Add-Skip 'no main chain (sidechain-only)'; continue }
  if (-not $p.Cwd)          { Add-Skip 'no cwd recorded';                continue }
  if (-not $p.FirstTs)      { Add-Skip 'no timestamps';                  continue }
  if ($CwdPrefix -and -not $p.Cwd.StartsWith($CwdPrefix)) { Add-Skip 'cwd outside -CwdPrefix'; continue }

  $t = Get-SessionTitle -Parsed $p

  # Start from the structural core, then override. Field order follows the most
  # recent app-written record so the result is shaped like something the app wrote.
  $rec = [ordered]@{}
  foreach ($k in $core.Keys) {
    if ($ResetFields.Contains($k)) { $rec[$k] = $ResetFields[$k] }
    else                           { $rec[$k] = $core[$k] }
  }
  $rec['sessionId']      = 'local_' + [guid]::NewGuid().ToString()
  $rec['cliSessionId']   = $stem
  $rec['cwd']            = $p.Cwd
  $rec['originCwd']      = $p.OriginCwd
  $rec['createdAt']      = (ConvertTo-EpochMs $p.FirstTs)
  $rec['lastActivityAt'] = (ConvertTo-EpochMs $p.LastTs)
  $rec['lastFocusedAt']  = (ConvertTo-EpochMs $p.LastTs)
  $rec['title']          = $t.Title
  $rec['titleSource']    = $t.Source
  $rec['completedTurns'] = $p.Turns

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
