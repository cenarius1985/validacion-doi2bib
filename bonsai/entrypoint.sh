#!/bin/sh
set -e

# Descarga el GGUF solo si no existe (una única vez; persiste en el volumen ./bonsai/models)
MODEL_DIR=/models
GGUF="${BONSAI_GGUF:-Ternary-Bonsai-8B-PQ2_0.gguf}"
HF_REPO="${BONSAI_HF_REPO:-prism-ml/Ternary-Bonsai-8B-gguf}"
MODEL_FILE="${MODEL_DIR}/${GGUF}"

if [ ! -s "$MODEL_FILE" ]; then
  echo ">> Descargando ${GGUF} desde ${HF_REPO} (solo la primera vez)..."
  mkdir -p "$MODEL_DIR"
  curl -fL --retry 5 --retry-delay 5 --continue-at - \
    -o "${MODEL_FILE}.part" \
    "https://huggingface.co/${HF_REPO}/resolve/main/${GGUF}"
  mv "${MODEL_FILE}.part" "$MODEL_FILE"
fi

# Perfil del repo oficial Bonsai-demo para la familia ternaria gen-1 (8B):
# thinking DESACTIVADO por defecto (respuestas rapidas en CPU) con muestreo
# temp 0.5 / top-p 0.85; -fa on y -np 1 (un solo slot, KV cache minimo).
# BONSAI_THINKING=1 activa el modo thinking de Qwen3 (temp 0.7 / top-p 0.95,
# respuestas mas lentas pero con razonamiento en reasoning_content).
set -- llama-server \
  -m "$MODEL_FILE" \
  --host 0.0.0.0 \
  --port 8080 \
  -ngl "${BONSAI_NGL:-0}" \
  -fa on \
  -c "${BONSAI_CTX:-8192}" \
  -np 1 \
  --alias "${BONSAI_ALIAS:-ternary-bonsai-8b}"

if [ "${BONSAI_THINKING:-0}" = "1" ]; then
  set -- "$@" \
    --temp "${BONSAI_TEMP:-0.7}" \
    --top-p 0.95 \
    --top-k 20 \
    --min-p 0
else
  set -- "$@" \
    --temp "${BONSAI_TEMP:-0.5}" \
    --top-p 0.85 \
    --top-k 20 \
    --min-p 0 \
    --reasoning-budget 0 \
    --reasoning-format deepseek \
    --chat-template-kwargs '{"enable_thinking": false}'
fi

if [ -n "${BONSAI_THREADS:-}" ]; then
  set -- "$@" -t "$BONSAI_THREADS"
fi

echo ">> Iniciando: $*"
exec "$@"
