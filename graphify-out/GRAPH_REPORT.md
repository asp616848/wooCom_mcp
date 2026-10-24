# Graph Report - .  (2026-10-24)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 2161 nodes · 5444 edges · 130 communities (110 shown, 20 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 473 edges (avg confidence: 0.58)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `c2257c9b`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9
- Community 10
- Community 11
- Community 12
- Community 13
- Community 14
- Community 15
- Community 16
- Community 17
- Community 18
- Community 19
- Community 20
- Community 21
- Community 22
- Community 23
- Community 24
- Community 25
- Community 26
- Community 27
- Community 28
- Community 29
- Community 30
- Community 31
- Community 32
- Community 33
- Community 34
- Community 35
- Community 36
- Community 37
- Community 38
- Community 39
- Community 40
- Community 41
- Community 42
- Community 43
- Community 44
- Community 45
- Community 46
- Community 47
- Community 48
- Community 49
- Community 50
- Community 51
- Community 52
- Community 53
- Community 54
- Community 55
- Community 56
- Community 57
- Community 58
- Community 59
- Community 60
- Community 61
- Community 62
- Community 63
- Community 64
- Community 65
- Community 66
- Community 67
- Community 68
- Community 69
- Community 70
- Community 71
- Community 72
- Community 73
- Community 74
- Community 75
- Community 76
- Community 77
- Community 78
- Community 79
- Community 80
- Community 81
- Community 82
- Community 83
- Community 84
- Community 85
- Community 86
- Community 87
- Community 88
- Community 89
- Community 90
- Community 91
- Community 92
- Community 93
- Community 94
- Community 95
- Community 96
- Community 97
- Community 98
- Community 99
- Community 100
- Community 101
- Community 102
- Community 103
- Community 104
- Community 105
- Community 106
- Community 107
- Community 108
- Community 109
- Community 110
- Community 111
- Community 112
- Community 113
- Community 114
- Community 115
- Community 116
- Community 117
- Community 118
- Community 119
- Community 121

## God Nodes (most connected - your core abstractions)
1. `AppError` - 87 edges
2. `Runtime` - 75 edges
3. `ApiModel` - 67 edges
4. `User` - 44 edges
5. `gateway_factory()` - 41 edges
6. `WooClient` - 38 edges
7. `UpstreamError` - 35 edges
8. `Settings` - 31 edges
9. `HealthChecker` - 31 edges
10. `http()` - 30 edges

## Surprising Connections (you probably didn't know these)
- `Concurrency` --uses--> `StaticCredentialMiddleware`  [INFERRED]
  services/gateway/tests/integration/conftest.py → packages/wmg-common/src/wmg_common/auth.py
- `FakeJobs` --uses--> `StaticCredentialMiddleware`  [INFERRED]
  services/gateway/tests/integration/conftest.py → packages/wmg-common/src/wmg_common/auth.py
- `Gateway` --uses--> `StaticCredentialMiddleware`  [INFERRED]
  services/gateway/tests/integration/conftest.py → packages/wmg-common/src/wmg_common/auth.py
- `Recorder` --uses--> `StaticCredentialMiddleware`  [INFERRED]
  services/gateway/tests/integration/conftest.py → packages/wmg-common/src/wmg_common/auth.py
- `ThreadServer` --uses--> `StaticCredentialMiddleware`  [INFERRED]
  services/gateway/tests/integration/conftest.py → packages/wmg-common/src/wmg_common/auth.py

## Import Cycles
- None detected.

## Communities (130 total, 20 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (67): MCPError, Catalog, export_metrics(), is_live(), load(), Any, Tool, In-memory catalog (DESIGN §5.8, D35): servers, tools and policies loaded from… (+59 more)

### Community 1 - "Community 1"
Cohesion: 0.09
Nodes (73): Namespace, parse(), Tiny MCP client for manual checks (DESIGN §10.1). uv run scripts/mcp_cli.py…, run(), gateway_factory(), Path, Start any ASGI app on loopback for the test (e.g. a scripted LLM); stopped…, serve() (+65 more)

### Community 2 - "Community 2"
Cohesion: 0.08
Nodes (70): add_member(), approve(), free_port(), full_grant(), mcp_client(), MemoryStorage, oauth_gateway(), pkce() (+62 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (46): AnyHttpUrl, AuthorizationCode, IdentityAssertionParams, RefreshToken, approve(), deny(), describe(), Any (+38 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (45): AuthSettings, CallToolRequestParams, AuditRecord, AuditWriter, Any, Audit log (DESIGN §5.10, D60): one row per `tools/call` (every outcome) and per…, summarize_args(), CatalogHolder (+37 more)

### Community 5 - "Community 5"
Cohesion: 0.07
Nodes (35): ServerEntry, Woo MCP Gateway (DESIGN §5)., Any, Upstream health (DESIGN §3.4, §5.6, §5.8, D36). The leader probes every enabled…, Probe one server and persist the outcome. Returns True if it is healthy., Persist a successful probe. Returns True if anything the catalog depends on…, Mark a server unreachable. Returns True if its status flipped (NOTIFY sent)., record_failure() (+27 more)

### Community 6 - "Community 6"
Cohesion: 0.17
Nodes (36): AppError, Exception, Raise from handlers to return a clean JSON error with the given HTTP status., BaseModel, API shape of a user (camelCase on the wire). Never carries the password hash., User, LoginBody, Invite (+28 more)

### Community 7 - "Community 7"
Cohesion: 0.10
Nodes (31): current(), increment(), Any, datetime, Fixed-window counters in `rate_counters`: login throttling and tool-call rate…, window_start(), Caller, caller_from() (+23 more)

### Community 8 - "Community 8"
Cohesion: 0.10
Nodes (37): compute(), dec(), Gst, Decimal, q(), GST math with Decimal, rounded half-up to paise (DESIGN §9.2). Pure functions,…, float → Decimal via its shortest repr, so 0.1 stays 0.1 (not…, supply_type() (+29 more)

### Community 9 - "Community 9"
Cohesion: 0.11
Nodes (26): EmptyState(), Badge(), badgeVariants, Input(), Label(), Select(), Table(), Td() (+18 more)

### Community 10 - "Community 10"
Cohesion: 0.14
Nodes (39): any_product_id(), chat(), delegated_tools(), eventually(), kubectl(), _leaves(), mcp_call(), mcp_tools() (+31 more)

### Community 11 - "Community 11"
Cohesion: 0.14
Nodes (39): chat(), deployer_pods(), eventually(), kubectl(), mcp_tools(), need(), probe(), Any (+31 more)

### Community 12 - "Community 12"
Cohesion: 0.15
Nodes (33): WooCommerce MCP server (DESIGN §9.1)., ASGI entrypoint: `uvicorn woo_mcp.main:app` (DESIGN §9.0-9.1)., BillingAddress, DateRangeOut, LineItem, _name(), order_detail(), order_summary() (+25 more)

### Community 13 - "Community 13"
Cohesion: 0.09
Nodes (21): EventListener, Cross-replica change events over Postgres LISTEN/NOTIFY (DESIGN §5.8, D40).…, ApiBodyLimitMiddleware, create_app(), McpRouter, Any, ASGIApp, FastAPI app factory: lifespan, health, metrics, /api routers, the MCP mount,… (+13 more)

### Community 14 - "Community 14"
Cohesion: 0.12
Nodes (26): _check_env(), create_hosted(), delete_hosted(), HostedCreate, HostedEnvelope, HostedListEnvelope, HostedOut, list_hosted() (+18 more)

### Community 15 - "Community 15"
Cohesion: 0.11
Nodes (25): IpAddress, check_url(), is_public(), parse(), Exception, Resolver, SSRF policy for upstream URLs (DESIGN §8.4, D43). `check_url` runs when a…, Full check for the API (create/update/test). Raises SsrfBlockedError. (+17 more)

### Community 16 - "Community 16"
Cohesion: 0.11
Nodes (20): api(), auditApi, AuditItem, AuthType, ConnectedApp, ConnectInfo, Insights, Invite (+12 more)

### Community 17 - "Community 17"
Cohesion: 0.11
Nodes (25): Emit, _call(), _clean(), llm_tools(), Any, APIError, CallToolResult, ChatCompletion (+17 more)

### Community 18 - "Community 18"
Cohesion: 0.09
Nodes (18): SecretStr, field_validator, Gateway configuration from env (DESIGN §5.2). Invalid env → the process exits…, Primary key first, then the fallback (D92); empty when chat is not configured., Settings, client(), Any, AsyncClient (+10 more)

### Community 19 - "Community 19"
Cohesion: 0.16
Nodes (16): auth_secret_name(), hosted_url(), hosted_values(), HostedReconciler, Any, UUID, Hosted MCP server reconciler (DESIGN §14, SO-5, D94): runs on the leader next…, Lock the row and confirm nobody changed its status/attempt since this tick read… (+8 more)

### Community 20 - "Community 20"
Cohesion: 0.15
Nodes (28): EventType, put, notify(), Any, create_server(), delete_server(), _fetch_out(), list_server_tools() (+20 more)

### Community 21 - "Community 21"
Cohesion: 0.20
Nodes (27): deployment_image(), eventually(), gateway_images(), helm_revision(), images(), in_wordpress(), kubectl(), marker_count() (+19 more)

### Community 22 - "Community 22"
Cohesion: 0.08
Nodes (25): DOM, DOM.Iterable, ES2023, node, src, vite/client, vite.config.ts, compilerOptions (+17 more)

### Community 23 - "Community 23"
Cohesion: 0.13
Nodes (16): components, Markdown(), STYLES, ApiError, ChatEvent, ChatMessage, MAX_CONTENT, MAX_MESSAGES (+8 more)

### Community 24 - "Community 24"
Cohesion: 0.13
Nodes (24): ApiKey, create_key(), KeyCreate, KeyCreated, KeyEnvelope, KeyLimit, KeysEnvelope, list_keys() (+16 more)

### Community 25 - "Community 25"
Cohesion: 0.14
Nodes (23): approve_request(), AppsEnvelope, AuthorizationRequest, ConnectedApp, deny_request(), get_request(), list_apps(), alias (+15 more)

### Community 26 - "Community 26"
Cohesion: 0.21
Nodes (23): Popen, _call(), finish_load(), gateway_pods(), in_cluster(), kill_leader_under_load(), kubectl(), leaders() (+15 more)

### Community 27 - "Community 27"
Cohesion: 0.17
Nodes (21): useMe(), errorMessage(), formatTime(), StatusBadge(), onError(), queryClient, root, router (+13 more)

### Community 28 - "Community 28"
Cohesion: 0.27
Nodes (23): APIStatusError, RateLimitError, LlmKeys, The LLM clients in key order (D92). A call that fails on the active key with a…, completion(), FakeLlm, FakeMcp, anyio (+15 more)

### Community 29 - "Community 29"
Cohesion: 0.19
Nodes (16): CodeBlock(), CopyButton(), ErrorState(), Loading(), PageHeader(), STATUS, Button(), buttonVariants (+8 more)

### Community 30 - "Community 30"
Cohesion: 0.22
Nodes (22): create_store(), delete_store(), list_stores(), _out(), Any, CurrentUser, get, Pool (+14 more)

### Community 31 - "Community 31"
Cohesion: 0.14
Nodes (19): _allow_any_url(), Concurrency, database_url(), _docker(), fake_jobs(), free_port(), legacy_only_app(), modern_app() (+11 more)

### Community 32 - "Community 32"
Cohesion: 0.22
Nodes (22): eventually(), hosted(), kubectl(), _mcp(), mcp_tools(), need(), pincode_image(), Any (+14 more)

### Community 33 - "Community 33"
Cohesion: 0.18
Nodes (21): set_session_cookie(), accept_invite(), create_invite(), invite_info(), InviteAccept, InviteCreate, InviteCreated, InviteInfo (+13 more)

### Community 34 - "Community 34"
Cohesion: 0.16
Nodes (18): load_places(), Place, BaseModel, Bundled serviceable PIN codes (`data/pincodes.json`) loaded once at startup., Demo delivery / PIN code checker MCP server (DESIGN §9.3)., build_server(), City, Delivery (+10 more)

### Community 35 - "Community 35"
Cohesion: 0.16
Nodes (19): BoundLogger, EventDict, configure_logging(), get_logger(), _is_sensitive(), Any, Structured JSON logging with secret redaction (D75). Every log line is one JSON…, structlog processor: redact sensitive keys at any depth of the event. (+11 more)

### Community 36 - "Community 36"
Cohesion: 0.17
Nodes (19): Period, DateRange, date, datetime, `period` / `date_from` / `date_to` → store-local date range → Woo query params…, WordPress stores either a named zone (`Asia/Kolkata`) or only a numeric UTC…, `period` and explicit dates are mutually exclusive; no filter at all → None…, Woo `after`/`before` are exclusive bounds in store-local time… (+11 more)

### Community 37 - "Community 37"
Cohesion: 0.10
Nodes (16): Producer, chat(), ChatMessage, ChatRequest, Any, CurrentUser, model_validator, post (+8 more)

### Community 38 - "Community 38"
Cohesion: 0.17
Nodes (16): emit(), items, listeners, push(), toast, Toaster(), ToastItem, Tone (+8 more)

### Community 39 - "Community 39"
Cohesion: 0.16
Nodes (17): bootstrap(), load_seed_servers(), Any, BaseModel, Path, First-boot data (DESIGN §6 "Bootstrap"). Creates the admin user, the seed…, `<dir>/<slug>/credential` comes from an optional projected Secret (`make seed-…, Returns True if it ran, False if bootstrap was already done. (+9 more)

### Community 40 - "Community 40"
Cohesion: 0.15
Nodes (18): migrate(), migration_files(), Any, Numbered `.sql` migrations (D72). All pending files run in ONE transaction…, Apply pending migrations, then run `after(conn)` (bootstrap) inside the same…, client(), Any, AsyncClient (+10 more)

### Community 41 - "Community 41"
Cohesion: 0.25
Nodes (7): prov_job_name(), Any, UUID, Lock the row and confirm nobody changed its status/attempt since this tick read…, Per-store Helm values (`/input/values.json`), layered over values-<env>.yaml…, store_values(), StoreReconciler

### Community 42 - "Community 42"
Cohesion: 0.11
Nodes (19): class-variance-authority, clsx, lucide-react, react, react-dom, react-markdown, react-router, dependencies (+11 more)

### Community 43 - "Community 43"
Cohesion: 0.11
Nodes (19): eslint, @eslint/js, devDependencies, eslint, @eslint/js, @tailwindcss/vite, @types/react-dom, typescript (+11 more)

### Community 44 - "Community 44"
Cohesion: 0.19
Nodes (17): create_pool(), _init_connection(), Any, asyncpg pool (D72). JSON/JSONB columns are decoded to Python objects., Postgres may still be starting when the gateway boots: retry every 2 s for up…, unhost_job_name(), fake_running(), Any (+9 more)

### Community 45 - "Community 45"
Cohesion: 0.28
Nodes (17): One pooled HTTP client per process; every call is signed fresh (new nonce +…, WooClient, call(), Any, parametrize, Tools through a real MCP client connected in-process (schemas, validation, Woo…, test_create_product_body(), test_create_product_rejects_bad_price() (+9 more)

### Community 46 - "Community 46"
Cohesion: 0.18
Nodes (12): Response, QueryValue, Request, _as_str(), _header_int(), Any, WooCommerce REST client: OAuth 1.0a one-legged signing (HMAC-SHA256) over plain…, Signed call to `/wp-json/wc/v3<path>`. Failures raise `ToolError` with a model-… (+4 more)

### Community 47 - "Community 47"
Cohesion: 0.28
Nodes (10): Card(), CardContent(), CardDescription(), CardHeader(), CardTitle(), consentRequestId(), LOOPBACK, safeAppRedirect() (+2 more)

### Community 48 - "Community 48"
Cohesion: 0.17
Nodes (14): DeployStatus, hostedApi, HostedInput, HostedServer, IMAGE, IMAGE_PREFIXES, imageProblem(), MAX_ENV (+6 more)

### Community 49 - "Community 49"
Cohesion: 0.17
Nodes (15): Endpoint, health_routes(), livez(), make_readyz(), ReadyCheck, Request, Route, Liveness / readiness endpoints shared by all services (D10). `/livez` only… (+7 more)

### Community 50 - "Community 50"
Cohesion: 0.23
Nodes (7): JobState, job_state(), KubeJobApi, Any, Final state from the Job conditions (Complete / Failed); anything else is still…, The official client is synchronous: every call runs in a worker thread (D77)., Last log lines of the Job's newest pod (best effort: '' if none).

### Community 51 - "Community 51"
Cohesion: 0.26
Nodes (16): asgi_call(), AsgiCall, fixture, make_app(), mcp_endpoint(), anyio, AsgiCall, parametrize (+8 more)

### Community 52 - "Community 52"
Cohesion: 0.21
Nodes (12): anyio_backend(), client(), FakeStore, make_settings(), Any, fixture, Request, Fake WooCommerce store (httpx2.MockTransport) + woo-mcp wired to it. Tests run… (+4 more)

### Community 53 - "Community 53"
Cohesion: 0.22
Nodes (14): _client_ip(), login(), logout(), me(), BaseModel, CurrentUser, get, Pool (+6 more)

### Community 54 - "Community 54"
Cohesion: 0.16
Nodes (14): max_length, AuditItem, AuditPage, list_audit(), alias, CurrentUser, datetime, ge (+6 more)

### Community 55 - "Community 55"
Cohesion: 0.20
Nodes (13): Reduce an MCP input schema to the JSON-Schema subset Gemini's function…, sanitize_schema(), APIError, parametrize, TEST-U-09 `sanitize_schema` and TEST-U-15 the chat loop (fake LLM, fake MCP…, test_allowlist_drops_unknown_keywords_recursively(), test_defs_are_inlined(), test_format_kept_only_for_date_time() (+5 more)

### Community 56 - "Community 56"
Cohesion: 0.16
Nodes (5): JobInfo, FakeJobs, Gateway, Any, In-memory JobApi: Jobs start 'running'; tests finish them with `finish()`.

### Community 57 - "Community 57"
Cohesion: 0.20
Nodes (14): Canonical in-cluster URL (DESIGN §2.3)., woo_mcp_url(), _cond(), _job(), Any, parametrize, P14 provisioner pure parts: deployer Job manifest, Job state, per-store values,…, _settings() (+6 more)

### Community 58 - "Community 58"
Cohesion: 0.29
Nodes (14): add_member(), job(), Any, AsyncClient, P14 (DESIGN §13): the stores API rules and the reconciler state machine, on…, Queueing, ready + registration, failure + logs, retry, delete (registration…, The Job name the reconciler uses for this store row (name + id prefix +…, B-02: names, duplicates, per-user and total limits, visibility, retry/delete… (+6 more)

### Community 59 - "Community 59"
Cohesion: 0.21
Nodes (12): Action, del_job_name(), deployer_job(), input_secret_name(), K8sError, Exception, Kubernetes access for the provisioner (D77, DESIGN §8.6, §13.2, §14). The…, Job running `<script> <target>` as `wmg-deployer` (§13.2): a store name or a… (+4 more)

### Community 60 - "Community 60"
Cohesion: 0.29
Nodes (13): active_admin_count(), create(), get(), get_login_row(), list_all(), mark_login(), Any, UUID (+5 more)

### Community 61 - "Community 61"
Cohesion: 0.16
Nodes (6): CredentialCipher, Upstream credentials encrypted at rest (D54). AES-256-GCM; stored as `v1:` +…, Raises `cryptography.exceptions.InvalidTag` if the value, key or server id…, AsyncBaseTransport, test_round_trip_with_prefix(), test_wrong_aad_or_tamper_or_key_fails()

### Community 62 - "Community 62"
Cohesion: 0.18
Nodes (6): AsyncClient, OAuthClientInformationFull, OAuthToken, TESTING §6 OA-01 (P17, D98): MCP OAuth against the running cluster (`make…, run_flow(), Storage

### Community 63 - "Community 63"
Cohesion: 0.21
Nodes (10): AsyncOpenAI, api_key(), available_models(), main(), LLM model selection helper (PLAN P10.1). Reads GEMINI_API_KEY from the…, One round trip: user → tool_call → tool result → answer. Returns None on…, smoke(), converse() (+2 more)

### Community 64 - "Community 64"
Cohesion: 0.19
Nodes (8): AuthMode, ASGIApp, Receive, Scope, Send, Static-credential guard for the MCP servers behind the gateway (DESIGN §9.0,…, StaticCredentialMiddleware, test_empty_token_rejected()

### Community 65 - "Community 65"
Cohesion: 0.21
Nodes (9): BaseSettings, McpServerSettings, Environment-driven settings (pydantic-settings). Invalid env stops the process…, Settings every service has. Field names map to upper-case env vars (`log_level`…, Settings shared by the MCP servers behind the gateway (DESIGN §9.0)., ServiceSettings, ASGI entrypoint: `uvicorn mcp_pincode.main:app` (DESIGN §9.0, §9.3)., PincodeSettings (+1 more)

### Community 66 - "Community 66"
Cohesion: 0.23
Nodes (11): list_users(), patch_user(), AdminUser, get, model_validator, patch, Pool, UUID (+3 more)

### Community 67 - "Community 67"
Cohesion: 0.21
Nodes (8): fresh_modern(), many_app(), many_tools(), ASGIApp, SO-4 fixture: 60 tools (over the chat search threshold of 40), one of them a…, A private modern fixture server (for tests that stop it or measure its…, uvicorn on 127.0.0.1:<port> in a daemon thread (own event loop); start() waits…, ThreadServer

### Community 68 - "Community 68"
Cohesion: 0.21
Nodes (12): _enc(), oauth_params(), PHP rawurlencode(): RFC 3986, everything but unreserved characters is percent-…, Return query + oauth params + `oauth_signature` for a request to `API_PREFIX +…, Any, parametrize, test_error_mapping(), test_json_body_sent_unsigned() (+4 more)

### Community 69 - "Community 69"
Cohesion: 0.39
Nodes (5): CredentialType, AccessToken, Any, An `mgo_` access token (D98): role and status are read live, so revoking the…, WmgTokenVerifier

### Community 70 - "Community 70"
Cohesion: 0.18
Nodes (5): decode(), Delegation, Delegated JWT for the chat agent (D53): HS256, `aud=wmg-mcp`, `iss=wmg-…, None for any invalid token (bad signature, expired, wrong audience/issuer,…, TEST-U-03 (api keys) and TEST-U-05 (delegated JWT).

### Community 71 - "Community 71"
Cohesion: 0.22
Nodes (9): current_user(), get_pool(), Any, CurrentUser, Pool, Request, User, FastAPI dependencies: database pool, current session user, admin check, CSRF… (+1 more)

### Community 72 - "Community 72"
Cohesion: 0.29
Nodes (6): Housekeeping, Any, datetime, Leader housekeeping (DESIGN §5.12, D60, D95): every minute, delete…, asyncpg's command tag, e.g. 'DELETE 12'., _rows()

### Community 73 - "Community 73"
Cohesion: 0.27
Nodes (9): FastAPI, build_snippets(), connect_info(), ConnectInfo, CurrentUser, get, Request, GET /api/connect-info (DESIGN §7.3): the gateway URL and ready-to-paste client… (+1 more)

### Community 74 - "Community 74"
Cohesion: 0.31
Nodes (7): anyio, AsgiCall, parametrize, ready_false(), test_health_is_get_only(), test_livez_always_ok(), test_readyz()

### Community 75 - "Community 75"
Cohesion: 0.33
Nodes (9): create(), delete(), Any, User, UUID, sessions table: server-side dashboard sessions (D51)., Active user behind a live session; slides the expiry forward when it is getting…, user_for_token() (+1 more)

### Community 76 - "Community 76"
Cohesion: 0.27
Nodes (9): FailureReason, Insights, AdminUser, get, Pool, GET /api/insights (DESIGN §7.3, §16, SO-8): tool-call health per server and per…, ServerStats, ToolStats (+1 more)

### Community 78 - "Community 78"
Cohesion: 0.44
Nodes (8): call(), client(), Any, AsyncClient, TESTING §3.4 (TEST-A-*): /api permission matrix, CSRF, member redaction, no…, _record(), test_A_audit_filters_and_pagination(), test_A_permission_matrix()

### Community 79 - "Community 79"
Cohesion: 0.36
Nodes (8): call(), Any, parametrize, TEST-U-14: zones, ETA, unknown PIN, format, sorted list, origin check., test_bad_format_rejected(), test_list_sorted_ten_cities(), test_unknown_pincode_not_serviceable(), test_zones()

### Community 80 - "Community 80"
Cohesion: 0.28
Nodes (6): main(), `python -m woo_mcp.selfcheck` — the store's `helm test` (DESIGN §4.4, §9.1). 1.…, run(), woo-mcp configuration from env (DESIGN §9.1). Secrets stay `SecretStr` so they…, WooMcpSettings, AsyncBaseTransport

### Community 81 - "Community 81"
Cohesion: 0.39
Nodes (8): Any, ASGIApp, parametrize, The ASGI app as the gateway sees it: credential check, Host guard, health, both…, running(), test_both_protocol_eras_list_tools(), test_health_is_open_and_mcp_needs_token(), test_unknown_host_rejected()

### Community 82 - "Community 82"
Cohesion: 0.36
Nodes (7): error_response(), ErrorBody, ErrorDetail, BaseModel, JSONResponse, JSON error envelope used by every HTTP API: {"error": {"code": "UPPER_SNAKE",…, test_error_response_envelope()

### Community 83 - "Community 83"
Cohesion: 0.39
Nodes (7): load_settings(), Build `cls` from the environment; on invalid config exit with one line per…, MonkeyPatch, test_invalid_settings_exit_without_echoing_values(), test_load_settings_from_env(), test_missing_required_setting(), S

### Community 85 - "Community 85"
Cohesion: 0.29
Nodes (4): Any, CallToolResult, ChatCompletion, Exception

### Community 86 - "Community 86"
Cohesion: 0.32
Nodes (5): ASGI entrypoint: `uvicorn mcp_gst.main:app` (DESIGN §9.0, §9.2)., GstSettings, mcp-gst configuration from env (DESIGN §9.2)., Bearer credential on /mcp; health open., test_bearer_required_and_tools_listed()

### Community 87 - "Community 87"
Cohesion: 0.33
Nodes (3): log(), recover_release(), lib.sh script

### Community 88 - "Community 88"
Cohesion: 0.38
Nodes (3): create_once(), ns(), prod-secrets.sh script

### Community 89 - "Community 89"
Cohesion: 0.29
Nodes (7): scripts, build, dev, format, lint, test, typecheck

### Community 90 - "Community 90"
Cohesion: 0.29
Nodes (5): Receive, Scope, Send, Per-request metadata (DESIGN §5.3 step 1): request id, MCP method/name headers,…, RequestMeta

### Community 91 - "Community 91"
Cohesion: 0.47
Nodes (5): RFC-3986, checks, get(), rfc3986(), signedPath()

### Community 92 - "Community 92"
Cohesion: 0.47
Nodes (4): authApi, Role, NAV, NavItem

### Community 93 - "Community 93"
Cohesion: 0.70
Nodes (4): db_ready(), setup.sh script, step(), wpa()

### Community 94 - "Community 94"
Cohesion: 0.40
Nodes (5): mcp-gst, mcp-pincode, wmg-common, wmg-gateway, woo-mcp

### Community 95 - "Community 95"
Cohesion: 0.40
Nodes (4): name, private, type, version

### Community 96 - "Community 96"
Cohesion: 0.50
Nodes (3): RESERVED_STORE_NAMES, STORE_NAME, storeNameProblem()

### Community 97 - "Community 97"
Cohesion: 0.60
Nodes (3): Receive, Scope, Send

### Community 98 - "Community 98"
Cohesion: 0.50
Nodes (4): anyio_backend(), base_env(), fixture, Valid settings except the database URL (tests add their own).

### Community 99 - "Community 99"
Cohesion: 0.40
Nodes (3): Any, Site basics change rarely: cache name/url/timezone/currency for 10 min;…, StoreInfoCache

### Community 101 - "Community 101"
Cohesion: 0.50
Nodes (3): call(), ASGIApp, Test helpers: drive an ASGI app directly (no HTTP client library needed).

### Community 102 - "Community 102"
Cohesion: 0.50
Nodes (3): Receive, Scope, Send

### Community 103 - "Community 103"
Cohesion: 0.67
Nodes (3): main(), TESTING §7 in-cluster latency (NFR-P1, NFR-P2): runs inside a gateway pod (no…, request()

## Knowledge Gaps
- **108 isolated node(s):** `deploy-hosted.sh script`, `lib.sh script`, `provision-store.sh script`, `remove-hosted.sh script`, `teardown-store.sh script` (+103 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **20 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Runtime` connect `Community 4` to `Community 0`, `Community 3`, `Community 37`, `Community 6`, `Community 69`, `Community 5`, `Community 13`, `Community 14`, `Community 50`, `Community 84`, `Community 25`, `Community 61`, `Community 30`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Why does `http()` connect `Community 1` to `Community 32`, `Community 2`, `Community 5`, `Community 10`, `Community 81`, `Community 21`, `Community 26`, `Community 62`, `Community 63`?**
  _High betweenness centrality (0.071) - this node is a cross-community bridge._
- **Why does `gateway_factory()` connect `Community 1` to `Community 2`, `Community 67`, `Community 44`, `Community 78`, `Community 56`, `Community 58`, `Community 31`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **Are the 51 inferred relationships involving `AppError` (e.g. with `LoginBody` and `UserEnvelope`) actually correct?**
  _`AppError` has 51 INFERRED edges - model-reasoned connections that need verification._
- **Are the 44 inferred relationships involving `Runtime` (e.g. with `ChatMessage` and `ChatRequest`) actually correct?**
  _`Runtime` has 44 INFERRED edges - model-reasoned connections that need verification._
- **Are the 54 inferred relationships involving `ApiModel` (e.g. with `AuditItem` and `AuditPage`) actually correct?**
  _`ApiModel` has 54 INFERRED edges - model-reasoned connections that need verification._
- **Are the 30 inferred relationships involving `User` (e.g. with `LoginBody` and `UserEnvelope`) actually correct?**
  _`User` has 30 INFERRED edges - model-reasoned connections that need verification._