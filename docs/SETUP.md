# Set up Spare Change, step by step

This guide takes you from nothing to your first **dry run**, which is a run that reads your real
purchases and plans a buy but places no order. Plan on about 45 minutes. You don't need to write
any code, but you will type a few commands into a terminal. Copy them exactly as written.

**Nothing in this guide can spend real money.** You start on Alpaca's *paper* account (practice
money), and dry run is on until you deliberately turn it off later.

**Never paste a key or token into a chat, an issue, an email or a file you commit.** The setup
command asks for each one with your typing hidden and sends it straight to GitHub.

| Step | What you do | Time |
|---|---|---|
| [1](#step-1-github-pro-and-your-private-copy) | GitHub Pro and your own private copy | 5 min |
| [2](#step-2-get-the-code-on-your-computer) | Install three tools and download your copy | 10 min |
| [3](#step-3-create-the-accounts) | Create the accounts: Alpaca, Teller, Telegram, Anthropic | 15 min |
| [4](#step-4-run-the-setup-command) | Run `spare setup` | 5 min |
| [5](#step-5-tell-it-about-yourself-over-telegram) | Tell it about yourself over Telegram | 5 min |
| [6](#step-6-your-first-dry-run) | Your first dry run | 5 min |

If something goes wrong, see [Troubleshooting](#troubleshooting) at the end.

---

## Step 1: GitHub Pro and your private copy

Spare Change runs on GitHub Actions in **your own private copy** of this repository. The copy has to
be private because it will hold your purchase history and holdings.

1. **Check your GitHub plan** at <https://github.com/settings/billing>. Each job keeps its own keys
   in a GitHub *environment*, and on the free plan environment secrets only work in public
   repositories. So a private copy needs **GitHub Pro** (about $4 a month) or a Team plan. If your
   plan already says Pro, go on.
2. **Make your private copy.** On this repository's main page, click the green **Use this template**
   button, then **Create a new repository**.
   - Owner: you.
   - Name: anything, for example `spare-change-private`.
   - Choose **Private**. This matters.
   - Click **Create repository**.

   If you don't see **Use this template**, the repository owner hasn't turned on
   *Settings → General → Template repository*. In that case, use the **Fork** alternative in
   [Troubleshooting](#troubleshooting).

From here on, "your copy" means this new private repository, not the public one.

---

## Step 2: Get the code on your computer

You need three free tools. On a Mac, open the **Terminal** app. On Windows, install
[WSL](https://learn.microsoft.com/windows/wsl/install) and use its Ubuntu terminal, because the
commands below are written for Mac and Linux.

1. **Python 3.11 or newer.** Check with `python3 --version`. If it's older or missing, install it from
   <https://www.python.org/downloads/>.
2. **Git 2.42 or newer.** Check with `git --version`. On a Mac, the version Apple installs may be
   older; `brew install git` gets a current one (Homebrew is at <https://brew.sh>).
3. **The GitHub CLI.** Install it from <https://cli.github.com>, then sign in:

   ```bash
   gh auth login
   ```

   Choose **GitHub.com**, then **HTTPS**, then **Login with a web browser**, and follow the prompts.

Now download your copy and step into it. Replace `YOUR-NAME/spare-change-private` with your copy's
name:

```bash
gh repo clone YOUR-NAME/spare-change-private
cd spare-change-private
python3 -m spare demo
```

The demo runs 12 pretend weeks with a fake bank and broker. If you see weekly lines like
`BUY $12.24: VTI $3.38, ...`, everything is installed correctly.

Last, make the folder where your settings will live. It's a separate branch called `state`, so
your data never mixes with the code:

```bash
git worktree add --orphan -b state state
```

This creates a `state/` folder. Every command below runs from the main `spare-change-private`
folder, not from inside `state/`.

> On a Mac, type `python3` wherever this guide says `python3`. If `python3` doesn't work on your
> system but `python` does, use that instead.

---

## Step 3: Create the accounts

Do each part below, then come back. Keep the browser tabs open; you'll paste values into
`spare setup` in step 4.

### 3a. Alpaca (the broker, on practice money)

1. Sign up at <https://alpaca.markets>. You don't need to fund anything for paper trading.
2. In the dashboard, make sure the account switcher at the top says **Paper**, not Live.
3. On the paper dashboard, find **API Keys** and click **Generate New Keys**.
4. You see a **Key ID** (it starts with `PK`) and a **Secret Key**. The secret is shown only once,
   so leave this tab open until step 4. If you lose it, just generate new keys.

Want to check them now? This is read-only and places nothing:

```bash
read -rs APCA_API_KEY_ID; export APCA_API_KEY_ID          # paste the Key ID, press Enter (nothing shows)
read -rs APCA_API_SECRET_KEY; export APCA_API_SECRET_KEY  # paste the Secret Key, press Enter
python3 -m spare alpaca-check
```

### 3b. Teller (reads your bank's purchases)

Teller gives Spare Change **read-only** access to your checking account's transactions. It can't
move money.

1. Sign up at <https://teller.io> and open the **Dashboard**.
2. Copy your **Application ID** (it starts with `app_`).
3. In **Certificates**, create a certificate and download it. You get two files,
   `certificate.pem` and `private_key.pem`. Make a folder called `teller` inside your
   `spare-change-private` folder and move both files into it. That folder is never committed.
4. Link your bank. This opens a page in your browser, on your own computer only:

   ```bash
   python3 -m spare enroll --app-id app_YOUR_ID
   ```

   Pick your bank, sign in the way you normally do, and choose your checking account. When the page
   says it's done, the access token is saved in `teller/token.txt`.
5. Check that purchases are read the right way round:

   ```bash
   export TELLER_ACCESS_TOKEN=$(cat teller/token.txt) TELLER_CERT=teller/certificate.pem TELLER_KEY=teller/private_key.pem
   python3 -m spare bank-check
   ```

   It lists the last 14 days. Your card purchases should say **purchase**, and paychecks or
   refunds should say **money in**. If they're backwards, see
   [Troubleshooting](#troubleshooting).

Teller doesn't support your bank? Spare Change also works with Plaid; choose `plaid` in step 4.

### 3c. Telegram (where you approve changes and get the Sunday update)

1. Install Telegram on your phone and open a chat with **@BotFather** (it has a blue check mark).
2. Send `/newbot`, then pick a display name and a username ending in `bot`.
3. BotFather replies with a **token** like `123456:ABC...`. Keep that chat open.
4. Open a chat with your new bot and send it any message, for example `hi`.
5. Find your chat id:

   ```bash
   read -rs TELEGRAM_BOT_TOKEN; export TELEGRAM_BOT_TOKEN   # paste the BotFather token
   python3 -m spare telegram-chat-id
   ```

   It prints a number. That's your **chat id**. The bot will only ever answer this chat.

### 3d. Anthropic (optional: the AI that suggests your mix)

Without this you get a plain starter mix, and everything else still works. With it, Claude suggests
a mix from your goals each week, and plain code still decides whether that's allowed.

1. Sign up at <https://console.anthropic.com>.
2. Add a little credit under **Billing**. Expect about $0.05 a week, and Spare Change stops calling
   the AI if it would pass $2 in a month.
3. Under **API Keys**, click **Create Key** and copy it. It starts with `sk-ant-`.

---

## Step 4: Run the setup command

```bash
python3 -m spare setup
```

It checks that your copy is private, then asks:

| Question | What to answer |
|---|---|
| Bank link | Press Enter for `teller` (or type `plaid`) |
| Broker | Press Enter for `alpaca` |
| Use Claude? | `y` if you did step 3d, otherwise `n` |
| Set TELLER_ACCESS_TOKEN now? | `y`, then paste the contents of `teller/token.txt` (run `cat teller/token.txt` in another terminal window to see it) |
| Path to your Teller certificate.pem | `teller/certificate.pem` |
| Path to your Teller private_key.pem | `teller/private_key.pem` |
| Alpaca PAPER key id / secret | Paste each from step 3a |
| Telegram bot token / chat id | Paste each from step 3c |
| Anthropic API key | Paste it from step 3d |
| Turn the scheduled jobs on now? | `y` |

When you paste a secret, **nothing appears on screen**. That's on purpose. Paste once and press
Enter.

What it did for you:

- Saved `state/config.toml` with **dry run on** and the Alpaca **paper** account.
- Created three GitHub environments, `money`, `telegram` and `ai`, each usable only from your `main`
  branch, and put each key only in the one its job needs. The AI job never gets your bank or broker
  keys.
- Protected your `main` branch, so the jobs can't change the code that handles your money.
- Turned the jobs on (`SPARE_ENABLED`).

It's safe to run again. It skips anything already set, and `python3 -m spare setup --replace`
asks for every secret again.

---

## Step 5: Tell it about yourself over Telegram

The bot asks three questions in your own words: what you're investing for, how you feel about risk,
and when you'll need the money. Then it asks for the round-up multiplier, your buy day, a monthly
cap, and whether to allow single stocks.

```bash
read -rs TELEGRAM_BOT_TOKEN; export TELEGRAM_BOT_TOKEN   # skip if you did this in step 3c, same terminal
export TELEGRAM_CHAT_ID=YOUR_CHAT_ID
python3 -m spare onboard
```

Answer on your phone. When it's done, the terminal prints
`Saved profile.toml and config.toml in state, and proposal ...`. That's your first suggested mix.
It waits for your **Approve** in Telegram.

Now upload your settings to the `state` branch, so the jobs on GitHub can read them:

```bash
cd state
git add -A
git commit -m "My settings"
git push -u origin state
cd ..
```

---

## Step 6: Your first dry run

1. **Get the proposal into Telegram.** On GitHub, open your copy, click **Actions**, then **bot** in
   the list on the left, then **Run workflow**. Within a minute or two, your bot sends the proposed
   mix with **Approve** and **Skip** buttons. Tap **Approve**, then run **bot** once more so it
   records your tap. (From now on it runs by itself every hour.)
2. **Run the money job once.** Go to **Actions → invest → Run workflow**. It reads your recent
   purchases, adds up the round-ups and, on your buy day, *plans* a buy. Dry run means nothing is
   ordered.
3. **Look at the result** on your computer:

   ```bash
   git -C state pull
   python3 -m spare status
   ```

   You see the pot (your saved spare change), your approved mix and the last run.

You're done. From now on it runs on its own:

- **Weekdays at 15:00 UTC**, `invest` collects round-ups. On your buy day it plans the week's buy.
- **Every hour**, `bot` sends messages and handles your taps. Send `/status`, `/pause` or `/resume`
  to your bot at any time.
- **Sundays at 16:00 UTC**, `brain` reviews your mix and sends a short weekly update.

### Next: practice with paper trading

When you're comfortable with the dry runs, set `dry_run = false` in `state/config.toml`, then commit
and push it (the same four `git` lines as in step 5). Spare Change then places real orders, but in
Alpaca's **paper** account with practice money. Run it like this for two weeks. Real money stays
locked until the two-week paper checklist (`spare doctor --go-live`) has passed.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| `Stopped: ... is public` | You're in the public repository. Make the private copy in step 1 and run setup inside it. |
| `Stopped: ... environment ...` or HTTP 403/404 during setup | Your plan doesn't include environment secrets for private repositories. Upgrade to GitHub Pro (step 1) and run setup again. It picks up where it left off. |
| No **Use this template** button | Instead, create an empty **private** repository on GitHub. Then run: `git clone --bare https://github.com/ianvega23/spare-change.git`, `cd spare-change.git`, `git push --mirror https://github.com/YOUR-NAME/spare-change-private.git`, `cd ..`, then `rm -rf spare-change.git`. |
| `git worktree add --orphan` says unknown option | Your Git is older than 2.42. Update it (step 2). |
| `python3: No module named spare` | Run the command from inside your `spare-change-private` folder, not from `state/` or elsewhere. |
| `bank-check` shows purchases as **money in** | Your bank signs amounts the other way. Add `teller_spending_sign = "positive"` to `state/config.toml`, then run `bank-check` again. |
| Teller asks to re-link later (password change, new MFA) | Run `python3 -m spare enroll --app-id app_YOUR_ID --relink ENROLLMENT_ID`, using the id in `teller/enrollment.json`, then `python3 -m spare setup --replace` to store the new token. |
| `telegram-chat-id` prints "none yet" | Send your bot a message first, then run it again. |
| The bot never sends anything | Check **Actions → bot** for a red run. The most common cause is a wrong chat id; run `python3 -m spare setup --replace` to fix it. |
| `spare doctor` on your computer says secrets are missing | That's expected. Your keys are on GitHub, not on your computer. `doctor` checks the keys of the terminal you run it in. |
| **Actions** shows "workflows disabled" | Click **I understand my workflows, go ahead and enable them**. |
| I want to stop everything | Send `/pause` to your bot, or run `gh variable set SPARE_ENABLED --body false`. |

Still stuck? Open an issue in the public repository, but describe the problem in words. Never
include a key, a token, your chat id or your ledger.
