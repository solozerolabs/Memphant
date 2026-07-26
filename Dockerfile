# syntax=docker/dockerfile:1

FROM rust:1.96.1-bookworm AS builder
WORKDIR /app

COPY . .
RUN cargo build --locked --release \
  -p memphant-server \
  -p memphant-worker \
  -p memphant-cli

FROM debian:bookworm-slim AS runtime

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd --system memphant \
  && useradd --system --gid memphant --home-dir /var/lib/memphant --create-home memphant

WORKDIR /app
COPY --from=builder /app/target/release/memphant-server /usr/local/bin/memphant-server
COPY --from=builder /app/target/release/memphant-worker /usr/local/bin/memphant-worker
COPY --from=builder /app/target/release/memphant-cli /usr/local/bin/memphant-cli
COPY --from=builder /app/config/structured-state-v2.txt /etc/memphant/structured-state-v2.txt
RUN ln -s /usr/local/bin/memphant-cli /usr/local/bin/memphant

USER memphant
ENV MEMPHANT_BIND=0.0.0.0:3000
ENV RUST_LOG=info
ENV MEMPHANT_STRUCTURED_STATE_PROMPT_PATH=/etc/memphant/structured-state-v2.txt
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://127.0.0.1:3000/v1/health >/dev/null || exit 1
CMD ["memphant-server"]
