# Combos RFQ Client + CLI Design

**Date:** 2026-10-26  
**Status:** Approved for planning (user design review pending)  
**Scope:** Foundation only — requester RFQ API client + CLI. No auto strategy.

## Goal

Ship a reusable Polymarket Combos **requester** client and a live CLI so a human can quote and fill multi-leg Combos from the terminal. Strategies (e.g. CSL auto-combo) come later and will call the same client.

## Decisions (locked)

| Topic | Choice |
| --- | --- |
| Scope | RFQ client + CLI only; no strategy |
| Default mode | **Live** — one command creates RFQ and accepts the winning quote |
| Market input | Both `--combo-id` and `--legs` |
| Quote selection | Accept gateway **best quote** immediately (no local re-rank, no price cap) |
| Size | Both flags exist; **buy requires `--usdc`**, **sell requires `--size`** (API: BUY=notional, SELL=shares) |
| Wait window | Fixed short HTTP timeout (~5s), aligned with accept window |
| Architecture | Standalone `CombosRfqClient` (not stuffed into `PolymarketClient`) |

## Non-goals

- Auto discovery / CSL / focus_club combo strategies
- Dry-run default or `--max-price` gates
- EOA (`signature_type=0`) — gateway rejects it
- Builder attribution (`builder` must be zero bytes32)
- Quoter / market-maker side APIs
- Idempotent create-retry after lost create response (API has no idempotency key)

## Architecture

```
cli.py combo …
        │
        ▼
CombosRfqClient  ──HTTP──►  combos-rfq-api (catalog, public)
        │            └──►  combos-rfq-gateway-requester-api (auth L2)
        │
        ├── L2 HMAC headers (same CLOB API creds as PolymarketClient)
        └── EIP-712 / Deposit TypedDataSign order signing (Exchange v3)
```

### New files

- `src/clients/combos_rfq_client.py` — sole RFQ HTTP + signing entrypoint
- CLI wiring in `cli.py` (`combo` subcommand); optional thin helper under `scripts/` only if it keeps `cli.py` readable

### Reuse (do not restructure)

- Env: `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_SIGNATURE_TYPE` (1/2/3), `POLYMARKET_FUNDER`
- Derive/reuse CLOB L2 API credentials the same way as `PolymarketClient` / `py-clob-client-v2`
- Do **not** add RFQ methods onto `PolymarketClient`

### Endpoints

| Role | Base |
| --- | --- |
| Catalog (public) | `https://combos-rfq-api.polymarket.com/v1/rfq/combo-markets` |
| Requester gateway | `https://combos-rfq-gateway-requester-api.polymarket.com/v1/requester/rfq` |

Auth on every gateway call: `POLY_ADDRESS`, `POLY_API_KEY`, `POLY_PASSPHRASE`, `POLY_TIMESTAMP`, `POLY_SIGNATURE` (HMAC-SHA256 over `timestamp + METHOD + path + body`; fresh timestamp per request including status polls).

## Execution flow

1. Resolve target → ordered unique `leg_position_ids` (2–50).
2. `POST /v1/requester/rfq/requests` with identity + size. Gateway blocks until quote competition ends.
3. On success: status `AWAITING_REQUESTER_ACCEPTANCE`, winning `quote`, `expires_at` (~5s window). On no quotes: HTTP 200 + `status=FAILED` / `NO_QUOTES` → CLI non-zero exit.
4. Build Exchange v3 order from request+quote (`tokenId` = combo YES position id; `makerAmount`/`takerAmount` from quote; `side` 0 BUY / 1 SELL; `builder`/`metadata` = zero).
5. Sign per wallet type:
   - Deposit (`3`): outer `TypedDataSign` + ERC-7739 wrap
   - Proxy (`1`) / Safe (`2`): sign Exchange `Order` directly
6. `POST .../requests/{rfq_id}/accept` with `quote_id` + `signed_order` before `expires_at`.
7. Poll `GET .../requests/{rfq_id}` until `FILLED`/`CONFIRMED` or terminal `FAILED`/`EXPIRED`/`CANCELED`. Print `rfq_id`, prices, `tx_hash` when present.

### Size mapping

| Command | Required flag | `requested_size` |
| --- | --- | --- |
| `combo buy` | `--usdc N` | `unit=notional`, `value_e6 = str(int(N * 1e6))` |
| `combo sell` | `--size N` | `unit=shares`, `value_e6 = str(int(N * 1e6))` |

Reject cross-mismatches (`buy --size` without `--usdc`, `sell --usdc` without `--size`) with a clear CLI error — platform BUY is notional-only and SELL is shares-only. `side` in the RFQ body is always `"YES"` (platform constraint). BUY notional is collateral budget **including fees** (`total_required_e6`).

### Timeouts and create ambiguity

- Client HTTP timeout for create/accept: fixed ~**5 seconds**.
- If create times out or connection drops: RFQ may exist without a returned `rfq_id`. **Do not auto-retry create.** Surface a clear error. Status recovery only when `rfq_id` is known.
- Rate limit: ≤15 creates / rolling minute / `maker_address` (`429 RATE_LIMITED`).

## CLI shape

```bash
python cli.py combo buy  --legs "condA:YES,condB:NO" --usdc 20
python cli.py combo buy  --combo-id <id-or-position-list> --usdc 20
python cli.py combo sell --legs "..." --size 10
python cli.py combo status --rfq-id <id>
python cli.py combo catalog [--limit 50] [--cursor ...]
```

- `buy` / `sell` are live by default (no confirmation prompt).
- `--legs` and `--combo-id` are mutually exclusive; one required for buy/sell.
- `buy` requires `--usdc`; `sell` requires `--size`.
- `catalog` is read-only (no auth) for discovering combo-eligible markets / position ids.
- `status` re-polls a known `rfq_id` with fresh L2 headers.

### `--legs` resolution

- Format: comma-separated entries, each either:
  - `condition_id:YES|NO` (case-insensitive), or
  - a raw `position_id` / token id
- Length: 2–50 unique legs.
- For `condition_id:outcome`: page the public catalog; index `0` = YES, `1` = NO in aligned `position_ids` / `outcomes`. Missing market → hard error (do not invent ids via Gamma).

### `--combo-id` resolution

Platform create API takes **legs**, not a named combo product id. Define:

1. If catalog (or a documented field discovered during implementation) exposes a reusable combo template id → resolve to `leg_position_ids`.
2. Otherwise treat `--combo-id` as a **shorthand**: comma-separated pre-resolved `position_id` list (equivalent to `--legs` with only token ids).

Document the resolved interpretation in CLI `--help` once implemented.

### Output and exit codes

- After create: print legs, `blended_price_e6`, `total_required_e6`, `net_receive_e6`, `rfq_id`, `expires_at`.
- After poll: print terminal status + `tx_hash` when present.
- Non-zero exit on: validation errors, `NO_QUOTES`, expired accept, signature failure, auth failure, terminal `FAILED`, insufficient balance / reservation errors. Never treat business `FAILED` under HTTP 200 as success.

## Client API sketch

```python
class CombosRfqClient:
    async def list_combo_markets(self, *, limit=50, cursor=None) -> dict: ...
    async def resolve_legs(self, legs: list[str]) -> list[str]: ...  # → position ids
    async def create_request(self, *, leg_position_ids, direction, size) -> RfqCreateResult: ...
    async def accept_quote(self, *, rfq_id, quote, request) -> RfqAcceptResult: ...
    async def get_status(self, rfq_id: str) -> RfqStatus: ...
    async def buy(self, *, legs_or_positions, usdc: float) -> RfqStatus: ...  # create+accept+poll
    async def sell(self, *, legs_or_positions, size: float) -> RfqStatus: ...
```

Typed results carry `rfq_id`, `status`, quote amounts, `error`, `tx_hash`.

## Testing / verification (planning phase will detail)

- Unit: HMAC header construction; size `value_e6` encoding; legs parser; Deposit wrap vs Proxy path branching.
- Integration (manual / guarded): `catalog` against live public API; one small live BUY only when operator intentionally runs CLI with funded Deposit wallet (`signature_type=3`).

## Open implementation notes (non-blocking)

- Exact Deposit ERC-7739 wrap helpers: port from official docs (viem example) into Python (`eth_account` / equivalent). Prefer a small dedicated signer helper next to the client if `py-clob-client-v2` does not already expose Exchange v3 Combos signing.
- Confirm at implement time whether any catalog field maps cleanly to `--combo-id` templates; fall back to position-id shorthand.

## Spec self-review

- Placeholders: none intentional; `--combo-id` resolution has an explicit fallback if catalog has no template id.
- Contradiction fixed: buy/sell size units now match platform (notional vs shares), not “either flag on either command”.
- Ambiguity: `--combo-id` documented as template-or-shorthand; implementer must print which path was used.
- Scope: no strategy, no dry-run default, no price cap — matches locked decisions.
