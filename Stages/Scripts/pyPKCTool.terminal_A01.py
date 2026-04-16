#!/usr/bin/env python3
import importlib.util
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(SCRIPT_DIR, 'pyPKCTool.StageWorldData.py')
DEFAULT_INPUT = os.path.join(SCRIPT_DIR, 'new_beach.pkc')
DEFAULT_JSON = os.path.join(SCRIPT_DIR, 'new_beach.json')
TOOL_DESCRIPTION = 'Edit new_beach.pkc via stage-world semantic JSON.'

spec = importlib.util.spec_from_file_location('pyPKCTool_StageWorldData', MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

if __name__ == '__main__':
	module.main_bound(DEFAULT_INPUT, DEFAULT_JSON, TOOL_DESCRIPTION)
