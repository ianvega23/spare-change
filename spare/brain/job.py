"""The `brain` command: the weekly allocation review (what brain.yml runs on Sundays).

Holds only the Anthropic key. It reads your profile and the ledger from the state branch, asks the
allocator, and writes one of: nothing, a small change to targets.json, or a proposal that waits for
your Approve in Telegram. It never touches the bank or the broker.
"""
from __future__ import annotations

import os
import tomllib
from datetime import date, datetime, timedelta, timezone

from ..core import config as config_mod
from ..core import guard, proposals
from ..core.state import State, StateError

from . import allocator, categorize, summary, telemetry
from .llm import ClaudeClient, LLMClient, LLMError, estimate_microcents

EXIT_OK, EXIT_FAILED = 0, 1
# Worst case for one run: two attempts of about 8K tokens in and 4K out.
RUN_ESTIMATE_TOKENS = (2 * 8_000, 2 * 4_000)


def read_profile(state: State) -> dict | None:
    path = state.root / "profile.toml"
    if not path.exists():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {k: str(data.get(k, "")) for k in ("goals", "risk", "horizon")}


def _brain_state(state: State) -> dict:
    data = state._read_json("brain.json")
    return data if isinstance(data, dict) else {}


def client_from_env(cfg: config_mod.Config, environ=None, *, cheap: bool = False) -> LLMClient:
    environ = os.environ if environ is None else environ
    key = environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise LLMError("ANTHROPIC_API_KEY isn't set")
    if cheap:
        return ClaudeClient(cfg.cheap_model, api_key=key, effort="low")
    return ClaudeClient(cfg.allocator_model, api_key=key)


def _record_calls(state: State, outcome: allocator.Outcome, purpose: str, now: datetime) -> None:
    for i, call in enumerate(outcome.calls):
        last = i == len(outcome.calls) - 1
        telemetry.record(state, call, purpose=purpose, prompt_version=outcome.prompt_version,
                         outcome=outcome.status if last else "retried", now=now)


def _ask(state: State, cfg: config_mod.Config, llm: LLMClient, profile: dict,
         current: dict[str, float], purpose: str, now: datetime) -> allocator.Outcome:
    universe = config_mod.load_universe()
    outcome = allocator.propose(llm, profile=profile, current=current,
                                holdings_cents=state.ledger().holdings_cents(),
                                universe=universe, limits=cfg.limits)
    _record_calls(state, outcome, purpose, now)
    return outcome


def _fits_budget(state: State, cfg: config_mod.Config, model: str, now: datetime) -> tuple[bool, int]:
    return telemetry.within_budget(state, cfg.ai_monthly_budget_cents,
                                   estimate_microcents(model, *RUN_ESTIMATE_TOKENS), now)


def run_brain(state_dir: str, llm: LLMClient | None = None, now: datetime | None = None,
              environ=None, out=print) -> int:
    now = now or datetime.now(timezone.utc)
    state = State(state_dir).ensure()
    try:
        cfg = config_mod.load(state.config_path) if state.config_path.exists() else config_mod.from_dict({})
    except config_mod.ConfigError as e:
        out(f"Config is invalid, so the allocator didn't run: {e}")
        return EXIT_FAILED
    brain = _brain_state(state)

    def finish(status: str, code: int = EXIT_OK, **extra) -> int:
        state._write_json("brain.json", {**brain, **extra, "last_run": now.isoformat(timespec="seconds"),
                                         "last_status": status})
        return code

    if not cfg.enabled or state.paused():
        out("Paused (kill switch). The allocator didn't run.")
        return finish("paused")
    profile = read_profile(state)
    if not profile:
        out("No profile.toml yet: run `python -m spare onboard` first.")
        return finish("no_profile")
    waiting = proposals.pending(state)
    if waiting:
        out(f"Proposal {waiting[0]['id']} is still waiting for your answer, so there's nothing new to ask.")
        return finish("waiting")
    fits, spent = _fits_budget(state, cfg, cfg.allocator_model, now)
    if not fits:
        out(f"This month's AI budget is used up ({telemetry.usd(spent)} of "
            f"${cfg.ai_monthly_budget_cents / 100:.2f}). Targets stay as they are; buying continues.")
        if brain.get("budget_alert") != now.strftime("%Y-%m"):
            state.queue_message("alert", "The monthly AI budget is used up, so the allocation review is "
                                "skipped until next month. Your targets stay and buying continues.")
        return finish("over_budget", budget_alert=now.strftime("%Y-%m"))

    try:
        current = state.targets()
        llm = llm or client_from_env(cfg, environ)
        outcome = _ask(state, cfg, llm, profile, current, "allocation", now)
    except (LLMError, StateError) as e:
        out(f"Allocator failed: {e}. Targets stay as they are.")
        return finish("error", EXIT_FAILED)

    cost = telemetry.usd(sum(c.cost_microcents for c in outcome.calls))
    out(f"Allocator ({outcome.model or 'no answer'}, {outcome.prompt_version}): {outcome.status}, "
        f"{len(outcome.calls)} call(s), {cost}")

    if outcome.status == "failed":
        for e in outcome.errors:
            out(f"  rejected: {e}")
        state.queue_message("alert", "The allocator's suggestion didn't pass the guardrails, so your "
                            "allocation stays as it is. (" + "; ".join(outcome.errors)[:300] + ")")
        return finish("failed")
    if outcome.status == "none":
        out("  No change: the current mix still fits.")
        return finish("none")
    source = f"allocator:{outcome.prompt_version}:{outcome.model}"
    if outcome.status == "small":
        last = brain.get("last_auto_change")
        if not guard.auto_change_allowed(datetime.fromisoformat(last) if last else None, now):
            out("  Small change found, but one was already applied this week. Skipping.")
            return finish("small_skipped")
        state._write_json("targets.json", {"targets": outcome.targets, "source": source,
                                           "changed_at": now.isoformat(timespec="seconds")})
        proposals.log_decision(state, "targets_changed", now, note=source, targets=outcome.targets)
        state.queue_message("targets", "Small allocation change applied (no approval needed): "
                            + ", ".join(f"{s} {w:.0%}" for s, w in sorted(outcome.targets.items(), key=lambda kv: -kv[1]))
                            + (f"\n{outcome.summary}" if outcome.summary else ""))
        out("  Applied to targets.json.")
        return finish("small", last_auto_change=now.isoformat(timespec="seconds"))
    proposal = proposals.create(state, outcome.targets, summary=outcome.summary, reasons=outcome.reasons,
                                source=source, now=now)
    out(f"  Big change: proposal {proposal['id']} goes to Telegram for your Approve.")
    return finish("big", last_proposal=proposal["id"])


def first_proposal(state_dir: str, profile: dict, llm: LLMClient | None = None,
                   now: datetime | None = None, environ=None) -> dict:
    """For onboarding: the allocator's first mix (always a big change). Raises LLMError on failure,
    so onboarding can fall back to the plain starter mix."""
    now = now or datetime.now(timezone.utc)
    state = State(state_dir).ensure()
    cfg = config_mod.load(state.config_path) if state.config_path.exists() else config_mod.from_dict({})
    if not _fits_budget(state, cfg, cfg.allocator_model, now)[0]:
        raise LLMError("the monthly AI budget is used up")
    llm = llm or client_from_env(cfg, environ)
    outcome = _ask(state, cfg, llm, profile, {}, "first_allocation", now)
    if outcome.status != "big":
        raise LLMError("; ".join(outcome.errors) or "no usable allocation")
    return {"targets": outcome.targets, "reasons": outcome.reasons, "summary": outcome.summary,
            "source": f"allocator:{outcome.prompt_version}:{outcome.model}"}


WEEKLY_ESTIMATE_TOKENS = (2 * 4_000, 2 * 2_000)


def run_weekly(state_dir: str, llm: LLMClient | None = None, now: datetime | None = None,
               environ=None, out=print) -> int:
    """The Sunday update. Always sends: with no key, no budget or a bad draft it uses the plain template."""
    now = now or datetime.now(timezone.utc)
    state = State(state_dir).ensure()
    try:
        cfg = config_mod.load(state.config_path) if state.config_path.exists() else config_mod.from_dict({})
    except config_mod.ConfigError as e:
        out(f"Config is invalid, so no weekly update: {e}")
        return EXIT_FAILED
    today: date = now.date()
    if llm is None:
        try:
            llm = client_from_env(cfg, environ, cheap=True)
        except LLMError:
            llm = None  # numbers-only template, no categories beyond the rules
    if llm is not None and not telemetry.within_budget(
            state, cfg.ai_monthly_budget_cents,
            estimate_microcents(llm.model, *WEEKLY_ESTIMATE_TOKENS), now)[0]:
        out("AI budget used up this month: the update uses the plain template.")
        llm = None

    start = today - timedelta(days=today.weekday())
    merchants = [e.get("merchant", "") for e in state.ledger().of_type("roundup")
                 if start.isoformat() <= str(e.get("date")) <= today.isoformat()]
    cats, cat_calls = categorize.categorize(state, merchants, llm, use_ai=cfg.send_merchants_to_ai)
    for call in cat_calls:
        telemetry.record(state, call, purpose="categorize", prompt_version=categorize.PROMPT_VERSION, now=now)

    facts = summary.facts(state, today, cats)
    base = summary.template(facts)
    message, calls, how = summary.draft(base, llm)
    for call in calls:
        telemetry.record(state, call, purpose="weekly_summary", prompt_version=summary.PROMPT_VERSION,
                         outcome=how, now=now)
    state.queue_message("weekly", message)
    out(message)
    out(f"(weekly update queued, {how} wording)")
    return EXIT_OK
