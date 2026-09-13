# Demo notes

## Letter-spelling TTS bug — 3 confirmed cases

Found via a scan of Weave `process_turn` traces (roundtrip ASR transcript)
for 3+ consecutive single-character tokens, a signature of TTS spelling a
word out letter-by-letter instead of pronouncing it. Use these for the demo.

| Drug name | Attempt | WER | ASR heard |
|---|---|---|---|
| Vraylar | 2 | 0.857 | "Here. V r. A y r..." |
| Esketamine | 2 | 1.0 | "Here s, k e. H 2 mean..." |
| Clozapine | 2 | 1.143 | "Your c l, o, h c u, h p..." |

## Additional cases (found via dashboard review, last attempt spells out chars)

Drug name / intended text — WER and exact ASR transcript not pulled yet, but
last attempt in the dashboard shows the same letter-spelling pattern:

- Alectinib — "Your Alectinib prescription is ready for pickup."
- Alfuzosin — "Your Alfuzosin prescription is ready for pickup."
- Amantadine — "Your Amantadine prescription is ready for pickup."
