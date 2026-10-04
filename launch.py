"""Source launcher. Portable packages use Start-Suite.cmd with their own runtime."""
from pathlib import Path
import runpy
import sys
sys.dont_write_bytecode=True
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parent/'components/workos/vendor')]
if __name__=='__main__':runpy.run_module('suite.server',run_name='__main__')
