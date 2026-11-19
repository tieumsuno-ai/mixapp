"""Double-clic pour lancer MixApp (Windows : .pyw = sans fenêtre de console).

« MixApp --cli … » donne accès à la ligne de commande (diagnostic).
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from mixapp.__main__ import main as cli_main
        sys.exit(cli_main(sys.argv[2:]))
    from mixapp.gui import main
    sys.exit(main())
