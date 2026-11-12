#!/usr/bin/env bash
# Run the acceptance checks against one gateway: ./run_spike.sh litellm | envoy
# Expects `docker compose up -d` in this directory first (WireMock, Redis, otel-lgtm).
set -euo pipefail
cd "$(dirname "$0")"
gw="${1:?usage: run_spike.sh litellm|envoy}"
export PROVIDER_KEY_A=sk-provider-a PROVIDER_KEY_B=sk-provider-b
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
log="results/$gw.log"; mkdir -p results
case "$gw" in
  litellm)
    export LITELLM_MASTER_KEY=sk-spike-client-key OTEL_SERVICE_NAME=litellm-spike
    export OTEL_EXPORTER=otlp_http OTEL_ENDPOINT=http://localhost:4318/v1/traces
    uvx --from 'litellm[proxy]==1.106.6' --with opentelemetry-sdk==1.38.0 \
      --with opentelemetry-exporter-otlp-proto-http==1.38.0 litellm --config litellm/config.yaml --port 4000 >"$log" 2>&1 &
    pid=$!; url=http://localhost:4000; health=/health/liveliness
    export SPIKE_ANTHROPIC_PATH=/v1/messages SPIKE_OPENAI_PATH=/v1/chat/completions
    export SPIKE_OTEL_SERVICE=litellm-spike SPIKE_THREAD_HEADER=x-litellm-session-id ;;
  envoy)
    export OTEL_SERVICE_NAME=envoy-spike AI_GATEWAY_TRACING_SEMCONV=gen_ai
    ./bin/aigw run envoy/config-standalone.yaml >"$log" 2>&1 &
    pid=$!; url=http://localhost:1975; health=/v1/models
    export SPIKE_ANTHROPIC_PATH=/anthropic/v1/messages SPIKE_OPENAI_PATH=/v1/chat/completions
    export SPIKE_OTEL_SERVICE=envoy-spike SPIKE_THREAD_HEADER=x-agent-thread ;;
  *) echo "unknown gateway $gw" >&2; exit 2 ;;
esac
trap 'kill $pid 2>/dev/null || true' EXIT
for _ in $(seq 1 120); do curl -s -o /dev/null "$url$health" && break; sleep 1; done
export SPIKE_GATEWAY=$gw SPIKE_BASE_URL=$url
cd .. && uv run pytest spikes -m live -p no:cacheprovider -rA -q --tb=line -s 2>&1 | tee "spikes/results/$gw.txt" | tail -30
