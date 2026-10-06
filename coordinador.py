#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Coordinador del Validador DOI2BIB: un solo comando para todo.

    python coordinador.py            levanta el stack y abre el navegador
    python coordinador.py --detener  detiene el stack (los datos persisten)
    python coordinador.py --no-navegador   igual, sin abrir el navegador
    python coordinador.py --puerto 9090    usa otro puerto para la web

Solo usa la librería estándar. Requiere Docker (Docker Desktop en
Windows/macOS); Python solo orquesta: construye, espera la web, abre el
navegador y reporta si el LLM Bonsai ya cargó su modelo.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
WEB_POR_DEFECTO = 8090
BONSAI_DEBUG_POR_DEFECTO = 4688

# Colores ANSI (se activan también en consolas Windows modernas).
os.system("")
OK, AVISO, ERROR, INFO, FIN = ("\033[92m", "\033[93m", "\033[91m",
                               "\033[96m", "\033[0m")


def paso(n, total, msg):
    print("%s[%d/%d]%s %s" % (INFO, n, total, FIN, msg))


def bien(msg):
    print("%s  ✔ %s%s" % (OK, msg, FIN))


def avisar(msg):
    print("%s  ! %s%s" % (AVISO, msg, FIN))


def fallar(msg):
    print("%s  ✖ %s%s" % (ERROR, msg, FIN))
    sys.exit(1)


def leer_env(clave, default):
    """Lee una variable del .env del repo (si existe); si no, el default."""
    env = RAIZ / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8", errors="replace").splitlines():
            linea = linea.strip()
            if linea.startswith(clave + "="):
                return linea.split("=", 1)[1].strip() or default
    return default


def comando_compose():
    """'docker compose' (v2) o 'docker-compose' (v1) — el que exista."""
    docker = shutil.which("docker")
    if docker:
        try:
            r = subprocess.run([docker, "compose", "version"], cwd=RAIZ,
                               capture_output=True, timeout=20)
            if r.returncode == 0:
                return [docker, "compose"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    legacy = shutil.which("docker-compose")
    if legacy:
        return [legacy]
    return None


def docker_arriba():
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        return subprocess.run([docker, "info"], cwd=RAIZ, capture_output=True,
                              timeout=25).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def esperar_url(url, segundos, etiqueta):
    """True si la URL responde antes de agotar el plazo."""
    print("     esperando %s " % etiqueta, end="", flush=True)
    limite = time.time() + segundos
    while time.time() < limite:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status < 500:
                    print("lista")
                    return True
        except Exception:  # noqa: BLE001
            pass
        print(".", end="", flush=True)
        time.sleep(3)
    print(" plazo agotado")
    return False


def servicios_compose(compose):
    """Servicios definidos en el compose del repo (set de nombres)."""
    try:
        r = subprocess.run(compose + ["config", "--services"], cwd=RAIZ,
                           capture_output=True, text=True, timeout=30)
        return set(r.stdout.split()) if r.returncode == 0 else set()
    except (OSError, subprocess.TimeoutExpired):
        return set()


def url_bonsai_externa():
    """Para la variante main-externo: BONSAI_BASE_URL del .env visto desde
    el host (host.docker.internal == localhost aquí)."""
    base = leer_env("BONSAI_BASE_URL", "")
    if "://" in base:
        rest = base.split("://", 1)[1].rstrip("/")
        rest = rest.replace("host.docker.internal", "localhost")
        return "http://%s/v1/models" % rest
    return None


def main():
    ap = argparse.ArgumentParser(
        description="Levanta el Validador DOI2BIB y abre el navegador.")
    ap.add_argument("--detener", action="store_true",
                    help="detiene el stack (los datos persisten en volúmenes)")
    ap.add_argument("--no-navegador", action="store_true",
                    help="no abrir el navegador al final")
    ap.add_argument("--puerto", type=int, default=None,
                    help="puerto del host para la web (default: WEB_PORT "
                         "del .env o %d)" % WEB_POR_DEFECTO)
    args = ap.parse_args()

    compose = comando_compose()
    if not compose:
        fallar("Docker no está instalado (o no está en el PATH). "
               "Instala Docker Desktop desde https://www.docker.com/products/docker-desktop")

    if args.detener:
        paso(1, 1, "deteniendo el stack (datos y modelo persisten)...")
        subprocess.run(compose + ["down"], cwd=RAIZ)
        bien("stack detenido. Para volver: python coordinador.py")
        return

    if not docker_arriba():
        avisar("Docker no responde: probablemente Docker Desktop está apagado.")
        if sys.platform == "win32":
            cand = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) \
                / "Docker/Docker/Docker Desktop.exe"
            if cand.exists():
                print("     intentando lanzar Docker Desktop...")
                subprocess.Popen(["cmd", "/c", "start", "", str(cand)],
                                 shell=False)
        print("     cuando Docker esté arriba, vuelve a ejecutar este comando.")
        if not esperar_docker(150):
            fallar("Docker sigue sin responder.")
        bien("Docker arriba")

    puerto_web = args.puerto or int(leer_env("WEB_PORT", WEB_POR_DEFECTO))
    puerto_bonsai = int(leer_env("BONSAI_HOST_PORT", BONSAI_DEBUG_POR_DEFECTO))
    url_web = "http://localhost:%d/" % puerto_web

    paso(1, 3, "construyendo y levantando el stack (web + LLM Bonsai)...")
    r = subprocess.run(compose + ["up", "-d", "--build"], cwd=RAIZ)
    if r.returncode != 0:
        fallar("docker compose falló; revisa el mensaje de arriba.")

    paso(2, 3, "esperando la aplicación...")
    if not esperar_url(url_web, 240, "la web"):
        avisar("la web no respondió en el plazo; abre %s a mano en un minuto."
               % url_web)

    if not args.no_navegador:
        try:
            webbrowser.open(url_web)
            bien("navegador abierto en %s" % url_web)
        except Exception:  # noqa: BLE001
            avisar("no pude abrir el navegador; entra a %s" % url_web)

    if "bonsai" in servicios_compose(compose):
        paso(3, 3, "esperando el LLM Bonsai (solo la 1ª vez descarga ~2 GB)...")
        url_bonsai = "http://localhost:%d/v1/models" % puerto_bonsai
        if esperar_url(url_bonsai, 150, "Bonsai"):
            bien("LLM Bonsai listo: la búsqueda por nombre y los veredictos "
                 "tendrán ayuda del modelo")
        else:
            avisar("Bonsai aún está cargando; la app YA funciona (validación "
                   "determinista) y el LLM se activará solo al terminar.")
    else:
        externa = url_bonsai_externa()
        paso(3, 3, "variante sin LLM en el stack: sondeando Bonsai externo...")
        if externa and esperar_url(externa, 15, "Bonsai externo"):
            bien("Bonsai externo detectado (%s)" % externa)
        else:
            avisar("Bonsai externo no responde; la app funciona igual "
                   "(validación determinista, veredictos LLM vacíos).")

    print()
    bien("Validador DOI2BIB: %s" % url_web)
    print("     - subir .bib -> progreso en vivo -> informe y corregido.bib")
    print("     - para detener todo:  python coordinador.py --detener")


def esperar_docker(segundos):
    limite = time.time() + segundos
    while time.time() < limite:
        if docker_arriba():
            return True
        time.sleep(5)
    return False


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrumpido; el stack sigue corriendo en segundo plano "
              "(usa --detener para pararlo)")
