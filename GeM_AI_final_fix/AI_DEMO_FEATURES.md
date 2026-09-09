# AI Features

## Real AI chatbot
The Tender Intelligence Assistant is connected to a real AI model through the backend endpoint `/api/ai/chat`.

The browser sends only the question and context IDs. The backend retrieves the tender/bid context and calls the AI provider using a server-side environment variable.

### Supported providers
- Groq: `GROQ_API_KEY`
- Optional Vercel AI Gateway: `AI_GATEWAY_API_KEY`

### Important
The compliance engine remains deterministic and is the source of the current rule-based compliance status/score. The real AI assistant provides natural-language explanation, contextual Q&A, summaries, comparisons and decision support. Final procurement decisions remain with a human evaluator.


### Provider
The real AI assistant uses Groq Chat Completions through the OpenAI-compatible endpoint. Default model: `openai/gpt-oss-120b`.
