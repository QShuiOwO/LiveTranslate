#!/usr/bin/env python3
import sys
from _bootstrap import ROOT
from livetranslate.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["transcribe", *sys.argv[1:]]))
