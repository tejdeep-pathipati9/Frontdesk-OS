# FrontDesk OS

A local-first prototype of the PRD: restaurant and salon operations, deterministic capacity checks, confirmed transactions with a configured authority, and shared tools for **Vapi** and **LiveKit + Ollama**.

## Run on your Mac

```bash
python3 scripts/dev.py
```

The launcher prepares the local owner account, preserves existing SQLite records, installs missing dependencies and starts both services:

- Dashboard: http://127.0.0.1:3000 — sign in with the local owner account.
- FastAPI / OpenAPI: http://127.0.0.1:8000/docs
- Native database: `frontdesk.db` (SQLite; ignored by Git).

Node 20.9+ and Python 3.12+ are required. The code was checked on Node 24 and Python 3.14. Initial locations use 24-hour opening hours so the prototype can be tested at any time. Change these under Settings. Ctrl+C stops both launcher processes. Run from the repository root so `.env` resolves correctly.

## Momentuum Bistro login and local data

The existing restaurant is now **Momentuum Bistro**. Its stable location ID is retained so existing table states, reservations, orders, customers, call history and saved voice mappings stay intact. Studio Bloom remains available as the existing salon workspace. Repeated setup does not reset records or staff verification.

```bash
.venv/bin/python scripts/setup_local.py --show-login
```

This displays the owner email and password from your ignored local `.env`. The default email is `owner@momentuumbistro.local`; the password is generated on first setup and retained afterward. No credentials are committed to Git. The login screen uses email/password; there is no passwordless shortcut. `DEMO_MODE=true` keeps prototype features and loopback HTTP working; it is separate from the disabled `ALLOW_DEMO_LOGIN` flag.

SQLite was already configured. `DATABASE_URL=sqlite:///./frontdesk.db` remains unchanged. SQLAlchemy, SQLite WAL, foreign keys and `BEGIN IMMEDIATE` serialize conflicting writes. Bookings, table/kitchen state, orders, customers, calls, tools and audit events persist in this file across restarts. Vapi's webhook and LiveKit's agent tools use the same API session layer and database. Dashboard updates still use FastAPI SSE. Local recordings/transcript retention settings continue to apply.

SQLite startup now applies Alembic migrations. Existing unversioned databases are adopted only after their schema matches the application exactly; unknown schemas are left untouched. Adoption/upgrades create a SQLite-consistent backup under ignored `.runtime/backups/`. Relative database paths resolve to the repository root, so launching Alembic from `services/api` does not create a second database.

No Supabase authentication, cloud database or Docker deployment is needed. PostgreSQL support is retained as an optional SQLAlchemy configuration for later; domain logic remains shared. Mock integration cards still say **Mock**, and non-audio tool tests remain identified separately from actual calls. Repetitive demo badges are removed from the business UI.

## Before the presentation

Keep the local app running with `python3 scripts/dev.py`. A few minutes before the presentation, refresh the seeded restaurant's observations:

```bash
.venv/bin/python scripts/prepare_rehearsal.py --refresh-sample-state
```

This backs up and checks SQLite, refreshes sample floor/kitchen verification, and retains bookings, orders, customers, call history, occupied tables, closures and pauses. It refuses connected-provider floor data. This is sample preparation, not a claim that occupancy was physically observed. Observations become stale after the configured freshness window (initially 30 minutes); verify actual changes from the floor/kitchen controls during use.

Keep `.venv/bin/python scripts/start_vapi_demo.py` running and the Mac awake for the temporary webhook tunnel. Restart that launcher after a network loss or tunnel expiry. Check readiness with:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/rehearse_local.py
.venv/bin/python scripts/check_local.py
.venv/bin/python scripts/check_vapi_setup.py
```

The rehearsal uses a disposable SQLite copy for booking/change/cancellation, kitchen/order, salon and mock-provider transactions; it does not add rehearsal records to your dashboard. The last two checks inspect the running app and Vapi configuration. Then make one real microphone call: request a table for two tomorrow at 7 PM, confirm once, and verify the booking reference and linked reservation in Calls. Automated checks do not replace this audio test. Vapi is the main voice demonstration; LiveKit voice still requires a configured server and speech worker. Local Ollama text/tool testing can run independently.

## Call history and AI Assistant

**Settings → Transcript retention** supports Off or Latest 10 calls. This is a per-location policy covering Vapi and LiveKit; diagnostics and text experiments do not consume that window. Final speech is captured from the beginning of the call, without requiring a pending booking. Replayed webhook events do not duplicate text. Contact/card digit sequences and credential-like strings are redacted; caller objections clear that call's text and stop storage while confirmation checks still run transiently. Retention policy is distinct from caller consent, so the application does not fabricate a consent flag. Reapply the Vapi assistant configuration after changing the notice policy.

Transcripts that were never saved cannot be recovered from the earlier calls. New retained transcripts appear in Calls, with reservation/order outcomes, seating estimates, waitlist entries and staff review decisions. Cancellations and reschedules link to the original booking even when it was created in another call. Open call details refresh as new events arrive. Confirmed backend actions update the operational pages through SSE; transcript prose cannot independently create a booking or order.

**AI Assistant** replaces the Customers navigation entry. `GET /api/v1/assistant/summary` provides scoped metrics; `POST /api/v1/assistant/query` interprets questions with local Ollama structured output and returns answers calculated from SQLite records, with timestamps and record links. The model selects only validated read-only filters: it never generates SQL, numeric facts, or business mutations. If Ollama is unavailable or returns an invalid plan, keyword routing supplies the supported saved-record reports. Calls, bookings, cancellations, orders, waits, menus, integrations and staff reviews are supported. Questions remain in the current browser session, not the call log.

## Optional PostgreSQL migration later

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
6. **AI Receptionist → Advanced settings → Action diagnostics:** exercise the real shared backend without voice credentials. Tool tests are labeled **no audio, no LLM** in call history. Confirmation uses an explicit test button.
7. **AI Receptionist → Advanced settings → Text test:** use the actual local Ollama model and shared tools. This is text only, labeled separately from voice. Default model: `qwen3.5:9b`, found installed on this Mac. Set `OLLAMA_MODEL` to a different installed tool-capable model if needed.
8. **Needs Attention:** verify stale tables, review failed tools and follow-ups, or approve/reject a prepared staff proposal. Voice proposals still require caller confirmation. **AI Assistant** answers questions about saved calls and operational activity with links to the supporting records. Guest data remains attached to bookings and orders; the separate Customers page has been removed.

Vapi is the **primary real phone/web voice path**. LiveKit + Ollama is the **additional local voice prototype**. Neither path is marked connected merely because its code exists. See [voice setup](#voice-setup).

The dashboard follows the supplied CRM reference with a neutral collapsible sidebar, compact header, flat tables and white board cards. Shared components cover floor management, calendars, kitchen orders, call timelines and attention requests. The workspace uses local SQLite through SQLAlchemy; FastAPI remains authoritative. Supabase is not required.

## Restaurant software and synchronization

Keep the existing seed location working. Use **Add location** to create a separate restaurant and choose existing software, or use **Integrations → Import supported configuration**. These are clearly labeled simulated providers, with independent persistent provider records rather than canned success responses. Live connectors remain separate and disabled until authorized access and normalization are activated.

| Source | Imported operations | Authoritative writes | Explicitly unavailable |
| --- | --- | --- | --- |
| OpenTable mock | Existing reservations and booking policies | Create, reschedule and cancel restaurant bookings | Menus, orders, physical table status |
| Toast mock | Table definitions, menu, POS check/payment signals and orders | None; internal or staff-confirmed booking fallback | Booking availability, order writes, verified occupancy |
| Square mock | Catalog, stock and orders | Takeout order submission/lifecycle | Restaurant reservations, table status |
| Internal | Staff tables, kitchen and reservations | Existing atomic booking/order engine | POS-derived occupancy |

Mock capabilities describe these fixtures, not the permissions of a real vendor account. [OpenTable access](https://docs.opentable.com/) is approval-based. [Square Bookings](https://developer.squareup.com/docs/bookings-api/what-it-is) handles staff/service appointments; this prototype does not treat it as a restaurant table-reservation API. [Toast reservation integrations](https://doc.toasttab.com/doc/cookbook/apiIntegrationChecklistReservation.html) can use configuration and POS activity where authorized; a paid check does not establish a free table.

Select one **booking source of truth** and one **order source of truth** in Integrations. An external commit creates one provider record and a local projection with the same external reference; it does not allocate internal tables or make a second internal reservation. Active conflicting commitments, proposals and uncertain writes block source changes. Imported POS orders remain read-only unless their provider is selected and supports writes. Catalog/source mismatches block submission.

For a party of 12, the OpenTable mock checks its verified private dining arrangement and existing reservations. It includes an existing conflicting booking tomorrow at 7 PM. Without provider booking support, only staff-configured individual tables or explicitly verified combinations can establish capacity. **Request staff review** adds an Attention item; staff can verify a physical arrangement, then verify table states and complete a separately confirmed booking. Staff-request mode disables automatic AI bookings.

Import, synchronize and reconnect retain stable provider identities. Projections carry provider, external ID, observed/received timestamps; connections expose full-sync/attempt timestamps, freshness and errors. Imported tables require staff verification. POS signals never refresh table verification. Staff pauses, table overrides, kitchen controls and disabled menu items survive sync. Catalog freshness and provider availability are rechecked before commitments. Disconnect hides imported floor/catalog configuration from the internal engine while retaining source records for traceability.

Use **Provider test scenario** then reimport to exercise `normal`, `unavailable`, `stale`, `failure` or `ambiguous`. An ambiguous creation is persisted as `pending_confirmation`; repeated commit calls do not repeat the write. A successful sync reconciles by external reference, updates the projection/call, and closes the pending attention item.

Optional polling of connected sample providers:

```bash
.venv/bin/python scripts/poll_integrations.py --once
.venv/bin/python scripts/poll_integrations.py --interval 60
```

The owner-authenticated demo event boundary is `POST /api/v1/integrations/{mock_provider}/mock-events`. It accepts `location_id`, `event_id`, `external_id`, `kind`, offset-aware `observed_at`, and a provider-specific validated `data` payload. OpenTable reservation events use `guest_name`, `party_size`, `scheduled_at`, `duration_minutes`, `seating_id`, `status`; Toast signals use `table_guid`, `check_state`; Square catalog events use `item_name`, `category`, `amount_minor`, `in_stock`. Duplicate and older events are harmless. A partial webhook never renews the full-sync heartbeat. Schemas and routes are in FastAPI OpenAPI.

For real integrations, retain this boundary: server-held account credentials and verified scopes → authorized fetch outside the write transaction → provider-specific validation/normalization → short transactional projection update. Poll with cursors/checkpoints where available; signed vendor webhooks use server-side account/location mapping and event deduplication. Capability and sync failures block dependent writes. Real write timeouts need durable provider idempotency/reconciliation before retry; do not execute network writes inside the prototype's global database lock. The current worker/event routes simulate this flow and make **no live vendor API writes**.

After upgrading an existing migrated PostgreSQL database, run `alembic upgrade head` before restarting. The additive migration keeps existing business records. Native startup migrates SQLite without resetting data. Vapi and LiveKit retain the same backend tools; regenerate Vapi configuration with the existing setup script after changing prompts or tool contracts.

## Voice setup

### Vapi

For automatic account configuration, save `VAPI_PUBLIC_KEY` and `VAPI_PRIVATE_KEY` in the ignored root `.env`, and generate a strong `VAPI_WEBHOOK_SECRET`. Keep the private key server-side. With FastAPI running, start the webhook-only gateway:

After installing `cloudflared`, the combined launcher starts the gateway/tunnel and updates both saved assistants to the new temporary URL:

```bash
.venv/bin/python scripts/start_vapi_demo.py
```

Keep that terminal and the Mac awake while testing. If the tunnel expires after a network loss, restart the launcher. It reuses the saved credential/assistant IDs; it does not create test calls. To start the components separately:

```bash
.venv/bin/python scripts/vapi_gateway.py
```

Point a temporary HTTPS tunnel at **port 8010**, which exposes only the authenticated Vapi webhook. For Cloudflare's local tunnel tool:

```bash
cloudflared tunnel --url http://127.0.0.1:8010 --no-autoupdate --protocol http2
```

Use its printed HTTPS base URL to configure and verify the two saved assistants:

```bash
.venv/bin/python scripts/setup_vapi.py --server-url https://YOUR-TUNNEL.trycloudflare.com --vertical both --apply
```

Web-call startup now checks the public, authenticated webhook before enabling audio. It returns a clear connection error when the tunnel is unreachable; it does not create a test call or booking. During a web call, the page checks for connection loss or a changed tunnel every 15 seconds and stops the session so it cannot keep offering disconnected business actions. Start a fresh call after reconnection.

`start_vapi_demo.py` keeps one connection manager running, checks webhook health, and creates/reconfigures a new tunnel after two failed checks or a stopped tunnel process. On macOS it prevents idle sleep while running. Keep this process open; shutting it down still disconnects the temporary webhook. Readiness probes use a HTTPS public-DNS lookup only for the configured `trycloudflare.com` hostname if system DNS fails, retaining TLS hostname verification and refusing private IP answers. No system DNS settings are changed. This is for the local demonstration; a permanent deployment should use a stable webhook URL.

The helper verifies the private key, creates/reuses a Bearer webhook credential, saves restaurant/salon assistants, disables provider-side recording and retained artifacts, and writes their IDs into `.env`. It does not place calls or purchase numbers. Its initial configuration uses the existing GPT-4.1 mini model, Deepgram Nova-3, and Vapi's Elliot voice; these can be changed in the Vapi dashboard. Restart FastAPI after updating `.env`, then run `.venv/bin/python scripts/check_vapi_setup.py`. Restrict the Vapi public key to your frontend origin (`http://127.0.0.1:3000` for native development) and these saved assistants. A temporary tunnel's URL changes when restarted; rerun setup with the new URL.

To configure manually through the Vapi dashboard:

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
5. Open **AI Receptionist → Test call → Start call** (Vapi selected in Advanced settings). Inbound phone calls additionally need a phone number attached to the saved assistant. Review provider recording/transcript settings before handling real callers.

The assistant must speak the exact backend readback and wait for caller confirmation. Authenticated final transcripts authorize commit; model-supplied confirmation flags do not. Live audio ordering and interruption behavior still require validation with a configured assistant.

Final assistant speech may arrive in segments. The backend matches the prepared party/service, date and time across consecutive final segments, tolerating spoken numbers, ordinal dates and clock formatting. New bookings permit narrow pronunciation differences in the guest name; this is not used to verify private lookups or booking changes. Readback matcher states and sanitized confirmation metadata persist when transcript storage is off; raw speech does not. Corrections reset the proposal's readback authorization. An interruption clears confirmation and resets an incomplete readback; a caller can still agree after a completed readback. Partial readbacks and early agreement cannot create a booking. Commit briefly waits outside the database writer lock for delayed final transcript delivery. Natural replies such as “That is right” are accepted; no magic confirmation phrase is required.

The voice model interprets customer requests and selects typed tools; tool definitions are API contracts, not a vocabulary of allowed customer commands. Vapi remains responsible for the conversation, transcription and speech. An optional **hybrid confirmation interpreter** inside FastAPI supports unfamiliar agreement wording for Vapi, LiveKit and the local text experiment. It does not replace the voice model or generate business actions.

```text
Authenticated final caller speech → current server-held proposal + completed readback
  → clear agreement: immediate deterministic confirmation
  → unfamiliar wording: local Ollama JSON classifier
  → confirm / change / reject / question / unclear
  → active call + latest turn + unchanged proposal + permissions
  → revalidate capacity, price and configured provider → idempotent commit
```

Enable it in the root `.env`:

```dotenv
OLLAMA_CONFIRMATION_ENABLED=true
OLLAMA_CONFIRMATION_MODEL=qwen3.5:9b
OLLAMA_CONFIRMATION_TIMEOUT=3
```

The existing installed model is used independently of `OLLAMA_MODEL` (the conversational/analytics model). Inference uses structured output, temperature zero and thinking disabled. Negative/conditional replies, apparent detail changes and incompatible action types cannot be authorized by a model override. Unfamiliar replies are held only in bounded transient memory for up to five minutes; persistent metadata contains the interpretation method, intent and latency, never the classifier's raw reply. Transcript retention is managed separately. No schema migration is needed.

The model call runs **outside SQLite's writer lock**. Applying its result rechecks the call, proposal fingerprint, completed readback, latest user-turn token and expiry. A new reply, interruption, hangup or replacement proposal invalidates older inference. Model errors/timeouts/missing transient evidence produce `unclear`, preserving the completed readback for one brief natural clarification. After another unclear attempt, the tool directs the agent to offer staff assistance instead of repeating confirmations. Corrections require a new proposal; agreement never bypasses authoritative provider commits, booking conflicts, kitchen pauses or role permissions.

Before a rehearsal, start Ollama and run the read-only evaluation to warm the interpreter and check varied wording across restaurant bookings, appointments, orders, cancellations and rescheduling:

```bash
.venv/bin/python scripts/evaluate_confirmation.py
# Evaluate a different INSTALLED model before changing OLLAMA_CONFIRMATION_MODEL:
.venv/bin/python scripts/evaluate_confirmation.py --model qwen3:1.7b
```

The evaluation downloads nothing, uses synthetic utterances and creates no business records. Its report is saved under ignored `.runtime/confirmation-evaluation.json`. The installed `qwen3.5:9b` passed 27/27 evaluated cases, with about 0.9 seconds median warm inference on this Mac. This is a regression sample, not proof of every possible utterance; cold loads and simultaneous local-model use can exceed the three-second timeout and require clarification. Small-model substitution should pass the same evaluation first. A real microphone rehearsal still checks transcription and end-to-end timing that these tests cannot cover. Restart FastAPI after changing `.env`, and reapply saved Vapi assistants after changing the shared dialogue prompt.

References: [Vapi server transcript and tool events](https://docs.vapi.ai/server-url/events), [Vapi function tools](https://docs.vapi.ai/tools/custom-tools), [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs).

New reservations/appointments/orders may omit contact phone. The seeded US businesses explicitly configure country code `1`; ordinary ten-digit, spaced or punctuated contact numbers are normalized server-side. Other businesses require a configured country code or an international number. Cancellation/rescheduling and private order lookups still require the matching contact phone. The assistant prepares before asking for confirmation, then commits the same action after an affirmative reply; staff escalation records an attention item without promising a callback or phone transfer.

The setup script uses the configured location timezone with [Vapi's dynamic date variables](https://docs.vapi.ai/assistants/dynamic-variables) to supply the business-local date at each call start. Past-time errors also return current local time for recovery. Reapply the saved assistant configuration after changing its location timezone or prompt.

For a local test, confirm the sample table states in **Floor & Tables → Verify current state** and kitchen state in **Kitchen & Orders → Verify current state**. Old unverified staff data correctly produces unknown availability/status. A party must fit a configured table or permitted combination; a larger party cannot be promised just because the sum of seats is sufficient. Start with a reservation for two, then inspect the linked booking and tool timeline in Calls.

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

5. Open **AI Receptionist → Advanced settings**, select **LiveKit + Ollama**, then **Test call → Start call**. Without the worker and speech servers, an available Ollama model alone cannot answer a voice call.

## Optional Supabase PostgreSQL configuration

Supabase is not used by the local workspace. For a future migration, storage configuration is prepared in `.env.supabase.example`; no project is connected by default. FastAPI remains responsible for session authorization, business mutations and SSE updates. No browser database credentials are required.

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

Staff table states have a configurable verification window. A long-lived occupied state is not silently freed. The engine returns unknown when it lacks verified release information. Mock provider heartbeats and operational imports are active. Live vendor activation remains gated.

## Repository

```text
apps/web/                 Next.js dashboard, Tailwind, Radix/shadcn-style UI
services/api/app/         API, schemas, ORM, capacity and action logic
services/api/migrations/  additive Alembic schema migrations
services/voice/           optional LiveKit worker and speech configuration
infra/                   Docker Compose
scripts/                 launcher, seed, provider config export, local checks
tests/                   domain, API, provider, LiveKit and Ollama tests
```

## Configuration and limits

- `.env.example` contains variable names and no real provider secrets. Secrets never go to browser JavaScript; Vapi’s **public** key is the intentional exception.
- Passwordless demo login is disabled with `ALLOW_DEMO_LOGIN=false`. Keep this local prototype on loopback. `DEMO_MODE=false` requires a strong `SESSION_SECRET`, `OWNER_PASSWORD`, and HTTPS for secure session cookies.
- Tenant isolation and role checks are enforced on the backend. The prototype supports one configured owner login plus a membership model; staff invitations and per-stylist visibility assignment are not implemented.
- Audio recording is off. Local transcript retention is enabled for Momentuum Bistro and Studio Bloom for the latest ten voice calls per location. Older transcript text is purged while call metadata, action logs and business records remain. Callers hear a transcription notice and can decline storage. Provider-side artifacts remain disabled; local retention does not enable vendor recordings.
- Restaurant bookings and orders use their explicitly selected authority: internal or an independent mock provider. Salon appointments retain the existing internal engine. Square and Toast HTTP clients and connection tests remain intact. Live OAuth/vault setup, account-specific permissions, production response mappings and signed vendor webhooks remain gated; external failures never silently fall back to internal success.
- Voice cancellation/rescheduling and order lookup verify the reference plus matching contact phone. Changes use the same readback/confirmation guard; bookings without a contact phone require staff assistance. Automatic phone transfers, SMS notifications, retention/deletion controls, and billing are not activated.
- This is a working prototype, not a claim that every PRD requirement or live-provider workflow is complete. Live voice and vendor activation remain gated; browser visual QA has not been verified.

No public deployment or external account changes were performed.
