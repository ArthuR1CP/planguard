{{- define "shop.labels" -}}
app.kubernetes.io/name: shop
app.kubernetes.io/instance: {{ .Release.Name }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
