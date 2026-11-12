#!/usr/bin/env bash
# Run the acceptance checks against a gateway in the kind cluster: k8s/run.sh envoy | litellm
# Expects k8s/setup.sh and `kubectl apply -f k8s/upstreams.yaml -f k8s/gateway.yaml -f k8s/litellm.yaml`.
set -euo pipefail
cd "$(dirname "$0")/.."
gw="${1:?usage: k8s/run.sh envoy|litellm}"
mkdir -p results/k8s
pkill -f "kubectl port-forward" 2>/dev/null || true
pf() { kubectl port-forward "$@" >/dev/null 2>&1 & }
for pair in anthropic-a:9101 anthropic-b:9102 vllm-1:9103 vllm-2:9104; do pf -n spike "svc/${pair%%:*}" "${pair##*:}:8080"; done
pf -n spike svc/otel-lgtm 3000:3000
trap 'pkill -f "kubectl port-forward" 2>/dev/null || true' EXIT
case "$gw" in
  envoy)
    svc=$(kubectl -n envoy-gateway-system get svc -l gateway.envoyproxy.io/owning-gateway-name=aigw-spike -o jsonpath='{.items[0].metadata.name}')
    pf -n envoy-gateway-system "svc/$svc" 1975:1975
    export SPIKE_BASE_URL=http://localhost:1975 SPIKE_ANTHROPIC_PATH=/anthropic/v1/messages
    export SPIKE_CLIENT_KEY=sk-spike-client-key SPIKE_BUDGET_KEY=sk-spike-budget-key
    export SPIKE_THREAD_HEADER=x-agent-thread SPIKE_OTEL_SERVICE=envoy-k8s-spike SPIKE_USAGE_SOURCE=envoy-k8s ;;
  litellm)
    pf -n spike svc/litellm 4000:4000
    sleep 3
    admin=sk-spike-master-key
    gen() { curl -sf -X POST localhost:4000/key/generate -H "Authorization: Bearer $admin" \
              -H 'content-type: application/json' -d "$1" | uv run --no-project python -c "import json,sys; print(json.load(sys.stdin)['key'])"; }
    client=$(gen '{"key_alias": "orchestrator", "models": ["claude-sonnet-5-5", "claude-limited", "qwen-test"], "model_tpm_limit": {"claude-limited": 2000}}')
    budget=$(gen '{"key_alias": "budget-client", "max_budget": 0.0003}')
    export SPIKE_BASE_URL=http://localhost:4000 SPIKE_ANTHROPIC_PATH=/v1/messages
    export SPIKE_CLIENT_KEY="$client" SPIKE_BUDGET_KEY="$budget" SPIKE_ADMIN_KEY=$admin
    export SPIKE_THREAD_HEADER=x-litellm-session-id SPIKE_OTEL_SERVICE=litellm-k8s-spike SPIKE_USAGE_SOURCE=litellm ;;
  *) echo "unknown gateway $gw" >&2; exit 2 ;;
esac
sleep 3
export SPIKE_GATEWAY="$gw-k8s" SPIKE_OPENAI_PATH=/v1/chat/completions
cd .. && uv run pytest spikes -m live -p no:cacheprovider -rA -q --tb=line -s 2>&1 \
  | tee "spikes/results/k8s/$gw.txt" | grep -E "^(PASSED|FAILED)|passed|failed|added latency|test_acceptance.py:[0-9]+: "
