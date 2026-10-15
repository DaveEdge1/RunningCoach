# Reference data

## ncaa_altitude_2009.csv
The percentage that altitude adds to race times, by venue. Derived from the NCAA's
**2009 NCAA Championship Qualifying Time Altitude Adjustments** (USTFCCCA):
https://www.ustfccca.org/assets/ncaa-info/2009-outdoor-alt-adjust.pdf

For each venue and event, the sheet lists a qualifying time at that altitude and the
seconds allowed for altitude. `pct = allowance / (time − allowance) × 100`, averaged over
the six columns (Division I–III, women and men), which agree to within about 0.05%.
`altitude_ft` is the venue altitude as printed. The sea-level row (0%) is included.

## Daniels altitude rule (config.json → altitude.daniels_sec_per_mile_per_1000ft)
Threshold and interval paces are about 4–5 s/mi slower per 1,000 ft above 3,000 ft
(15–18 s/mi at 7,000 ft). Source: Jack Daniels, "How Do You Adjust Threshold Pace At Altitude?",
V.O2 News, March 2011: https://news.vdoto2.com/2011/03/ask-a-coach-how-do-you-adjust-threshold-pace-at-altitude/

## Race recovery (config.json → aet_pace.recovery_km_per_day)
One easy day for every 3 km raced before returning to training (15K → 5 days, half marathon → 7),
so a 10K takes 3. Pace at AeT leaves those days out of its trend. Source: Jack Daniels,
*Daniels' Running Formula*, 4th ed. (Human Kinetics), excerpt:
https://us.humankinetics.com/blogs/excerpt/sample-running-program-from-daniels-running-formula

## Load ramp (config.json → load_ramp)
Acute:chronic workload ratio: last 7 days of load ÷ the weekly average of the last 28. 0.8–1.3 is the
"sweet spot" and ≥ 1.5 the "danger zone" for injury. Source: Gabbett TJ, "The training–injury prevention
paradox: should athletes be training smarter and harder?", Br J Sports Med 2016;50:273–280:
https://pmc.ncbi.nlm.nih.gov/articles/PMC4789704 (acute = 1 week; chronic = rolling 3–6 weeks).
Critique: Impellizzeri FM et al., "Acute:Chronic Workload Ratio: Conceptual Issues and Fundamental
Pitfalls", Int J Sports Physiol Perform 2020;15:907–913. The site treats it as a guide, not a risk score.

## Aerobic decoupling (config.json → decoupling)
Pa:HR: (speed/HR in the first half − speed/HR in the second half) ÷ first half. Under 5% means aerobic
endurance is sound for that duration; 5–10% shows limits or fatigue. Source: Joe Friel, "Aerobic
Endurance and Decoupling", TrainingPeaks:
https://www.trainingpeaks.com/learn/articles/aerobic-endurance-and-decoupling/

## Best efforts (config.json → best_efforts)
Efforts and races are compared on grade-adjusted time (Minetti et al. 2002, J Appl Physiol 93:1039, the same
model as pace at AeT), not net drop. The World Athletics road-record rule (net drop ≤ 1 m/km,
https://worldathletics.org/records/recordsrules) was tried first and dropped: on 2026-09-03 a 2-mile span
dropped 11 ft net but climbed 193 ft, and grade-adjusted it was 30 s faster than the clock (harder than flat),
yet the rule rejected it as downhill. Efforts with a net grade over 5% are excluded, since Minetti's running
model fits walking on steep ground poorly.

## Planned hike load (config.json → cross_training.default_load_per_min.hike)
Until hikes are recorded with HR, a planned hike counts 1 load per minute (the Z1 weight). Derivation for the
Grand Canyon trip (79.8 kg, 35-lb / 15.9 kg pack; Tanner 10 mi down in 4 h, New Hance 8.5 mi up 4,500 ft in 6 h):
- Pandolf KB, Givoni B, Goldman RF, "Predicting energy expenditure with loads while standing or walking very
  slowly", J Appl Physiol 1977;43:577–581: M = 1.5W + 2.0(W+L)(L/W)² + η(W+L)(1.5V² + 0.35VG), terrain factor
  η 1.2–1.5. The descent uses G = 0, an upper bound (Pandolf doesn't model downhill).
- VO2 = M / 20.1 kJ per L O2, as % of VO2max taken as VDOT at Flagstaff altitude (42.2).
- Swain DP et al., "Target heart rates for the development of cardiorespiratory fitness", Med Sci Sports Exerc
  1994;26:112–116: %HRmax = 0.64 × %VO2max + 37.
Result: Tanner ~107–113 bpm, New Hance ~119–127 bpm, both Z1 (< 135). HR drift late in a 6-hour climb may
push some time into Z2, so the climb's load is likely a little low.
