#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Golden tests de regresión del comparador (F3-4 del runbook).

Casos REALES del proyecto (lote 321, 2026-10): si este archivo no pasa,
cualquier cambio en bib.py/buscador.py que altere una decisión es un bug.
Solo librería estándar + bib.py: no requiere Django ni red.

    python tests/golden_test.py
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from referencias.services import bib  # noqa: E402


class Normalizacion(unittest.TestCase):
    def test_kohler_acento_latex_con_llaves(self):
        # Weiger_2012: doi2bib trae K\"{o}hler y quedaba 'k o hler'
        self.assertEqual(bib.apellidos('K\\"{o}hler, Matthias'), {"kohler"})

    def test_sufijos_jr_iii(self):
        # Melton III: el sufijo no es parte del apellido
        self.assertEqual(bib.apellidos("Melton, L. J., III"), {"melton"})

    def test_guion_unicode_crossref(self):
        # Cui2023: Crossref usa U+2010 'Three‐Dimensional'
        self.assertEqual(bib.norm("Three\u2010Dimensional"),
                         "three dimensional")

    def test_html_y_latex(self):
        self.assertEqual(bib.norm("Garc{\\'i}a"), "garcia")
        self.assertEqual(bib.norm("A <i>bold</i> title"), "a bold title")


class Anios(unittest.TestCase):
    def test_anio_pm1_es_warn_print_online(self):
        # Chang2015UTE: 2015 local vs 2014 online
        loc = "@article{x, title={T}, author={A, B}, year={2015},"
        self.assertEqual(
            bib.comparar(loc, loc.replace("2015", "2014"))["estado"], "WARN")

    def test_anio_lejano_con_titulo_y_autores_iguales_es_reparable(self):
        # kanis2009meta: citado 2009, real 2004 — el año local era el erróneo
        loc = "@article{x, title={Meta analysis of fracture}, author={Kanis, J and Oden, A}, year={2009},"
        rem = "@article{y, title={Meta analysis of fracture}, author={Kanis, J and Oden, A}, year={2004},"
        self.assertEqual(bib.comparar(loc, rem)["estado"], "WARN")

    def test_anio_lejano_y_titulo_distinto_es_mismatch(self):
        loc = "@article{x, title={Other work entirely}, author={A, B}, year={2009},"
        rem = "@article{y, title={Different study here}, author={A, B}, year={2004},"
        self.assertEqual(bib.comparar(loc, rem)["estado"], "MISMATCH")


class AceptacionDeterminista(unittest.TestCase):
    def test_subtitulo_anadido_un_lado(self):
        ok, _ = bib.coincide_busqueda(
            "@article{x, title={Three-dimensional MRI of fractures}, "
            "author={Perez, A}, year={2020},",
            "@article{y, title={Three-dimensional MRI of fractures: a review}, "
            "author={Perez, A}, year={2020},")
        self.assertTrue(ok)

    def test_cui2023_palabra_intercalada_via_solapamiento(self):
        loc = ("@article{x, title={Three-Dimensional MRI FRACTURE (Fast Field "
               "Echo Resembling A CT) in bone}, author={Cui, E H}, year={2024},")
        rem = ("@article{y, title={Three\u2010Dimensional MRI Fast Field Echo "
               "Resembling a Computed Tomography in bone}, "
               "author={Cui, E H and Chen, T}, year={2024},")
        ok, res = bib.coincide_busqueda(loc, rem)
        self.assertFalse(ok)                 # no es exacta
        self.assertTrue(res.get("cercano"))  # pero va a Bonsai

    def test_kanis_mismo_doi_acepta_ano_lejano(self):
        loc = ("@article{x, title={A meta-analysis of corticosteroid use and "
               "fracture risk}, author={Kanis, J A and Oden, A}, year={2009},")
        rem = ("@article{y, title={A Meta-Analysis of Corticosteroid Use and "
               "Fracture Risk}, author={Kanis, John A and Oden, Anders}, "
               "year={2004},")
        ok, res = bib.coincide_busqueda(loc, rem, mismo_doi=True)
        self.assertTrue(ok)

    def test_gonzalez_woods_book_review_rechazado(self):
        # falso aceptado detectado a mano el 2026-10-05: era la RESEÑA
        loc = "@article{x, title={Digital Image Processing}, author={Gonzalez, R}, year={2008},"
        rem = ("@article{y, title={Book Review: Digital Image Processing, "
               "Third Edition}, author={Masters, B R}, year={2009},")
        ok, res = bib.coincide_busqueda(loc, rem)
        self.assertFalse(ok)
        self.assertIsNone(res.get("cercano"))

    def test_link2009_enciclopedia_rechazada(self):
        # falso aceptado: capítulo 'Osteoporosis' contenido en el título local
        loc = ("@article{x, title={Osteoporosis imaging: state of the art and "
               "advanced imaging}, author={Link, T}, year={2009},")
        rem = "@article{y, title={Osteoporosis}, author={Link, T}, year={2009},"
        ok, res = bib.coincide_busqueda(loc, rem)
        self.assertFalse(ok)
        self.assertIsNone(res.get("cercano"))

    def test_lu2023_parte_de_libro_no_es_cercana(self):
        # el título local es la parte del libro; el DOI, otro capítulo
        loc = ("@article{x, title={MRI of Short- and Ultrashort-T2 Tissues}, "
               "author={Lu, Aiming}, year={2023},")
        rem = ("@article{y, title={Two-Dimensional Ultrashort Echo Time "
               "(2D UTE) Imaging}, author={Lu, Aiming}, year={2023},")
        ok, res = bib.coincide_busqueda(loc, rem)
        self.assertFalse(ok)
        self.assertIsNone(res.get("cercano"))

    def test_generico_titulo_contenido_en_largo_rechazado(self):
        loc = "@article{x, title={Magnetic resonance imaging}, author={A, B}, year={2020},"
        rem = ("@article{y, title={Magnetic resonance imaging of fracture "
               "healing: a pilot study}, author={A, B}, year={2020},")
        ok, res = bib.coincide_busqueda(loc, rem)
        self.assertFalse(ok)


class Guardias(unittest.TestCase):
    def test_buckwalter_corto_ambiguo_no_acepta_solo_determinista(self):
        # quedó en Bonsai (sim 0.774): el determinista NO debe aceptarlo
        loc = ("@article{x, title={Bone biology. Part I: Structure, blood "
               "supply, cells, matrix}, author={Buckwalter, J}, year={1995},")
        rem = "@article{y, title={Bone Biology}, author={Buckwalter, J}, year={1995},"
        ok, _ = bib.coincide_busqueda(loc, rem)
        self.assertFalse(ok)

    def test_solapamiento_bidireccional(self):
        s_loc, s_rem = bib.solapamiento_palabras(
            "Osteoporosis imaging: state of the art", "Osteoporosis")
        self.assertGreater(s_rem, s_loc)  # el corto está contenido en el largo
        self.assertLess(s_loc, 0.5)


class NoArticulo(unittest.TestCase):
    def test_erratum_corrigendum_retraction(self):
        from referencias.services import buscador
        self.assertTrue(buscador.es_no_articulo(
            "Erratum to: Bone density measurement"))
        self.assertTrue(buscador.es_no_articulo(
            "Corrigendum to \u201cUTE imaging of cortical bone\u201d"))
        self.assertTrue(buscador.es_no_articulo(
            "Retraction notice: Deep learning MRI"))
        self.assertFalse(buscador.es_no_articulo(
            "UTE imaging of cortical bone"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
