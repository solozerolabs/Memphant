# syntax=docker/dockerfile:1

FROM rust:1.96.1-trixie AS builder
WORKDIR /app

COPY . .
# trixie (glibc >= 2.38): the vendored ONNX Runtime binaries reference
# __isoc23_strto* symbols that bookworm's glibc 2.36 lacks (rust-lld:
# undefined symbol at link). Runtime stage must match the builder's glibc.
RUN cargo build --locked --release -j 2 \
  -p memphant-server \
  -p memphant-worker \
  -p memphant-cli \
  -p memphant-mcp

# Fetch the bge-small model into the fastembed cache layout so the runtime image
# can bake it (see runtime stage). Done HERE, in the builder, so the image is
# self-contained — the model is gitignored and absent from a fresh checkout's
# build context (a `COPY .fastembed_cache …` fails on the remote CI builder).
# `hf_hub_download` writes the exact `models--Xenova--bge-small-en-v1.5/{blobs,
# refs,snapshots}` layout (refs/main -> ea104dac…) fastembed resolves at load.
# Only the 5 files fastembed requests for BgeSmallEnV15. python/pip stay in the
# throwaway builder; the runtime image never carries them.
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 python3-pip \
  && pip3 install --break-system-packages --no-cache-dir huggingface_hub \
  && python3 -c "from huggingface_hub import hf_hub_download; [hf_hub_download('Xenova/bge-small-en-v1.5', f, cache_dir='/app/.fastembed_cache') for f in ['config.json','onnx/model.onnx','special_tokens_map.json','tokenizer.json','tokenizer_config.json']]" \
  && rm -rf /var/lib/apt/lists/*

# One-shot bootstrap image: applies the bundled migrations and gives the served
# capability roles usable credentials. Kept as its own stage so the runtime
# image never carries psql, python, or the migration corpus.
FROM debian:trixie-slim AS bootstrap

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates postgresql-client python3 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY memphant_migrations /app/memphant_migrations
COPY scripts/apply_memphant_migrations.py /app/scripts/apply_memphant_migrations.py
COPY scripts/provision_login_roles.sh /app/scripts/provision_login_roles.sh

FROM debian:trixie-slim AS runtime

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd --system memphant \
  && useradd --system --gid memphant --home-dir /var/lib/memphant --create-home memphant

WORKDIR /app
COPY --from=builder /app/target/release/memphant-server /usr/local/bin/memphant-server
COPY --from=builder /app/target/release/memphant-worker /usr/local/bin/memphant-worker
COPY --from=builder /app/target/release/memphant-cli /usr/local/bin/memphant-cli
COPY --from=builder /app/target/release/memphant-mcp /usr/local/bin/memphant-mcp
COPY --from=builder /app/config/structured-state-v2.txt /etc/memphant/structured-state-v2.txt
RUN ln -s /usr/local/bin/memphant-cli /usr/local/bin/memphant

# Bake the bge-small embedder (downloaded in the builder) so a
# MEMPHANT_EMBEDDINGS-on process never network-pulls the model from HuggingFace
# at first embed (the cold-start dependency that crashed first boot). fastembed
# reads FASTEMBED_CACHE_DIR (else `.fastembed_cache` under CWD, which is /app).
# The internal blobs+snapshots relative symlinks are preserved by COPY because
# both live in the subtree. Server, worker, and mcp all run from /app off this
# one baked path.
COPY --from=builder --chown=memphant:memphant \
     /app/.fastembed_cache/models--Xenova--bge-small-en-v1.5 \
     /app/.fastembed_cache/models--Xenova--bge-small-en-v1.5

USER memphant
ENV MEMPHANT_BIND=0.0.0.0:3000
ENV RUST_LOG=info
ENV FASTEMBED_CACHE_DIR=/app/.fastembed_cache
ENV MEMPHANT_STRUCTURED_STATE_PROMPT_PATH=/etc/memphant/structured-state-v2.txt
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://127.0.0.1:3000/v1/health >/dev/null || exit 1
CMD ["memphant-server"]
