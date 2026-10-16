---
title: Validation severity
description: Rates the severity of build validation findings.
---
# Validation Severity Assessment

You are a Python build validation expert. Analyze the following validation findings from a build/execution cycle and assess each finding's severity.

## Context

**Project directory**: {project_directory}
**Iteration**: {iteration}

## Findings to Assess

{findings_json}

## Instructions

For each finding, assess:
1. **severity**: `low` (cosmetic/informational), `medium` (should fix but not blocking), `high` (likely blocks functionality), `critical` (definitely blocks, system broken)
2. **reasoning**: Brief explanation of why this severity level
3. **suggested_fix**: Concrete remediation step the developer should take
4. **is_root_cause**: Whether this finding is the root cause of other findings in the list

## Severity Guidelines

- **critical**: Smoke test failures, import errors, syntax errors, missing entrypoints, crash-on-start
- **high**: Test failures, type errors that break runtime, missing dependencies, unhandled exceptions
- **medium**: Type warnings, deprecation warnings, style issues flagged by tools, slow performance
- **low**: Informational messages, optional improvements, minor style suggestions

Also identify which finding IDs should **block progress** (i.e., the build should not be considered successful until these are resolved).
