from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable

from .msaa import download_latest_msaa, inspect_windows_msaa


@dataclass(frozen=True)
class ChatAccessibilitySnapshot:
    hwnd: int
    available: bool
    in_progress: bool
    composer_state: str
    error_text: str
    response_fingerprint: str
    response_text_length: int
    download_count: int
    backend: str = ""
    composer_name: str = ""
    document_name: str = ""
    document_value: str = ""
    document_key: str = ""
    has_assistant_response: bool = False
    worked_for_text: str = ""
    latest_response_complete: bool = False


def _powershell_executable() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if executable:
        return executable
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(candidate):
        return candidate
    raise FileNotFoundError("Windows PowerShell is required for browser accessibility monitoring.")


def _encoded_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


_SCAN_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$handles = @()
if ($env:PRM_UIA_HWNDS) {
    foreach ($part in $env:PRM_UIA_HWNDS.Split(',')) {
        $value = 0L
        if ([Int64]::TryParse($part, [ref]$value) -and $value -gt 0) { $handles += $value }
    }
}

function Get-AccessibleName($element) {
    $name = ''
    try { $name = [string]$element.Current.Name } catch {}
    if (-not [string]::IsNullOrWhiteSpace($name)) { return $name.Trim() }
    try {
        $legacy = $element.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
        if ($null -ne $legacy) {
            $name = [string]$legacy.Current.Name
            if (-not [string]::IsNullOrWhiteSpace($name)) { return $name.Trim() }
        }
    } catch {}
    return ''
}

function Is-LiveElement($element) {
    try {
        if (-not $element.Current.IsEnabled) { return $false }
        if ($element.Current.IsOffscreen) { return $false }
    } catch {}
    return $true
}

$results = @()
foreach ($rawHandle in $handles) {
    try {
        $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$rawHandle)
        if ($null -eq $root) { throw 'No UI Automation root for window.' }

        # ChatGPT keeps the DOM id composer-submit-button for BOTH states.
        # Do not infer state from that id/class. The accessible NAME is the
        # authoritative signal supplied by Firefox:
        #   Stop answering -> generating
        #   Send prompt    -> idle / completed
        # Start Voice is a separate control and is intentionally ignored.
        $all = $root.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Automation]::ControlViewCondition
        )
        $rows = New-Object System.Collections.Generic.List[object]
        $stopFound = $false
        $sendFound = $false
        $voiceFound = $false
        $errorText = ''
        $assistantStart = -1
        $workedFor = ''
        $responseActionsFound = $false
        $copyResponseFound = $false
        $responseVoiceFound = $false
        $limit = [Math]::Min($all.Count, 8000)

        for ($i = 0; $i -lt $limit; $i++) {
            $el = $all.Item($i)
            $name = Get-AccessibleName $el
            if ([string]::IsNullOrWhiteSpace($name)) { continue }
            $type = ''
            try { $type = [string]$el.Current.ControlType.ProgrammaticName } catch {}
            $lower = $name.ToLowerInvariant()
            $live = Is-LiveElement $el

            if ($live -and $lower -in @('stop answering','stop generating','stop response','stop generating response')) {
                $stopFound = $true
            }
            if ($live -and $lower -in @('send prompt','send message','submit prompt')) {
                $sendFound = $true
            }
            if ($live -and $lower -eq 'start voice') {
                $voiceFound = $true
            }

            if (-not $errorText -and $lower -match '(message delivery timed out\.?\s*please try again\.?|there was an error|something went wrong|network error|error generating|failed to generate|try again later|unable to load|rate limit|you.ve reached)') {
                $errorText = $name
            }
            if ($lower -match '^(chatgpt said:?|assistant said:?|assistant response:?|chatgpt response:?)$') {
                $assistantStart = $i
                $workedFor = ''
                $responseActionsFound = $false
                $copyResponseFound = $false
                $responseVoiceFound = $false
            } elseif ($assistantStart -ge 0 -and $lower -match '^worked for\s+(.+)$') {
                $workedFor = $Matches[1].Trim()
            } elseif ($assistantStart -ge 0 -and $lower -eq 'response actions') {
                $responseActionsFound = $true
            } elseif ($assistantStart -ge 0 -and $lower -eq 'copy response') {
                $copyResponseFound = $true
            } elseif ($assistantStart -ge 0 -and $lower -eq 'start voice') {
                $responseVoiceFound = $true
            }

            $rows.Add([pscustomobject]@{
                Index = $i
                Name = $name
                Type = $type
                Element = $el
                Live = [bool]$live
            })
        }

        # Some Firefox builds expose web accessibles only in UIA Raw View.
        # Fall back to that view only when Control View did not reveal either
        # composer state. This keeps normal scans lighter while still matching
        # Firefox's internal accessibility tree more closely.
        if (-not $stopFound -and -not $sendFound -and -not $voiceFound) {
            try {
                $rawAll = $root.FindAll(
                    [System.Windows.Automation.TreeScope]::Descendants,
                    [System.Windows.Automation.Automation]::RawViewCondition
                )
                $rawLimit = [Math]::Min($rawAll.Count, 12000)
                for ($ri = 0; $ri -lt $rawLimit; $ri++) {
                    $rawEl = $rawAll.Item($ri)
                    $rawName = Get-AccessibleName $rawEl
                    if ([string]::IsNullOrWhiteSpace($rawName)) { continue }
                    if (-not (Is-LiveElement $rawEl)) { continue }
                    $rawLower = $rawName.ToLowerInvariant()
                    if ($rawLower -in @('stop answering','stop generating','stop response','stop generating response')) {
                        $stopFound = $true
                        break
                    }
                    if ($rawLower -in @('send prompt','send message','submit prompt')) {
                        $sendFound = $true
                    }
                    if ($rawLower -eq 'start voice') {
                        $voiceFound = $true
                    }
                }
            } catch {}
        }

        $latestComplete = [bool](
            $assistantStart -ge 0 -and
            -not [string]::IsNullOrWhiteSpace($workedFor) -and
            $responseActionsFound -and
            $copyResponseFound -and
            $responseVoiceFound
        )
        $composerState = if ($latestComplete) { 'voice' } elseif ($stopFound) { 'stop' } elseif ($sendFound) { 'send' } elseif ($voiceFound) { 'voice' } else { 'unknown' }
        $progress = ($composerState -eq 'stop')

        $responseNames = New-Object System.Collections.Generic.List[string]
        $downloadCount = 0
        foreach ($row in $rows) {
            if ($assistantStart -ge 0 -and $row.Index -le $assistantStart) { continue }
            $n = ([string]$row.Name).Trim()
            $lower = $n.ToLowerInvariant()
            if ($lower -match '^(copy|good response|bad response|read aloud|share|more actions|regenerate|retry)$') { continue }
            if ($n.Length -gt 0) { $responseNames.Add($n) }
            if ($row.Live -and $lower -match '^download(?:\s|$|:)') { $downloadCount++ }
        }

        if ($assistantStart -lt 0) {
            $responseNames.Clear()
            $downloadCount = 0
            $tail = @($rows | Select-Object -Last 240)
            foreach ($row in $tail) {
                $n = ([string]$row.Name).Trim()
                $lower = $n.ToLowerInvariant()
                if ($lower -match '^(copy|good response|bad response|read aloud|share|more actions|regenerate|retry)$') { continue }
                if ($n.Length -gt 0) { $responseNames.Add($n) }
                if ($row.Live -and $lower -match '^download(?:\s|$|:)') { $downloadCount++ }
            }
        }

        $joined = [string]::Join("`n", $responseNames)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($joined)
            $fingerprint = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
        } finally { $sha.Dispose() }

        $results += [pscustomobject]@{
            hwnd = [Int64]$rawHandle
            available = $true
            in_progress = [bool]$progress
            composer_state = $composerState
            error_text = $errorText
            response_fingerprint = $fingerprint
            response_text_length = $joined.Length
            download_count = [int]$downloadCount
            has_assistant_response = [bool]($assistantStart -ge 0)
            worked_for_text = $workedFor
            latest_response_complete = $latestComplete
        }
    } catch {
        $results += [pscustomobject]@{
            hwnd = [Int64]$rawHandle
            available = $false
            in_progress = $false
            composer_state = 'unknown'
            error_text = ''
            response_fingerprint = ''
            response_text_length = 0
            download_count = 0
            has_assistant_response = $false
            worked_for_text = ''
            latest_response_complete = $false
        }
    }
}
$results | ConvertTo-Json -Compress
'''

_DOWNLOAD_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$rawHandle = [Int64]$env:PRM_UIA_HWND
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$rawHandle)
if ($null -eq $root) { throw 'No UI Automation root for browser window.' }

function Get-AccessibleName($element) {
    $name = ''
    try { $name = [string]$element.Current.Name } catch {}
    if (-not [string]::IsNullOrWhiteSpace($name)) { return $name.Trim() }
    try {
        $legacy = $element.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
        if ($null -ne $legacy) {
            $name = [string]$legacy.Current.Name
            if (-not [string]::IsNullOrWhiteSpace($name)) { return $name.Trim() }
        }
    } catch {}
    return ''
}

function Normalize-Token([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) { return '' }
    return (($value.ToLowerInvariant()) -replace '[^a-z0-9]+','')
}

$matchTerms = @()
if ($env:PRM_UIA_DOWNLOAD_TERMS) {
    try {
        $decoded = $env:PRM_UIA_DOWNLOAD_TERMS | ConvertFrom-Json
        if ($decoded -is [System.Array]) { $matchTerms = @($decoded) }
        elseif ($null -ne $decoded) { $matchTerms = @($decoded) }
    } catch {}
}
$normalizedTerms = @(
    $matchTerms |
    ForEach-Object { Normalize-Token ([string]$_) } |
    Where-Object { $_.Length -ge 3 } |
    Select-Object -Unique
)

$all = $root.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Automation]::ControlViewCondition
)
$candidates = New-Object System.Collections.Generic.List[object]
$assistantMarkers = New-Object System.Collections.Generic.List[int]
$limit = [Math]::Min($all.Count, 8000)
for ($i = 0; $i -lt $limit; $i++) {
    $el = $all.Item($i)
    $name = Get-AccessibleName $el
    if ([string]::IsNullOrWhiteSpace($name)) { continue }
    $lowerName = $name.ToLowerInvariant()
    if ($lowerName -match '^(chatgpt said:?|assistant said:?|assistant response:?|chatgpt response:?)$') { $assistantMarkers.Add($i) }
    if ($lowerName -notmatch '^download(?:\s|$|:)') { continue }
    try {
        if (-not $el.Current.IsEnabled) { continue }
    } catch {}
    $normalizedName = Normalize-Token $name
    $matchesProject = $false
    foreach ($term in $normalizedTerms) {
        if ($normalizedName.Contains($term)) {
            $matchesProject = $true
            break
        }
    }
    $candidates.Add([pscustomobject]@{
        Index = $i
        Name = $name
        Element = $el
        MatchesProject = [bool]$matchesProject
    })
}

if ($candidates.Count -eq 0) {
    # Firefox may place web accessibles only in UIA Raw View on some builds.
    # Retry there before concluding that no Download buttons exist.
    try {
        $rawAll = $root.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Automation]::RawViewCondition
        )
        $rawLimit = [Math]::Min($rawAll.Count, 12000)
        for ($ri = 0; $ri -lt $rawLimit; $ri++) {
            $el = $rawAll.Item($ri)
            $name = Get-AccessibleName $el
            if ([string]::IsNullOrWhiteSpace($name)) { continue }
            $lowerName = $name.ToLowerInvariant()
            if ($lowerName -match '^(chatgpt said:?|assistant said:?|assistant response:?|chatgpt response:?)$') { $assistantMarkers.Add($ri) }
            if ($lowerName -notmatch '^download(?:\s|$|:)') { continue }
            try { if (-not $el.Current.IsEnabled) { continue } } catch {}
            $normalizedName = Normalize-Token $name
            $matchesProject = $false
            foreach ($term in $normalizedTerms) {
                if ($normalizedName.Contains($term)) { $matchesProject = $true; break }
            }
            $candidates.Add([pscustomobject]@{
                Index = $ri
                Name = $name
                Element = $el
                MatchesProject = [bool]$matchesProject
            })
        }
    } catch {}
}

# Find the response containing the bottom-most Download control. Prefer an
# explicit ChatGPT/assistant response heading as the boundary; only fall back to
# a short trailing cluster when Firefox omits those headings.
$latest = @()
if ($candidates.Count -gt 0) {
    $lastDownloadIndex = [int]$candidates[$candidates.Count - 1].Index
    $boundary = -1
    foreach ($marker in $assistantMarkers) {
        if ([int]$marker -le $lastDownloadIndex -and [int]$marker -gt $boundary) { $boundary = [int]$marker }
    }
    if ($boundary -ge 0) {
        $latest = @($candidates | Where-Object { [int]$_.Index -gt $boundary })
    } else {
        # No response boundary means grouping multiple Download controls would
        # be a guess. The final control in accessibility document order is the
        # only deterministic "most recent available download".
        $latest = @($candidates[$candidates.Count - 1])
    }
}
$selected = @($latest | Where-Object { $_.MatchesProject })
if ($selected.Count -eq 0) { $selected = @($latest) }

$clicked = New-Object System.Collections.Generic.List[string]
$seen = @{}
foreach ($row in $selected) {
    $key = "{0}|{1}" -f $row.Index, $row.Name
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true
    $el = $row.Element
    $invoked = $false
    try {
        $pattern = $el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        if ($null -ne $pattern) {
            $pattern.Invoke()
            $invoked = $true
        }
    } catch {}
    if (-not $invoked) {
        try {
            $legacy = $el.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
            if ($null -ne $legacy) {
                $legacy.DoDefaultAction()
                $invoked = $true
            }
        } catch {}
    }
    if ($invoked) {
        $clicked.Add([string]$row.Name)
        Start-Sleep -Milliseconds 220
    }
}
[pscustomobject]@{ clicked = @($clicked | ForEach-Object { $_ }); count = $clicked.Count; candidates = @($candidates | ForEach-Object { $_.Name }) } | ConvertTo-Json -Compress
'''


def _run_powershell(script: str, env_updates: dict[str, str], timeout: float = 20.0) -> object:
    if os.name != "nt":
        raise RuntimeError("Windows accessibility is only available on Windows.")
    env = os.environ.copy()
    env.update(env_updates)
    completed = subprocess.run(
        [
            _powershell_executable(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-STA",
            "-EncodedCommand",
            _encoded_powershell(script),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"PowerShell exited {completed.returncode}"
        raise RuntimeError(f"Windows accessibility query failed: {detail}")
    payload = completed.stdout.strip()
    if not payload:
        return []
    return json.loads(payload)


def inspect_chatgpt_windows(hwnds: Iterable[int]) -> dict[int, ChatAccessibilitySnapshot]:
    handles = sorted({int(hwnd) for hwnd in hwnds if int(hwnd) > 0})
    if not handles:
        return {}

    # Firefox exposes its web accessibility tree primarily through MSAA /
    # IAccessible2. Query that directly first. UI Automation remains a fallback
    # for browsers/builds which expose a native UIA tree.
    msaa_rows = inspect_windows_msaa(handles) if os.name == "nt" else {}
    result: dict[int, ChatAccessibilitySnapshot] = {}
    unresolved: list[int] = []
    for hwnd in handles:
        row = msaa_rows.get(hwnd)
        if row is None:
            unresolved.append(hwnd)
            continue
        result[hwnd] = ChatAccessibilitySnapshot(
            hwnd=hwnd,
            available=bool(row.available),
            in_progress=row.composer_state == "stop",
            composer_state=row.composer_state,
            error_text=row.error_text,
            response_fingerprint=row.response_fingerprint,
            response_text_length=row.response_text_length,
            download_count=row.download_count,
            backend="msaa",
            composer_name=row.composer_name,
            document_name=row.document_name,
            document_value=row.document_value,
            document_key=row.document_key,
            has_assistant_response=any(
                element.name.strip().casefold() in {
                    "chatgpt said:", "chatgpt said", "assistant said:", "assistant said",
                    "assistant response:", "assistant response", "chatgpt response:", "chatgpt response",
                }
                for element in row.elements
            ),
            worked_for_text=row.worked_for_text,
            latest_response_complete=row.latest_response_complete,
        )
        if row.composer_state not in {"stop", "send", "voice"}:
            unresolved.append(hwnd)

    if not unresolved:
        return result

    # Fallback only for HWNDs where MSAA did not expose the exact composer
    # control. This also avoids launching a PowerShell UIA scan every 800 ms
    # when Firefox's native accessibility path is already working.
    raw = _run_powershell(_SCAN_SCRIPT, {"PRM_UIA_HWNDS": ",".join(str(value) for value in unresolved)})
    if isinstance(raw, dict):
        rows = [raw]
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        hwnd = int(row.get("hwnd", 0) or 0)
        if not hwnd:
            continue
        fallback = ChatAccessibilitySnapshot(
            hwnd=hwnd,
            available=bool(row.get("available", False)),
            in_progress=bool(row.get("in_progress", False)),
            composer_state=str(row.get("composer_state", "unknown") or "unknown"),
            error_text=str(row.get("error_text", "") or ""),
            response_fingerprint=str(row.get("response_fingerprint", "") or ""),
            response_text_length=int(row.get("response_text_length", 0) or 0),
            download_count=int(row.get("download_count", 0) or 0),
            backend="uia",
            composer_name=(
                "Stop answering" if str(row.get("composer_state", "")) == "stop"
                else "Send prompt" if str(row.get("composer_state", "")) == "send"
                else "Start Voice" if str(row.get("composer_state", "")) == "voice"
                else ""
            ),
            document_name="",
            document_value="",
            document_key="",
            has_assistant_response=bool(row.get("has_assistant_response", False)),
            worked_for_text=str(row.get("worked_for_text", "") or ""),
            latest_response_complete=bool(row.get("latest_response_complete", False)),
        )
        current = result.get(hwnd)
        if current is None or fallback.composer_state in {"stop", "send", "voice"}:
            result[hwnd] = fallback
    return result


def download_latest_chatgpt_response_links(
    hwnd: int,
    match_terms: Iterable[str] = (),
    expected_document_key: str = "",
    *,
    anchor_source_hwnd: int = 0,
    anchor_object_id: int = 0,
    anchor_child_id: int = 0,
) -> list[str]:
    """Download from Firefox's live MSAA/IA2 chat document only.

    The former PowerShell/UIA fallback could see a different Firefox tab or a
    stale response and therefore click the second-most-recent artifact.  For a
    user-triggered download, returning no match is safer than invoking a control
    whose document identity cannot be proven.
    """
    terms = [str(value).strip() for value in match_terms if str(value).strip()]
    try:
        return download_latest_msaa(
            int(hwnd),
            terms,
            expected_document_key=str(expected_document_key or ""),
            anchor_source_hwnd=int(anchor_source_hwnd or 0),
            anchor_object_id=int(anchor_object_id or 0),
            anchor_child_id=int(anchor_child_id or 0),
        )
    except Exception:
        return []
