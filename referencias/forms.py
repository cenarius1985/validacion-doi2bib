# -*- coding: utf-8 -*-
from django import forms


class SubirBibForm(forms.Form):
    nombre = forms.CharField(
        label="Nombre del proyecto o bibliografía (p. ej. MRI, US, paper-x)",
        max_length=200, required=False,
        widget=forms.TextInput(attrs={"placeholder": "Ej.: MRI-bibliografia"}),
    )
    archivo = forms.FileField(label="Archivo .bib")
    tabs = forms.IntegerField(label="Pestañas doi2bib en paralelo",
                              min_value=1, max_value=20, initial=10)
    usar_bonsai = forms.BooleanField(label="Usar Bonsai (LLM local) para casos "
                                           "dudosos y ranking", required=False,
                                     initial=True)
    buscar_por_nombre = forms.BooleanField(
        label="Buscar por nombre (Google/Crossref) las entradas sin DOI o rotas",
        required=False, initial=True)

    def clean_archivo(self):
        f = self.cleaned_data["archivo"]
        if not f.name.lower().endswith(".bib"):
            raise forms.ValidationError("El archivo debe tener extensión .bib")
        if f.size > 20 * 1024 * 1024:
            raise forms.ValidationError("Máximo 20 MB")
        return f
