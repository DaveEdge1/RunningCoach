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
