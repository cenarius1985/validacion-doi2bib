# -*- coding: utf-8 -*-
"""Comando CLI headless: procesa un .bib (o un lote existente) SIN abrir
nada en pantalla y muestra solo los resultados.

Uso:
    python manage.py validar --bib /ruta/archivo.bib [--esperar]
    python manage.py validar --lote 3 --reprocesar --esperar
    python manage.py validar --bib x.bib --tabs 10 --sin-bonsai --sin-busqueda
"""
import time

from django.core.management.base import BaseCommand, CommandError

from referencias.models import Lote, Referencia
from referencias.services import pipeline


class Command(BaseCommand):
    help = ("Valida referencias de un .bib contra doi2bib.org (headless, "
            "N pestañas) y genera informe + corregido.bib")

    def add_arguments(self, parser):
        parser.add_argument("--bib", help="ruta a un .bib (crea lote nuevo)")
        parser.add_argument("--lote", type=int, help="id de lote existente")
        parser.add_argument("--tabs", type=int, default=10)
        parser.add_argument("--sin-bonsai", action="store_true")
        parser.add_argument("--sin-busqueda", action="store_true",
                            help="desactiva la búsqueda por nombre")
        parser.add_argument("--reprocesar", action="store_true",
                            help="resetea RATE_LIMIT/ERROR/MANUAL antes de correr")
        parser.add_argument("--esperar", action="store_true",
                            help="espera el término e imprime resumen")

    def handle(self, *args, **options):
        if options["bib"]:
            with open(options["bib"], encoding="utf-8", errors="replace") as f:
                texto = f.read()
            lote = pipeline.crear_lote_desde_texto(
                options["bib"].replace("\\", "/").split("/")[-1], texto,
                tabs=options["tabs"],
                usar_bonsai=not options["sin_bonsai"],
                buscar_por_nombre=not options["sin_busqueda"])
            self.stdout.write("Lote %d creado (%d referencias)"
                              % (lote.pk, lote.total))
        elif options["lote"]:
            lote = Lote.objects.get(pk=options["lote"])
            if options["reprocesar"] and lote.estado in ("finalizado", "error"):
                pipeline.preparar_reproceso(lote)
        else:
            raise CommandError("indique --bib o --lote")

        pipeline.ejecutar_lote(lote.pk)  # síncrono: corre aquí, headless

        if options["esperar"]:
            while lote.estado not in ("finalizado", "error"):
                time.sleep(2)
                lote.refresh_from_db()

        lote.refresh_from_db()
        self.stdout.write(self.style.HTTP_INFO(
            "\n=== Lote %d: %s ===" % (lote.pk, lote.estado)))
        for r in lote.referencias.order_by("orden"):
            self.stdout.write("%s %-30s %-10s %s" %
                              (r.icono, r.clave[:30], r.estado,
                               (r.detalle or "")[:110]))
        rep = pipeline.ruta_reportes(lote) / "doi_check_report.md"
        corr = pipeline.ruta_reportes(lote) / "corregido.bib"
        self.stdout.write("\nInforme:   %s" % rep)
        self.stdout.write("Corregido: %s" % corr)
        malos = lote.referencias.filter(
            estado__in=["MISMATCH", "BROKEN", "ERROR", "RATE_LIMIT"]).count()
        if malos:
            self.stdout.write(self.style.WARNING(
                "%d referencias requieren atención" % malos))
