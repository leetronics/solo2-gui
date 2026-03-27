#!/usr/bin/env python3
"""Test script to verify SoloKeys GUI setup without running GUI."""

import sys
import importlib.util


def check_module_import(module_name, description):
    """Check if a module can be imported."""
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is not None:
            print(f"✓ {description}")
            return True
        else:
            print(f"✗ {description} - Not found")
            return False
    except ImportError as e:
        print(f"✗ {description} - Error: {e}")
        return False


def main():
    """Check all required dependencies."""
    print("SoloKeys GUI - Dependency Check")
    print("=" * 40)

    # Check required modules
    modules = [
        ("PySide6", "Qt6 GUI framework"),
        ("fido2", "FIDO2/WebAuthn library"),
        ("usb.core", "USB device access"),
        # ("pyscard", "Smart card interface"),  # Optional - requires PCSC dev headers
        ("cryptography", "Cryptographic functions"),
        ("requests", "HTTP client"),
    ]

    all_available = True
    for module, description in modules:
        if not check_module_import(module, description):
            all_available = False

    print("\n" + "=" * 40)

    if all_available:
        print("✓ All dependencies are available!")
        print("The GUI should run successfully.")
        print("\nTo run the GUI:")
        print("  PYTHONPATH=src python -m solo_gui.main")
        return 0
    else:
        print("✗ Some dependencies are missing.")
        print("Install with: pip install -r requirements.txt")
        return 1


if __name__ == "__main__":
    sys.exit(main())
