{{- define "mcp-platform.selectorLabels" -}}
{{- $ctx := index . 0 -}}
app.kubernetes.io/name: {{ index . 1 }}
app.kubernetes.io/instance: {{ $ctx.Release.Name }}
{{- end }}

{{- define "mcp-platform.labels" -}}
{{- $ctx := index . 0 -}}
{{ include "mcp-platform.selectorLabels" . }}
app.kubernetes.io/part-of: mcp-platform
app.kubernetes.io/managed-by: {{ $ctx.Release.Service }}
helm.sh/chart: {{ $ctx.Chart.Name }}-{{ $ctx.Chart.Version }}
{{- end }}

{{- define "mcp-platform.secretName" -}}
{{- .Values.existingSecret | default (printf "%s-credentials" .Release.Name) -}}
{{- end }}

{{/* Usage: include "mcp-platform.image" .Values.gateway.image */}}
{{- define "mcp-platform.image" -}}
{{- if not .tag -}}
{{- fail (printf "%s: image.tag is required (make platform sets it from .image-tag)" .repository) -}}
{{- end -}}
{{ .repository }}:{{ .tag }}
{{- end }}

{{- define "mcp-platform.url" -}}
{{- $ctx := index . 0 -}}
{{ $ctx.Values.scheme }}://{{ index . 1 }}
{{- end }}

{{/* Restricted-profile container hardening (DESIGN §8.5). */}}
{{- define "mcp-platform.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* NetworkPolicy peer: the Ingress controller (Traefik) pods in kube-system. */}}
{{- define "mcp-platform.traefikPeer" -}}
- namespaceSelector:
    matchLabels:
      kubernetes.io/metadata.name: kube-system
  podSelector:
    matchLabels:
      {{- toYaml .Values.networkPolicy.traefikPodLabels | nindent 6 }}
{{- end }}
