# -*- coding: utf-8 -*-
"""Vistas: subida de .bib, progreso en vivo, resultados y descargas."""
import shutil
import threading

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import SubirBibForm
from .models import Lote, Referencia
from .services import pipeline

LOTES_POR_PAGINA = 10


def _lanzar_en_hilo(lote_id):
    threading.Thread(target=pipeline.ejecutar_lote, args=(lote_id,),
                     daemon=True).start()


def index(request):
    error = None
    if request.method == "POST":
        form = SubirBibForm(request.POST, request.FILES)
        if form.is_valid():
            texto = request.FILES["archivo"].read().decode("utf-8", "replace")
            nombre = (form.cleaned_data.get("nombre") or ""
                      ).strip() or request.FILES["archivo"].name
            lote = pipeline.crear_lote_desde_texto(
                nombre, texto,
                tabs=form.cleaned_data["tabs"],
                usar_bonsai=form.cleaned_data.get("usar_bonsai", False),
                buscar_por_nombre=form.cleaned_data.get("buscar_por_nombre", False))
            _lanzar_en_hilo(lote.pk)
            return redirect("referencias:lote", lote_id=lote.pk)
        error = form.errors.as_text()
    else:
        form = SubirBibForm()
    lotes = Paginator(Lote.objects.all(), LOTES_POR_PAGINA)
    pagina = lotes.get_page(request.GET.get("page"))
    return render(request, "referencias/index.html",
                  {"form": form, "pagina": pagina, "lotes": pagina.object_list,
                   "error": error})


def eliminar_lote(request, lote_id):
    """Elimina un lote (con sus referencias, informe y corregido) para no
    acumular; se bloquea si está en curso."""
    lote = get_object_or_404(Lote, pk=lote_id)
    if request.method == "POST":
        if lote.estado == "en_curso":
            messages.error(request,
                           "El lote %s está en curso: espera a que termine "
                           "o recarga antes de eliminarlo." % lote.nombre)
            return redirect("referencias:lote", lote_id=lote.pk)
        shutil.rmtree(pipeline.ruta_reportes(lote), ignore_errors=True)
        if lote.archivo:
            lote.archivo.delete(save=False)
        nombre = lote.nombre
        lote.delete()
        messages.success(request, "Lote «%s» eliminado." % nombre)
    return redirect(reverse("referencias:index"))


def lote(request, lote_id):
    lote = get_object_or_404(Lote, pk=lote_id)
    filtro = request.GET.get("estado", "")
    refs = lote.referencias.order_by("orden")
    if filtro:
        refs = refs.filter(estado=filtro)
    return render(request, "referencias/lote.html",
                  {"lote": lote, "refs": refs, "filtro": filtro,
                   "estados": [e[0] for e in Referencia.ESTADOS]})


def estado(request, lote_id):
    """JSON de progreso para el polling de la plantilla."""
    lote = get_object_or_404(Lote, pk=lote_id)
    return JsonResponse({
        "estado": lote.estado,
        "total": lote.total,
        "procesadas": lote.referencias.exclude(estado="PENDIENTE").count(),
        "por_estado": lote.resumen_estados(),
    })


def reprocesar(request, lote_id):
    lote = get_object_or_404(Lote, pk=lote_id)
    if request.method == "POST" and lote.estado in ("finalizado", "error"):
        pipeline.preparar_reproceso(lote)
        _lanzar_en_hilo(lote.pk)
    return redirect("referencias:lote", lote_id=lote.pk)


def referencia(request, ref_id):
    ref = get_object_or_404(Referencia.objects.select_related("lote"), pk=ref_id)
    return render(request, "referencias/referencia.html", {"ref": ref})


def _servir(lote, nombre):
    ruta = pipeline.ruta_reportes(lote) / nombre
    if not ruta.exists():
        raise Http404("Aún no generado (¿está el lote en curso?)")
    return FileResponse(open(ruta, "rb"), as_attachment=True,
                        filename="%s-%s" % (lote.pk, nombre))


def informe(request, lote_id):
    return _servir(get_object_or_404(Lote, pk=lote_id), "doi_check_report.md")


def corregido(request, lote_id):
    return _servir(get_object_or_404(Lote, pk=lote_id), "corregido.bib")
