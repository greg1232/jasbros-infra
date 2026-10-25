{{- define "candy.name" -}}candy-watch{{- end -}}
{{- define "candy.labels" -}}
app.kubernetes.io/name: {{ include "candy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
