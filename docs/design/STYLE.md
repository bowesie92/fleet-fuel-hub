# Fleet Fuel Hub: Style Guide

One visual system for every screen. Reference mockups are in `design-refs/` (gitignored, local only). **Match their layout and feel, not their text.** Section 6 lists what NOT to copy from them.

Reference map:

| File | Use it for |
|---|---|
| 01-missed-savings.png | Missed savings page: hero number card, weekly bar chart, sortable table, route comparison mini map |
| 02-overview.png | Overview page and the overall shell: navy sidebar, SYNTHETIC strip, KPI tiles, area chart, corridor map card |
| 03-station-map.png | Station map page: full-bleed map, price-colored dots, partner stars, legend card, right detail panel, insight callout |
| 04-test-route.png | Test route page: numbered stop list, inserted fuel stop card, teal route line, amber dashed detour, route totals footer |
| 05-data-hub.png | Data hub page: three source cards, processing metrics column, unified output table, 3-step stepper |
| 06-hero-laptop-phone.png | README hero image only (dark theme is for this marketing shot, not the app) |
| 07-mobile-recommend.png | Mobile layout: bottom sheet with three metrics, navy button, bottom tab bar |

---

## 1. App shell (every page)

```
+-----------------------------------------------------------------------+
|  [flask icon] SYNTHETIC DATA        (i) Demonstration only. Not real data. |  <- top strip, full width
+-------------+---------------------------------------------------------+
|  Fleet Fuel |  Page title (H1)                    [controls on right] |
|  Hub        |  One-line subtitle in muted gray                         |
|             |                                                          |
|  Overview   |  [ cards in a 12-column grid, 24px gaps ]                |
|  Station map|                                                          |
|  Test route |                                                          |
|  Missed     |                                                          |
|  savings    |                                                          |
|  Data hub   |                                                          |
|             |                                                          |
|  [Data as of card]                                                     |
|  [Help / Assumptions]                                                  |
+-------------+---------------------------------------------------------+
```

- **Sidebar:** deep navy, 240px wide, white text, outline icons. The active item is a teal pill with white text (from 02). Product name "Fleet Fuel Hub" with the subtitle "Fuel analytics demo".
- **Sidebar footer:** a "Data as of" card with the build date and "Synthetic data, rebuilt on each deploy", plus links to Assumptions and Help.
- **Top strip:** light gray, centered "SYNTHETIC DATA" with a flask icon, and "Demonstration only. Not real data." on the right (from 02). On every page, always.
- **Page header:** H1 title, muted subtitle, and controls on the right (date range, route selector, Export). The controls are outlined buttons with icons (from 01).
- **Nav items, in order:** Start here (`guide.html`, v1.17), Overview, Station map, Test route, Missed savings, Data hub. Later: Partner scout, Review flags. **No "Drivers" page.**
- **Full-bleed map pages** (Station map, Test route): the sidebar stays and the map fills the rest. Panels float on top of the map as white cards.

## 2. Design tokens

Put these in `site/css/tokens.css` and use only these variables:

```css
:root {
  /* Brand and surfaces */
  --navy-900: #0B1F3A;   /* sidebar, primary buttons, current-period bar */
  --navy-700: #16325C;   /* sidebar hover */
  --teal-600: #0E8C8C;   /* primary accent, chosen route line, active nav, main series */
  --teal-500: #14A3A3;   /* hover, highlights */
  --teal-50:  #E7F5F5;   /* insight callout background, chips */
  --amber-500:#D9930D;   /* detour line, attention, estimates */
  --amber-50: #FDF4E3;
  --bg:       #F5F7FA;   /* page background */
  --card:     #FFFFFF;
  --border:   #E3E8EF;

  /* Text */
  --text-900: #111827;
  --text-600: #5B6472;   /* subtitles, axis labels */
  --text-400: #8A93A3;   /* footnotes */

  /* Money meaning (only for money) */
  --gain:     #1E8E4E;   /* savings, below average */
  --loss:     #C8372D;   /* missed savings, above average */

  /* Benchmarks */
  --benchmark:#9AA3B2;   /* EIA or regional average, always dashed */

  /* Price scale on the map, cheap to expensive (5 bins) */
  --price-1:  #1A9850;
  --price-2:  #91CF60;
  --price-3:  #F2C744;
  --price-4:  #F08A3C;
  --price-5:  #D73027;

  /* Shape and type */
  --radius:   12px;
  --radius-sm:8px;
  --shadow:   0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.08);
  --font:     "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
}
```

**Color rules:**
- Teal means "the thing we chose / primary data." Amber means "a detour or something to look at." Green and red are **only** for money (saved vs missed) and the price scale. Navy is structure.
- Benchmarks (the EIA weekly average, the regional average) are always a **gray dashed line**, never a color.
- **Accessibility:** green-to-red is hard to read for people with red-green color blindness (common in men). So the map legend always shows dollar ranges, every dot shows its price on hover, and partner stations use a **shape** (navy star), never just a color.

## 3. Typography

- **Font:** Inter (Google Fonts), falling back to system fonts.
- **Numbers:** `font-variant-numeric: tabular-nums` everywhere, so columns of prices line up.
- **Scale:** H1 28px/700 · card title 15px/600, uppercase with letter spacing only for hero-number labels (01) · body 14px/400 · footnote 12px/400, color `--text-400` · KPI value 32px/700 · hero number 56px/800 (01).
- **Money:** always `$1,234.56`, price per gallon `$3.42/gal`, cents in chips as `9¢`.

## 4. Components

| Component | Look | From |
|---|---|---|
| **Card** | White, `--radius`, 1px `--border`, `--shadow`, 24px padding, title + muted subtitle at top, optional control top right | all |
| **KPI tile** | Round 44px icon badge (teal or amber tint), label, big value, then "vs prior 4 weeks ↑ 3.2%". Arrow color shows good or bad for the business, not just up or down | 02 |
| **Hero number card** | Uppercase label with info icon, 56px number, delta line, then a divider and a one-line note | 01 |
| **Area / line chart** | Teal line, soft teal gradient fill, dots on points, the latest value in a teal tag at the end. Benchmark as gray dashed line. Footnote inside the card: data source + "(synthetic)" | 02, 03 |
| **Bar chart by week** | Past weeks light gray-blue, current week navy | 01 |
| **Data table** | Sortable headers with arrows, 48px rows, money right-aligned, gains green and losses red, small colored dot before the extra-miles value, pagination and rows-per-page at the bottom, "Customize columns" button top right | 01 |
| **Insight callout** | `--teal-50` background, lightbulb icon, bold one-line takeaway, smaller line with the assumptions behind it ("Based on 150 gal fill and 6 mi extra round trip") | 03 |
| **Chip** | Pill with tinted background, e.g. "9¢ below regional avg", "Partner", "Ready", "SYNTHETIC DATA" | 03, 05, 07 |
| **Map legend card** | White card bottom-left: price bins with dollar ranges, partner star | 03, 04 |
| **Map detail panel** | Right side, 360px, closes with X: station name, location, partner badge, price block with chip, price-paid vs benchmark chart, insight callout at the bottom | 03 |
| **Stop list** | Numbered teal circles, stop name, time window, cases. An inserted fuel stop is a **green-tinted card** in the list that says where it was inserted, plus miles, minutes and net saving | 04 |
| **Route lines** | Chosen route: solid teal 4px. Detour or alternative: dashed amber 3px. Numbered stop markers in teal circles. Fuel stop: amber pump marker with a tooltip "2.1 mi · 6 min" | 04, 01 |
| **Route totals footer** | Bottom of the stop list: total miles, drive time, end time | 04 |
| **Stepper** | Numbered circles 1, 2, 3 with labels: Select sources, Integrate & clean, Review | 05 |
| **Source card** | Colored outline per source (fuel card = blue, telematics = green, route plans = orange), a mini messy table, a red "Messy · Inconsistent · Duplicates" note | 05 |
| **Processing metric** | Check icon, label, big number, a one-line "how" ("Fuzzy name matching + distance") | 05 |
| **Status badge** | "Ready" green pill on the unified table | 05 |

## 5. Page layouts

- **Overview (02):** 4 KPI tiles in a row (total gallons, average net price per gallon, missed savings, partner share). Below: the weekly price trend (8 cols, with the EIA benchmark dashed) and the Phoenix to Tucson corridor map card (4 cols) with a "View station map →" link.
- **Station map (03):** full-bleed map of the I-10 corridor and the Tucson metro. Price-colored dots, partner stars, legend bottom-left, zoom and locate controls, detail panel on the right when a station is clicked. The top bar has the corridor selector.
- **Test route (04):** route selector at the top. Left panel is the stop list (380px) with the totals footer. Map on the right with the route, the detour and the station tooltip. Clicking a station on the map inserts it and adds the green card to the list.
- **Missed savings (01):** hero number card (5 cols) + weekly bar chart (7 cols). Below: the details table (8 cols) + route options comparison map (4 cols) that updates when a row is clicked.
- **Data hub (05):** stepper at the top. Three columns: source cards, arrows into the processing metrics, unified output table with pagination and "Export unified data".
- **Mobile, under 768px (07):** the sidebar becomes a bottom tab bar (Overview, Map, Route, Savings, Hub). Map pages show a draggable bottom sheet with a three-metric row (price per gallon, detour, net savings) and a full-width navy button. The SYNTHETIC chip stays visible.

## 6. Content rules (overrides anything in the mockups)

Things in the reference images that must **not** be copied:

- **No real brand logos.** Station rows and markers use a generic pump icon. A brand name may appear as plain text, because it comes from OpenStreetMap.
- **No product names from the mockups** ("FuelOptix", "FleetFuel", "Arizona Fuel Insights"). The product is "Fleet Fuel Hub".
- **No real-looking customer names or street addresses.** Stops are "Customer 014 · Midtown" (an anonymized ID plus a neighborhood).
- **No "Current price" or "Last updated today".** The data is not live. Use "Avg paid, last 4 weeks (synthetic)" and the "Data as of" date.
- **Units:** gallons, miles, USD. Never liters or km.
- **Time:** Arizona time, labeled "MST (Arizona, no daylight saving time)". The unified table shows local time, not UTC.
- **No driver names or driver pages.** Use truck IDs only (TRK-107).
- **No fuel-type donut or gasoline split** (06). The fleet buys diesel only.
- **Every chart has a footnote:** what the data is and "(synthetic)". EIA credit follows CLAUDE.md rule 11. A chart that plots actual EIA numbers carries "Source: U.S. Energy Information Administration (EIA)". Anything showing synthetic prices, or numbers built from them, carries "Synthetic prices built from the EIA weekly diesel average (benchmark source: U.S. Energy Information Administration)". A chart with both carries both. Nothing may imply EIA endorses the project.
- **No em dashes** in any UI copy.

## 7. Dark mode

The app ships in light mode. The dark look in 06 is only for the README hero image. Dark mode can be a later toggle that swaps the token values, so no component ever hard-codes a color.
