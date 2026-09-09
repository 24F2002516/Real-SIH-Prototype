# GeM Bid Compliance Verification — SIH Prototype

## Deploy on Vercel
1. Import this repository/ZIP into Vercel.
2. Framework preset: **Other** (or let Vercel detect it).
3. No build command is required.
4. Deploy.

The frontend calls the API through `/api`, so it works on the deployed domain and does not depend on `localhost`.

### Database persistence
For a reliable multi-user deployment, add a PostgreSQL `DATABASE_URL` environment variable (Neon/Postgres works well with Vercel). Without it, the app uses SQLite under `/tmp`; that is suitable for a short SIH demo but data can reset when the serverless instance is recreated.

## API
- `GET /api/health`
- `GET/POST /api/tenders`
- `GET /api/tenders/<id>`
- `GET/POST /api/tenders/<id>/bids`
- `GET /api/bids/<id>`
- `POST /api/bids/<id>/reevaluate`
- `GET /api/tenders/<id>/report`
- `GET /api/bids/<id>/report`

Supported uploads: PDF, DOCX, TXT, CSV. Maximum request size: 20 MB.

## Real AI Tender Intelligence Chatbot

The Tender Intelligence Assistant now calls an actual AI model through the server-side `/api/ai/chat` endpoint. The browser never receives the API key.

### Vercel setup

Add one of these environment variables in **Vercel → Project → Settings → Environment Variables**:

- `GROQ_API_KEY` — Groq API access for the real AI assistant
- `AI_GATEWAY_API_KEY` — optional Vercel AI Gateway access

Optional:
- `GROQ_MODEL` — defaults to `openai/gpt-oss-120b` (you can change it to another supported Groq model).
- `AI_GATEWAY_BASE_URL` — defaults to `https://ai-gateway.vercel.sh/v1`.
- `GROQ_BASE_URL` — defaults to `https://api.groq.com/openai/v1`.

Do **not** put the key in `index.html`, frontend JavaScript, GitHub, or the ZIP. Add it as a server-side Vercel environment variable and redeploy.

The chatbot sends the user's question plus the selected tender, current bid, compliance results, and extracted document evidence to the AI model. The model's answer is returned to the browser and displayed in the Tender Intelligence Assistant.


## Real AI troubleshooting
The chat endpoint calls the Groq Chat Completions API server-side. Set `GROQ_API_KEY` in Vercel for Production and redeploy. The default model is `openai/gpt-oss-120b`; you can override it with `GROQ_MODEL`. The frontend never receives the API key. If the endpoint returns 503, the response includes a sanitized provider error and Vercel Function Logs record the provider status/message.


## Groq 403 / error code 1010 fix
The Groq server request includes an explicit application User-Agent because Groq is protected by Cloudflare and Python urllib's default `Python-urllib/...` signature can be rejected with HTTP 403 / error code 1010.
