"""
Polymarket CLOB client — exchange-layer adapter for the trading bot.

Wraps `py-clob-client-v2` (synchronous, CLOB V2) in an async interface that
mirrors the public shape of the legacy `PolymarketClient` so that strategies,
jobs and the dashboard can swap exchanges without rewriting business logic.

Concept mapping (legacy exchange shape → Polymarket):
  ticker (string)               → condition_id (hex string)
  yes/no on one ticker          → two ERC1155 token_ids
  prices in cents (1–99)        → prices in dollars (0.01–0.99)
  RSA-signed REST headers       → EIP-712 signed orders + L2 API creds
  /trade-api/v2/markets         → Gamma API (see gamma_client.py — Step 3)
  /portfolio/balance            → USDC.e balance on Polygon (web3 RPC)

The adapter caches `(condition_id → (yes_token_id, no_token_id))` so callers
can refer to markets by a single identifier. The cache is populated either by
the ingestion job calling :meth:`register_market`, or lazily through an
attached `GammaClient` (set on construction or via :meth:`set_gamma_client`).

Constructor is intentionally cheap (no network I/O). Underlying clients are
materialised on first use, so importing this module never fails just because
`py-clob-client-v2` is not installed yet.
"""

import asyncio
import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.logging_setup import TradingLoggerMixin
from src.config.settings import validate_endpoint_url


# --------------------------------------------------------------------------
# Constants — CLOB V2 (py-clob-client-v2). Verify against SDK config module.
# --------------------------------------------------------------------------

# Legacy USDC.e (still useful for wrap/balance diagnostics).
USDC_E_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
# CLOB V2 collateral: pUSD (Polymarket USD).
PUSD_POLYGON = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
# Prefer pUSD for trading balance; fall back to USDC.e when unset.
COLLATERAL_TOKEN_POLYGON = PUSD_POLYGON

# Default endpoints / chain
DEFAULT_HOST = "https://clob.polymarket.com"
DEFAULT_GAMMA_HOST = "https://gamma-api.polymarket.com"
DEFAULT_DATA_HOST = "https://data-api.polymarket.com"  # positions/portfolio data
DEFAULT_CHAIN_ID = 137  # Polygon mainnet
DEFAULT_RPC_URL = "https://polygon-rpc.com"
DEFAULT_GEOBLOCK_URL = "https://polymarket.com/api/geoblock"

# On-disk cache for the condition_id → token_ids map. Persists across bot
# restarts so we don't pay a Gamma round-trip on the first order after each
# launch. Plain JSON; safe to delete to force re-discovery.
DEFAULT_TOKEN_CACHE_PATH = Path("data") / "token_cache.json"

# CLOB V2 exchange contracts that need ERC20 allowance on collateral (pUSD)
# and set-approval-for-all on conditional tokens. Addresses from
# py_clob_client_v2.config.get_contract_config(137).
POLYMARKET_SPENDERS = {
    "ctf_exchange_v2":       "0xE111180000d2663C0091e4f400237545B87B996B",
    "neg_risk_exchange_v2": "0xe2222d279d744050d28e00520010520000310F59",
    "neg_risk_adapter":     "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
}

# Conditional Tokens Framework (ERC1155) on Polygon.
CTF_ERC1155_POLYGON = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
_ZERO_BYTES32 = b"\x00" * 32
_REDEEM_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# Treat anything ≥ this allowance as "set" for routine bot operation.
# 10k USDC covers any realistic single-bot deployment; users running
# enterprise-scale capital can bump it. Set very low (or to 0) and you'll
# get false positives — better to leave as-is and rely on the SDK's own
# insufficient-allowance error if a single order exceeds the approval.
ALLOWANCE_OK_THRESHOLD = 10_000 * 10**6  # 10 000 USDC in 6-decimal units

# When the standard `set_allowances.py` script is run it always approves
# `MAX_UINT256` (2**256-1), so any threshold below that flags the wallet as
# correctly set up. The gist below the script comments still describes the
# canonical "approve MAX" pattern.


# --------------------------------------------------------------------------
# Exception hierarchy — kept distinct so callers can react to specific
# failure classes (cash, allowance, rate limit) rather than parsing strings.
# --------------------------------------------------------------------------

class PolymarketAPIError(Exception):
    """Generic Polymarket / CLOB failure."""


class PolymarketAuthError(PolymarketAPIError):
    """Bad private key, missing API creds, or signature rejected."""


class InsufficientFundsError(PolymarketAPIError):
    """Funder USDC balance is too low for the requested order."""


class RateLimitError(PolymarketAPIError):
    """CLOB returned 429 / signalled rate limit."""


class UnknownMarketError(PolymarketAPIError):
    """No token_ids cached for the given condition_id. Call
    :meth:`register_market` before placing the order, or attach a
    GammaClient for lazy lookup."""


class AllowanceError(PolymarketAPIError):
    """USDC / CTF allowance not set on one of the Polymarket contracts.
    Run `python scripts/set_allowances.py` to provision them once per wallet."""


class GeoblockError(PolymarketAPIError):
    """This IP is in a region Polymarket does not allow to open new orders."""


# --------------------------------------------------------------------------
# Internal data structures
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenIds:
    """YES / NO ERC1155 token IDs + market-level routing metadata for a
    single Polymarket condition.

    `neg_risk=True` markets settle through `neg_risk_exchange` rather than the
    default `ctf_exchange`; the SDK uses this flag to pick the right contract
    when signing the EIP-712 order.

    `tick_size` is the minimum price increment for the market. Polymarket
    publishes one of {0.001, 0.01, 0.1} per market — defaults to 0.01 which
    is the most common.
    """
    yes: str
    no: str
    neg_risk: bool = False
    tick_size: float = 0.01


# Minimal ERC20 ABI fragments — only what we read. Full ABI is pulled from
# web3.py if needed elsewhere.
_ERC20_BALANCE_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "remaining", "type": "uint256"}],
        "type": "function",
    },
]


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

class PolymarketClient(TradingLoggerMixin):
    """Async wrapper around `py-clob-client` mirroring PolymarketClient's surface.

    Differences from PolymarketClient that callers must know about:
      * Markets are addressed by **condition_id** (hex string), not ticker.
      * Prices are quoted in **dollars** (0.0–1.0), not cents.
      * `get_balance()` reads USDC.e on Polygon via web3.
      * Market discovery lives in :class:`GammaClient` — `get_markets()` here
        raises NotImplementedError pointing to it.
      * ERC20 allowances must be set once per wallet before the first order
        (scripts/set_allowances.py); the client raises :class:`AllowanceError`
        with a clear message if the CLOB rejects the order for that reason.
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        funder: Optional[str] = None,
        host: Optional[str] = None,
        chain_id: Optional[int] = None,
        signature_type: Optional[int] = None,
        polygon_rpc_url: Optional[str] = None,
        gamma_client: Optional[Any] = None,  # GammaClient
        token_cache_path: Optional[Path] = None,
        max_retries: int = 5,
        backoff_factor: float = 0.5,
    ) -> None:
        self.private_key = (private_key or os.getenv("POLYMARKET_PRIVATE_KEY", "")).strip()
        self.funder = (funder or os.getenv("POLYMARKET_FUNDER", "") or "").strip() or None
        self.host = validate_endpoint_url(
            (host or os.getenv("POLYMARKET_HOST", DEFAULT_HOST)).rstrip("/"),
            what="POLYMARKET_HOST",
        )
        self.chain_id = int(chain_id or os.getenv("POLYMARKET_CHAIN_ID", DEFAULT_CHAIN_ID))
        self.signature_type = (
            signature_type
            if signature_type is not None
            else int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "3"))
        )
        # CLOB V2 rejects EOA makers. If funder is missing but we have a key,
        # resolve proxyWallet from Gamma on first authenticated use.
        self._proxy_resolved = bool(self.funder)
        self.polygon_rpc_url = validate_endpoint_url(
            (polygon_rpc_url or os.getenv("POLYGON_RPC_URL", "") or DEFAULT_RPC_URL),
            what="POLYGON_RPC_URL",
        )
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        if not self.private_key:
            # Defer the hard failure to the first authenticated call so that
            # imports / dashboards / health checks can still load the module.
            self.logger.warning(
                "POLYMARKET_PRIVATE_KEY not set — authenticated calls will fail."
            )

        # Lazy underlyings
        self._client: Optional[Any] = None  # py_clob_client_v2.client.ClobClient
        self._w3: Optional[Any] = None      # web3.Web3
        self._signer_address: Optional[str] = None
        self._api_creds_set = False

        # condition_id → TokenIds. Loaded from disk if a previous run wrote
        # one; written back on flush_token_cache() / close() so the next
        # process start finds an already-warm cache (avoids the Gamma round-
        # trip that would otherwise hit on every cold-start order).
        self._token_cache_path = (
            token_cache_path if token_cache_path is not None
            else DEFAULT_TOKEN_CACHE_PATH
        )
        self._token_cache: Dict[str, TokenIds] = self._load_token_cache()
        self._token_cache_dirty = False
        self._ob_sema = asyncio.Semaphore(8)
        self._gamma = gamma_client
        self._geoblocked = False
        self._geoblock_status: Optional[Dict[str, Any]] = None

        self.logger.info(
            "PolymarketClient initialized (lazy)",
            host=self.host,
            chain_id=self.chain_id,
            signature_type=self.signature_type,
            funder_set=bool(self.funder),
        )

    # ------------------------------------------------------------------
    # Lazy underlying clients
    # ------------------------------------------------------------------

    def set_gamma_client(self, gamma_client: Any) -> None:
        """Attach a GammaClient used for lazy `condition_id → token_ids`
        resolution. Optional — not needed if the ingestion job pre-registers
        every market via :meth:`register_market`."""
        self._gamma = gamma_client

    def _resolve_proxy_wallet_sync(self) -> None:
        """Look up Gamma public-profile.proxyWallet for the signer EOA."""
        if getattr(self, "_proxy_resolved", False) and self.funder:
            return
        try:
            import httpx

            eoa = self._get_signer_address()
            url = f"https://gamma-api.polymarket.com/public-profile?address={eoa}"
            with httpx.Client(timeout=15.0) as http:
                resp = http.get(url)
                resp.raise_for_status()
                data = resp.json() or {}
            proxy = (data.get("proxyWallet") or "").strip()
            if proxy:
                self.funder = proxy
                self.logger.info(
                    "Resolved Polymarket proxyWallet as funder",
                    eoa=eoa,
                    funder=proxy,
                )
            else:
                self.logger.warning(
                    "Gamma public-profile has no proxyWallet; set POLYMARKET_FUNDER",
                    eoa=eoa,
                )
        except Exception as exc:
            self.logger.warning(f"Failed to resolve proxyWallet: {exc}")
        finally:
            self._proxy_resolved = True

    def _ensure_clob(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from py_clob_client_v2.client import ClobClient
        except ImportError as exc:  # pragma: no cover - import guard
            raise PolymarketAPIError(
                "py-clob-client-v2 is not installed. "
                "Run `pip install -r requirements.txt` "
                "(CLOB V2 requires py-clob-client-v2, not legacy py-clob-client)."
            ) from exc

        if not self.private_key:
            raise PolymarketAuthError(
                "POLYMARKET_PRIVATE_KEY missing — cannot construct CLOB client."
            )

        # CLOB V2 deposit-wallet flow needs funder before client construction.
        if self.signature_type != 0 and not self.funder:
            self._resolve_proxy_wallet_sync()

        kwargs: Dict[str, Any] = {
            "key": self.private_key,
            "chain_id": self.chain_id,
            "signature_type": self.signature_type,
            "retry_on_error": True,
        }
        if self.funder:
            kwargs["funder"] = self.funder

        try:
            self._client = ClobClient(self.host, **kwargs)
        except Exception as exc:
            raise PolymarketAuthError(
                f"Failed to construct ClobClient: {exc}"
            ) from exc

        self.logger.info(
            "py-clob-client-v2 connected",
            host=self.host,
            signature_type=self.signature_type,
            funder=self.funder,
        )
        return self._client

    async def _ensure_api_creds(self) -> None:
        """Derive (or create) L2 API credentials from the wallet signature.
        Polymarket's CLOB requires an HMAC-style L2 API key for authenticated
        endpoints; the SDK derives it from a signature over a fixed payload.
        Idempotent — safe to call before every authenticated request."""
        if self._api_creds_set:
            return
        client = self._ensure_clob()

        def _set_creds() -> None:
            # py-clob-client-v2: create_or_derive_api_key (v1 used *_api_creds)
            creds = client.create_or_derive_api_key()
            client.set_api_creds(creds)

        try:
            await asyncio.to_thread(_set_creds)
        except Exception as exc:
            raise PolymarketAuthError(
                f"Failed to derive Polymarket API credentials: {exc}"
            ) from exc
        self._api_creds_set = True
        self.logger.info("Polymarket API credentials set")

    def _ensure_w3(self) -> Any:
        if self._w3 is not None:
            return self._w3
        try:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware
        except ImportError as exc:
            raise PolymarketAPIError(
                "web3 is not installed. Run `pip install -r requirements.txt`."
            ) from exc
        self._w3 = Web3(Web3.HTTPProvider(self.polygon_rpc_url))
        # Polygon is a POA chain — without this, eth_getBlockByNumber can fail
        # with ExtraDataLengthError and balance reads become flaky.
        try:
            self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except Exception:
            pass
        return self._w3

    def _get_signer_address(self) -> str:
        if self._signer_address is not None:
            return self._signer_address
        try:
            from eth_account import Account
        except ImportError as exc:
            raise PolymarketAPIError(
                "eth-account is not installed. Run `pip install -r requirements.txt`."
            ) from exc
        if not self.private_key:
            raise PolymarketAuthError("POLYMARKET_PRIVATE_KEY not set.")
        acct = Account.from_key(self.private_key)
        self._signer_address = acct.address
        return self._signer_address

    def _get_funding_address(self) -> str:
        """Address that holds collateral and is the CLOB order maker.

        For signature_type 0 (legacy EOA) this is the signer. For deposit /
        proxy flows (1/2/3) it is POLYMARKET_FUNDER / Gamma proxyWallet.
        """
        if not self.funder and self.signature_type != 0:
            self._resolve_proxy_wallet_sync()
        return self.funder or self._get_signer_address()

    # ------------------------------------------------------------------
    # Market identifier resolution
    # ------------------------------------------------------------------

    def register_market(
        self,
        condition_id: str,
        yes_token_id: str,
        no_token_id: str,
        neg_risk: bool = False,
        tick_size: float = 0.01,
    ) -> None:
        """Register YES/NO token_ids and routing metadata for a condition_id.

        The ingestion job calls this for every market pulled from Gamma so
        that subsequent order/orderbook calls hit the CLOB without an extra
        Gamma round-trip. Routing metadata (`neg_risk`, `tick_size`) is
        forwarded to the SDK on order placement — getting either wrong is the
        most common cause of "invalid order" rejections from the CLOB.

        Persistence is deferred — call :meth:`flush_token_cache` once per
        scan (not per market). Writing the full JSON on every register was
        taking ~2.5 minutes per 2000-market cycle.
        """
        incoming = TokenIds(
            yes=str(yes_token_id),
            no=str(no_token_id),
            neg_risk=bool(neg_risk),
            tick_size=float(tick_size or 0.01),
        )
        existing = self._token_cache.get(condition_id)
        if existing == incoming:
            return
        self._token_cache[condition_id] = incoming
        self._token_cache_dirty = True

    def _load_token_cache(self) -> Dict[str, TokenIds]:
        """Read the persisted condition_id → TokenIds map. Returns {} on any
        failure (corrupt JSON, missing file, schema drift) — the cache will
        rebuild itself from the next ingestion cycle."""
        path = self._token_cache_path
        try:
            if not path.exists():
                return {}
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, TokenIds] = {}
        for cond, entry in raw.items():
            try:
                out[cond] = TokenIds(
                    yes=str(entry["yes"]),
                    no=str(entry["no"]),
                    neg_risk=bool(entry.get("neg_risk", False)),
                    tick_size=float(entry.get("tick_size", 0.01) or 0.01),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def flush_token_cache(self) -> None:
        """Persist dirty token-id cache once. Safe to call if nothing changed."""
        if not self._token_cache_dirty:
            return
        self._save_token_cache()

    def _save_token_cache(self) -> None:
        """Write the cache to disk. Best-effort — never raises into callers
        because token_id resolution still works in-memory if persistence fails."""
        try:
            path = self._token_cache_path
            path.parent.mkdir(parents=True, exist_ok=True)
            serializable = {
                cond: asdict(ids) for cond, ids in self._token_cache.items()
            }
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(serializable, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
            self._token_cache_dirty = False
        except OSError:
            pass

    def get_token_ids(self, condition_id: str) -> Optional[TokenIds]:
        """Return cached token IDs for a condition_id, or None if not seen."""
        return self._token_cache.get(condition_id)

    async def _ensure_token_ids(self, condition_id: str) -> TokenIds:
        """Return cached token ids, lazily resolving via Gamma when attached."""
        ids = self._token_cache.get(condition_id)
        if ids is not None:
            return ids
        if self._gamma is None:
            raise UnknownMarketError(
                f"No token_ids cached for condition_id={condition_id}. "
                "Register the market or attach a GammaClient."
            )
        try:
            yes_id, no_id = await self._gamma.get_token_ids(condition_id)
        except Exception as exc:
            raise UnknownMarketError(
                f"Gamma lookup for condition_id={condition_id} failed: {exc}"
            ) from exc
        self.register_market(condition_id, yes_id, no_id)
        return self._token_cache[condition_id]

    async def _resolve_token_id(self, condition_id: str, side: str) -> str:
        side_u = side.upper()
        if side_u not in ("YES", "NO"):
            raise ValueError(f"side must be 'YES' or 'NO', got {side!r}")
        ids = await self._ensure_token_ids(condition_id)
        return ids.yes if side_u == "YES" else ids.no

    # ------------------------------------------------------------------
    # Balance / portfolio  (pUSD preferred; USDC.e still reported)
    # ------------------------------------------------------------------

    async def get_balance(self, include_mtm: bool = True) -> Dict[str, Any]:
        """Collateral balance for the funding address (+ optional MTM).

        CLOB V2 settles in pUSD. We read pUSD first and also USDC.e (legacy /
        pre-wrap). ``balance_dollars`` prefers pUSD; if pUSD is zero but
        USDC.e remains, we surface USDC.e so ops can see funds that still
        need wrapping via Polymarket UI / onramp.
        """
        addr = self._get_funding_address()

        def _read_token_balance(token: str) -> int:
            w3 = self._ensure_w3()
            erc20 = w3.eth.contract(
                address=w3.to_checksum_address(token),
                abi=_ERC20_BALANCE_ABI,
            )
            return erc20.functions.balanceOf(w3.to_checksum_address(addr)).call()

        try:
            pusd_raw, usdc_raw = await asyncio.gather(
                asyncio.to_thread(_read_token_balance, PUSD_POLYGON),
                asyncio.to_thread(_read_token_balance, USDC_E_POLYGON),
            )
        except Exception as exc:
            raise PolymarketAPIError(
                f"Failed to read collateral balance for {addr}: {exc}"
            ) from exc

        pusd_dollars = pusd_raw / 1_000_000
        usdc_dollars = usdc_raw / 1_000_000
        if pusd_dollars > 0:
            dollars = pusd_dollars
            collateral = "pUSD"
        else:
            dollars = usdc_dollars
            collateral = "USDC.e"
            if usdc_dollars > 0:
                self.logger.warning(
                    "pUSD balance is 0 but USDC.e remains — wrap USDC.e to pUSD "
                    "on Polymarket before CLOB V2 trading will succeed",
                    usdc_e=round(usdc_dollars, 2),
                )

        portfolio_dollars = 0.0
        if include_mtm:
            try:
                positions_resp = await self.get_positions()
                for pos in positions_resp.get("market_positions", []):
                    size = float(pos.get("size", 0) or 0)
                    if size <= 0:
                        continue
                    cur = float(pos.get("current_price", 0) or 0)
                    portfolio_dollars += size * cur
            except Exception as exc:
                self.logger.warning(
                    f"MTM calculation failed (returning 0): {exc}"
                )

        return {
            "balance":                 int(round(dollars * 100)),
            "balance_dollars":         dollars,
            "portfolio_value":         int(round(portfolio_dollars * 100)),
            "portfolio_value_dollars": portfolio_dollars,
            "address":                 addr,
            "eoa_address":             self._get_signer_address(),
            "funder_address":          addr,
            "collateral_token":        collateral,
            "pusd_dollars":            pusd_dollars,
            "usdc_e_dollars":          usdc_dollars,
        }

    async def get_recent_collateral_deposits(
        self, *, lookback_blocks: int = 50_000, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return recent pUSD Transfer-in events to the funding (deposit) wallet.

        Used by the dashboard to show deposit address + inbound amounts + tx
        hashes. Public RPCs often cap eth_getLogs to ~10k blocks, so we page.
        """
        addr = self._get_funding_address()

        def _fetch() -> List[Dict[str, Any]]:
            from web3 import Web3

            w3 = self._ensure_w3()
            token = w3.to_checksum_address(PUSD_POLYGON)
            to_addr = w3.to_checksum_address(addr)
            latest = int(w3.eth.block_number)
            from_block = max(0, latest - int(lookback_blocks))
            transfer_topic = w3.keccak(text="Transfer(address,address,uint256)")
            to_topic = "0x" + ("0" * 24) + to_addr[2:].lower()

            chunk = 9_000
            logs: List[Any] = []
            start = from_block
            while start <= latest:
                end = min(start + chunk - 1, latest)
                try:
                    batch = w3.eth.get_logs(
                        {
                            "fromBlock": start,
                            "toBlock": end,
                            "address": token,
                            "topics": [transfer_topic, None, to_topic],
                        }
                    )
                    logs.extend(batch or [])
                except Exception as exc:
                    self.logger.debug(
                        f"deposit log chunk {start}-{end} failed: {exc}"
                    )
                start = end + 1

            out: List[Dict[str, Any]] = []
            for log in logs[-limit:]:
                fr = "0x" + log["topics"][1].hex()[-40:]
                raw = log["data"]
                raw_hex = raw.hex() if hasattr(raw, "hex") else str(raw)
                if raw_hex.startswith("0x"):
                    raw_hex = raw_hex[2:]
                amount = int(raw_hex or "0", 16) / 1_000_000
                txh = log["transactionHash"]
                tx_hash = txh.hex() if hasattr(txh, "hex") else str(txh)
                if not tx_hash.startswith("0x"):
                    tx_hash = "0x" + tx_hash
                out.append(
                    {
                        "tx_hash": tx_hash,
                        "from": Web3.to_checksum_address(fr),
                        "to": to_addr,
                        "amount_dollars": amount,
                        "block_number": int(log["blockNumber"]),
                        "token": "pUSD",
                    }
                )
            out.reverse()  # newest first
            return out

        try:
            return await asyncio.to_thread(_fetch)
        except Exception as exc:
            self.logger.warning(f"get_recent_collateral_deposits failed: {exc}")
            return []

    async def get_allowance(self, spender: str) -> int:
        """Read the pUSD allowance granted to `spender` from the funding
        address. Returns the raw 6-decimal integer."""
        addr = self._get_funding_address()

        def _read() -> int:
            w3 = self._ensure_w3()
            erc20 = w3.eth.contract(
                address=w3.to_checksum_address(COLLATERAL_TOKEN_POLYGON),
                abi=_ERC20_BALANCE_ABI,
            )
            return erc20.functions.allowance(
                w3.to_checksum_address(addr),
                w3.to_checksum_address(spender),
            ).call()

        try:
            return await asyncio.to_thread(_read)
        except Exception as exc:
            raise PolymarketAPIError(
                f"Failed to read pUSD allowance for {spender}: {exc}"
            ) from exc

    async def check_allowances(self) -> Dict[str, bool]:
        """Return `{spender_label: bool_is_set}` for every Polymarket spender.
        A wallet must have all three set before placing the first order. This
        method is non-mutating — use scripts/set_allowances.py to provision
        them on-chain.
        """
        results: Dict[str, bool] = {}
        for label, address in POLYMARKET_SPENDERS.items():
            try:
                amount = await self.get_allowance(address)
                results[label] = amount >= ALLOWANCE_OK_THRESHOLD
            except Exception:
                results[label] = False
        return results

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    async def get_positions(
        self, condition_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """List open positions for the funder.

        Polymarket positions are ERC1155 token holdings, exposed via the
        Polymarket Data API at `https://data-api.polymarket.com/positions?user=
        {address}`. The CLOB SDK does NOT have a positions endpoint — this is
        a separate authenticated-by-address (no signing) HTTP read.

        Returns a legacy-shaped dict so dashboard / cli.py status / close-all
        paths keep working unchanged:
            {
              "market_positions": [{...}, ...],
              "event_positions":  [{...}, ...],   # alias — Polymarket has
                                                  # no "event" parent concept
            }
        """
        addr = self._get_funding_address()
        url = f"{DEFAULT_DATA_HOST}/positions"
        params = {"user": addr}
        try:
            import httpx
            async with httpx.AsyncClient(timeout=20.0) as http:
                resp = await http.get(url, params=params)
                resp.raise_for_status()
                raw = resp.json() or []
        except Exception as exc:
            raise PolymarketAPIError(
                f"Failed to fetch positions from data-api: {exc}"
            ) from exc

        if not isinstance(raw, list):
            # Some Polymarket endpoints wrap the list in {"data": [...]}.
            raw = raw.get("data", []) if isinstance(raw, dict) else []

        positions: List[Dict[str, Any]] = []
        for p in raw:
            cond = p.get("conditionId") or p.get("condition_id")
            if condition_id and cond != condition_id:
                continue
            token = str(p.get("asset") or p.get("token_id") or "")
            outcome_idx = p.get("outcomeIndex", p.get("outcome_index", 0))
            side = "YES" if outcome_idx == 0 else "NO"
            size = float(p.get("size", 0) or 0)
            avg = float(p.get("avgPrice", p.get("avg_price", 0)) or 0)
            cur = float(p.get("curPrice", p.get("current_price", 0)) or 0)
            pnl = float(p.get("realizedPnl", p.get("realized_pnl", 0)) or 0)

            positions.append({
                # Polymarket-native fields
                "condition_id":          cond,
                "token_id":              token,
                "side":                  side,
                "size":                  size,
                "avg_price":             avg,
                "current_price":         cur,
                "realized_pnl_dollars":  pnl,
                "title":                 p.get("title") or "",
                "endDate":               p.get("endDate") or p.get("end_date") or "",
                "redeemable":            bool(p.get("redeemable", False)),
                "negative_risk":         bool(p.get("negativeRisk", p.get("negRisk", False))),
                "event_slug":            p.get("eventSlug") or "",
                # legacy legacy-shape aliases for the dashboard / cli
                "ticker":                cond,
                "event_ticker":          cond,
                "position":              int(size) if side == "YES" else -int(size),
                "event_exposure_dollars": str(size * cur),
                "total_cost_dollars":     str(size * avg),
                "fees_paid_dollars":      "0",
            })

        return {"market_positions": positions, "event_positions": positions}

    async def redeem_condition(
        self, condition_id: str, neg_risk: bool = False
    ) -> Dict[str, Any]:
        """Redeem a resolved CTF condition back to collateral.

        EOA wallets (signature_type=0) send `redeemPositions` on-chain.
        Proxy / deposit wallets hold tokens on the funder, so the EOA cannot
        redeem directly — callers should treat that as `redeem_needed`.
        """
        if int(self.signature_type or 0) != 0:
            raise PolymarketAPIError(
                "Proxy/deposit wallet: redeem in the Polymarket UI or Relayer "
                f"(condition={condition_id[:18]})"
            )
        if not condition_id:
            raise PolymarketAPIError("redeem_condition requires condition_id")

        def _send():
            from eth_account import Account
            from web3 import Web3

            w3 = self._ensure_w3()
            acct = Account.from_key(self.private_key)
            target = (
                POLYMARKET_SPENDERS["neg_risk_adapter"] if neg_risk
                else CTF_ERC1155_POLYGON
            )
            ctf = w3.eth.contract(
                address=Web3.to_checksum_address(target),
                abi=_REDEEM_ABI,
            )
            cond = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
            if len(cond) != 32:
                raise PolymarketAPIError(f"condition_id must be 32 bytes, got {len(cond)}")
            tx = ctf.functions.redeemPositions(
                Web3.to_checksum_address(COLLATERAL_TOKEN_POLYGON),
                _ZERO_BYTES32,
                cond,
                [1, 2],
            ).build_transaction({
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "chainId": self.chain_id,
            })
            signed = acct.sign_transaction(tx)
            raw = getattr(signed, "rawTransaction", None) or signed.raw_transaction
            tx_hash = w3.eth.send_raw_transaction(raw)
            return tx_hash.hex()

        try:
            tx_hash = await asyncio.to_thread(_send)
        except PolymarketAPIError:
            raise
        except Exception as exc:
            raise PolymarketAPIError(f"redeemPositions failed: {exc}") from exc
        self.logger.info("Redeem submitted", condition_id=condition_id[:18], tx=tx_hash)
        return {"tx_hash": tx_hash, "condition_id": condition_id}

    # ------------------------------------------------------------------
    # Markets / orderbook
    # ------------------------------------------------------------------

    async def get_markets(self, **kwargs) -> Dict[str, Any]:
        """Market discovery is NOT a CLOB responsibility on Polymarket.

        Use `src.clients.gamma_client.GammaClient.get_markets()` instead.
        Kept as a stub so legacy callers fail loudly with a clear pointer
        rather than silently returning empty data.
        """
        raise NotImplementedError(
            "Use GammaClient.get_markets() — Polymarket market discovery is "
            "via Gamma API, not the CLOB."
        )

    async def get_market(self, condition_id: str) -> Dict[str, Any]:
        """Return a legacy-shaped market dict for `condition_id`.

        Pulls live prices from the order books for the YES and NO tokens and,
        if a GammaClient is attached, layers the metadata (title, category,
        rules, expiry) on top. Returns the same envelope shape as
        PolymarketClient.get_market: `{"market": {...}}`.
        """
        ids = await self._ensure_token_ids(condition_id)

        # Fetch both books in parallel, then collapse into the canonical
        # legacy shape: best ask = lowest sell price on each side.
        yes_book, no_book = await asyncio.gather(
            self._fetch_book_one(ids.yes),
            self._fetch_book_one(ids.no),
        )

        yes_bid = _best_bid_dollars(yes_book)
        yes_ask = _best_ask_dollars(yes_book)
        no_bid = _best_bid_dollars(no_book)
        no_ask = _best_ask_dollars(no_book)

        market_meta: Dict[str, Any] = {}
        if self._gamma is not None:
            try:
                market_meta = await self._gamma.get_market(condition_id) or {}
            except Exception:  # pragma: no cover - metadata is optional
                market_meta = {}

        return {
            "market": {
                "ticker":            condition_id,
                "condition_id":      condition_id,
                "title":             market_meta.get("question", market_meta.get("title", "")),
                "category":          market_meta.get("category", "unknown"),
                "status":            market_meta.get("status", "active"),
                # Dollar-denominated fields (preferred — *_dollars naming)
                "yes_bid_dollars":   yes_bid,
                "yes_ask_dollars":   yes_ask,
                "no_bid_dollars":    no_bid,
                "no_ask_dollars":    no_ask,
                # Legacy cent fields kept for old callers — same number ×100
                "yes_bid":           int(round(yes_bid * 100)),
                "yes_ask":           int(round(yes_ask * 100)),
                "no_bid":            int(round(no_bid * 100)),
                "no_ask":            int(round(no_ask * 100)),
                "yes_price":         int(round(yes_ask * 100)),
                "no_price":          int(round(no_ask * 100)),
                "last_price_dollars": yes_ask,
                "volume_fp":         market_meta.get("volume", 0),
                "volume":            market_meta.get("volume", 0),
                "expiration_time":   market_meta.get("end_date_iso", ""),
                "expiration_ts":     market_meta.get("end_ts", 0),
                "rules":             market_meta.get("description", ""),
                "yes_token_id":      ids.yes,
                "no_token_id":       ids.no,
            }
        }

    async def get_orderbook(
        self, condition_id: str, depth: int = 100
    ) -> Dict[str, Any]:
        """Return the orderbook for both YES and NO tokens of `condition_id`.

        Shape (legacy-compatible):
            {
              "orderbook": {
                "yes":      [[price_dollar_str, size_str], ...],  # YES bids
                "yes_asks": [[price_dollar_str, size_str], ...],  # YES asks
                "no":       [[price_dollar_str, size_str], ...],  # NO bids
                "no_asks":  [[price_dollar_str, size_str], ...],  # NO asks
              }
            }
        Levels are sorted highest-bid-first / lowest-ask-first (matching
        Polymarket convention so callers like `cli.py close-all` and
        `safe_compounder` keep working).
        """
        ids = await self._ensure_token_ids(condition_id)

        async with self._ob_sema:
            yes_book, no_book = await asyncio.gather(
                self._fetch_book_one(ids.yes, depth=depth),
                self._fetch_book_one(ids.no, depth=depth),
            )
        return {
            "orderbook": {
                "yes":      _bids_to_levels(yes_book),
                "yes_asks": _asks_to_levels(yes_book),
                "no":       _bids_to_levels(no_book),
                "no_asks":  _asks_to_levels(no_book),
            }
        }

    async def _fetch_book_one(self, token_id: str, depth: int = 100) -> Any:
        """Fetch a single token's book via the SDK. Returns the raw SDK object
        (`OrderBookSummary` in py-clob-client) for downstream parsers."""
        client = self._ensure_clob()

        def _call():
            return client.get_order_book(token_id)

        try:
            return await asyncio.to_thread(_call)
        except Exception as exc:
            msg = str(exc).lower()
            if (
                "404" in msg
                or "no orderbook" in msg
                or "server disconnected" in msg
                or "request exception" in msg
            ):
                # Resolved / unlisted / transient network — callers treat empty
                # bids/asks as skip. Do not escalate to ERROR spam.
                return None
            raise PolymarketAPIError(
                f"get_order_book failed for token_id={token_id}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Order placement / cancellation
    # ------------------------------------------------------------------

    async def place_order(
        self,
        ticker: str,                       # condition_id (kept name for compat)
        client_order_id: str,
        side: str,                         # "yes" / "no" — outcome side
        action: str,                       # "buy" / "sell"
        count: int,                        # share count
        type_: str = "market",             # "market" | "limit"
        yes_price: Optional[int] = None,   # cents (legacy shape)
        no_price: Optional[int] = None,    # cents (legacy shape)
        expiration_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Place a buy/sell order on the YES or NO side of a market.

        Argument shape mirrors PolymarketClient.place_order so existing callers
        compile without changes. Internally:
          * `ticker` is treated as `condition_id`
          * `side` (yes/no) → `token_id` via the cache
          * `yes_price` / `no_price` cents → dollars (Polymarket native)
          * `action` buy/sell → `BUY` / `SELL` on the resolved token_id
          * `type_` market/limit → `OrderType.FOK` / `OrderType.GTC`

        For market BUY orders the SDK expects an `amount` in USDC (the dollar
        amount you're willing to spend). We compute it as `count × price` if a
        price hint was passed; otherwise we look up the current ask. For market
        SELL orders the SDK expects `amount` in shares.
        """
        # Hard safety gate: refuse CLOB orders unless live trading is enabled.
        # Callers in paper/DRY_RUN must not reach the SDK.
        from src.config.settings import settings as _settings
        if self._geoblocked:
            geo = self._geoblock_status or {}
            raise GeoblockError(
                "Trading restricted in this region "
                f"(country={geo.get('country')}, ip={geo.get('ip')}). "
                "Move the bot to a Polymarket-allowed jurisdiction; "
                "do not try to bypass geoblock."
            )

        if not _settings.trading.live_trading_enabled:
            raise PolymarketAPIError(
                "Refusing to place order: live trading is disabled "
                "(DRY_RUN=true / LIVE_TRADING_ENABLED=false). "
                "Enable live trading explicitly before sending real orders."
            )

        try:
            from py_clob_client_v2.clob_types import (
                OrderArgs, MarketOrderArgs, OrderType, PartialCreateOrderOptions,
            )
            from py_clob_client_v2.order_builder.constants import BUY, SELL
        except ImportError as exc:
            raise PolymarketAPIError(
                "py-clob-client-v2 is not installed. "
                "Run `pip install -r requirements.txt`."
            ) from exc

        action_l = action.lower()
        side_l = side.lower()
        type_l = type_.lower()

        if action_l not in ("buy", "sell"):
            raise ValueError(f"action must be 'buy' or 'sell', got {action!r}")
        if type_l not in ("market", "limit"):
            raise ValueError(f"type_ must be 'market' or 'limit', got {type_!r}")

        token_id = await self._resolve_token_id(ticker, side_l)
        client = self._ensure_clob()
        await self._ensure_api_creds()

        # Pull market-level routing metadata cached at registration time.
        # Defaults are safe: standard CTF exchange + 1¢ tick. neg_risk markets
        # rejected with `not allowed for this market` if not flagged correctly.
        token_meta = self._token_cache.get(ticker)
        neg_risk = bool(token_meta.neg_risk) if token_meta else False
        tick_size = float(token_meta.tick_size) if token_meta else 0.01
        # py-clob-client ROUNDING_CONFIG is keyed by strings ("0.01"). A float
        # 0.01 raises KeyError(0.01) which we used to surface as
        # `order failed: 0.01`.
        order_options = PartialCreateOrderOptions(
            tick_size=_clob_tick_size(tick_size), neg_risk=neg_risk,
        )

        # Resolve price hint (cents → dollars). Required for limit orders;
        # optional but useful for market BUY (to compute USDC amount).
        price_cents = yes_price if side_l == "yes" else no_price
        price_dollars: Optional[float] = (
            price_cents / 100.0 if price_cents is not None else None
        )

        if type_l == "limit" and price_dollars is None:
            raise PolymarketAPIError(
                "Limit orders require yes_price or no_price (in cents)."
            )
        if price_dollars is not None and not (0.01 <= price_dollars <= 0.99):
            raise PolymarketAPIError(
                f"price {price_dollars:.4f} outside the valid range 0.01–0.99"
            )
        # Snap price to the market's tick size; otherwise the CLOB rejects with
        # "invalid price". Most markets are 0.01 ticks (already aligned), but
        # high-volume political markets sometimes use 0.001.
        if price_dollars is not None and tick_size > 0:
            price_dollars = round(round(price_dollars / tick_size) * tick_size, 4)

        poly_side = BUY if action_l == "buy" else SELL

        def _send():
            if type_l == "market":
                # SDK convention: BUY market amount = USDC dollars,
                #                 SELL market amount = share count.
                if poly_side == BUY:
                    if price_dollars is None:
                        raise PolymarketAPIError(
                            "market BUY needs a price hint (yes_price/no_price) "
                            "to compute the USDC spend; pass the current ask."
                        )
                    amount = count * price_dollars
                else:
                    amount = count
                args = MarketOrderArgs(
                    token_id=token_id, amount=amount, side=poly_side,
                )
                signed = client.create_market_order(args, order_options)
                return client.post_order(signed, order_type=OrderType.FOK)
            else:
                args = OrderArgs(
                    token_id=token_id,
                    price=price_dollars,
                    size=count,
                    side=poly_side,
                )
                signed = client.create_order(args, order_options)
                return client.post_order(signed, order_type=OrderType.GTC)

        try:
            resp = await asyncio.to_thread(_send)
        except Exception as exc:
            classified = _classify_order_error(exc)
            if isinstance(classified, GeoblockError):
                self._geoblocked = True
            raise classified from exc

        return _normalize_order_response(
            resp,
            condition_id=ticker,
            token_id=token_id,
            side=side_l,
            action=action_l,
            count=count,
            client_order_id=client_order_id,
        )

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel a single resting order by ID."""
        client = self._ensure_clob()
        await self._ensure_api_creds()

        def _call():
            try:
                from py_clob_client_v2.clob_types import OrderPayload
                return client.cancel_order(OrderPayload(orderID=str(order_id)))
            except TypeError:
                # Older SDKs accepted a bare order id / cancel()
                if hasattr(client, "cancel_order"):
                    return client.cancel_order(order_id)
                return client.cancel(order_id=order_id)

        try:
            resp = await asyncio.to_thread(_call)
        except Exception as exc:
            raise PolymarketAPIError(
                f"cancel_order({order_id}) failed: {exc}"
            ) from exc
        return resp if isinstance(resp, dict) else {"order_id": order_id, "raw": resp}

    async def cancel_all(self) -> Dict[str, Any]:
        """Cancel every resting order for this wallet."""
        client = self._ensure_clob()
        await self._ensure_api_creds()
        try:
            resp = await asyncio.to_thread(client.cancel_all)
        except Exception as exc:
            raise PolymarketAPIError(f"cancel_all failed: {exc}") from exc
        return resp if isinstance(resp, dict) else {"raw": resp}

    # ------------------------------------------------------------------
    # Order / trade history
    # ------------------------------------------------------------------

    # Map legacy-style status names → Polymarket CLOB statuses. Kept as a
    # module-private constant so callers can pass either vocabulary.
    _STATUS_ALIASES = {
        "resting": {"LIVE", "live", "open", "OPEN", "MATCHED", "matched"},
        "open":    {"LIVE", "live", "open", "OPEN"},
        "live":    {"LIVE", "live"},
    }

    async def get_orders(
        self,
        ticker: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return resting orders, optionally filtered by condition_id (`ticker`)
        or by status. Wraps the SDK's `get_orders(OpenOrderParams)`.

        `status` accepts either Polymarket vocabulary (`LIVE`, `MATCHED`, ...)
        or the legacy Polymarket term `resting` (mapped to `{LIVE, MATCHED, open}`),
        so calls written for the Polymarket client survive without changes.
        Add `ticker=condition_id` to scope to a single market.
        """
        try:
            from py_clob_client_v2.clob_types import OpenOrderParams
        except ImportError as exc:
            raise PolymarketAPIError(
                "py-clob-client is not installed. "
                "Run `pip install -r requirements.txt`."
            ) from exc

        client = self._ensure_clob()
        await self._ensure_api_creds()

        params = OpenOrderParams(market=ticker) if ticker else OpenOrderParams()

        def _call():
            # py-clob-client-v2 renamed get_orders → get_open_orders
            getter = getattr(client, "get_open_orders", None) or getattr(
                client, "get_orders", None
            )
            return getter(params)

        try:
            raw = await asyncio.to_thread(_call) or []
        except Exception as exc:
            raise PolymarketAPIError(f"get_orders failed: {exc}") from exc

        if status:
            allowed = self._STATUS_ALIASES.get(status.lower(), {status})
        else:
            allowed = None

        # Normalize each SDK order into a legacy-shape dict so downstream code
        # (cli.py, safe_compounder) can read fields like `ticker`, `side`,
        # `status`, `order_id`, `yes_price` without per-exchange conditionals.
        orders: List[Dict[str, Any]] = []
        for o in raw:
            cond = o.get("market") or o.get("conditionId") or o.get("condition_id") or ""
            if ticker and cond != ticker:
                continue
            if allowed is not None and o.get("status") not in allowed:
                continue
            side_outcome = (o.get("outcome") or "").upper()
            if not side_outcome:
                # Fall back to numeric outcome index (0=YES, 1=NO)
                idx = o.get("outcomeIndex", o.get("outcome_index", 0))
                side_outcome = "YES" if idx == 0 else "NO"
            price = float(o.get("price", 0) or 0)
            order_id = o.get("id") or o.get("orderID") or o.get("order_id")
            normalized = {
                **o,  # keep all native fields too
                "ticker":    cond,
                "condition_id": cond,
                "order_id":  order_id,
                "side":      side_outcome.lower(),  # 'yes' / 'no'
                "status":    o.get("status"),
                "yes_price": int(round(price * 100)) if side_outcome == "YES" else 0,
                "no_price":  int(round(price * 100)) if side_outcome == "NO" else 0,
                "price_dollars": price,
                "count":     int(float(o.get("size", 0) or 0)),
            }
            orders.append(normalized)
        return {"orders": orders}

    async def _fetch_trades_data_api(
        self,
        limit: int = 100,
        ticker: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch recent trades from Polymarket data-api (no CLOB auth, faster)."""
        addr = self._get_funding_address()
        params: Dict[str, Any] = {"user": addr, "limit": max(1, min(limit, 500))}
        if ticker:
            params["market"] = ticker
        try:
            import httpx
            async with httpx.AsyncClient(timeout=12.0) as http:
                resp = await http.get(f"{DEFAULT_DATA_HOST}/trades", params=params)
                resp.raise_for_status()
                raw = resp.json() or []
        except Exception as exc:
            raise PolymarketAPIError(
                f"data-api trades fetch failed: {exc}"
            ) from exc

        if not isinstance(raw, list):
            raw = raw.get("data", []) if isinstance(raw, dict) else []

        trades: List[Dict[str, Any]] = []
        for t in raw[:limit]:
            cond = t.get("conditionId") or t.get("condition_id") or ""
            if ticker and cond and cond != ticker:
                continue
            ts = t.get("timestamp") or t.get("match_time") or ""
            trades.append({
                "market": cond,
                "conditionId": cond,
                "side": t.get("side") or "",
                "outcome": t.get("outcome") or "",
                "size": float(t.get("size", 0) or 0),
                "price": float(t.get("price", 0) or 0),
                "status": t.get("status") or "CONFIRMED",
                "match_time": str(ts),
                "last_update": str(ts),
                "transaction_hash": t.get("transactionHash") or t.get("transaction_hash") or "",
                "title": t.get("title") or "",
                "slug": t.get("slug") or "",
            })
        return trades

    async def get_trades(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return executed trades for the funder.

        Tries the CLOB SDK first (with a short timeout), then falls back to the
        Polymarket data-api which is faster and more reliable for dashboard reads.
        """
        try:
            from py_clob_client_v2.clob_types import TradeParams
        except ImportError as exc:
            raise PolymarketAPIError(
                "py-clob-client is not installed. "
                "Run `pip install -r requirements.txt`."
            ) from exc

        client = self._ensure_clob()
        await self._ensure_api_creds()

        params = TradeParams(market=ticker) if ticker else TradeParams()

        def _call():
            return client.get_trades(params, cursor) if cursor else client.get_trades(params)

        try:
            raw = await asyncio.wait_for(asyncio.to_thread(_call), timeout=8.0) or []
            trades = []
            for t in raw[:limit]:
                cond = t.get("market") or t.get("conditionId") or t.get("condition_id")
                if ticker and cond != ticker:
                    continue
                trades.append(t)
            return {"trades": trades, "cursor": None, "source": "clob"}
        except Exception as exc:
            self.logger.warning(
                "CLOB get_trades failed (%s); falling back to data-api", exc,
            )

        trades = await self._fetch_trades_data_api(limit=limit, ticker=ticker)
        return {"trades": trades, "cursor": None, "source": "data-api"}

    # `get_fills` is the legacy-named alias for executed trades.
    async def get_fills(
        self, ticker: Optional[str] = None, limit: int = 100
    ) -> Dict[str, Any]:
        return await self.get_trades(ticker=ticker, limit=limit)

    async def get_market_history(
        self,
        ticker: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Time-series price history for a market.

        Polymarket exposes this via the data API (`/prices-history?market=...`)
        rather than the CLOB. The Gamma client (Step 3) wraps that endpoint;
        until then this raises NotImplementedError so callers fail loudly
        instead of getting empty data.
        """
        if self._gamma is not None and hasattr(self._gamma, "get_price_history"):
            return await self._gamma.get_price_history(
                ticker, start_ts=start_ts, end_ts=end_ts, limit=limit
            )
        raise NotImplementedError(
            "Polymarket price history lives on the data API. "
            "Attach a GammaClient with get_price_history() (Step 3)."
        )

    async def check_geoblock(self) -> Dict[str, Any]:
        """Ask Polymarket whether this host's IP may open new orders.

        Docs: GET https://polymarket.com/api/geoblock
        Does not bypass restrictions; callers should fall back to paper
        trading when `blocked` is true.
        """

        def _call() -> Dict[str, Any]:
            import urllib.request

            req = urllib.request.Request(
                DEFAULT_GEOBLOCK_URL,
                headers={"User-Agent": "polymarket-trading-bot/geoblock-check"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())

        try:
            data = await asyncio.to_thread(_call)
        except Exception as exc:
            self.logger.warning(f"geoblock check failed: {exc}")
            return {"blocked": False, "error": str(exc)}

        if not isinstance(data, dict):
            return {"blocked": False, "error": "unexpected geoblock payload"}

        self._geoblock_status = data
        if data.get("blocked"):
            self._geoblocked = True
            self.logger.error(
                "Polymarket geoblock: this IP cannot open new orders",
                country=data.get("country"),
                region=data.get("region"),
                ip=data.get("ip"),
            )
        else:
            self._geoblocked = False
            self.logger.info(
                "Polymarket geoblock: trading allowed",
                country=data.get("country"),
                region=data.get("region"),
            )
        return data

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Flush token cache; py-clob-client does not hold a session to close."""
        self.flush_token_cache()
        self.logger.info("PolymarketClient closed")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

_CLOB_TICK_SIZES = ("0.1", "0.01", "0.001", "0.0001")


def _clob_tick_size(tick: Any) -> str:
    """Map a numeric tick to the string literals py-clob-client expects."""
    try:
        t = float(tick)
    except (TypeError, ValueError):
        return "0.01"
    if t <= 0:
        return "0.01"
    for token in _CLOB_TICK_SIZES:
        if abs(float(token) - t) < 1e-12:
            return token
    return min(_CLOB_TICK_SIZES, key=lambda token: abs(float(token) - t))


def _safe_float(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _book_levels(book: Any, side: str) -> List[Any]:
    """Extract bids/asks from either a V2 dict book or a legacy object book."""
    if book is None:
        return []
    if isinstance(book, dict):
        return book.get(side) or []
    return getattr(book, side, None) or []


def _level_price_size(level: Any) -> tuple:
    """Return (price, size) from a dict level or an object with attributes."""
    if isinstance(level, dict):
        return level.get("price"), level.get("size")
    return getattr(level, "price", None), getattr(level, "size", None)


def _bids_to_levels(book: Any) -> List[List[str]]:
    """Convert CLOB book bids → [[price_str, size_str], ...] highest-bid-first.

    py-clob-client-v2 returns a plain dict with ``bids``/``asks`` lists of
    ``{"price": "...", "size": "..."}``; legacy SDK used objects with attrs.
    """
    out: List[List[str]] = []
    for level in _book_levels(book, "bids"):
        price, size = _level_price_size(level)
        if price is None or size is None:
            continue
        out.append([str(price), str(size)])
    out.sort(key=lambda lv: _safe_float(lv[0]), reverse=True)
    return out


def _asks_to_levels(book: Any) -> List[List[str]]:
    out: List[List[str]] = []
    for level in _book_levels(book, "asks"):
        price, size = _level_price_size(level)
        if price is None or size is None:
            continue
        out.append([str(price), str(size)])
    out.sort(key=lambda lv: _safe_float(lv[0]))
    return out


def _best_bid_dollars(book: Any) -> float:
    bids = _bids_to_levels(book)
    return _safe_float(bids[0][0]) if bids else 0.0


def _best_ask_dollars(book: Any) -> float:
    asks = _asks_to_levels(book)
    return _safe_float(asks[0][0]) if asks else 0.0


def _classify_order_error(exc: Exception) -> Exception:
    """Map a raw SDK exception to one of our typed errors so callers can
    react sensibly. Falls back to `PolymarketAPIError` on no match."""
    msg = str(exc).lower()
    if "insufficient" in msg and ("fund" in msg or "balance" in msg):
        return InsufficientFundsError(str(exc))
    if "allowance" in msg or "approval" in msg or "approve" in msg:
        return AllowanceError(
            f"{exc} — run `python scripts/set_allowances.py` to provision USDC + CTF allowances."
        )
    if "rate" in msg and "limit" in msg:
        return RateLimitError(str(exc))
    if "signature" in msg or "unauthorized" in msg or "401" in msg:
        return PolymarketAuthError(str(exc))
    if "geoblock" in msg or "trading restricted in your region" in msg:
        return GeoblockError(str(exc))
    return PolymarketAPIError(f"order failed: {exc}")


def _normalize_order_response(
    resp: Any,
    *,
    condition_id: str,
    token_id: str,
    side: str,
    action: str,
    count: int,
    client_order_id: str,
) -> Dict[str, Any]:
    """Wrap the SDK response in a legacy-shaped envelope so existing callers
    (`execute.py`, `cli.py close-all`) keep working: `{"order": {...}}`."""
    if isinstance(resp, dict):
        order_id = (
            resp.get("orderID")
            or resp.get("order_id")
            or resp.get("id")
            or client_order_id
        )
        success = resp.get("success", True)
        status = resp.get("status", "filled" if success else "failed")
    else:
        order_id = client_order_id
        success = True
        status = "submitted"

    return {
        "order": {
            "order_id":         order_id,
            "client_order_id":  client_order_id,
            "ticker":           condition_id,
            "condition_id":     condition_id,
            "token_id":         token_id,
            "side":             side.upper(),
            "action":           action.lower(),
            "count":            count,
            "status":           status,
            "raw":              resp,
        },
        "success": success,
    }
