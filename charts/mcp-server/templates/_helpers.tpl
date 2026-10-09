{{/* Labels. app.kubernetes.io/name = release name (= server name), so NetworkPolicies can select one server. */}}
{{- define "mcp-server.selectorLabels" -}}
app.kubernetes.io/name: {{ .Release.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "mcp-server.labels" -}}
{{ include "mcp-server.selectorLabels" . }}
app.kubernetes.io/part-of: mcp-server
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{- define "mcp-server.image" -}}
{{- if or (not .Values.image.repository) (not .Values.image.tag) -}}
{{- fail "image.repository and image.tag are required" -}}
{{- end -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end }}

{{- define "mcp-server.authSecretName" -}}
{{- .Values.auth.existingSecret | default (printf "%s-mcp-auth" .Release.Name) -}}
{{- end }}

{{- define "mcp-server.resources" -}}
{{- if eq .Values.size "medium" -}}
requests: {cpu: 100m, memory: 160Mi}
limits: {cpu: 500m, memory: 320Mi}
{{- else if eq .Values.size "small" -}}
requests: {cpu: 25m, memory: 96Mi}
limits: {cpu: 200m, memory: 192Mi}
{{- else -}}
{{- fail "size must be small or medium" -}}
{{- end -}}
{{- end }}

{{/* Host header values the server accepts (DNS-rebinding guard): every form of the Service name, each also with any port. */}}
{{- define "mcp-server.allowedHosts" -}}
{{- $hosts := list -}}
{{- range $h := list .Release.Name (printf "%s.%s" .Release.Name .Release.Namespace) (printf "%s.%s.svc" .Release.Name .Release.Namespace) (printf "%s.%s.svc.cluster.local" .Release.Name .Release.Namespace) -}}
{{- $hosts = concat $hosts (list $h (printf "%s:*" $h)) -}}
{{- end -}}
{{- join "," $hosts -}}
{{- end }}

{{- define "mcp-server.probe" -}}
{{- $ctx := index . 0 -}}
{{- if eq $ctx.Values.probes.type "tcp" }}
tcpSocket:
  port: http
{{- else }}
httpGet:
  path: {{ index . 1 }}
  port: http
{{- end }}
{{- end }}
