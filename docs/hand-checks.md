# Hand-checked missed-savings fills

SPEC section 10 asks for the Missed savings total to match a hand-checked calculation for 3 sample fills. These are the three final fills, worked by hand from the inputs below. `tests/test_analytics_handchecked.py` checks the same fills against the committed data, the shared cost function and `site/data/missed.json`.

Checked Nov 18, 2026, contract v1.16, after 38 customer points were redrawn so every point has at least 3 candidate businesses, and the fleet data was resynthesized. If an assumption changes, rerun the arithmetic and update this file and the test together.

## The rule

```
net saving = price gap x gallons
           + detour cost of the station actually used
           - detour cost of the lower-cost option

detour cost = fuel burned  (extra miles / mpg x that station's price)
            + driver time  (extra minutes x rush-hour factor / 60 x driver cost per hour)
            + wear         (extra miles x wear cost per mile)
```

Schedule risk is not a cost (contract 11.1), so it appears nowhere below.

## Assumptions used (config/assumptions.yaml)

| Key | Value |
|---|---|
| `fleet.local_mpg` | 6.0 mpg |
| `savings.driver_cost_per_hour` | $38.00 an hour |
| `savings.variable_cost_per_mile` | $0.25 a mile (wear) |
| `time_cost.travel_time_index` | 1.17 in a weekday rush hour (6 to 9 am, 4 to 7 pm), else 1 |
| `time_cost.max_detour_minutes` | 15 effective minutes, for alternatives only |
| `savings.min_saving_threshold_usd` | $5.00 |

## Where each input comes from

- Truck, time, gallons and price paid: `data/raw/fuel_card.csv` (`Unit`, `TransDate`, `TransTime`, `Gallons`, `PPG`).
- Alternative's price: `data/raw/station_prices.csv`, posted price that day (none of these stations is a partner, so net = posted).
- Detour miles and minutes: `ref_detour`, from the cached openrouteservice matrix (`data/cache/ors/`). Shown here to 4 decimals; the test uses full precision.

## Fill 1: FC5659816

TUC-11, Friday Dec 5, 2025, 07:02 (rush hour, factor 1.17), route TUC-R10, 76.960 gallons. Why: both.
Used Pilot Express (STN-032) at $4.017. Lower-cost option: CFN 1792 Benson Hwy (STN-A03) at $3.891.

- Price gap: (4.017 - 3.891) x 76.960 = 0.126 x 76.960 = **$9.6970**
- Detour to Pilot Express (after stop 10): 21.0847 mi, 36.0037 min
  - fuel 21.0847 / 6 x 4.017 = 14.1162
  - time 36.0037 x 1.17 = 42.1243 min x 38 / 60 = 26.6787
  - wear 21.0847 x 0.25 = 5.2712
  - total **$46.0661**
- Detour to CFN 1792 (after stop 0): 3.3397 mi, 7.8515 min
  - fuel 3.3397 / 6 x 3.891 = 2.1658
  - time 7.8515 x 1.17 = 9.1863 min x 38 / 60 = 5.8180
  - wear 3.3397 x 0.25 = 0.8349
  - total **$8.8187**
- Net: 9.6970 + 46.0661 - 8.8187 = 46.9444, shown as **$46.94**
- On the site the parts read $9.70 + $46.07 - $8.83. The $8.8187 shows as $8.83 rather than $8.82, so the rounded parts add exactly to the rounded net (rounding only, contract 11.5).

The driver went 21.1 miles out of the way to Pilot Express in the morning rush. CFN 1792 needs only a 3.3-mile detour, so the page says "Lower pump price and saves a 17.8-mile detour." The station a driver actually used is priced however long its detour; the 15-minute limit applies only to the option (CFN 1792's 7.9 minutes become 9.2 effective minutes).

## Fill 2: FC8960414

TUC-10, Wednesday Oct 28, 2026, 05:15 (before the 6 am rush, factor 1), route TUC-R09, 73.606 gallons. Why: lower price.
Used CFN 1792 Benson Hwy (STN-A03) at $6.501. Lower-cost option: CFN 5915 Tucson (STN-A02) at $6.375.

- Price gap: (6.501 - 6.375) x 73.606 = 0.126 x 73.606 = **$9.2744**
- Detour to CFN 1792 (after stop 11): 0.6794 mi, 1.5567 min
  - fuel 0.6794 / 6 x 6.501 = 0.7361
  - time 1.5567 x 1 = 1.5567 min x 38 / 60 = 0.9859
  - wear 0.6794 x 0.25 = 0.1698
  - total **$1.8918**
- Detour to CFN 5915 (after stop 3): 0.7520 mi, 2.2723 min
  - fuel 0.7520 / 6 x 6.375 = 0.7990
  - time 2.2723 x 1 = 2.2723 min x 38 / 60 = 1.4391
  - wear 0.7520 x 0.25 = 0.1880
  - total **$2.4262**
- Net: 9.2744 + 1.8918 - 2.4262 = 8.7400, shown as **$8.74**
- On the site the parts read $9.27 + $1.89 - $2.42. The $2.4262 shows as $2.42 rather than $2.43, the same rounding-only balancing.

The lower-cost option's detour is a little longer, so the saving is a little less than the price gap.

## Fill 3: FC4426227

TUC-07, Friday Oct 30, 2026, 08:47 (rush hour, factor 1.17), route TUC-R04, 79.289 gallons. Why: shorter detour.
Used CFN 5915 Tucson (STN-A02) at $6.394. Lower-cost option: CFN 1792 Benson Hwy (STN-A03) at $6.489, a HIGHER pump price.

- Price gap: (6.394 - 6.489) x 79.289 = -0.095 x 79.289 = **-$7.5325** (the option costs more per gallon)
- Detour to CFN 5915 (after stop 1): 6.5469 mi, 8.4547 min
  - fuel 6.5469 / 6 x 6.394 = 6.9768
  - time 8.4547 x 1.17 = 9.8920 min x 38 / 60 = 6.2649
  - wear 6.5469 x 0.25 = 1.6367
  - total **$14.8785**
- Detour to CFN 1792 (after stop 0): 0.1328 mi, 0.6048 min
  - fuel 0.1328 / 6 x 6.489 = 0.1436
  - time 0.6048 x 1.17 = 0.7077 min x 38 / 60 = 0.4482
  - wear 0.1328 x 0.25 = 0.0332
  - total **$0.6250**
- Net: -7.5325 + 14.8785 - 0.6250 = 6.7210, shown as **$6.72**
- On the site the parts read -$7.53 + $14.88 - $0.63. The alt detour is $0.62498 before rounding, so it would round to $0.62; it shows as $0.63, the same rounding-only balancing.

The page's line reads "Higher pump price, but saves a 6.4-mile detour." Paying 9.5 cents a gallon more still saves $6.72, because it avoids a 6.5-mile rush-hour detour.

## History: why the earlier reports showed other numbers

Three sets of figures were reported before this file existed. Two sets came from the real data; one came from made-up test fixtures.

| Report | Fill | Truck, date | Stations (used -> alternative) | Then | Now |
|---|---|---|---|---|---|
| v1.6 (one cost formula) | FC1774473 | TUC-08, Jul 24 07:57 | Pilot -> CFN 1792 | $32.03 | $33.60 |
| v1.6 | FC6279902 | TUC-14, Jun 29 08:54 | CFN 5915 -> CFN 1792 | $8.06 | $9.19 |
| v1.6 | FC7730694 | TUC-06, Mar 30 09:25 | CFN 5915 -> CFN 1792 | $5.08 | $5.08 |
| v1.9 (time-cost upgrade) | FC1774473 | TUC-08, Jul 24 07:57 | Pilot -> CFN 1792 | $40.71 | $33.60 |
| v1.9 | FC0733626 | TUC-01, Mar 19 05:42 | CFN 1792 -> CFN 5915 | $8.94 | $9.39 |
| v1.9 | FC9591385 | TUC-05, Mar 23 06:40 | CFN 1792 -> CFN 5915 | $5.68 | $6.03 |
| v1.11 test report | F1, F2, F3 | made-up fixture fills, not real data | fixture stations | $13.25, $9.24, $12.23 | unchanged |
| v1.12 final (before v1.14) | FC1774473, FC0733626, FC9591385 | TUC-08 Jul 24; TUC-01 Mar 19; TUC-05 Mar 23 | Pilot -> CFN 1792; CFN 1792 -> CFN 5915; CFN 1792 -> CFN 5915 | $33.60, $9.39, $6.03 | no longer exist |
| v1.14 final (seeded move) | FC2028603, FC4274959, FC0528809 | TUC-10 Jun 10; TUC-12 Feb 26; TUC-11 Oct 2 | Pilot Express -> CFN 1792; CFN 1792 -> CFN 5915; CFN 5915 -> CFN 0307 | $40.37, $12.30, $10.36 | no longer exist |
| v1.15 final (unseeded move) | FC2438413, FC8300755, FC2072415 | TUC-03 Sep 3; TUC-14 May 22; TUC-04 Dec 4 | Pilot Express -> CFN 1792; CFN 1792 -> CFN 5915; CFN 5915 -> CFN 1792 | $52.22, $8.87, $13.61 | no longer exist |

Why each changed:
- **FC1774473.**
  - v1.6 had no rush-hour factor and no schedule risk: 13.27 + 19.26 - 0.51 = $32.03.
  - v1.9 applied the FHWA factor 1.07 and added $8.03 of schedule risk on the Pilot detour (inserted before the last delivery): 13.27 + 27.96 - 0.53 = $40.71.
  - Now the factor is 1.17 and schedule risk is gone: $33.60.
- **FC6279902.**
  - v1.6 priced it off-peak at $8.06.
  - It is a Monday 08:54 fill, so the rush-hour factor now applies to the 10.9-minute detour it avoids: $9.19.
  - v1.9 did not hand-check it; the new costs had reordered the list, so other rows were picked.
  - Note: the alternative's pump price is higher here (gap -$3.74). The whole saving comes from avoiding a 5.1-mile detour.
- **FC7730694.** Off-peak, and no schedule risk applied in v1.6, so it is still $5.08. As with FC6279902, the alternative's pump price is higher; the saving is the avoided detour.
- **FC0733626.** v1.9 added $0.45 of schedule risk to the alternative (inserted before the last delivery). With risk gone it is $9.39 (8.94 + 0.45).
- **FC9591385.**
  - v1.9 used factor 1.07 and charged schedule risk on both detours: 6.43 + 5.34 - 6.09 = $5.68.
  - Now the factor is 1.17 and there is no risk: $6.03.
- **F1, F2, F3** ($13.25, $9.24, $12.23) are hand-made fills in a small test database (`tests/fixtures/analytics/hubkit.py`). They test the machinery, not the real data, so they were never the same fills as above. They are kept as fixture tests in `tests/test_analytics_fixture_fills.py`.

- **FC1774473, FC0733626, FC9591385 (v1.12 final checks).** These fill IDs no longer exist. Contract v1.14 moved every customer point 300 to 600 m off real businesses, so the routes changed. The generator then drew a new year of fuel stops from the same seed, with new transaction IDs. The three fills above replace them.

- **FC2028603, FC4274959, FC0528809 (v1.14 final checks).** These no longer exist either. qa-reviewer showed the v1.14 move could be replayed from the public seed. The points were therefore redrawn from unseeded randomness (contract 15.1), with every point reachable by truck (15.4), and the fleet data was resynthesized again.

- **FC2438413, FC8300755, FC2072415 (v1.15 final checks).** These no longer exist either. Cass chose a rule that every moved point needs at least 3 candidate businesses 300 to 600 m away (contract 16). The 38 points that failed it were redrawn, which changed the routes, and the fleet data was resynthesized again.

Why these three are final: they cover each answer of the Missed savings "Why" column. FC5659816 is "both": rush hour, and the avoided detour is most of the saving. FC8960414 is "lower price": off-peak, where the option's detour is the longer one. FC4426227 is "shorter detour": rush hour, with a higher pump price. All three show the one-cent balancing on the option's detour.
