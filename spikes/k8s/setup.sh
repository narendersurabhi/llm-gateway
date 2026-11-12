#!/usr/bin/env bash
# Local Kubernetes for the Envoy AI Gateway spike. Pinned versions; values files fetched at the
# release tag (not main). Re-runnable. Tear down with: bin/kind delete cluster --name aigw-spike
set -euo pipefail
cd "$(dirname "$0")/.."
KIND=bin/kind HELM=bin/helm
AIGW=v1.2.0 EG=v1.9.3
RAW=https://raw.githubusercontent.com/theagentrouter/agent-router/$AIGW
mkdir -p results/k8s
$KIND get clusters | grep -qx aigw-spike || $KIND create cluster --name aigw-spike --wait 120s
kubectl config use-context kind-aigw-spike >/dev/null

curl -sSfo results/k8s/eg-values.yaml "$RAW/manifests/envoy-gateway-values.yaml"
curl -sSfo results/k8s/eg-ratelimit-addon.yaml "$RAW/examples/token_ratelimit/envoy-gateway-values-addon.yaml"
curl -sSfo results/k8s/redis.yaml "$RAW/examples/token_ratelimit/redis.yaml"

kubectl apply -f results/k8s/redis.yaml
$HELM upgrade -i eg oci://docker.io/envoyproxy/gateway-helm --version "$EG" \
  --namespace envoy-gateway-system --create-namespace \
  -f results/k8s/eg-values.yaml -f results/k8s/eg-ratelimit-addon.yaml
kubectl wait --timeout=5m -n envoy-gateway-system deployment/envoy-gateway --for=condition=Available

$HELM upgrade -i aieg-crd oci://docker.io/envoyproxy/ai-gateway-crds-helm --version "$AIGW" \
  --namespace envoy-ai-gateway-system --create-namespace
$HELM upgrade -i aieg oci://docker.io/envoyproxy/ai-gateway-helm --version "$AIGW" \
  --namespace envoy-ai-gateway-system --create-namespace \
  --set "extProc.extraEnvVars[0].name=AI_GATEWAY_TRACING_SEMCONV" \
  --set "extProc.extraEnvVars[0].value=gen_ai" \
  --set "extProc.extraEnvVars[1].name=OTEL_EXPORTER_OTLP_ENDPOINT" \
  --set "extProc.extraEnvVars[1].value=http://otel-lgtm.spike.svc.cluster.local:4318" \
  --set "extProc.extraEnvVars[2].name=OTEL_SERVICE_NAME" \
  --set "extProc.extraEnvVars[2].value=envoy-k8s-spike"
kubectl wait --timeout=5m -n envoy-ai-gateway-system deployment/ai-gateway-controller --for=condition=Available
kubectl rollout status -n redis-system deployment/redis --timeout=3m
echo "installed: Envoy Gateway $EG, AI Gateway $AIGW"
