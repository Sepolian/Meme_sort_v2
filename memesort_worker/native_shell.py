from __future__ import annotations

import os
import subprocess
from pathlib import Path


def pick_folder(
    title: str = "Choose a folder",
    initial_path: str | None = None,
) -> str | None:
    script_lines = [
        "Add-Type -AssemblyName System.Windows.Forms",
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
        f"$dialog.Description = '{_ps_single_quote(title)}'",
        "$dialog.ShowNewFolderButton = $true",
    ]
    if initial_path:
        script_lines.append(f"$dialog.SelectedPath = '{_ps_single_quote(initial_path)}'")
    script_lines.extend(
        [
            "$result = $dialog.ShowDialog()",
            "if ($result -eq [System.Windows.Forms.DialogResult]::OK) {",
            "  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "  Write-Output $dialog.SelectedPath",
            "}",
        ]
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-STA",
            "-Command",
            "\n".join(script_lines),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or "Folder picker failed")
    selected = (result.stdout or "").strip()
    return selected or None


def pick_file(
    title: str = "Choose a file",
    initial_path: str | None = None,
    filter_string: str = "Image Files|*.jpg;*.jpeg;*.png;*.webp;*.gif;*.bmp|All Files|*.*",
) -> str | None:
    resolved_initial_dir = None
    if initial_path:
        candidate = Path(initial_path).expanduser()
        if candidate.exists() and candidate.is_file():
            resolved_initial_dir = str(candidate.parent)
        elif candidate.exists() and candidate.is_dir():
            resolved_initial_dir = str(candidate)

    script_lines = [
        "Add-Type -AssemblyName System.Windows.Forms",
        "$dialog = New-Object System.Windows.Forms.OpenFileDialog",
        f"$dialog.Title = '{_ps_single_quote(title)}'",
        f"$dialog.Filter = '{_ps_single_quote(filter_string)}'",
        "$dialog.Multiselect = $false",
        "$dialog.CheckFileExists = $true",
    ]
    if resolved_initial_dir:
        script_lines.append(f"$dialog.InitialDirectory = '{_ps_single_quote(resolved_initial_dir)}'")
    script_lines.extend(
        [
            "$result = $dialog.ShowDialog()",
            "if ($result -eq [System.Windows.Forms.DialogResult]::OK) {",
            "  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "  Write-Output $dialog.FileName",
            "}",
        ]
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-STA",
            "-Command",
            "\n".join(script_lines),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or "File picker failed")
    selected = (result.stdout or "").strip()
    return selected or None


def open_path(path: Path | str) -> None:
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists():
        raise ValueError(f"Path does not exist: {candidate}")
    os.startfile(str(candidate))  # type: ignore[attr-defined]


def reveal_path_in_file_explorer(path: Path | str) -> None:
    candidate = Path(path).expanduser().resolve()
    if not candidate.exists():
        raise ValueError(f"Path does not exist: {candidate}")
    subprocess.Popen(["explorer.exe", f"/select,{candidate}"])


def _ps_single_quote(value: str) -> str:
    return value.replace("'", "''")
