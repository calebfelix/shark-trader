# Deploying shark-trader on an Ubuntu VPS

This sets up the **dashboard** (a web page for trades, balance, settings and
start/stop) and the **trading bot** on an Ubuntu server, with the dashboard
reachable only from your own computer over an SSH tunnel.

- Supported: **Ubuntu 22.04 or 24.04** (Python 3.10+ is required; 20.04 is too old).
- The server should have a **fixed public IPv4**, because Shark API keys are
  IP-whitelisted.
- The bot starts in **dry run** (simulated, no real orders). Switching to real
  money is a separate, deliberate step - see [Going live](#going-live).

> Honest status: the code is verified on Windows. The setup script and these
> steps were written for Ubuntu but have not been run on a real server yet. If a
> step fails, fix this document.

## 1. What you need first

1. An Ubuntu 22.04/24.04 VPS and a user with `sudo` (not root).
2. SSH access, ideally with a key.
3. A Shark Exchange API key + secret.
4. This repo on GitHub: `https://github.com/calebfelix/shark-trader`.

## 2. Install (one command)

SSH into the server, then:

```bash
git clone https://github.com/calebfelix/shark-trader.git
cd shark-trader
bash deploy/setup_ubuntu.sh
```

The script (safe to re-run) will:

1. install `python3`, `python3-venv`, `git`, `curl` and check Python is 3.10+;
2. turn on NTP time sync (signed requests fail if the clock drifts more than 5 s);
3. create `venv/` and install `requirements.txt`;
4. run the test suite (must pass before it continues);
5. install and start the `shark-dashboard` systemd service (starts on boot);
6. install the optional `shark-bot` service **without** starting it;
7. print the server's public IP and your next steps.

It never touches API keys, never enables real-account mode, and never changes
the firewall or SSH.

## 3. Whitelist the server's IP on your Shark API key

Use the IPv4 the script printed (or run `curl -4 ifconfig.me` on the server).
In your Shark Exchange API settings, add that address to the key's allowed IPs,
save, and confirm with OTP if asked. The IP must be the **server's**, not your
home IP. Until this is done, balance and live calls fail with
`error 4009 "IP address not whitelisted"`.

## 4. Open the dashboard

The dashboard listens on `127.0.0.1:8765` **on the server only** and has **no
login**, so you reach it through an SSH tunnel. On **your own computer**
(Windows PowerShell/cmd, macOS or Linux all have `ssh`):

```bash
ssh -L 8765:127.0.0.1:8765 YOUR_USER@YOUR_VPS_IP
```

Leave that window open, then browse to **http://127.0.0.1:8765**.

Never open port 8765 in a firewall or bind the dashboard to `0.0.0.0`: anyone
who could reach it could start a live bot and read or replace your API keys.

## 5. First run (dry run)

1. Open **Settings** and paste your API key and secret. They are saved on the
   server in `credentials.json` (permission 600, gitignored, never pushed).
2. Check the top of the main page shows **DRY RUN**.
3. Review `config.json` on the server (see [Configuration](#configuration)).
4. Pick **psar_trend** and press **Start**. Watch "Bot output" and the status
   line for a while. `psar_trend` trades roughly once every 15-20 hours, so
   no trades for hours is normal.

## Configuration

`config.json` ships with the SOL setup:

| Setting | Value | Notes |
|---|---|---|
| `symbol` | `SOLUSDT` | SOL's exchange minimum order is small enough for a small account. |
| `account_capital` | `5000.0` | **Set this to your real balance** - position sizing is a % of it. |
| `risk_per_trade_pct` | `1.0` | Risk per trade as % of capital. |
| `leverage.value` | `20` | Sizing is leverage-aware; see `CLAUDE.md`. |
| `leverage.allow_min_notional_override` | `true` | Only matters if the risk-sized order is below the exchange minimum; it then sizes up and real risk becomes higher than configured. |
| `dry_run` | `true` | Leave it. Real/dry is switched on the Settings page, not here. |

Edit it on the server (`nano config.json`) and **restart the bot** (Stop, then
Start in the dashboard) - config is read at startup.

## Operations

| Task | Command (on the server, in the repo folder) |
|---|---|
| Dashboard status | `sudo systemctl status shark-dashboard` |
| Dashboard logs | `journalctl -u shark-dashboard -f` |
| Bot output (when started from the dashboard) | `tail -f dashboard_bot.log` |
| Restart dashboard | `sudo systemctl restart shark-dashboard` (a running bot keeps running) |
| Is a bot running? | `pgrep -af "main.py run"` |
| Trade history | `shark_bot_trade_log_<strategy>.csv` |

**Update to the latest code**

```bash
cd ~/shark-trader
git pull
venv/bin/pip install -r requirements.txt
sudo systemctl restart shark-dashboard
```

`config.json` is tracked in git. If you edited it on the server and `git pull`
complains: `git stash`, `git pull`, `git stash pop`, then re-check the values.

**Back up** the files that live only on the server: `credentials.json`,
`local_settings.json`, `shark_bot_trade_log_*.csv`. `dashboard_bot.log` grows
over time; with the bot stopped, `truncate -s 0 dashboard_bot.log` resets it.

Times in the trade log and "today" on the dashboard use the server's timezone.
Optionally: `sudo timedatectl set-timezone Asia/Kolkata`.

## Optional: restart the bot automatically

If you want the bot to come back after a crash, a reboot, or an exchange
maintenance window at startup (the bot exits if the exchange is down when it
starts), use the bot service **instead of** starting from the dashboard:

```bash
sudo systemctl enable --now shark-bot      # runs psar_trend, restarts after 60 s on failure
sudo systemctl status shark-bot
journalctl -u shark-bot -f
sudo systemctl stop shark-bot              # stop it with systemctl, not the dashboard
```

While `shark-bot` runs, the dashboard's **Start** refuses (one bot at a time),
and its **Stop** would just be undone by systemd restarting the bot. To change
strategy, edit `ExecStart` in `/etc/systemd/system/shark-bot.service`, then
`sudo systemctl daemon-reload && sudo systemctl restart shark-bot`.

## Going live

Real-account mode places **real orders with real money**. Before you switch:

- Run dry run for several days and read the trade log.
- The bot's real-order path has **never been exercised end to end**. Only a
  single resting limit order was ever placed and cancelled by
  `verify_order_endpoint.py`; a market order has never been tested.
- `set_leverage` has previously failed on this account with `error 2314
  "Cross margin is not enabled"` - a live run calls it at startup and may stop
  there. Set leverage in the Shark app if so.
- Start with a very small `account_capital`.
- The dashboard's **Stop does not close a real open position** - check the
  exchange yourself after stopping a live bot.
- Switching needs saved keys and typing `REAL MONEY` on the Settings page, and
  is refused while a bot is running. Each live Start asks for confirmation.
  Switching back to dry run is one click.

In real-account mode the dashboard also shows an **Exchange wallet** card (real
balance, read-only). "Bot equity (calculated)" remains the bot's own estimate
and can differ from the wallet.

## Security checklist

- SSH key login; disable password auth (`PasswordAuthentication no` in
  `/etc/ssh/sshd_config`, then `sudo systemctl restart ssh`).
- Firewall allowing SSH only. **Allow SSH first or you will lock yourself out:**
  ```bash
  sudo ufw allow OpenSSH
  sudo ufw enable
  sudo ufw status
  ```
  (If SSH runs on a non-standard port, allow that port instead.)
- Dashboard reachable only through the SSH tunnel (never exposed).
- API key IP-whitelisted to the server, with only the permissions you need.
- Keep `credentials.json` out of git (it is gitignored) and out of backups you
  share.
- Optional: `sudo apt install unattended-upgrades` for automatic security updates.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Setup script: "Python 3.10+ is required" | Use Ubuntu 22.04 or 24.04. |
| Setup script: tests fail | Don't continue; paste the failure output. |
| `4009 IP address not whitelisted` | Whitelist the **server's** IPv4 on the key; save/confirm; wait a few minutes. |
| `4008 Timestamp older than 5 seconds` | Clock drift. `sudo timedatectl set-ntp true`; check `timedatectl` says synchronized. |
| `503 ... no healthy upstream` | Shark maintenance/outage. Wait and restart (or use the bot service, which retries). |
| `400 Invalid host header` opening the dashboard | Use exactly `http://127.0.0.1:8765` or `http://localhost:8765` through the tunnel; other hostnames are blocked on purpose. |
| Browser can't connect | Is the SSH tunnel window still open? Is the service up (`systemctl status shark-dashboard`)? |
| Start says "A bot is already running" | `pgrep -af "main.py run"`; maybe the `shark-bot` service is running. |
| Wallet card says "No API credentials saved" | Save them on Settings (and the mode must be real account to show the card). |

## Uninstall

```bash
sudo systemctl disable --now shark-dashboard shark-bot
sudo rm /etc/systemd/system/shark-dashboard.service /etc/systemd/system/shark-bot.service
sudo systemctl daemon-reload
rm -rf ~/shark-trader        # also deletes credentials.json and trade logs
```
