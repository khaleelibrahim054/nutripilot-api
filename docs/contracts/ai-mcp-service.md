# Contract: NutriPilot API → AI/MCP Service

The AI/MCP service is a **separately deployed application**. This API is its only client; web and
mobile apps never call it. Client: `app/integrations/ai_mcp_service/`.

## Transport

| | |
|---|---|
| Base URL | `AI_SERVICE_URL` (e.g. `http://ai-service.internal:8100`) |
| Request | `POST <relative path>`, JSON body, `Accept: text/event-stream` |
| Correlation | `X-Request-ID` forwarded from the originating API request |
| Success | `2xx` with `Content-Type: text/event-stream` |
| Termination | end of body, or an event whose data is `[DONE]` (not delivered to callers) |
| Heartbeats | SSE comment lines (`: keep-alive`), ignored by the parser |

Timeouts: `AI_SERVICE_TIMEOUT_SECONDS` for connect/write/pool, and
`AI_SERVICE_STREAM_IDLE_TIMEOUT_SECONDS` as the maximum gap between stream chunks. The service
must emit heartbeats more often than the idle timeout during long generations.

Endpoint paths and event payload schemas are defined per AI task when that feature is built
(meal plan generation, recipe generation, meal replacement, …). This client deliberately knows
nothing about them.

## Client behaviour

`AiMcpServiceClient.stream_events(path, payload)` yields `ServerSentEvent(event, data, id, retry)`
as soon as each event is complete. The parser handles events split across chunks, several events
per chunk, multi-line `data`, CRLF/LF/CR line endings and UTF-8 characters split across chunks.
A single line or event over 1,000,000 characters is rejected.

| Failure | Exception |
|---|---|
| connection refused, DNS, TLS, connection dropped, timeout | `AiServiceUnavailableError` |
| non-2xx status | `AiServiceResponseError` (`status_code`) |
| 2xx that is not `text/event-stream`, or oversized event | `AiServiceProtocolError` |

All inherit `AiServiceError`. Callers translate them into a public `stream.error` event or a
`DomainError`; upstream messages are never forwarded to end users.

If the caller stops iterating (for example the end user disconnected), the upstream HTTP response
is closed, which aborts the generation request on the AI service.

## Trust boundary

Event `data` is **untrusted**. The owning module must validate it against its own Pydantic schema,
resolve food/product IDs, recompute nutrition and prices deterministically, and check allergies
before anything is persisted or shown as final (`docs/api-system-design.md` §8). The public SSE
contract sent to clients (`stream.started`, `generation.progress`, …) is NutriPilot's own and is
mapped by the owning module, not passed through.
