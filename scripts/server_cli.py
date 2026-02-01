#!/usr/bin/env python
"""
Quick Start: Running Backend

This guide shows you how to run and test the backend.
"""

import subprocess
import sys
from pathlib import Path


def run_tests():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("RUNNING TESTS")
    print("=" * 70)

    # Unit tests
    print("\n1. Unit Tests (pytest)...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/backend/", "-v"],
        cwd=Path(__file__).parent,
    )
    if result.returncode != 0:
        return False

    # Integration tests
    print("\n2. Integration Tests...")
    result = subprocess.run(
        [sys.executable, "tests/backend/test_integration.py"],
        cwd=Path(__file__).parent,
    )
    if result.returncode != 0:
        return False

    # E2E tests
    print("\n3. End-to-End Tests (with mock Progenitor)...")
    result = subprocess.run(
        [sys.executable, "tests/backend/test_e2e.py"],
        cwd=Path(__file__).parent,
    )
    if result.returncode != 0:
        return False

    print("\n" + "=" * 70)
    print("✓ ALL TESTS PASSED!")
    print("=" * 70)
    return True


def start_server():
    """Start the backend."""
    print("\n" + "=" * 70)
    print("STARTING BACKEND")
    print("=" * 70)
    print("\nBackend will start on http://localhost:8000")
    print("API Docs available at http://localhost:8000/docs")
    print("\nPress Ctrl+C to stop\n")

    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.backend.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
            ],
            cwd=Path(__file__).parent,
        )
    except KeyboardInterrupt:
        print("\n\nServer stopped.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backend CLI")
    parser.add_argument(
        "command",
        choices=["test", "serve", "mock"],
        help="Command to run",
    )

    args = parser.parse_args()

    if args.command == "test":
        success = run_tests()
        sys.exit(0 if success else 1)

    elif args.command == "serve":
        start_server()

    elif args.command == "mock":
        print("Starting mock Progenitor...")
        subprocess.run(
            [sys.executable, "tests/backend/mock_progenitor.py"],
            cwd=Path(__file__).parent,
        )
