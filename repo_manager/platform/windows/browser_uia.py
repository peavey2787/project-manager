from __future__ import annotations

import os
from typing import Iterable

from .uia_fallback import _run_powershell


_BROWSER_URL_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$handles = @()
if ($env:PRM_BROWSER_UIA_HWNDS) {
    foreach ($part in $env:PRM_BROWSER_UIA_HWNDS.Split(',')) {
        $value = 0L
        if ([Int64]::TryParse($part, [ref]$value) -and $value -gt 0) { $handles += $value }
    }
}

function Get-ElementName($element) {
    try {
        $name = [string]$element.Current.Name
        if (-not [string]::IsNullOrWhiteSpace($name)) { return $name.Trim() }
    } catch {}
    try {
        $legacy = $element.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
        if ($null -ne $legacy) {
            $name = [string]$legacy.Current.Name
            if (-not [string]::IsNullOrWhiteSpace($name)) { return $name.Trim() }
        }
    } catch {}
    return ''
}

function Get-ElementValue($element) {
    try {
        $pattern = $element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        if ($null -ne $pattern) {
            $value = [string]$pattern.Current.Value
            if (-not [string]::IsNullOrWhiteSpace($value)) { return $value.Trim() }
        }
    } catch {}
    try {
        $legacy = $element.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
        if ($null -ne $legacy) {
            $value = [string]$legacy.Current.Value
            if (-not [string]::IsNullOrWhiteSpace($value)) { return $value.Trim() }
        }
    } catch {}
    return ''
}

function Coerce-Url([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) { return '' }
    $text = $value.Trim()
    if ($text.Contains("`n") -or $text.Contains("`r")) { return '' }
    $lower = $text.ToLowerInvariant()
    foreach ($prefix in @('about:','chrome:','edge:','brave:','file:')) {
        if ($lower.StartsWith($prefix)) { return '' }
    }
    if ($text -notmatch '^[a-zA-Z][a-zA-Z0-9+.-]*://') {
        $first = ($text -split '[/\\?#]', 2)[0]
        if ($first -notmatch '\.' -and $first.ToLowerInvariant() -ne 'localhost') { return '' }
        $text = 'https://' + $text
    }
    try {
        $uri = [Uri]$text
        if (($uri.Scheme -ne 'http' -and $uri.Scheme -ne 'https') -or [string]::IsNullOrWhiteSpace($uri.Host)) { return '' }
        return $text
    } catch { return '' }
}

function Score-Element($element, [double]$rootTop, [double]$rootHeight) {
    $value = Get-ElementValue $element
    $url = Coerce-Url $value
    if (-not $url) { return $null }

    $name = (Get-ElementName $element).ToLowerInvariant()
    $type = ''
    $automationId = ''
    $top = 99999.0
    try { $type = [string]$element.Current.ControlType.ProgrammaticName } catch {}
    try { $automationId = ([string]$element.Current.AutomationId).ToLowerInvariant() } catch {}
    try { $top = [double]$element.Current.BoundingRectangle.Top } catch {}

    $score = 0
    foreach ($hint in @('address and search bar','address bar','search with','enter address','location','omnibox')) {
        if ($name.Contains($hint)) { $score += 120; break }
    }
    foreach ($hint in @('urlbar','address','omnibox','location')) {
        if ($automationId.Contains($hint)) { $score += 90; break }
    }
    if ($type -eq 'ControlType.Edit' -or $type -eq 'ControlType.ComboBox') { $score += 45 }
    if ($value.StartsWith('http://') -or $value.StartsWith('https://')) { $score += 20 }
    if ($top -lt ($rootTop + [Math]::Min(260.0, [Math]::Max(120.0, $rootHeight * 0.22)))) { $score += 35 }

    if ($score -lt 70) { return $null }
    return [pscustomobject]@{ score = [int]$score; url = $url }
}

$results = @()
foreach ($rawHandle in $handles) {
    $bestScore = -1
    $bestUrl = ''
    try {
        $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$rawHandle)
        if ($null -eq $root) { throw 'No UI Automation root.' }
        $rootRect = $root.Current.BoundingRectangle

        foreach ($condition in @(
            [System.Windows.Automation.Automation]::ControlViewCondition,
            [System.Windows.Automation.Automation]::RawViewCondition
        )) {
            try {
                $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
                $limit = [Math]::Min($all.Count, 5000)
                for ($i = 0; $i -lt $limit; $i++) {
                    $candidate = Score-Element $all.Item($i) ([double]$rootRect.Top) ([double]$rootRect.Height)
                    if ($null -ne $candidate -and [int]$candidate.score -gt $bestScore) {
                        $bestScore = [int]$candidate.score
                        $bestUrl = [string]$candidate.url
                    }
                }
                if ($bestScore -ge 180) { break }
            } catch {}
        }
    } catch {}
    $results += [pscustomobject]@{ hwnd = [Int64]$rawHandle; url = $bestUrl }
}
$results | ConvertTo-Json -Compress
'''


def read_browser_active_urls_uia(hwnds: Iterable[int]) -> dict[int, str]:
    """Passively read browser address bars with Windows UI Automation.

    This is a fallback for already-open/background browser windows whose MSAA
    chrome tree is temporarily dormant. It never activates or focuses a window.
    A single PowerShell/UIA process handles the whole batch so startup/manual
    reconciliation does not spawn one process per browser HWND.
    """
    handles = sorted({int(hwnd) for hwnd in hwnds if int(hwnd or 0) > 0})
    if os.name != "nt" or not handles:
        return {}

    try:
        payload = _run_powershell(
            _BROWSER_URL_SCRIPT,
            {"PRM_BROWSER_UIA_HWNDS": ",".join(str(hwnd) for hwnd in handles)},
            timeout=8.0,
        )
    except Exception:
        return {}
    if isinstance(payload, dict):
        payload = [payload]
    result: dict[int, str] = {}
    for row in payload if isinstance(payload, list) else []:
        try:
            hwnd = int(row.get("hwnd", 0))
            url = str(row.get("url", "") or "").strip()
        except (AttributeError, TypeError, ValueError):
            continue
        if hwnd and url:
            result[hwnd] = url
    return result
