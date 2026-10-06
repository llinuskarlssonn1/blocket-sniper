# blocket-sniper
This project blocket-sniper is a project which me and my friend are doing where we are builduing an AI agent which look for deals on the swedish marketplace "Blocket". The first step is to have a script running which gathers information from the blocket website and does the heavy sorting based on item, location etc. After that weedout the ads for the items are given to an agent which looks at the images, price, compares with other similar items and determines if the ad is a deal or not which we could make money off by selling it after buying it.
## Collector

Python 3.10 or newer; no third-party dependencies required.

On macOS, if Python has no installed root certificates, the collector loads the
system CA bundle at `/etc/ssl/cert.pem`. Certificate and hostname verification
remain enabled. Explicit `SSL_CERT_FILE` / `SSL_CERT_DIR` settings are respected.

The initial target is **iPhones in Göteborg municipality**, including its districts
(e.g. Angered, Torslanda, Hovås and Västra Frölunda), with no price limit. Both
private and business ads can qualify. This uses Blocket's advertised location;
the seller's description can specify a different pickup address.

Only listings at most **24 hours old** qualify, based on the timestamp supplied
by Blocket (not when this script first discovered the ad). Older listings are
excluded before fetching their details. Missing, invalid or future timestamps
are also excluded. Every export removes saved candidates that have crossed the
24-hour limit, even if the latest search failed; SQLite retains their history.
Restart a running collector to apply this change.

Blocket's [robots.txt](https://www.blocket.se/robots.txt) states that automated
crawling requires prior written permission. Obtain that permission before running
scheduled collection. The collector does not bypass login or access challenges.

From this directory, run one collection cycle:

```sh
python3 blocket_sniper.py --once
```

Or run continuously, with a cycle starting approximately every 30 seconds:

```sh
python3 blocket_sniper.py
```

Stop with Ctrl+C. Keep the computer awake and connected to the internet. Run only
one collector instance per data directory. No AI account or API key is needed.
The collector never contacts sellers or makes purchases.

### Saved results

- `data/ads.sqlite3`: persistent ad records, first/last seen times, description check
  times, rejection reasons and a history of observed changes, including prices.
- `data/candidates.json`: an atomically replaced JSON array for a future agent.
  Includes ad ID, title (`heading`), URL (`canonical_url`), SEK price, location,
  coordinates when supplied, full description, condition, category, image URLs,
  source timestamp and collection timestamps. Images are linked, not downloaded.

Ads are deduplicated by ID across restarts. Candidates have passed the text filter;
their physical condition and resale value remain unverified. Rejected ads remain
in SQLite for auditing. Missing or unparseable descriptions stay pending and are
excluded from the export. A changed search summary invalidates the previous
assessment until its description is checked again.

The filters require the configured phrase in the title or full description and reject
obvious accessories (when searching the mobile-phone category), wanted ads, and common
Swedish/English descriptions of damage, faults or locks. They account for simple
negations such as “inga sprickor”. These are heuristic rules: unusual wording,
complex negations, undisclosed faults, or damage visible only in photos can escape
them; some usable phones may also be rejected. Review rejected records when tuning
`DAMAGE`, `NEGATION` and `ACCESSORY` in `blocket_sniper.py`.

### Configuration and coverage

Edit `config.json`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `product_category` | `2.93.3217.39` | Blocket category ID; empty string searches all categories |
| `query` | `iphone` | Search text sent to Blocket |
| `must_have` | `iphone` | Case-insensitive literal substring required in the title or description; empty string disables this check |
| `location_id` | `1.300014.311480` | Blocket's Göteborg municipality filter |
| `poll_seconds` | `30` | Target interval between successful cycle starts |
| `pages` | `1` | Number of newest search-result pages fetched per cycle |
| `details_per_poll` | `10` | Maximum ad-detail requests per cycle |
| `request_delay_seconds` | `2` | Minimum time between request starts |
| `detail_refresh_hours` | `24` | Recheck descriptions even when summaries are unchanged |
| `data_dir` | `data` | Output directory, relative to the config file |

For example, keep the mobile-phone category and set `query` to `Samsung Galaxy`
and `must_have` to `Galaxy S24` to search for that model. The required phrase is
literal text, not a regular expression or a list of separate words.

Restart the collector after editing the config. Changes to the query, category,
required phrase or location exclude previous-search results from the current
export and recheck listings returned by the new search. Their database records
and history are preserved. Damage and 24-hour age filters still apply.

The default monitors the newest page, not the entire historical inventory. Pending
ads persist and are processed over subsequent cycles. Increase `pages` to cover
more results within the 24-hour window, at the cost of additional requests. Search pagination is not
an atomic snapshot, so this cannot guarantee capturing every ad during rapid
turnover. Disappearance from search is not proof of sale. Previously collected ads
are rechecked periodically, and explicit unavailable results or HTTP 404/410 remove
them from the candidate export. Until rechecked, a candidate can be stale.

Cycles never overlap. Slow cycles take longer than 30 seconds. Network/server
failures and HTTP 429 cause exponential backoff (up to 30 minutes, or longer if
`Retry-After` requests it); HTTP 401/403 stops the collector. `--once` returns a
nonzero exit code on a failed collection cycle. Parser changes are logged rather
than silently treated as an empty search.

Blocket page structures can change. The parser was checked against public search
and item HTML on 2026-10-06. It reads the embedded search state and the full visible
ad description, rather than the truncated SEO description.

### Tests

```sh
python3 -m unittest discover -s tests -v
```

Offline tests cover parsing, incomplete pages, fault filters and negation,
deduplication, price history, export, detail caching and periodic rechecks.

## Codex resale agent

The collector gathers ads; a separate one-shot Codex review researches resale value.
It uses the installed Codex CLI and your existing Codex login/model configuration.
If needed, sign in with `codex login`. A review uses your Codex account's usage
allowance. It is not automatically launched by the collector.

```sh
# Prepare a snapshot and prompt without invoking the model:
python3 review_candidates.py --dry-run

# Research candidates and generate a report:
python3 review_candidates.py
```

The runner snapshots `data/candidates.json`, rechecks the 24-hour limit, excludes
invalid/nonpositive prices and non-SEK prices, and reviews up to 20 newest items.
It saves timestamped runs under `data/reviews/`, each containing:

- `snapshot.json`: selected candidates plus excluded/deferred IDs and reasons.
- `settings.json`: valuation assumptions used for that run.
- `prompt.md`: the complete instructions and input supplied to Codex.
- `report.md`: the final research report (actual review runs only).

Reports include a ranked shortlist, comparable links and evidence types, estimated
resale ranges, net profit, ROI, maximum purchase prices, and reasons for rejecting
or requesting manual review of each item. Old reports are historical snapshots;
check their dates and availability before acting. Failed/interrupted runs may
leave incomplete reports. An empty eligible input produces a no-candidates report
without invoking Codex. Deferred items are explicitly not assessed; raise
`max_candidates` to cover a larger batch.

The agent uses live web research. It distinguishes **confirmed completed sales**
from **active asking prices**, dealer prices and buyback offers. An ended or removed
listing is not proof of a sale or its final price. Inaccessible sources, insufficient
comparables or unverified condition can result in `insufficient_evidence` or
`needs_review` instead of a recommendation. Image review is attempted through
available tools and explicitly marked unavailable when images cannot be inspected.

Edit `resale_agent.json` to tune the agent (these are application settings consumed
by `review_candidates.py`, not a native Codex config file):

| Setting | Default | Meaning |
| --- | --- | --- |
| `candidates_path` | `data/candidates.json` | Input path, relative to the agent config file |
| `reports_dir` | `data/reviews` | Run directories, relative to the agent config file |
| `model` | `null` | Use your configured Codex model; optionally specify an available model ID |
| `max_candidates` | `20` | Maximum candidates researched per run, newest first |
| `minimum_net_profit_sek` | `500` | Minimum conservative net profit |
| `minimum_roi_percent` | `20` | Minimum net profit divided by acquisition cost, in percent |
| `transport_and_handling_sek` | `150` | Per-item acquisition cost allowance |
| `selling_fee_percent` | `0` | Assumed percentage of resale price; adjust for your selling route |
| `risk_reserve_percent` | `10` | Reserve deducted from resale proceeds |
| `minimum_comparables` | `3` | Recent matched completed sales needed for `potential_deal` |
| `sold_comparable_max_age_days` | `90` | Maximum age for recent completed-sale evidence |
| `asking_price_discount_percent` | `15` | Minimum discount when estimating from asking prices only |
| `market` | Sweden, prioritizing Gothenburg | Geographic research focus |
| `report_language` | English | Report language; e.g. change to `Swedish` |

These thresholds and costs are starting assumptions, not verified market fees or
promises of profit. Estimates are before tax. The instructions require explicit
cost arithmetic and flag unverified fees; asking-price-only estimates cannot earn
`potential_deal`. Valuation decisions are model judgments, not deterministic
validation by the launcher.

The reusable agent instructions live in `agents/resale-review.md`. To run the same
workflow in a Codex chat, first create a dry run, then ask Codex to follow the
specific generated `data/reviews/<run>/prompt.md` and return its report. The CLI
launcher is the reproducible path and captures the final answer automatically.

The launcher uses a read-only command sandbox, requests live web search, and feeds
the snapshot through stdin. The agent is instructed to research only, leave source
files untouched, and never message sellers or purchase items. The launcher writes
run artifacts; your collector keeps running independently. Existing account or
workspace policies can restrict web/image access. No global Codex settings are
changed.

Implementation follows the official Codex documentation for
[non-interactive runs and final-message output](https://developers.openai.com/codex/noninteractive)
and [configuration overrides and web search](https://developers.openai.com/codex/config-reference).
