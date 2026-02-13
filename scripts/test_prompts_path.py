#!/usr/bin/env python
"""Quick test to check prompts path resolution."""

import os

print("=== Environment Variables ===")
print(f"PROGENITOR_PROMPTS_DIR: {os.getenv('PROGENITOR_PROMPTS_DIR', 'NOT SET')}")

print("\n=== DEFAULT_PROMPTS_DIR Calculation ===")
from app.progenitor.llm.prompts import DEFAULT_PROMPTS_DIR

print(f"DEFAULT_PROMPTS_DIR: {DEFAULT_PROMPTS_DIR}")
print(f"Exists: {DEFAULT_PROMPTS_DIR.exists()}")

print("\n=== PromptTemplates Instance ===")
from app.progenitor.llm.prompts import PromptTemplates

pt = PromptTemplates()
print(f"Instance prompts_dir: {pt.prompts_dir}")
print(f"Exists: {pt.prompts_dir.exists()}")

print("\n=== Config from_env ===")
from app.progenitor.config import StorageConfig

cfg = StorageConfig.from_env()
print(f"Config prompts_dir: {cfg.prompts_dir}")
print(f"Exists: {cfg.prompts_dir.exists()}")

print("\n=== Try loading a prompt ===")
try:
    content = pt.get("decide.md")
    print(f"✓ Successfully loaded decide.md ({len(content)} chars)")
except Exception as e:
    print(f"✗ Error: {e}")
