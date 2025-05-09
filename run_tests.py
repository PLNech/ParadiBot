#!/usr/bin/env python
"""
Test runner for Paradiso Bot.

This script runs all the unit tests for the Paradiso Bot.
"""

import unittest
import pytest
import sys


if __name__ == "__main__":
    # Run tests using unittest discovery
    print("Running tests with unittest...")
    test_suite = unittest.defaultTestLoader.discover("test")
    test_runner = unittest.TextTestRunner(verbosity=2)
    result = test_runner.run(test_suite)
    
    # Exit with appropriate code
    sys.exit(not result.wasSuccessful()) 