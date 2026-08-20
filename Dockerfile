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

USER memphant
ENV MEMPHANT_BIND=0.0.0.0:3000
ENV RUST_LOG=info
ENV MEMPHANT_STRUCTURED_STATE_PROMPT_PATH=/etc/memphant/structured-state-v2.txt
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://127.0.0.1:3000/v1/health >/dev/null || exit 1
CMD ["memphant-server"]
