# Set up Spare Change, step by step

This guide takes you from nothing to your first **dry run**, which is a run that reads your real
purchases and plans a buy but places no order. Plan on about 45 minutes. You don't need to write
any code, but you will type a few commands into a terminal. Copy them exactly as written.

It works on **Windows, Mac and Linux**. Where the steps differ, there's a section for each. The
commands are the same everywhere, with one difference: on **Windows** you type `python`, and on
**Mac or Linux** you type `python3`. This guide writes `python`, so on a Mac or Linux add the 3.

**Nothing in this guide can spend real money.** You start on Alpaca's *paper* account (practice
money), and dry run is on until you deliberately turn it off later.

**Never paste a key or token into a chat, an issue, an email or a file you commit.** Whenever a
command needs a key, it asks for it with your typing hidden. Paste it (Ctrl+V on Windows, Cmd+V on a
Mac, or right-click) and press Enter. Nothing appears on screen while you paste, and that's on purpose.

| Step | What you do | Time |
|---|---|---|
| [1](#step-1-github-pro-and-your-private-copy) | GitHub Pro and your own private copy | 5 min |
| [2](#step-2-get-the-code-on-your-computer) | Install three tools and download your copy | 10 min |
| [3](#step-3-create-the-accounts) | Create the accounts: Alpaca, Plaid, Telegram, Anthropic | 15 min |
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

You need three free tools: **Python**, **Git** and the **GitHub CLI**.

### On Windows

1. Open **PowerShell**: press the Windows key, type `PowerShell`, and press Enter.
2. Install the three tools with `winget`, which is built into Windows 10 and 11:

   ```powershell
   winget install -e --id Python.Python.3.13
   winget install -e --id Git.Git
   winget install -e --id GitHub.cli
   ```

   Say yes to any prompts. If `winget` isn't found, install each one from its website instead:
   <https://www.python.org/downloads/> (tick **Add python.exe to PATH** on the first screen),
   <https://git-scm.com/download/win> and <https://cli.github.com>.
3. **Close PowerShell and open it again**, so it finds the new tools. Check them:

   ```powershell
   python --version
   git --version
   gh --version
   ```

   You need Python 3.11 or newer and Git 2.42 or newer. If `python` opens the Microsoft Store
   instead, see [Troubleshooting](#troubleshooting).

### On a Mac

1. Open the **Terminal** app (press Cmd+Space, type `Terminal`, press Enter).
2. Install [Homebrew](https://brew.sh) if you don't have it, then:

   ```bash
   brew install python git gh
   ```

   Or install Python from <https://www.python.org/downloads/>, Git with `xcode-select --install`,
   and the GitHub CLI from <https://cli.github.com>.
3. Check them with `python3 --version`, `git --version` and `gh --version`. You need Python 3.11 or
   newer and Git 2.42 or newer.

### On Linux

Install `python3` (3.11 or newer), `git` (2.42 or newer) and `gh` with your package manager. For
`gh`, see <https://github.com/cli/cli#installation>.

### Everyone: sign in and download your copy

Sign in to GitHub from the terminal:

```bash
gh auth login
```

Choose **GitHub.com**, then **HTTPS**, then **Login with a web browser**, and follow the prompts.

Now download your copy and step into it. Replace `YOUR-NAME/spare-change-private` with your copy's
name:

```bash
gh repo clone YOUR-NAME/spare-change-private
cd spare-change-private
python -m spare demo
```

The demo runs 12 pretend weeks with a fake bank and broker. If you see weekly lines like
`BUY $12.24: VTI $3.38, ...`, everything is installed correctly.

Last, make the folder where your settings will live. It's a separate branch called `state`, so
your data never mixes with the code:

```bash
git worktree add --orphan -b state state
```

This creates a `state` folder. Every command below runs from the main `spare-change-private`
folder, not from inside `state`.

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

Want to check them now? This is read-only and places nothing. It asks for both keys:

```bash
python -m spare alpaca-check
```

### 3b. Plaid (reads your bank's purchases)

Plaid gives Spare Change **read-only** access to your checking account's transactions. It can't
move money. Plaid's free **Trial plan** is for personal use: real bank data, up to 10 bank
connections, and it includes Chase and most other US banks.

1. Go to <https://dashboard.plaid.com/signup> and choose **Personal use**.
2. Verify your email and finish the identity check. Most people are approved right away; if Plaid
   needs to look closer, it emails you within 2 to 3 business days.
3. If the Trial plan isn't on yet, request it at <https://dashboard.plaid.com/trial-plan>.
4. In the dashboard, find your API keys (usually under **Developers → Keys**). You need the
   **client_id** and the **Production** secret. The Trial plan's real connections live in
   Production, not Sandbox.
5. Link your bank. This opens a page in your browser, on your own computer only. It asks for the
   client_id and secret first:

   ```bash
   python -m spare enroll
   ```

   Click **Open Plaid**, pick your bank, sign in the way you normally do, and choose your checking
   account. When the page says it's done, the access token is saved in `plaid/token.txt`. That
   folder is never committed.
6. Check that your purchases come through:

   ```bash
   python -m spare bank-check
   ```

   It uses the token in the `plaid` folder by itself and asks for the client_id and secret again.
   It lists the last 14 days. Your card purchases should say **purchase**, and paychecks or refunds
   should say **money in**.

> **Why not Teller?** Earlier versions of this guide used Teller. Teller no longer offers a public
> sign-up page, so new users can't get an account. If you already have a Teller developer account,
> it still works: run `python -m spare enroll --app-id app_YOUR_ID` with your certificate and key in
> a `teller` folder, and choose `teller` in step 4.

### 3c. Telegram (where you approve changes and get the Sunday update)

1. Install Telegram on your phone and open a chat with **@BotFather** (it has a blue check mark).
2. Send `/newbot`, then pick a display name and a username ending in `bot`.
3. BotFather replies with a **token** like `123456:ABC...`. Keep that chat open.
4. Open a chat with your new bot and send it any message, for example `hi`.
5. Find your chat id. This asks for the BotFather token:

   ```bash
   python -m spare telegram-chat-id
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
python -m spare setup
```

It checks that your copy is private, then asks:

| Question | What to answer |
|---|---|
| Bank link | Press Enter for `plaid` |
| Broker | Press Enter for `alpaca` |
| Use Claude? | `y` if you did step 3d, otherwise `n` |
| Plaid client id / Production secret | Paste each from step 3b |
| Set PLAID_ACCESS_TOKEN now? / Use the token saved in plaid/token.txt? | `y`, then Enter |
| Alpaca PAPER key id / secret | Paste each from step 3a |
| Telegram bot token / chat id | Paste each from step 3c |
| Anthropic API key | Paste it from step 3d |
| Turn the scheduled jobs on now? | `y` |

When you paste a secret, **nothing appears on screen**. That's on purpose. Paste once and press
Enter. If it asks for a file path, you can drag the file into the window or paste a copied path,
quotes and all.

What it did for you:

- Saved `state/config.toml` with **dry run on** and the Alpaca **paper** account.
- Created three GitHub environments, `money`, `telegram` and `ai`, each usable only from your `main`
  branch, and put each key only in the one its job needs. The AI job never gets your bank or broker
  keys.
- Protected your `main` branch, so the jobs can't change the code that handles your money.
- Turned the jobs on (`SPARE_ENABLED`).

It's safe to run again. It skips anything already set, and `python -m spare setup --replace`
asks for every secret again.

---

## Step 5: Tell it about yourself over Telegram

The bot asks three questions in your own words: what you're investing for, how you feel about risk,
and when you'll need the money. Then it asks for the round-up multiplier, your buy day, a monthly
cap, and whether to allow single stocks.

```bash
python -m spare onboard
```

It asks for the bot token and your chat id first.

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
   python -m spare status
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
| No **Use this template** button | Instead, create an empty **private** repository on GitHub. Then run: `git clone --bare https://github.com/ianvega23/spare-change.git`, `cd spare-change.git`, `git push --mirror https://github.com/YOUR-NAME/spare-change-private.git`, `cd ..`, and delete the `spare-change.git` folder. |
| Windows: `python` opens the Microsoft Store, or says it isn't found | Use `py` instead of `python` (for example `py -m spare demo`). Or open **Settings → Apps → Advanced app settings → App execution aliases**, turn off the two **python** entries, and reopen PowerShell. |
| Windows: a tool says "not recognized" right after installing | Close PowerShell and open a new one, so it picks up the new tools. |
| Windows: pasting into a hidden prompt does nothing | Right-click in the window to paste, then press Enter. |
| `git worktree add --orphan` says unknown option | Your Git is older than 2.42. Update it (step 2; on Windows, `winget upgrade Git.Git`). |
| `git worktree add` says `fatal: 'state' already exists` | You already made the `state` folder earlier, so there's nothing to do. Check with `git worktree list`; if it shows a `state` line, carry on. |
| Your private copy is missing a newer fix from the public repo | Copies made with "Use this template" don't update by themselves. Once: `git remote add upstream https://github.com/ianvega23/spare-change.git`. Then each time: `git fetch upstream`, `git checkout upstream/main -- .`, `git commit -m "Update from the public repo"`, `git push`. Your `state` branch isn't touched. |
| `No module named spare` | Run the command from inside your `spare-change-private` folder, not from `state/` or elsewhere. |
| Plaid says INVALID_API_KEYS | Use the **Production** secret, not the Sandbox one, and check the client_id. |
| The bank needs re-linking later (password change, new MFA) | Your bot tells you. Run `python -m spare enroll --relink`. It keeps the same token, so nothing else changes. |
| Teller users: `bank-check` shows purchases as **money in** | Add `teller_spending_sign = "positive"` to `state/config.toml`, then run `bank-check` again. |
| `telegram-chat-id` prints "none yet" | Send your bot a message first, then run it again. |
| The bot never sends anything | Check **Actions → bot** for a red run. The most common cause is a wrong chat id; run `python -m spare setup --replace` to fix it. |
| `spare doctor` on your computer says secrets are missing | That's expected. Your keys are on GitHub, not on your computer. `doctor` checks the keys of the terminal you run it in. |
| **Actions** shows "workflows disabled" | Click **I understand my workflows, go ahead and enable them**. |
| I want to stop everything | Send `/pause` to your bot, or run `gh variable set SPARE_ENABLED --body false`. |

Still stuck? Open an issue in the public repository, but describe the problem in words. Never
include a key, a token, your chat id or your ledger.
