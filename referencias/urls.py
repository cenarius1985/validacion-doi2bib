# -*- coding: utf-8 -*-
from django.urls import path

from . import views

app_name = "referencias"

urlpatterns = [
    path("", views.index, name="index"),
    path("lote/<int:lote_id>/", views.lote, name="lote"),
    path("lote/<int:lote_id>/estado.json", views.estado, name="estado"),
    path("lote/<int:lote_id>/reprocesar", views.reprocesar, name="reprocesar"),
    path("lote/<int:lote_id>/informe", views.informe, name="informe"),
    path("lote/<int:lote_id>/evidencia.json", views.evidencia, name="evidencia"),
    path("lote/<int:lote_id>/corregido.bib", views.corregido, name="corregido"),
    path("lote/<int:lote_id>/eliminar", views.eliminar_lote, name="eliminar"),
    path("referencia/<int:ref_id>/", views.referencia, name="referencia"),
]
