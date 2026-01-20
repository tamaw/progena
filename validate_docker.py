#!/usr/bin/env python
"""
Docker Setup Validation

Checks that Docker and docker-compose files are properly configured.
"""

import subprocess
import sys
from pathlib import Path


def check_file_exists(path: Path, name: str) -> bool:
    """Check if a required file exists."""
    if not path.exists():
        print(f"✗ Missing: {name} ({path})")
        return False
    print(f"✓ Found: {name}")
    return True


def validate_dockerfile():
    """Validate Dockerfile."""
    dockerfile = Path("Dockerfile")
    if not check_file_exists(dockerfile, "Dockerfile"):
        return False

    content = dockerfile.read_text()

    checks = [
        ("python:3.12", "Base image"),
        ("app.chat_server", "Chat server package"),
        ("EXPOSE 8000", "Exposed port"),
        ("curl", "Health check dependency"),
    ]

    all_good = True
    for check, name in checks:
        if check in content:
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ Missing {name}")
            all_good = False

    return all_good


def validate_compose():
    """Validate docker-compose.yml."""
    compose = Path("docker-compose.yml")
    if not check_file_exists(compose, "docker-compose.yml"):
        return False

    content = compose.read_text()

    checks = [
        ("chat_server:", "Chat server service"),
        ("progenitor:", "Progenitor service"),
        ("8000:8000", "Port mapping"),
        ("uvicorn", "Chat server command"),
        ("healthcheck:", "Health check"),
        ("progenitor-net", "Network"),
    ]

    all_good = True
    for check, name in checks:
        if check in content:
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ Missing {name}")
            all_good = False

    return all_good


def validate_env_example():
    """Validate .env.example."""
    env_example = Path(".env.example")
    if not check_file_exists(env_example, ".env.example"):
        return False

    content = env_example.read_text()

    checks = [
        ("OPENAI_API_KEY", "OpenAI key"),
        ("AZURE_OPENAI_ENDPOINT", "Azure endpoint"),
        ("CHAT_SERVER", "Chat server config"),
    ]

    all_good = True
    for check, name in checks:
        if check in content:
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ Missing {name}")
            all_good = False

    return all_good


def check_docker_installed():
    """Check if Docker is installed."""
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            print(f"✓ Docker installed: {result.stdout.strip()}")
            return True
        else:
            print("✗ Docker not working")
            return False
    except Exception as e:
        print(f"✗ Docker not found: {e}")
        return False


def check_compose_installed():
    """Check if docker-compose is installed."""
    try:
        result = subprocess.run(
            ["docker-compose", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            print(f"✓ docker-compose installed: {result.stdout.strip()}")
            return True
        else:
            print("✗ docker-compose not working")
            return False
    except Exception:
        # Try docker compose (v2)
        try:
            result = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                print(f"✓ docker compose installed: {result.stdout.strip()}")
                return True
        except Exception as e:
            print(f"✗ docker-compose not found: {e}")
            return False


def main():
    """Run all validations."""
    print("=" * 70)
    print("Docker Setup Validation")
    print("=" * 70)
    print()

    all_good = True

    print("1. Checking Docker files...")
    print("-" * 70)
    all_good &= validate_dockerfile()
    print()

    all_good &= validate_compose()
    print()

    all_good &= validate_env_example()
    print()

    print("2. Checking Docker installation...")
    print("-" * 70)
    all_good &= check_docker_installed()
    all_good &= check_compose_installed()
    print()

    print("=" * 70)
    if all_good:
        print("✓ All validations passed!")
        print()
        print("To start the services:")
        print("  1. Copy .env.example to .env and configure")
        print("  2. Run: docker-compose up --build")
        print("  3. Access chat server at http://localhost:8000")
        return 0
    else:
        print("✗ Some validations failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
