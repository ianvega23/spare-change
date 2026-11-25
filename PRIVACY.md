# Privacy

Spare Change runs in **your own** private copy of this repository, with your own accounts. There is no
Spare Change server, and the project's authors never see your data.

## Where your data lives

- **The `state` branch of your private repo**: the ledger (round-ups, purchase amounts, merchant names
  with card and reference numbers removed, buys), your target allocation, proposals and decisions,
  your three profile sentences, AI call logs and the last market values. Keep the repo private.
- **GitHub Actions secrets**: your Teller, Alpaca, Telegram and Anthropic keys, each in its own
  environment, so no job holds more than one provider's key.

## What goes to each service

| Service | Gets | Never gets |
|---|---|---|
| Teller (or Plaid) | your bank login, through their own enrollment page | anything from Spare Change |
| Alpaca | buy orders and account reads | your bank data, profile or merchants |
| Telegram | the messages you'd expect: weekly update, buys, proposals, alerts. Totals, fund names and spending categories only | account numbers, full transaction lists |
| Anthropic (Claude) | **allocation:** your three profile sentences, current weights, the fund list and limits. **Weekly update:** merchant names (for categories) and the week's totals | account numbers, card numbers, balances, prices |

Anthropic's [commercial terms](https://www.anthropic.com/legal/commercial-terms) apply to what the
Anthropic API receives.

## Turning things off

- `send_merchants_to_ai = false` in `config.toml`: merchant names never leave GitHub. Categories then
  come only from simple built-in rules, and everything else shows as "other".
- Leave out `ANTHROPIC_API_KEY`: no AI at all. Onboarding uses a plain starter mix, and the weekly
  update uses a plain template.
- `/pause` in Telegram stops collecting round-ups and buying.

## Deleting your data

Delete the `state` branch (or the whole private repo), revoke the Teller enrollment in your Teller
dashboard, and delete the API keys at Alpaca, Anthropic and BotFather.
