# Metadata Agent API — Deployment (Kubernetes / Docker)

## Container Image

Das Image wird automatisch bei jedem Push auf `main` und bei neuen Tags gebaut und zu Docker Hub gepusht:

```
openeduhub/metadata-agent-api
```

**Docker Hub:** https://hub.docker.com/r/openeduhub/metadata-agent-api

**Verfügbare Tags:**
- `main` — aktueller Stand von `main`
- `v2.0.0` — spezifische Version (bei Git-Tag `v2.0.0`)

**Pull:**
```bash
docker pull openeduhub/metadata-agent-api:main
```

---

## Container starten

```bash
docker run -d \
  --name metadata-agent-api \
  -p 8000:8000 \
  -e B_API_KEY=<key> \
  -e WLO_GUEST_USERNAME=<user> \
  -e WLO_GUEST_PASSWORD=<pass> \
  openeduhub/metadata-agent-api:main
```

---

## Umgebungsvariablen

### Pflicht (Secrets)

| Variable | Beschreibung |
|---|---|
| `B_API_KEY` | API-Key für B-API LLM-Provider (OpenAI + AcademicCloud via B-API) |

### Optional (Secrets)

| Variable | Beschreibung | Benötigt für |
|---|---|---|
| `OPENAI_API_KEY` | API-Key für nativen OpenAI-Zugang | Nur wenn `llm_provider=openai` |
| `WLO_GUEST_USERNAME` | edu-sharing Login für Upload | `/upload` Endpunkt |
| `WLO_GUEST_PASSWORD` | edu-sharing Passwort für Upload | `/upload` Endpunkt |
| `WLO_REPOSITORY_BASE_URL` | Custom Repository-URL (überschreibt Staging/Prod) | Optional |

### Konfiguration (mit Defaults)

Alle Konfigurationsvariablen haben das Prefix `METADATA_AGENT_`:

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_LLM_PROVIDER` | `b-api-openai` | LLM-Provider: `b-api-openai`, `b-api-academiccloud`, `openai` |
| `METADATA_AGENT_DEBUG` | `false` | Debug-Modus |
| `METADATA_AGENT_DEFAULT_MAX_WORKERS` | `10` | Parallele LLM-Worker (1–20) |
| `METADATA_AGENT_REQUEST_TIMEOUT` | `60` | Request-Timeout in Sekunden |
| `METADATA_AGENT_DEFAULT_CONTEXT` | `default` | Standard Schema-Kontext |
| `METADATA_AGENT_DEFAULT_VERSION` | `1.8.1` | Standard Schema-Version |
| `METADATA_AGENT_NORMALIZATION_ENABLED` | `true` | Normalisierung aktivieren |
| `METADATA_AGENT_SCREENSHOT_METHOD` | `pageshot` | `pageshot` (extern) oder `playwright` (intern, im Container verfügbar) |
| `METADATA_AGENT_CORS_ORIGINS` | `*` | CORS Origins (komma-getrennt oder `*`) |
| `METADATA_AGENT_REPOSITORY_DEFAULT` | `staging` | Standard-Repository: `staging` oder `prod` |

### LLM-Provider-spezifisch

| Variable | Default | Beschreibung |
|---|---|---|
| `METADATA_AGENT_B_API_OPENAI_BASE` | `https://b-api.staging.openeduhub.net/api/v1/llm/openai` | B-API OpenAI Endpoint |
| `METADATA_AGENT_B_API_OPENAI_MODEL` | `gpt-4.1-mini` | B-API OpenAI Modell |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_BASE` | `https://b-api.staging.openeduhub.net/api/v1/llm/academiccloud` | B-API AcademicCloud Endpoint |
| `METADATA_AGENT_B_API_ACADEMICCLOUD_MODEL` | `deepseek-r1` | B-API AcademicCloud Modell |
| `METADATA_AGENT_OPENAI_API_BASE` | `https://api.openai.com/v1` | Nativer OpenAI Endpoint |
| `METADATA_AGENT_OPENAI_MODEL` | `gpt-4o-mini` | Nativer OpenAI Modell |

---

## Health Check

```
GET http://localhost:8000/health
```

Response:
```json
{
  "status": "ok",
  "version": "2.0.0"
}
```

Im Container ist ein HEALTHCHECK konfiguriert (Interval: 30s, Timeout: 10s, Retries: 3).

---

## Ports

| Port | Protokoll | Beschreibung |
|---|---|---|
| `8000` | HTTP | FastAPI / Uvicorn |

---

## Ressourcen-Empfehlung (Kubernetes)

| Ressource | Request | Limit |
|---|---|---|
| CPU | 500m | 2000m |
| Memory | 512Mi | 2Gi |

Das Image enthält Playwright + Chromium für Screenshots (~400 MB). Wenn nur `pageshot` (externe API) genutzt wird, reicht weniger Memory.

---

## Kubernetes Deployment (Beispiel)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: metadata-agent-api
  labels:
    app: metadata-agent-api
spec:
  replicas: 1
  selector:
    matchLabels:
      app: metadata-agent-api
  template:
    metadata:
      labels:
        app: metadata-agent-api
    spec:
      containers:
        - name: metadata-agent-api
          image: openeduhub/metadata-agent-api:main
          ports:
            - containerPort: 8000
          env:
            - name: B_API_KEY
              valueFrom:
                secretKeyRef:
                  name: metadata-agent-secrets
                  key: b-api-key
            - name: WLO_GUEST_USERNAME
              valueFrom:
                secretKeyRef:
                  name: metadata-agent-secrets
                  key: wlo-username
            - name: WLO_GUEST_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: metadata-agent-secrets
                  key: wlo-password
            - name: METADATA_AGENT_LLM_PROVIDER
              value: "b-api-openai"
            - name: METADATA_AGENT_DEFAULT_MAX_WORKERS
              value: "10"
            - name: METADATA_AGENT_SCREENSHOT_METHOD
              value: "playwright"
          resources:
            requests:
              cpu: 500m
              memory: 512Mi
            limits:
              cpu: 2000m
              memory: 2Gi
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: metadata-agent-api
spec:
  selector:
    app: metadata-agent-api
  ports:
    - port: 80
      targetPort: 8000
  type: ClusterIP
```

### Secrets anlegen:

```bash
kubectl create secret generic metadata-agent-secrets \
  --from-literal=b-api-key='<B_API_KEY>' \
  --from-literal=wlo-username='<WLO_GUEST_USERNAME>' \
  --from-literal=wlo-password='<WLO_GUEST_PASSWORD>'
```

---

## API-Dokumentation

Nach dem Start erreichbar unter:
- **Swagger UI:** `http://<host>:8000/docs`
- **ReDoc:** `http://<host>:8000/redoc`
- **OpenAPI JSON:** `http://<host>:8000/openapi.json`

---

## Unterschied zu Vercel-Deployment

| Feature | Docker / K8s | Vercel |
|---|---|---|
| Playwright Screenshots | ✅ (`playwright` Methode) | ❌ (nur `pageshot` extern) |
| Langlaufende Requests | ✅ (kein Timeout-Limit) | ⚠️ (max 60s) |
| Skalierung | Manuell (Replicas) | Automatisch |
| Kosten | Eigene Infrastruktur | Vercel Free/Pro |
