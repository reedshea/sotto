"""Windows service management for Sotto via NSSM (Non-Sucking Service Manager).

Provides `sotto install-service` and `sotto uninstall-service` CLI commands
that configure Sotto to run as a persistent Windows service, surviving reboots
and logoffs.

On non-Windows platforms, falls back to a systemd unit file generator.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("sotto.service")

SERVICE_NAME = "Sotto"
SERVICE_DISPLAY = "Sotto Voice Transcription"
SERVICE_DESCRIPTION = "Private voice transcription server — receives audio and transcribes locally."


def _find_nssm() -> str | None:
    """Locate nssm.exe on PATH."""
    return shutil.which("nssm")


def _find_sotto_exe() -> str:
    """Locate the sotto entry point executable."""
    # When installed via pip, sotto.exe lives next to python.exe on Windows
    if sys.platform == "win32":
        scripts_dir = Path(sys.executable).parent / "Scripts"
        candidate = scripts_dir / "sotto.exe"
        if candidate.exists():
            return str(candidate)
    # Fallback: run via python -m sotto.cli
    return sys.executable


def install_service(config_path: str | None = None) -> bool:
    """Install Sotto as a Windows service using NSSM, or generate a systemd unit on Linux."""
    if sys.platform == "win32":
        return _install_windows_service(config_path)
    else:
        return _install_systemd_unit(config_path)


def uninstall_service() -> bool:
    """Remove the Sotto service."""
    if sys.platform == "win32":
        return _uninstall_windows_service()
    else:
        return _uninstall_systemd_unit()


def service_status() -> str | None:
    """Check if the service is running. Returns status string or None."""
    if sys.platform == "win32":
        return _status_windows()
    else:
        return _status_systemd()


# ---------------------------------------------------------------------------
# Windows (NSSM)
# ---------------------------------------------------------------------------

def _install_windows_service(config_path: str | None = None) -> bool:
    nssm = _find_nssm()
    if not nssm:
        print("NSSM not found on PATH.")
        print()
        print("Install NSSM (Non-Sucking Service Manager) to manage Sotto as a Windows service:")
        print("  1. Download from https://nssm.cc/download")
        print("  2. Extract and add the directory containing nssm.exe to your PATH")
        print("  3. Re-run: sotto install-service")
        print()
        print("Alternatively, install via winget:")
        print("  winget install nssm")
        print()
        _print_manual_nssm_instructions(config_path)
        return False

    sotto_exe = _find_sotto_exe()

    # Build the command arguments
    if sotto_exe.endswith("sotto.exe"):
        app_path = sotto_exe
        app_args = "start"
        if config_path:
            app_args += f" --config {config_path}"
    else:
        # Running via python -m
        app_path = sotto_exe
        app_args = "-m sotto.cli start"
        if config_path:
            app_args += f" --config {config_path}"

    # Resolve config path to an absolute path so the service finds it
    # regardless of which account (SYSTEM vs user) runs the process.
    if config_path:
        resolved_config = str(Path(config_path).expanduser().resolve())
    else:
        # Use the default config location, resolved to the *installing* user's home
        default_cfg = Path("~/.config/sotto/config.yaml").expanduser()
        resolved_config = str(default_cfg) if default_cfg.exists() else None

    # Rebuild app_args with the resolved absolute config path
    if resolved_config:
        if sotto_exe.endswith("sotto.exe"):
            app_args = f"start --config \"{resolved_config}\""
        else:
            app_args = f"-m sotto.cli start --config \"{resolved_config}\""

    try:
        # Install the service
        subprocess.run([nssm, "install", SERVICE_NAME, app_path, app_args], check=True)

        # Configure service properties
        subprocess.run([nssm, "set", SERVICE_NAME, "DisplayName", SERVICE_DISPLAY], check=True)
        subprocess.run([nssm, "set", SERVICE_NAME, "Description", SERVICE_DESCRIPTION], check=True)
        subprocess.run([nssm, "set", SERVICE_NAME, "Start", "SERVICE_AUTO_START"], check=True)

        # Set SOTTO_CONFIG env var so the process can find config even if ~ is wrong
        if resolved_config:
            subprocess.run(
                [nssm, "set", SERVICE_NAME, "AppEnvironmentExtra", f"SOTTO_CONFIG={resolved_config}"],
                check=True,
            )

        # Configure restart on failure
        subprocess.run([nssm, "set", SERVICE_NAME, "AppExit", "Default", "Restart"], check=True)
        subprocess.run([nssm, "set", SERVICE_NAME, "AppRestartDelay", "5000"], check=True)

        # Log stdout/stderr — use absolute path based on installing user's home
        log_dir = Path("~/.local/share/sotto/logs").expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [nssm, "set", SERVICE_NAME, "AppStdout", str(log_dir / "sotto-stdout.log")],
            check=True,
        )
        subprocess.run(
            [nssm, "set", SERVICE_NAME, "AppStderr", str(log_dir / "sotto-stderr.log")],
            check=True,
        )
        subprocess.run(
            [nssm, "set", SERVICE_NAME, "AppStdoutCreationDisposition", "4"],  # append
            check=True,
        )
        subprocess.run(
            [nssm, "set", SERVICE_NAME, "AppStderrCreationDisposition", "4"],  # append
            check=True,
        )

        print(f"Service '{SERVICE_NAME}' installed successfully.")
        if resolved_config:
            print(f"Config: {resolved_config}")
        else:
            print("Config: using built-in defaults (no config file found)")
        print(f"Logs:   {log_dir}")
        print()
        print("To start the service now:")
        print(f"  nssm start {SERVICE_NAME}")
        print()
        print("Or use Windows Services (services.msc) to start/stop it.")
        return True

    except subprocess.CalledProcessError as e:
        print(f"Failed to install service: {e}")
        print("Make sure you are running this command as Administrator.")
        return False


def _uninstall_windows_service() -> bool:
    nssm = _find_nssm()
    if not nssm:
        print("NSSM not found. Cannot uninstall service.")
        return False

    try:
        # Stop first, ignore errors if not running
        subprocess.run([nssm, "stop", SERVICE_NAME], capture_output=True)
        subprocess.run([nssm, "remove", SERVICE_NAME, "confirm"], check=True)
        print(f"Service '{SERVICE_NAME}' removed.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to remove service: {e}")
        return False


def _status_windows() -> str | None:
    nssm = _find_nssm()
    if not nssm:
        return None
    try:
        result = subprocess.run(
            [nssm, "status", SERVICE_NAME], capture_output=True, text=True
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _print_manual_nssm_instructions(config_path: str | None = None) -> None:
    """Print manual NSSM commands the user can copy-paste in an admin PowerShell."""
    sotto_exe = _find_sotto_exe()

    # Resolve config to absolute path for the installing user
    if config_path:
        resolved_config = str(Path(config_path).expanduser().resolve())
    else:
        default_cfg = Path("~/.config/sotto/config.yaml").expanduser()
        resolved_config = str(default_cfg) if default_cfg.exists() else None

    if sotto_exe.endswith("sotto.exe"):
        app_args = "start"
        if resolved_config:
            app_args += f' --config "{resolved_config}"'
    else:
        app_args = "-m sotto.cli start"
        if resolved_config:
            app_args += f' --config "{resolved_config}"'

    print("If you already have NSSM, run these commands in an Administrator PowerShell:")
    print()
    print(f'  nssm install {SERVICE_NAME} "{sotto_exe}" "{app_args}"')
    print(f'  nssm set {SERVICE_NAME} DisplayName "{SERVICE_DISPLAY}"')
    print(f'  nssm set {SERVICE_NAME} Start SERVICE_AUTO_START')
    print(f'  nssm set {SERVICE_NAME} AppExit Default Restart')
    print(f'  nssm set {SERVICE_NAME} AppRestartDelay 5000')
    if resolved_config:
        print(f'  nssm set {SERVICE_NAME} AppEnvironmentExtra "SOTTO_CONFIG={resolved_config}"')
    print(f"  nssm start {SERVICE_NAME}")


# ---------------------------------------------------------------------------
# Linux / macOS (systemd)
# ---------------------------------------------------------------------------

SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description={description}
After=network.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=sotto

[Install]
WantedBy=multi-user.target
"""


def _install_systemd_unit(config_path: str | None = None) -> bool:
    sotto_exe = shutil.which("sotto")
    if not sotto_exe:
        sotto_exe = f"{sys.executable} -m sotto.cli"

    exec_start = f"{sotto_exe} start"
    if config_path:
        exec_start += f" --config {config_path}"

    unit_content = SYSTEMD_UNIT_TEMPLATE.format(
        description=SERVICE_DESCRIPTION,
        exec_start=exec_start,
    )

    # Write to user systemd directory
    unit_dir = Path("~/.config/systemd/user").expanduser()
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "sotto.service"
    unit_path.write_text(unit_content, encoding="utf-8")

    print(f"Systemd unit written to {unit_path}")
    print()
    print("To enable and start:")
    print("  systemctl --user daemon-reload")
    print("  systemctl --user enable sotto")
    print("  systemctl --user start sotto")
    print()
    print("To check status:")
    print("  systemctl --user status sotto")
    print("  journalctl --user -u sotto -f")
    return True


def _uninstall_systemd_unit() -> bool:
    unit_path = Path("~/.config/systemd/user/sotto.service").expanduser()
    if not unit_path.exists():
        print("No systemd unit found.")
        return False

    # Stop and disable
    subprocess.run(["systemctl", "--user", "stop", "sotto"], capture_output=True)
    subprocess.run(["systemctl", "--user", "disable", "sotto"], capture_output=True)
    unit_path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    print("Sotto systemd service removed.")
    return True


def _status_systemd() -> str | None:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "sotto"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return None
