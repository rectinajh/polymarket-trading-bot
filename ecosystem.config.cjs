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
      args: "cli.py run --conservative --live --loop --interval 300 --log-level INFO",
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
  ],
};
