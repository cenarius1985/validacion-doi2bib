# -*- coding: utf-8 -*-
from django.contrib import admin

from .models import Lote, Referencia


class ReferenciaInline(admin.TabularInline):
    model = Referencia
    extra = 0
    readonly_fields = [f.name for f in Referencia._meta.fields]
    can_delete = False


@admin.register(Lote)
class LoteAdmin(admin.ModelAdmin):
    list_display = ("id", "nombre", "estado", "total", "procesadas",
                    "tabs", "usar_bonsai", "buscar_por_nombre", "creado")
    list_filter = ("estado",)
    readonly_fields = ("creado", "iniciado", "terminado")
    inlines = [ReferenciaInline]


@admin.register(Referencia)
class ReferenciaAdmin(admin.ModelAdmin):
    list_display = ("clave", "lote", "estado", "doi", "doi_propuesto",
                    "similitud", "verificada")
    list_filter = ("estado", "lote")
    search_fields = ("clave", "doi", "titulo")
    readonly_fields = [f.name for f in Referencia._meta.fields]
