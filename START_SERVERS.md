# Start Server Steps

## Prerequisites
- Docker and Docker Compose installed
- Node.js installed
- Python 3.x installed
- Backend environment variables configured in `backend/.env`

## 1. Start Docker Services (Dify + Chatwoot)

```bash
docker-compose up
```

**Services:**
- **Dify Web**: http://localhost:3001
- **Dify API**: http://localhost:5001
- **Chatwoot**: http://localhost:3002
- **PostgreSQL & Redis**: For data storage

## 2. Start Backend

```bash
cd backend
source .venv/bin/activate  # Activate virtual environment
python main.py
```

Backend runs on http://localhost:8000 with hot reload enabled

**Required Environment Variables** (in `backend/.env`):
- `CHATWOOT_BASE_URL` - Chatwoot instance URL
- `CHATWOOT_API_TOKEN` - Chatwoot API token
- `CHATWOOT_ACCOUNT_ID` - Your Chatwoot account ID
- `DIFY_API_URL` - Dify API endpoint
- `DIFY_API_KEY` - Dify API key
- `SPREADSHEET_ID` - Google Sheets spreadsheet ID

## 3. Expose Backend to Internet (for Chatwoot Webhooks)

Chatwoot needs to send webhooks to your backend. Use a tunnel service:

### Option A: localtunnel (Recommended - No signup required)

```bash
npm install -g localtunnel
lt --port 8000
```

### Option B: ngrok (Requires account)

```bash
# Install and authenticate
brew install ngrok
ngrok config add-authtoken YOUR_AUTHTOKEN

# Start tunnel
ngrok http 8000
```

### Option C: Cloudflare Tunnel

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8000
```

**Configure Chatwoot Webhook:**
1. Go to Chatwoot Settings → Integrations → Webhooks
2. Add webhook URL: `https://your-tunnel-url/webhook/chatwoot`
3. Select events: `Message Created`

## 4. Start Frontend

```bash
cd local-connect
npm run dev
```

Frontend runs on http://localhost:3000

**Other commands:**
- `npm run build` - Build for production
- `npm start` - Start production server
- `npm run lint` - Run ESLint

## Startup Order

1. **Docker services** (must be first - backend depends on these)
2. **Backend** (must be running before frontend)
3. **Tunnel for webhooks** (after backend is running)
4. **Frontend** (can start in parallel with backend)

## Troubleshooting

### Port 8000 already in use
```bash
lsof -ti:8000 | xargs kill -9
```

### Port 3000 already in use
```bash
lsof -ti:3000 | xargs kill -9
```

### Check if Docker services are running
```bash
docker-compose ps
```

### Stop all services
```bash
# Stop Docker
docker-compose down

# Stop backend (Ctrl+C in terminal)

# Stop frontend (Ctrl+C in terminal)

# Stop tunnel (Ctrl+C in terminal)
```

## Architecture Overview

```
Frontend (Next.js)     Backend (FastAPI)     Dify API    Chatwoot
localhost:3000    →    localhost:8000    →  localhost:5001  localhost:3002
                        ↓
                    Google Sheets
```

**Webhook Flow:**
1. User sends message via Chatwoot
2. Chatwoot sends webhook to Backend (via tunnel)
3. Backend processes with Dify API
4. Backend sends response back to Chatwoot
5. User receives response in Chatwoot interface
