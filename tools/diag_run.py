"""Lance une commande avec une limite de temps et affiche la fin de sa sortie.
    python tools/diag_run.py SECONDES commande arguments…"""
import subprocess, sys, time
limit = float(sys.argv[1]); cmd = sys.argv[2:]
t = time.time()
with open("diag.log", "w") as log:
    p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    try:
        code = p.wait(timeout=limit)
        status = f"terminé (code {code})"
    except subprocess.TimeoutExpired:
        p.kill(); status = f"BLOQUÉ : arrêté au bout de {limit:.0f} s"
print(f"=== {' '.join(cmd[:3])}… : {status} en {time.time()-t:.0f} s")
out = open("diag.log", errors="replace").read().replace("\r", "\n")
print(out[-4000:])
