/**
 * PM2 process file for Polymarket trading bot + Streamlit dashboard.
 *
 * Usage:
 *   pm2 start ecosystem.config.cjs
 *   pm2 save
 */
module.exports = {
  apps: [
    {
      name: "polymarket-bot",
      cwd: "/www/polymarket-trading-bot",
      script: "/www/polymarket-trading-bot/.venv/bin/python",
      args: "cli.py run --conservative --live --loop --interval 240 --log-level INFO",
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "10s",
      restart_delay: 5000,
      kill_timeout: 15000,
      max_memory_restart: "800M",
      env: {
        PYTHONUNBUFFERED: "1",
        // Wallet/auth: load from .env (POLYMARKET_SIGNATURE_TYPE=3,
        // POLYMARKET_FUNDER=proxyWallet). Do not hardcode keys here.
      },
      out_file: "/www/polymarket-trading-bot/logs/pm2-bot-out.log",
      error_file: "/www/polymarket-trading-bot/logs/pm2-bot-error.log",
      merge_logs: true,
      time: true,
    },
    {
      name: "polymarket-dashboard",
      cwd: "/www/polymarket-trading-bot",
      script: "/www/polymarket-trading-bot/.venv/bin/python",
      args:
        "-m streamlit run scripts/trading_dashboard.py " +
        "--server.address 0.0.0.0 --server.port 8501 " +
        "--server.headless true --browser.gatherUsageStats false",
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "10s",
      restart_delay: 3000,
      max_memory_restart: "500M",
      env: {
        PYTHONUNBUFFERED: "1",
      },
      out_file: "/www/polymarket-trading-bot/logs/pm2-dashboard-out.log",
      error_file: "/www/polymarket-trading-bot/logs/pm2-dashboard-error.log",
      merge_logs: true,
      time: true,
    },
    {
      name: "polymarket-lottery",
      cwd: "/www/polymarket-trading-bot",
      script: "/www/polymarket-trading-bot/.venv/bin/python",
      // Entertainment longshots: week $10, ≤5/day, YES≤15¢, min CLOB ticket.
      // Isolated from EU5 fair-value; expect negative EV.
      args: "cli.py run --lottery --live --loop --interval 300 --log-level INFO",
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "10s",
      restart_delay: 5000,
      kill_timeout: 15000,
      max_memory_restart: "400M",
      env: {
        PYTHONUNBUFFERED: "1",
      },
      out_file: "/www/polymarket-trading-bot/logs/pm2-lottery-out.log",
      error_file: "/www/polymarket-trading-bot/logs/pm2-lottery-error.log",
      merge_logs: true,
      time: true,
    },
    {
      name: "polymarket-eu5",
      cwd: "/www/polymarket-trading-bot",
      script: "/www/polymarket-trading-bot/.venv/bin/python",
      // EU5 fair-value: Pinnacle ref vs PM top-5 leagues. Needs THE_ODDS_API_KEY;
      // idles safely (no orders) until the key is set.
      args: "cli.py run --eu5 --live --loop --interval 300 --log-level INFO",
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "10s",
      restart_delay: 5000,
      kill_timeout: 15000,
      max_memory_restart: "500M",
      env: {
        PYTHONUNBUFFERED: "1",
      },
      out_file: "/www/polymarket-trading-bot/logs/pm2-eu5-out.log",
      error_file: "/www/polymarket-trading-bot/logs/pm2-eu5-error.log",
      merge_logs: true,
      time: true,
    },
    {
      name: "polymarket-btc15m",
      cwd: "/www/polymarket-trading-bot",
      script: "/www/polymarket-trading-bot/.venv/bin/python",
      // Dry-run: Completeness almost never fills at ~$100 NAV (P1); save API.
      args: "cli.py run --btc-15m-completeness --loop --interval 60 --log-level INFO",
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "10s",
      restart_delay: 5000,
      kill_timeout: 15000,
      max_memory_restart: "500M",
      env: {
        PYTHONUNBUFFERED: "1",
      },
      out_file: "/www/polymarket-trading-bot/logs/pm2-btc15m-out.log",
      error_file: "/www/polymarket-trading-bot/logs/pm2-btc15m-error.log",
      merge_logs: true,
      time: true,
    },
    {
      name: "polymarket-ops-alerts",
      cwd: "/www/polymarket-trading-bot",
      script: "/www/polymarket-trading-bot/.venv/bin/python",
      args: "scripts/ops_alerts.py --loop --interval 120",
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "5s",
      restart_delay: 5000,
      env: {
        PYTHONUNBUFFERED: "1",
      },
      out_file: "/www/polymarket-trading-bot/logs/pm2-ops-alerts-out.log",
      error_file: "/www/polymarket-trading-bot/logs/pm2-ops-alerts-error.log",
      merge_logs: true,
      time: true,
    },
    {
      name: "polymarket-sports-rn1",
      cwd: "/www/polymarket-trading-bot",
      script: "/www/polymarket-trading-bot/.venv/bin/python",
      // P4 RN1 copy: soccer+ATP/WTA title, 30s poll, ≤$1/order, no Odds API.
      args: "cli.py run --sports-rn1 --live --loop --interval 30 --log-level INFO",
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "10s",
      restart_delay: 5000,
      kill_timeout: 15000,
      max_memory_restart: "500M",
      env: {
        PYTHONUNBUFFERED: "1",
      },
      out_file: "/www/polymarket-trading-bot/logs/pm2-sports-rn1-out.log",
      error_file: "/www/polymarket-trading-bot/logs/pm2-sports-rn1-error.log",
      merge_logs: true,
      time: true,
    },
    {
      name: "polymarket-csl-explore",
      cwd: "/www/polymarket-trading-bot",
      script: "/www/polymarket-trading-bot/.venv/bin/python",
      // CSL explore weekend: min ~$1.01/match, week budget $8.08, deduped.
      args: "cli.py run --csl-explore --live --loop --interval 120 --log-level INFO",
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "10s",
      restart_delay: 5000,
      kill_timeout: 15000,
      max_memory_restart: "400M",
      env: {
        PYTHONUNBUFFERED: "1",
      },
      out_file: "/www/polymarket-trading-bot/logs/pm2-csl-explore-out.log",
      error_file: "/www/polymarket-trading-bot/logs/pm2-csl-explore-error.log",
      merge_logs: true,
      time: true,
    },
  ],
};
