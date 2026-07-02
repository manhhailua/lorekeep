"""OS service integration: install lorekeep daemon as a persistent service.

Supports:
  - Linux: systemd user service
  - macOS: launchd LaunchAgent
  - Windows: Startup folder VBS script

Generated configs use the lorekeep data home path + the resolved
lorekeep command (uvx or direct binary).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _find_lorekeep_command() -> tuple[str, list[str]]:
    """Find the lorekeep executable.

    Returns (command, prefix_args). For uvx: ('uvx', ['lorekeep']).
    For direct install: ('lorekeep', []).
    """
    lorekeep_path = shutil.which("lorekeep")
    if lorekeep_path:
        return (lorekeep_path, [])

    uvx_path = shutil.which("uvx")
    if uvx_path:
        return (uvx_path, ["lorekeep"])

    return ("lorekeep", [])


def _service_label() -> str:
    return "com.lorekeep.daemon"


# ── systemd (Linux) ────────────────────────────────────────────────────────


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "lorekeep.service"


def _systemd_unit(home: Path) -> str:
    cmd, args = _find_lorekeep_command()
    exec_parts = " ".join([cmd] + args + ["agent", "watch"])
    return f"""\
[Unit]
Description=Lorekeep Knowledge Graph Daemon
After=network.target

[Service]
Type=simple
ExecStart={exec_parts}
Restart=on-failure
RestartSec=10
Environment=LOREKEEP_HOME={home}

[Install]
WantedBy=default.target
"""


def install_systemd(home: Path) -> Path:
    """Install systemd user service. Returns the unit file path."""
    unit_path = _systemd_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(_systemd_unit(home), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "lorekeep"], check=False)
    subprocess.run(["systemctl", "--user", "start", "lorekeep"], check=False)
    return unit_path


def uninstall_systemd() -> bool:
    """Remove systemd user service. Returns True if removed."""
    unit_path = _systemd_unit_path()
    if not unit_path.exists():
        return False
    subprocess.run(["systemctl", "--user", "stop", "lorekeep"], check=False)
    subprocess.run(["systemctl", "--user", "disable", "lorekeep"], check=False)
    unit_path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return True


def status_systemd() -> str:
    """Return systemd service status string."""
    r = subprocess.run(
        ["systemctl", "--user", "is-active", "lorekeep"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() or r.stderr.strip()


# ── launchd (macOS) ────────────────────────────────────────────────────────


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_service_label()}.plist"


def _launchd_plist(home: Path) -> str:
    cmd, args = _find_lorekeep_command()
    all_args = args + ["agent", "watch"]
    args_xml = "\n".join(f"        <string>{a}</string>" for a in all_args)
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_service_label()}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{cmd}</string>
{args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>LOREKEEP_HOME</key>
        <string>{home}</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{home}/daemon.log</string>
    <key>StandardErrorPath</key>
    <string>{home}/daemon.err.log</string>
</dict>
</plist>
"""


def install_launchd(home: Path) -> Path:
    """Install launchd LaunchAgent. Returns the plist path."""
    plist_path = _launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(_launchd_plist(home), encoding="utf-8")
    subprocess.run(["launchctl", "load", str(plist_path)], check=False)
    return plist_path


def uninstall_launchd() -> bool:
    """Remove launchd LaunchAgent. Returns True if removed."""
    plist_path = _launchd_plist_path()
    if not plist_path.exists():
        return False
    subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
    plist_path.unlink()
    return True


def status_launchd() -> str:
    """Return launchd service status."""
    r = subprocess.run(
        ["launchctl", "list", _service_label()],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return "running"
    return "not loaded"


# ── Startup folder (Windows) ───────────────────────────────────────────────


def _windows_startup_path() -> Path:
    appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _windows_script(home: Path) -> str:
    cmd, args = _find_lorekeep_command()
    full_cmd = " ".join([cmd] + args + ["agent", "watch"])
    return f'''\
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c set LOREKEEP_HOME={home} && {full_cmd}", 0, False
Set WshShell = Nothing
'''


def install_windows(home: Path) -> Path:
    """Install Windows startup script. Returns the script path."""
    startup = _windows_startup_path()
    startup.mkdir(parents=True, exist_ok=True)
    script_path = startup / "lorekeep-daemon.vbs"
    script_path.write_text(_windows_script(home), encoding="utf-8")
    return script_path


def uninstall_windows() -> bool:
    """Remove Windows startup script. Returns True if removed."""
    script_path = _windows_startup_path() / "lorekeep-daemon.vbs"
    if not script_path.exists():
        return False
    script_path.unlink()
    return True


def status_windows() -> str:
    """Return Windows startup script status."""
    script_path = _windows_startup_path() / "lorekeep-daemon.vbs"
    return "installed" if script_path.exists() else "not installed"


# ── Platform dispatch ──────────────────────────────────────────────────────


def install(home: Path) -> tuple[str, Path]:
    """Install daemon service for the current platform.

    Returns (platform_name, config_path).
    """
    if sys.platform == "linux":
        return ("systemd", install_systemd(home))
    if sys.platform == "darwin":
        return ("launchd", install_launchd(home))
    if sys.platform == "win32":
        return ("startup", install_windows(home))
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def uninstall() -> bool:
    """Uninstall daemon service for the current platform."""
    if sys.platform == "linux":
        return uninstall_systemd()
    if sys.platform == "darwin":
        return uninstall_launchd()
    if sys.platform == "win32":
        return uninstall_windows()
    return False


def status() -> str:
    """Return daemon service status for the current platform."""
    if sys.platform == "linux":
        return f"systemd: {status_systemd()}"
    if sys.platform == "darwin":
        return f"launchd: {status_launchd()}"
    if sys.platform == "win32":
        return f"startup: {status_windows()}"
    return f"unsupported: {sys.platform}"
