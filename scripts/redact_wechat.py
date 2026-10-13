#!/usr/bin/env python3
"""Standalone redaction entrypoint using the shared harness implementation."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from personal_agent.credentials import MARK, Redactor, redact_file, run, main
if __name__ == "__main__":
    main()
