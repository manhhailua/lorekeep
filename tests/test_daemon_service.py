"""Tests for daemon service installation (systemd, launchd, Windows startup)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from lorekeep.daemon_service import (
    _find_lorekeep_command,
    _service_label,
    _systemd_unit,
    _launchd_plist,
    _windows_script,
    _systemd_unit_path,
    _launchd_plist_path,
    _windows_startup_path,
    install,
    uninstall,
    status,
)


class TestFindCommand:
    def test_finds_direct_binary(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/local/bin/lorekeep" if cmd == "lorekeep" else None)
        cmd, args = _find_lorekeep_command()
        assert cmd == "/usr/local/bin/lorekeep"
        assert args == []

    def test_falls_back_to_uvx(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: "/home/user/.local/bin/uvx" if cmd == "uvx" else None)
        cmd, args = _find_lorekeep_command()
        assert cmd == "/home/user/.local/bin/uvx"
        assert args == ["lorekeep"]

    def test_falls_back_to_bare_name(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        cmd, args = _find_lorekeep_command()
        assert cmd == "lorekeep"
        assert args == []


class TestSystemdUnit:
    def test_unit_contains_execstart(self, tmp_path):
        with patch("lorekeep.daemon_service._find_lorekeep_command",
                   return_value=("/usr/local/bin/lorekeep", [])):
            unit = _systemd_unit(tmp_path)
        assert "ExecStart=/usr/local/bin/lorekeep agent watch" in unit
        assert "Restart=on-failure" in unit
        assert f"LOREKEEP_HOME={tmp_path}" in unit

    def test_unit_with_uvx(self, tmp_path):
        with patch("lorekeep.daemon_service._find_lorekeep_command",
                   return_value=("/home/user/.local/bin/uvx", ["lorekeep"])):
            unit = _systemd_unit(tmp_path)
        assert "ExecStart=/home/user/.local/bin/uvx lorekeep agent watch" in unit

    def test_unit_path_in_config(self):
        path = _systemd_unit_path()
        assert ".config/systemd/user" in str(path)
        assert path.name == "lorekeep.service"

    def test_install_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
        with patch("lorekeep.daemon_service._find_lorekeep_command",
                   return_value=("/usr/local/bin/lorekeep", [])):
            from lorekeep.daemon_service import install_systemd
            unit_path = install_systemd(tmp_path / "data")
        assert unit_path.exists()
        content = unit_path.read_text()
        assert "lorekeep agent watch" in content


class TestLaunchdPlist:
    def test_plist_contains_label(self, tmp_path):
        plist = _launchd_plist(tmp_path)
        assert "com.lorekeep.daemon" in plist
        assert "RunAtLoad" in plist
        assert "KeepAlive" in plist

    def test_plist_contains_command(self, tmp_path):
        with patch("lorekeep.daemon_service._find_lorekeep_command",
                   return_value=("/usr/local/bin/lorekeep", [])):
            plist = _launchd_plist(tmp_path)
        assert "/usr/local/bin/lorekeep" in plist
        assert "agent" in plist
        assert "watch" in plist

    def test_plist_contains_env(self, tmp_path):
        plist = _launchd_plist(tmp_path)
        assert f"LOREKEEP_HOME" in plist
        assert str(tmp_path) in plist

    def test_plist_path_in_library(self):
        path = _launchd_plist_path()
        assert "Library/LaunchAgents" in str(path)
        assert path.name == "com.lorekeep.daemon.plist"

    def test_install_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
        with patch("lorekeep.daemon_service._find_lorekeep_command",
                   return_value=("/usr/local/bin/lorekeep", [])):
            from lorekeep.daemon_service import install_launchd
            plist_path = install_launchd(tmp_path / "data")
        assert plist_path.exists()


class TestWindowsScript:
    def test_script_contains_command(self, tmp_path):
        with patch("lorekeep.daemon_service._find_lorekeep_command",
                   return_value=("C:\\lorekeep\\lorekeep.exe", [])):
            script = _windows_script(tmp_path)
        assert "lorekeep.exe" in script
        assert "agent" in script
        assert "watch" in script

    def test_script_sets_env(self, tmp_path):
        script = _windows_script(tmp_path)
        assert "LOREKEEP_HOME" in script

    def test_install_writes_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
        with patch("lorekeep.daemon_service._find_lorekeep_command",
                   return_value=("lorekeep", [])):
            from lorekeep.daemon_service import install_windows
            script_path = install_windows(tmp_path / "data")
        assert script_path.exists()
        assert script_path.suffix == ".vbs"


class TestPlatformDispatch:
    def test_install_dispatches_correctly(self, monkeypatch, tmp_path):
        for platform, func_name in [("linux", "install_systemd"),
                                     ("darwin", "install_launchd"),
                                     ("win32", "install_windows")]:
            monkeypatch.setattr(sys, "platform", platform)
            with patch(f"lorekeep.daemon_service.{func_name}",
                       return_value=Path("/fake/path")) as mock_fn:
                name, path = install(tmp_path / "data")
                assert mock_fn.called

    def test_uninstall_dispatches_correctly(self, monkeypatch):
        for platform, func_name in [("linux", "uninstall_systemd"),
                                     ("darwin", "uninstall_launchd"),
                                     ("win32", "uninstall_windows")]:
            monkeypatch.setattr(sys, "platform", platform)
            with patch(f"lorekeep.daemon_service.{func_name}", return_value=True) as mock_fn:
                result = uninstall()
                assert mock_fn.called
                assert result is True

    def test_status_dispatches(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        with patch("lorekeep.daemon_service.status_systemd", return_value="active"):
            s = status()
            assert "systemd" in s

    def test_unsupported_platform(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "freebsd")
        with pytest.raises(RuntimeError, match="Unsupported"):
            install(Path("/tmp"))


class TestUninstall:
    def test_uninstall_returns_false_when_not_installed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = uninstall()
        assert result is False
