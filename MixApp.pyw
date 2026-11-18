"""Double-clic pour lancer MixApp (Windows : .pyw = sans fenêtre de console)."""
import multiprocessing
import sys

from mixapp.gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
