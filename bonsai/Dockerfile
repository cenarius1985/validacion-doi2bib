# Ternary Bonsai 8B servido con el fork PrismML de llama.cpp.
# OJO: el llama.cpp/Ollama estándar NO puede ejecutar estos GGUF ternarios
# (cuantización custom, ggml type 142); se usa el binario precompilado
# oficial (Ubuntu x64, CPU) del release fijado del fork.
# Vendido en ESTE repo para que el stack sea autocontenido: ningún repo
# externo, ninguna imagen custom en un registry — solo GitHub + HuggingFace.
FROM ubuntu:24.04

ARG PRISM_RELEASE=prism-b10735-842b188
ARG PRISM_ASSET=llama-prism-b10735-842b188-bin-ubuntu-x64.tar.gz

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/llama
RUN curl -fsSL -o /tmp/llama.tar.gz \
      "https://github.com/PrismML-Eng/llama.cpp/releases/download/${PRISM_RELEASE}/${PRISM_ASSET}" \
 && tar -xzf /tmp/llama.tar.gz -C /opt/llama --strip-components=1 \
 && rm -f /tmp/llama.tar.gz \
 && ln -s "$(find /opt/llama -type f -name llama-server | head -n1)" /usr/local/bin/llama-server

ENV LD_LIBRARY_PATH=/opt/llama/lib:/opt/llama/bin:/opt/llama

# El modelo NO se hornea en la imagen (~2 GB): lo descarga el entrypoint al
# volumen bonsai_models la primera vez que se levanta el stack.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
