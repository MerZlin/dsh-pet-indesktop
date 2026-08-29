# -*- coding: utf-8 -*-
"""PyInstaller entry for the desktop-pet build without the AI chat feature."""
import sys
from pet.app import main

if __name__ == "__main__":
    sys.exit(main(enable_chat=False))
