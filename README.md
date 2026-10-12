# FrontDesk OS

A local-first prototype of the PRD: restaurant and salon operations, deterministic capacity checks, confirmed internal transactions, and shared tools for **Vapi** and **LiveKit + Ollama**.

## Run on your Mac

```bash
python3 scripts/dev.py
```

The launcher installs missing dependencies, seeds once, and starts both services:

- Dashboard: http://127.0.0.1:3000 — select **Open local demo**.
- FastAPI / OpenAPI: http://127.0.0.1:8000/docs
- Native database: `frontdesk.db` (SQLite; ignored by Git).

Node 20.9+ and Python 3.12+ are required. The code was checked on Node 24 and Python 3.14. Seeded locations use 24-hour demo opening hours so the prototype can be tested at any time. Change these under Settings. Ctrl+C stops both launcher processes. Run from the repository root so `.env` resolves correctly.

## Run with PostgreSQL

Start Docker Desktop, then:

```bash
docker compose -f infra/docker-compose.yml up --build
```

This starts PostgreSQL, applies Alembic migrations, seeds the two locations, and starts the API and dashboard. Database data persists in a named Docker volume. Host ports bind to loopback. `.env` is optional in demo mode; copy `.env.example` when configuring credentials. The Docker production stack was built and exercised, including clean migrations, seed, authenticated proxy requests, commit replay, and an actual PostgreSQL booking race.

## Try the working internal flows

1. **Floor:** select a table, seat a party, clear it to cleaning, then make it available. Verify stale staff states before asking for capacity. Available seats across unrelated tables are never treated as one table.
2. **Reservations:** create a future booking, review the readback, and explicitly confirm. Two simultaneous requests cannot commit an overlapping interval. Cancellation and rescheduling are supported for staff.
3. **Waitlist:** add a guest and calculate seating estimates. Estimates simulate earlier waiting parties and include source, timestamp, and confidence.
4. **Takeout:** pause takeout in Kitchen; an attempted order is rejected. Resume and verify the kitchen; select catalog items/modifiers, review price/tax/pickup, confirm, then move the ticket through preparing, ready, and picked up.
5. **Salon:** use the workspace switcher to open Studio Bloom. Book appointments, block a stylist’s time, and check alternatives. The calendar has day and week views.
6. **AI Receptionist → Business tool test:** exercise the real shared backend without voice credentials. Tool tests are labeled **no audio, no LLM** in call history. Confirmation uses an explicit test button.
7. **AI Receptionist → Ollama text experiment:** use the actual local Ollama model and shared tools. This is text only, labeled separately from voice. Default model: `qwen3.5:9b`, found installed on this Mac. Set `OLLAMA_MODEL` to a different installed tool-capable model if needed.
8. **Needs Attention:** verify stale tables, review failed tools and follow-ups, or approve/reject a prepared staff proposal. Voice proposals still require caller confirmation. **Customers** shows real location-associated records with masked contact details.

Vapi is the **primary real phone/web voice path**. LiveKit + Ollama is the **additional local voice prototype**. Neither path is marked connected merely because its code exists. See [voice setup](#voice-setup).

The dashboard follows the supplied CRM reference with a neutral collapsible sidebar, compact header, flat tables and white board cards. Shared components cover floor management, calendars, kitchen orders, call timelines and attention requests. Optional [Supabase PostgreSQL configuration](#supabase-postgresql) is prepared without switching the current data source; FastAPI remains authoritative.

## Voice setup

### Vapi

1. Expose FastAPI through your own HTTPS tunnel or deployment. In Vapi, create a Custom Credential with a Bearer Token matching `VAPI_WEBHOOK_SECRET`.
2. Create a saved assistant with its Server URL set to `https://your-api.example/api/v1/voice/vapi`, referencing that credential. Enable status, final transcript, tool-call and end-of-call-report server messages. Configure the model, STT and TTS in Vapi.
3. Export the shared prompt and tool configuration:

   ```bash
   mkdir -p .runtime
   .venv/bin/python scripts/export_vapi.py \
     --server-url https://your-api.example \
     --credential-id your-credential-id \
     --output .runtime/vapi-assistant.template.json
   ```

   Review/import the draft into your saved assistant. Use a separate saved assistant for the salon.
4. Set `VAPI_PUBLIC_KEY`, `VAPI_RESTAURANT_ASSISTANT_ID`, `VAPI_SALON_ASSISTANT_ID` and `VAPI_WEBHOOK_SECRET` in `.env`, then restart FastAPI.
5. Open **AI Receptionist → Vapi → Start web call**. Inbound phone calls additionally need a phone number attached to the saved assistant. Review provider recording/transcript settings before handling real callers.

The assistant must speak the exact backend readback and wait for caller confirmation. Authenticated final transcripts authorize commit; model-supplied confirmation flags do not. Live audio ordering and interruption behavior still require validation with a configured assistant.

### LiveKit + Ollama

Ollama supplies the LLM; speech recognition and synthesis require separate local services.

1. Start Ollama with an installed tool-capable model. Set `OLLAMA_MODEL` if using a model other than `qwen3.5:9b`.
2. Start the loopback development LiveKit server:

   ```bash
   docker compose -f infra/docker-compose.yml --profile local-voice up livekit
   ```

   Set `LIVEKIT_URL=ws://127.0.0.1:7880`, `LIVEKIT_API_KEY=devkey`, `LIVEKIT_API_SECRET=secret` for this development server.
3. Supply local STT and TTS servers through the `LOCAL_STT_*` and `LOCAL_TTS_*` variables in `.env`. STT must support `/v1/audio/transcriptions`; TTS must support `/v1/audio/speech` with 24 kHz PCM output. These services/models are not installed by the project.
4. Set a long random `AGENT_API_TOKEN` shared by FastAPI and the worker, and set `FRONTDESK_API_URL`. Restart FastAPI, then run:

   ```bash
   .venv/bin/python -m pip install -r services/voice/requirements.txt
   .venv/bin/python services/voice/agent.py dev
   ```

5. Open **AI Receptionist → LiveKit + Ollama → Start web call**. Without the worker and speech servers, an available Ollama model alone cannot answer a voice call.

## Supabase PostgreSQL

Supabase storage configuration is prepared in `.env.supabase.example`; no project is connected by default. FastAPI remains responsible for session authorization, business mutations and SSE updates. No browser database credentials are required.

Use a dedicated Supabase project. Copy its exact session pooler connection details from the Connect panel, URL-encode the password, and download its root certificate for the template's `verify-full` TLS configuration. Preserve your existing `.env` and database before changing only `DATABASE_URL`. With the API stopped, apply migrations from the repository root:

```bash
.venv/bin/alembic -c services/api/alembic.ini upgrade head
```

Optionally run `.venv/bin/python scripts/seed_demo.py` for sample data, then restart with `python3 scripts/dev.py`. Existing SQLite data is not transferred automatically. Integrations reports Supabase connected only after FastAPI successfully queries that database. The default Docker stack continues using its own PostgreSQL service.

## Validate

```bash
.venv/bin/python -m pytest tests -q
npm run typecheck
npm run build
docker compose -f infra/docker-compose.yml config --quiet
```

With local servers running:

```bash
.venv/bin/python scripts/check_local.py
.venv/bin/python scripts/check_ollama.py
```

`check_ollama.py` uses the real local model against a disposable isolated database. It does not create bookings in your dashboard. The unit and contract tests use isolated databases and provider fixtures, never live vendor writes.

For the isolated PostgreSQL check (separate ports and database volume):

```bash
docker compose -p frontdesk-verify -f infra/docker-compose.yml -f infra/docker-compose.verify.yml up --build -d
.venv/bin/python scripts/check_postgres.py
docker compose -p frontdesk-verify -f infra/docker-compose.yml -f infra/docker-compose.verify.yml stop
```

This verification script creates sample records only inside that verification stack.

## Architecture

```text
Next.js dashboard / Vapi / LiveKit worker / Ollama text experiment
                    ↓
           authenticated FastAPI tool boundary
                    ↓
       tenant-scoped operational and capacity engine
                    ↓
    prepare → exact readback → confirmation → recheck → commit
                    ↓
       PostgreSQL or SQLite + audit/event persistence
                    ↓
               SSE dashboard refresh
```

Internal writes are serialized using SQLite `BEGIN IMMEDIATE` or a PostgreSQL transaction advisory lock. This prototype deliberately uses one global write lock; large deployments should scope ordered locks by location. Kitchen buckets are allocated inside that same transaction. Quotes expire after five minutes; price or pickup changes require a new readback. UUID action IDs provide idempotency. Database constraints provide a second guard.

Staff table states have a configurable verification window. A long-lived occupied state is not silently freed. The engine returns unknown when it lacks verified release information. Provider heartbeats and operational imports are not yet activated.

## Repository

```text
apps/web/                 Next.js dashboard, Tailwind, Radix/shadcn-style UI
services/api/app/         API, schemas, ORM, capacity and action logic
services/api/migrations/  Alembic initial schema
services/voice/           optional LiveKit worker and speech configuration
infra/                   Docker Compose
scripts/                 launcher, seed, provider config export, local checks
tests/                   domain, API, provider, LiveKit and Ollama tests
```

## Configuration and limits

- `.env.example` contains variable names and no real provider secrets. Secrets never go to browser JavaScript; Vapi’s **public** key is the intentional exception.
- Demo login is intentionally available when `DEMO_MODE=true`. Keep this prototype on loopback. `DEMO_MODE=false` requires a strong `SESSION_SECRET`, `OWNER_PASSWORD`, and HTTPS for secure session cookies.
- Tenant isolation and role checks are enforced on the backend. The prototype supports one configured owner login plus a membership model; staff invitations and per-stylist visibility assignment are not implemented.
- Recording and persistent transcript retention are off. Final transcripts can still be processed transiently to authorize voice actions. Provider-side recording/transcript settings must also be configured; the API cannot erase artifacts a vendor independently retains.
- Restaurant transactions and salon appointments currently use the **internal authoritative engine**. Square and Toast have real HTTP adapters, connection tests and write guards. OAuth onboarding, provider-to-resource mapping, operational import, and durable reconciliation are unfinished; external actions cannot silently fall back to internal success.
- Voice cancellation/rescheduling and order lookup verify the reference plus matching contact phone. Changes use the same readback/confirmation guard; bookings without a contact phone require staff assistance. Automatic phone transfers, SMS notifications, retention/deletion controls, and billing are not activated.
- This is a working prototype, not a claim that every PRD requirement or live-provider workflow is complete. Live voice and vendor activation remain gated; browser visual QA has not been verified.

No public deployment or external account changes were performed.
