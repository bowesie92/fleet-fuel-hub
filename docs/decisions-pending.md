# Decisions for Cass

Kept by the orchestrator during unattended runs. Each open item says what needs your call,
the default used so work could continue, and what changes if you pick differently.
**BLOCKING** items stopped work on something. Resolved items keep your answer.

## Open

### H1. Defaults that move the headline numbers (you asked to see these)
You said to use my defaults for D1 to D11 unless one changes the headline numbers (gallons, average net price, missed savings, partner share) or the net-savings formula. **None of them changes the formula.** These do change headline numbers:
- **D3. Cardlock pricing and access.** Cardlock sites are assumed usable by the fleet and priced 10 cents below the EIA average (`prices.class_offset_per_gal.cardlock: -0.10`). If chain-locator research adds a cardlock site, this moves average net price, missed savings and the partner scout.
- **D4. No fills at unverified stations.** Synthetic drivers only fuel where truck access is confirmed. This sets where every gallon is bought, so it moves average net price and partner share. Missed savings and the partner scout also use confirmed stations by default (the site toggle shows the other case).
- **D7. Linehaul runs, now fixed** (see the sanity check under Resolved). Five daily runs instead of one raises gallons and fills about fourfold for linehaul and changes every headline number.
- **D13. Partner set, resolved by Cass on Nov 17:** a stated assumption (confirmed truck stops on I-10 near Casa Grande), no longer a random draw.

- **Routing candidate radius, raised from 10 to 15 miles** (`routing.candidate_station_radius_mi`). Drivers in synth already search 15 miles, so at 10 a truck could fuel somewhere with no detour figures. This changes which alternatives exist, so it moves missed savings and the scout.
- **Cardlock class for the three CFN sites.** They are priced as cardlocks (D3), not as branded retail, which makes them among the cheapest Tucson options.
- **Partner scout counts only what the deal creates.** At a 0 cent discount it shows $0. Savings already available at posted prices are missed savings and appear on a separate line ("Already available at posted prices"), so no dollar is counted twice. Counting them in the deal would roughly add $1,846 a year to the top prospect.
- **Result of D3 on real data:** the truck access toggle changes no recommendation and no missed-savings total. The cardlocks, at 10 cents below the EIA average, always beat the unverified retail stations. Lower the cardlock discount and the toggle starts to matter.
- **Result of D13 on real data:** partner share of gallons is 68%, because the random draw includes the busy corridor station that most linehaul fills use.

### Y1. Phoenix DC yard fueling leaves zero linehaul card fills
- **What happens:** a linehaul round trip (about 281 actual miles at 6.8 mpg) uses about 41 of 200 gallons, roughly 20% of the tank. Filling at the Phoenix DC before each departure, and skipping 3% of departures (`yard_fuel.skip_rate`), never drops a truck below the 35% refuel point. That would take 3 skipped yard fills in a row by the same truck; qa-reviewer found at most 1 in the year.
- **Result:** the year has 1,508 yard fills (64,657 gallons) and 223 card fills, all by Tucson local trucks. Linehaul has no card fills, missed savings, or partner scout volume.
- **Default:** keep it. It follows from the assumptions and matches "card fills only as exceptions".
- **To show a few exceptions:** raise `yard_fuel.skip_rate`, or add a linehaul-only refuel threshold. Both are assumptions you would be inventing to get a visible effect, so it is your call.

### R1. RESOLVED (Cass, Nov 17): the scout counts only savings the deal creates
Formula changed (SPEC section 6, contract v1.8): each fill's deal value is capped at the discount on the gallons it captures, and pre-existing gaps are never deal value, so value per captured gallon can never exceed the discount. Original note kept below.

### R1 (original note). Why the scout's cents per gallon can beat the discount
- At a 12 cent discount, the top Tucson prospect shows **12.50 cents per captured gallon**, a little more than 12.
- Under the contract method, a fill that moves to the station at that discount keeps its whole saving. That includes any price gap that already existed but was below the $5 missed-savings threshold, so it was not counted at 0 cents.
- **Default:** keep the method and say so in the scout's method line; it follows the contract.
- **Alternative:** subtract each moved fill's pre-existing gap, so the figure never exceeds the discount on moved gallons. It reads more cleanly, but it is a formula change, so it is your call.

### Y2. RESOLVED (Cass, Nov 17): partners are a stated set
The random draw is replaced: the partner program is the truck-confirmed stations on I-10 between Casa Grande and Eloy (`prices.partner_set_bbox`; 6 stations on the current data). The partner tile now reads "Tucson card gallons at partner pricing" and links to the scout. Original note kept below for the record.

### Y2 (original note). Partner share of card gallons is now 0%
- The six partners were drawn at random from truck-confirmed stations, and none is in Tucson. Since the yard tank change, every card fill is a Tucson local fill, so no card gallon goes to a partner.
- **This sharpens the scout headline:** the partner program, as drawn, does nothing for the only trucks that use the card. The scout shows the Tucson stations worth signing.
- **Options:** keep the random draw (default; honest about a mismatched program), or draw partners from stations the card fleet actually uses (`prices.partner_count` with a Tucson rule). Your call.

### S2. Small Wave 2 notes (no action unless you want one)
- **CFN source links.** The three CFN `add` entries cite the network's locator page (cfnfleetwide.com/site-locator/), not a per-site link, because the locator has no per-site pages. Please confirm cfnfleetwide.com is CFN's own site when you do the Street View pass.
- **Break-even leaves out wear.** SPEC section 6's break-even formula has no per-mile wear term, while missed savings and the test route include it ($0.25 a mile). So break-even slightly understates the gap a detour needs. Say if you want wear added; it is a SPEC formula change, so I left it.
- **Test route reason wording.** When a better stop is only a hair off the route, the sentence reads "even after the 0.0 extra miles and under 1 minute of extra driving". It is true but clunky; "with almost no extra driving" would read better. Cosmetic.
- **Routing's daily call counter** resets on every run, so it is not a true daily cap. With the cache, a normal build makes zero calls, so this is low risk. Fix later if refetches become frequent.

### D9. Customer neighborhoods ("Customer 014 · Midtown")
STYLE.md shows a neighborhood next to each customer, but the data has none, so stops show "Customer 014" only. Adding it needs a public neighborhood boundary file and a `neighborhood` field. That makes customers slightly less anonymous, so it is your call. Default: leave it out.

### D10. Mockup details the data does not have
Stop time windows, cases per stop, route end time and a date-range control are left out, because STYLE.md section 6 forbids faking data. To add them, synth would generate time windows and case counts. Cheap if you want them. Default: leave them out.

### D11. Unnamed truck stations
10 bare OSM truck points display as "Unnamed fuel station · STN-0xx". You can rename or exclude any in `config/station_overrides.yaml`. Default: leave them.

### P1. Moved customer points with too few candidate businesses (resolved Nov 18)
- **What qa-reviewer found:** 16 of the 300 moved points had exactly one of the 995 OpenStreetMap food-service places 300 to 600 m away, so anyone rerunning the public Overpass query could name the business. Another 22 had only two.
- **Decision (Cass, Nov 18):** every point needs at least 3 candidate businesses in its ring (`fetch.customers.min_candidate_originals: 3`). The 38 points that failed were redrawn, unseeded, under every other rule, and re-checked for truck reachability. The other 262 did not move. Contract section 16.
- **Related, minor:** the final reachability answer is not kept in the cache, because pruning removes it. The stops used on routes are proven reachable by the cached routes; the rest were checked at draw time only.

### P2. GPS jitter around customer stops (open, low priority, Nov 18)
- **What qa-reviewer noticed:** synthetic telematics adds GPS jitter at stops, so 11 parked pings sit 17 to 25 m from an original business (their stops are 36 to 59 m away). The 5 m privacy check passes, but the jitter makes the 50 m "clear of any original" rule less strict than it sounds.
- **Default:** leave it. A jittered ping sits near a moved stop, and each stop has 3 or more candidate businesses.
- **Alternative:** cap the jitter radius below the clear distance (a synth change and one more resynth).

### D14. GitHub Pages: live (resolved Nov 18)
- **Done:** Cass said go. The public repo `bowesie92/fleet-fuel-hub` holds one clean commit, and GitHub Pages serves https://bowesie92.github.io/fleet-fuel-hub/ from the Pages workflow. The old working history stays in the private `bowesie92/fleet-fuel-hub-dev`. Details in `docs/publishing-plan.md`.

## Resolved

- **S1 closed (Cass, Nov 17): no Street View check; borderline sites stay unverified.**
  - None of the borderline sites gets an `add` override, so none is confirmed.
  - Unverified stations are hidden by default (`savings.include_unverified_truck_access_default: false`) and are left out of recommendations, missed savings and the scout unless the site toggle is on.
  - Five of the six sites (Pacific Pride and Senergy on Highway Dr, Senergy on Michigan St, Circle K #3387, Speedway 43032 and 2938) are not in the station list at all. OpenStreetMap has no diesel or truck tags for them, so they appear nowhere.
  - Pilot Express #1178 (STN-032) stays confirmed. That comes from its own OpenStreetMap truck tag, not from the borderline evidence, and it was not demoted.
- **Site review answers (Cass, Nov 17):**
  - C1: keep the generic fuel-card network wording; no network names.
  - C2: driver wage basis is BLS Tucson metro median $27.23 an hour (May 2025).
  - C3: keep the ATRI "as reported, not checked by this project" caveat.
  - T1: Travel Time Index 1.17 (Texas A&M 2025 Urban Mobility Report, freeways and arterials); FHWA 1.07 is shown as the cross-check.
  - L1: MIT licence for the code (`LICENSE`); data keeps its own licences per `DATA_LICENSES.md`, in separate files.
  - Also: schedule risk is a ranking penalty only; 15-minute detour cap; typical fill 72 gallons. Worked example changed by the orchestrator to 12 minutes, 4 miles at 7 am so it stays under the new 15-minute cap and falls in the hours Tucson routes run (was 30 minutes at 5 pm).
- **D0 (your answer, Nov 17):** option 3, modified.
  - Confirm Tucson truck sites only from a chain's or cardlock network's own locator, never from satellite imagery. Record each as an `add` entry with name, coordinates, source URL and reason.
  - List borderline sites for you to check in Street View.
  - Keep the other Tucson diesel stations as unverified. Proceed even at 4 or 5 confirmed; thin options are a finding.
  - **Result:** 4 sites qualified from official locators: Pilot #593 (it matched the existing OSM Pilot, STN-006) and three CFN cardlocks (new: STN-A02, A03 and A04). With the Pilot Express already confirmed by OSM, the Tucson box has **5 confirmed** stations and **13 unverified** diesel stations. Love's and TA-Petro have no sites in the box. I checked every evidence quote and coordinate myself against saved copies of the official pages.
- **D12 and D13 (your answer):** the headline, not a bug. The Tucson partnership scout moves into v1 as the Overview lead insight, labeled a synthetic scenario. SPEC sections 5, 7 and 9 updated.
- **Sanity check of 318 swipes (your request):**
  - **Linehaul was inconsistent.** Config has 6 Phoenix tractors, but the generator sent one linehaul run a day, so five sat idle. Fixed: `routes.linehaul_runs_per_day: 5` (one spare), with routes `PHX-L01` to `PHX-L05`.
  - **Tucson is consistent but thin.** Fourteen trucks run 10 routes of 18 to 39 miles a day (about 30 average). That is 93,493 miles and 15,582 gallons a year, at about 72 gallons a fill, which predicts 216 fills; 221 happened. Each Tucson truck fuels about once every 3 weeks.
- **D1, D2, D5, D6, D8:** defaults kept, with no effect on headline numbers.
  - D1: overrides keyed by `osm_ref`.
  - D2: superseded by the D0 answer.
  - D5: contract changes land with their code.
  - D6: wave gates skipped during unattended runs.
  - D8: removing either copy of a duplicate pair counts as a correct dedupe.
