#!/bin/bash
# Arranque del validador: migraciones + servidor (herramienta local).
set -e
cd /app
python manage.py makemigrations referencias --noinput
python manage.py migrate --noinput
exec python manage.py runserver 0.0.0.0:8000
