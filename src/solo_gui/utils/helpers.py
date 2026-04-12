"""Utility functions for SoloKeys GUI."""

import time
from typing import Optional

from PySide6.QtWidgets import QMessageBox, QWidget


def format_firmware_version(version: str) -> str:
    """Format firmware version for display.

    Args:
        version: Raw version string

    Returns:
        Formatted version string
    """
    if not version:
        return "Unknown"

    # Clean up version string
    version = version.strip()

    # If it's already in a nice format, return as-is
    if len(version) <= 20 and all(c.isalnum() or c in ".-_" for c in version):
        return version

    # Otherwise, try to extract meaningful parts
    if len(version) > 30:
        return version[:30] + "..."

    return version


def get_device_capabilities(device_info: dict) -> list[str]:
    """Extract device capabilities from device info.

    Args:
        device_info: Device info dictionary from CTAP2

    Returns:
        List of capability names
    """
    capabilities = []

    if not device_info:
        return capabilities

    options = device_info.get("options", {})
    if options.get("clientPin"):
        capabilities.append("PIN")
    if options.get("up"):
        capabilities.append("User Presence")
    if options.get("uv"):
        capabilities.append("User Verification")
    if options.get("rk"):
        capabilities.append("Resident Keys")
    if options.get("plat"):
        capabilities.append("Platform Device")

    # Add algorithm support
    algorithms = device_info.get("algorithms", [])
    alg_names = []
    for alg in algorithms:
        if alg.get("type") == "public-key":
            alg_val = alg.get("alg", -1)
            if alg_val == -7:
                alg_names.append("ES256")
            elif alg_val == -8:
                alg_names.append("EdDSA")
            elif alg_val == -6:
                alg_names.append("RS256")

    if alg_names:
        capabilities.append(f"Algorithms: {', '.join(alg_names)}")

    return capabilities


def format_timestamp(timestamp: int) -> str:
    """Format Unix timestamp for display.

    Args:
        timestamp: Unix timestamp

    Returns:
        Formatted date/time string
    """
    if timestamp <= 0:
        return "Unknown"

    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
    except Exception:
        return "Invalid"


def confirm_dangerous_action(
    action: str, details: str = "", parent: Optional[QWidget] = None
) -> bool:
    """Show confirmation dialog for dangerous actions.

    Args:
        action: Description of the action
        details: Additional details about consequences
        parent: Parent widget

    Returns:
        True if user confirms, False otherwise
    """
    message = f"Are you sure you want to {action}?"
    if details:
        message += f"\n\n{details}"

    reply = QMessageBox.question(
        parent,
        "Confirm Action",
        message,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )

    return reply == QMessageBox.Yes


def show_touch_prompt(action: str, parent: Optional[QWidget] = None) -> None:
    """Show dialog prompting user to touch device.

    Args:
        action: Description of the action requiring touch
        parent: Parent widget
    """
    msg_box = QMessageBox(parent)
    msg_box.setWindowTitle("Touch Required")
    msg_box.setText(f"Please touch your SoloKeys device to {action}.")
    msg_box.setStandardButtons(QMessageBox.Cancel)
    msg_box.show()

    # Note: This would need to be hidden after the touch completes
    # TODO: Implement proper touch prompt handling
