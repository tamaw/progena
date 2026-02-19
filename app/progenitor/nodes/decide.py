"""
Decide Node (compatibility wrapper).

The actual decide node implementation lives in app.progenitor.runtime.decide so it can be
shared by different graphs. This module exists so:
  - self_reflect can point users to nodes/decide.py
  - tests and older imports expecting app.progenitor.nodes.decide continue to work
"""

from __future__ import annotations


