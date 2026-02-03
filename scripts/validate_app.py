#!/usr/bin/env python
"""
Application Validation Script

Quick validation that the Progenitor app parses and imports correctly.
"""

import sys
from pathlib import Path


def test_imports():
    """Test that all critical modules can be imported."""
    print("=" * 70)
    print("TESTING IMPORTS")
    print("=" * 70)

    tests = [
        ("Core config", "from app.progenitor.config import ProgenitorConfig, get_config"),
        ("State models", "from app.progenitor.state.models import WorkItem, ProgenitorState"),
        ("State manager", "from app.progenitor.state.manager import StateManager"),
        ("LLM client", "from app.progenitor.llm.client import LLMClient"),
        ("Outer loop graph", "from app.progenitor.graph_outer_loop import create_progenitor_graph"),
        ("Inner loop graph", "from app.progenitor.graph_inner_loop import create_inner_loop_graph"),
        ("Progenitor main", "from app.progenitor.main import Progenitor"),
        ("Backend", "from app.backend.main import app"),
        ("Backend client", "from app.backend.client import ProgenitorClient"),
    ]

    all_passed = True
    for name, import_stmt in tests:
        try:
            exec(import_stmt)
            print(f"✓ {name}")
        except Exception as e:
            print(f"✗ {name}: {e}")
            all_passed = False

    print()
    return all_passed


def test_file_compilation():
    """Test that key Python files compile without syntax errors."""
    print("=" * 70)
    print("TESTING FILE COMPILATION")
    print("=" * 70)

    import py_compile

    files = [
        "app/progenitor/main.py",
        "app/progenitor/graph_outer_loop.py",
        "app/progenitor/graph_inner_loop.py",
        "app/progenitor/config.py",
        "app/backend/main.py",
        "app/backend/client.py",
        "cli.py",
    ]

    all_passed = True
    for file_path in files:
        try:
            py_compile.compile(file_path, doraise=True)
            print(f"✓ {file_path}")
        except Exception as e:
            print(f"✗ {file_path}: {e}")
            all_passed = False

    print()
    return all_passed


def test_fastapi_app():
    """Test that FastAPI app is properly configured."""
    print("=" * 70)
    print("TESTING FASTAPI APP")
    print("=" * 70)

    try:
        from app.backend.main import app

        print(f"✓ FastAPI app created")
        print(f"✓ Routes: {len(app.routes)} endpoints")

        # Check for key endpoints
        paths = [r.path for r in app.routes if hasattr(r, 'path')]
        required_endpoints = ['/health', '/v1/models', '/v1/chat/completions']

        for endpoint in required_endpoints:
            if endpoint in paths:
                print(f"✓ Endpoint: {endpoint}")
            else:
                print(f"✗ Missing endpoint: {endpoint}")
                return False

        print()
        return True
    except Exception as e:
        print(f"✗ FastAPI app failed: {e}")
        print()
        return False


def check_config_files():
    """Check that config files exist."""
    print("=" * 70)
    print("CHECKING CONFIG FILES")
    print("=" * 70)

    files = [
        "config/progenitor.json",
        "requirements.txt",
        "Dockerfile",
        "docker-compose.yml",
    ]

    all_exist = True
    for file_path in files:
        path = Path(file_path)
        if path.exists():
            print(f"✓ {file_path}")
        else:
            print(f"✗ Missing: {file_path}")
            all_exist = False

    print()
    return all_exist


def main():
    """Run all validation tests."""
    print("\n" + "=" * 70)
    print("PROGENITOR APPLICATION VALIDATION")
    print("=" * 70)
    print()

    results = {
        "File compilation": test_file_compilation(),
        "Module imports": test_imports(),
        "FastAPI app": test_fastapi_app(),
        "Config files": check_config_files(),
    }

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    all_passed = True
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status} - {test_name}")
        all_passed = all_passed and passed

    print("=" * 70)

    if all_passed:
        print("✓ ALL VALIDATIONS PASSED!")
        print()
        print("The application is ready to run:")
        print("  • CLI: python cli.py serve")
        print("  • Docker: docker-compose up --build")
        print("  • Tests: python -m pytest tests/ -v")
        return 0
    else:
        print("✗ SOME VALIDATIONS FAILED")
        print()
        print("Please fix the issues above before running the application.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
