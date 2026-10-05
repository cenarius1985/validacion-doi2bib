# -*- coding: utf-8 -*-
"""Modelos del validador: un Lote por .bib subido, una Referencia por entrada."""
from django.db import models


class Lote(models.Model):
    ESTADOS = [
        ("pendiente", "Pendiente"),
        ("en_curso", "En curso"),
        ("finalizado", "Finalizado"),
        ("error", "Error"),
    ]
    archivo = models.FileField(upload_to="bibs/")
    nombre = models.CharField(max_length=200, help_text="Nombre del .bib original")
    creado = models.DateTimeField(auto_now_add=True)
    iniciado = models.DateTimeField(null=True, blank=True)
    terminado = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=12, choices=ESTADOS, default="pendiente")
    total = models.IntegerField(default=0)
    procesadas = models.IntegerField(default=0)
    tabs = models.IntegerField(default=10, help_text="Pestañas doi2bib en paralelo")
    usar_bonsai = models.BooleanField(default=True)
    buscar_por_nombre = models.BooleanField(default=True)
    log = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-creado"]
        verbose_name = "Lote de validación"
        verbose_name_plural = "Lotes de validación"

    def __str__(self):
        return f"{self.nombre} ({self.creado:%Y-%m-%d %H:%M})"

    def resumen_estados(self):
        return dict(
            self.referencias.values_list("estado").annotate(
                n=models.Count("id")).order_by("estado"))


class Referencia(models.Model):
    ESTADOS = [
        ("PENDIENTE", "Pendiente"),
        ("OK", "OK"),
        ("WARN", "Aviso menor"),
        ("MISMATCH", "Discrepancia"),
        ("BROKEN", "DOI roto"),
        ("SIN_DOI", "Sin DOI"),
        ("HALLADO", "DOI hallado"),
        ("MANUAL", "Revisar a mano"),
        ("RATE_LIMIT", "Rate limit"),
        ("ERROR", "Error"),
    ]
    ICONOS = {
        "PENDIENTE": "⬜", "OK": "✅", "WARN": "⚠️", "MISMATCH": "❌",
        "BROKEN": "⛔", "SIN_DOI": "➖", "HALLADO": "🔎", "MANUAL": "🖐️",
        "RATE_LIMIT": "⏳", "ERROR": "❓",
    }
    lote = models.ForeignKey(Lote, on_delete=models.CASCADE,
                             related_name="referencias")
    orden = models.IntegerField(default=0)
    clave = models.CharField(max_length=200)
    tipo = models.CharField(max_length=50, blank=True)
    doi = models.CharField(max_length=300, blank=True)
    titulo = models.CharField(max_length=600, blank=True)
    autor = models.CharField(max_length=900, blank=True)
    anio = models.CharField(max_length=10, blank=True)
    journal = models.CharField(max_length=400, blank=True)
    cuerpo = models.TextField(blank=True, help_text="Cuerpo BibTeX original")
    estado = models.CharField(max_length=12, choices=ESTADOS, default="PENDIENTE")
    detalle = models.TextField(blank=True)
    doi2bib_bibtex = models.TextField(blank=True)
    doi_propuesto = models.CharField(max_length=300, blank=True)
    candidatos = models.JSONField(default=list, blank=True)
    veredicto_bonsai = models.JSONField(null=True, blank=True)
    similitud = models.FloatField(null=True, blank=True)
    verificada = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["lote", "orden"]
        unique_together = [("lote", "orden")]
        verbose_name = "Referencia"
        verbose_name_plural = "Referencias"

    def __str__(self):
        return f"{self.clave} [{self.estado}]"

    @property
    def icono(self):
        return self.ICONOS.get(self.estado, "❓")
