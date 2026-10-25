{{- define "fleet.name" -}}speaker-fleet-dashboard{{- end -}}
{{- define "fleet.labels" -}}
app.kubernetes.io/name: {{ include "fleet.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "fleet.config" -}}
{{- $keys := list "pollSeconds" "timeoutSeconds" "wifiEveryMinutes" "downAfterMinutes" "historyDays" "rescan" "speakers" -}}
{{- $out := dict -}}
{{- range $keys }}{{- $_ := set $out . (index $.Values .) -}}{{- end -}}
{{- toPrettyJson $out -}}
{{- end -}}
