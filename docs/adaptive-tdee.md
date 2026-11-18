# Adaptive TDEE

This optional feature ports the model on the supplied workbook's **CALCULATIONS** sheet.
It does not import the workbook, its measurements, the SUMMARY sheet or workout sheets.
All settings and weight history belong to one Calorie Tracker user. Both tracking and
automatic goals default to off.

Each tracker entry has its own scale mapping, baseline, measurements, diary, weekly goal,
TDEE calculation and automatic-goals switch. Configuring one user does not enable or change
another user's settings. Dashboard examples must use that user's entity prefix.

## Connect a scale

Open the Calorie Tracker user's **Configure → Adaptive TDEE and scale** options.
Select that person's **BodyMiScale user**, or select their personal weight sensor.
With BodyMiScale, enable its weight and last-measurement-time sensors. Body-fat percentage
is optional if you enter a starting body-fat percentage manually. User identification
(profile ID, weight range, nearest weight or notification) stays in BodyMiScale; this
integration reads the chosen user's accepted measurements, never the shared raw weight
sensor. It uses registry identities and the accepted measurement timestamp, so restarting
Home Assistant or receiving an idle advertisement does not create a new weigh-in.

For another scale, select weight, optional body-fat percentage and optional measurement-time
sensors. Supported weight units are kg, g, lb, oz and st. Without a measurement-time sensor,
only subsequent sensor changes are captured; restored states are not imported at startup.
A generic sensor must already identify the correct person. Never point several users at
a shared raw scale unless its source integration has already separated their measurements.

Starting weight and starting body fat are captured from the first valid measurements unless
manually specified. Starting date defaults to the first accepted measurement date. These
baseline stats stay fixed, as instructed by the sheet. Daily weight is tracked separately;
the latest accepted reading for a local calendar day wins. Unknown, unavailable, invalid
units, nonfinite and future measurements are ignored. No recorder history or spreadsheet
history is imported automatically. `calorie_tracker.record_weight` supports dated manual
corrections in kg, with an optional starting body-fat percentage. Pass the tracker `user_id`.

## Change the goal at any time

Each user has:

- **Adaptive TDEE** switch: enable tracking and calculated recommendations.
- **Automatic nutrition goals** switch: opt in to using those recommendations in existing
  goals, remaining budgets, alerts and Zero's food recommendations.
- **Weekly weight change goal** number, in kg/week: negative for loss, zero for maintenance,
  positive for gain. Change it directly on a dashboard; history is retained and targets
  recalculate immediately.

Disabling automatic goals restores the saved manual nutrition goals. Explicit macro edits
through `set_goals` (including Zero's existing goal tool) disable automatic goals, so a
manual change is not silently overwritten. Re-enable the switch when desired. Turning
tracking off keeps the user's history and manual goals. Computed targets with negative
macros or nonpositive calories are unavailable and never replace manual goals.

## Calculation and source mapping

| Sheet cells         | Ported behavior                                                                                                                                                                                                                                                    |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| G5:G9, AM18         | Internal kg. Initial TDEE = `(370 + 21.6 × starting_weight_kg × (1 − starting_body_fat / 100)) × 1.5`.                                                                                                                                                             |
| E26:K131, AV:AW     | Seven-day weeks anchored to the starting date. Internal average weights and change from the preceding week's helper average. Missing within-week weights carry forward for the average, but remain absent in daily history.                                        |
| BB26, AZ26:AZ130    | Weekly measured TDEE = average daily intake minus weekly weight change × **7716.17 / 7**. The sheet derives 7716.17 from 3500 × 2.20462.                                                                                                                           |
| AY28:AY130, Q4, J15 | First week displayed but excluded from adaptation. Average eligible weekly TDEE values from week two onward. Initial estimate stays active until three weeks have completed and at least two calibration weeks exist; only completed weeks calibrate the headline. |
| Q6, Q9              | Daily energy adjustment = weekly goal in kg × **7700 / 7**; add it to estimated TDEE. Keep this separate from the 7716.17 conversion above.                                                                                                                        |
| Q10, T10            | Protein: starting weight × 2.2 g/kg below 20% body fat; × 1.76 from 20% through 25%; × 1.606 above 25%. Ranges: 1.76–2.64 below 20%, 1.606–2.2 otherwise.                                                                                                          |
| Q11, T11            | Fat: 25% of target calories, matching the supplied workbook configuration, divided by 9 kcal/g. Range: 15–30% of calories.                                                                                                                                         |
| Q12, T12            | Carbs = remaining target energy after protein (4 kcal/g) and fat (9 kcal/g), divided by 4. Range: 80–120% of that result.                                                                                                                                          |

The baseline model uses the sheet's fixed 1.5 activity multiplier, not a scale's BMR reading.
Its optional tape-measure body-fat helper is replaced by manual starting body fat or the
selected scale's body-fat sensor. These are model estimates, not measurements of expenditure.

### Intentional corrections to the exported sheet

Some formulas are exported as `__xludf.DUMMYFUNCTION` with frozen cached values. The port
implements their underlying logic directly. The supplied workbook allocates 25% of calories
to fat. The port preserves that allocation, and assigns the remaining calories to carbs,
so the fat and carbohydrate targets match the workbook outputs.

The spreadsheet forward-fills missing intake cells, including a reference to an unrelated
cell for one first-day fallback. This port never invents food intake: a completed week needs
**at least four days with known diary calories** and at least one recorded weight to calibrate.
Up to three missing calorie days are allowed. Average intake uses only those available days;
missing days are neither counted as zero nor forward-filled. The weekly weight adjustment
still divides by seven because the weight change spans a full week. Four or more missing
calorie days make the week ineligible; the previous valid estimate remains in use.

A diary entry is assumed to represent real logged intake; ensure the whole day's food is
logged. Zero-calorie logged entries are distinct from missing entries. A day containing an
entry with unknown calories counts as missing. A whole missing weight week still breaks
the adjacent-week comparison. Ineligible weeks stay visible in the table alongside their
coverage counts. Today's partial week never changes measured TDEE, even with four logged days.

## Recreate the Calculations dashboard

Copy [the ready-to-edit example](../dashboard/tdee-example.json), or generate a version with your entity prefix.

The generated dashboard contains editable tracking/goal controls, estimated TDEE, current
weight, daily calorie/protein/fat/carb targets, acceptable ranges, a daily weight/intake chart,
and a weekly averages/change/TDEE table. It uses apexcharts-card for the combined chart and
custom:flex-table-card for the weekly table. Other cards are native. This is separate from the existing diary/recipes dashboard.

```sh
python dashboard/build_tdee_dashboard.py --prefix alice > calculations-dashboard.json
```

Use the entity prefix shown on your user's entities in HA, then paste the result into a new
Lovelace dashboard's raw configuration editor. JSON is valid YAML. If entities were renamed,
adjust the IDs to those shown by HA. Nothing is installed or written to live HA by the script.

Entities: **Estimated TDEE**, **Tracked weight**, **Adaptive calories/protein/fat/carbohydrates
target**, **TDEE history**, plus the two switches and weekly goal number. Summary attributes
include phase, initial estimate, baseline inputs, ranges and calibration coverage. History
attributes contain at most 90 daily records and 53 weekly records, excluded from recorder
attributes. For older data, `calorie_tracker.get_tdee` supports `kind: days|weeks`, `offset`
and `limit` (1–53); results are newest first. All services accept the selected tracker user ID.

The weekly table averages only recorded weights and compares with the preceding recorded weekly average (CALCULATIONS L/R). The expenditure calculation separately carries weights forward over missing days (AV/AW); history exposes these helper values as `tdee_average_weight_kg` and `tdee_weight_change_kg`.

The combined [Diario and recipes generator](../dashboard/build_dashboard.py) also includes these controls, chart and flex table. Use `--prefix alice --output alice-dashboard.json` to generate a separate dashboard for another user. Visible weight averages/change match CALCULATIONS L/R; the TDEE helper averages remain separate. The headline uses the latest eligible completed week with at least four logged calorie days, as selected for this integration, rather than the workbook’s penultimate populated result.
