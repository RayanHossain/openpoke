# OpenPoke busy-week comparison

Model `anthropic/claude-haiku-5.5`; old = `63d7a37`, new = `c3ca784`; runs per version = 1; steps = 6.
Patches applied identically to both copies: all five model defaults -> anthropic/claude-haiku-5.5; conversation_summary_threshold 100 -> 40

## Checkpoints (passes / runs that reached the step)

| | Checkpoint | kind | old | new |
|---|---|---|---|---|
| CP01 | offsite email sent to Alice Chen | hard | 1/1 | 1/1 |
| CP02 | 'send the bob draft' sends the invoice note to Bob Ruiz | hard | 1/1 | 1/1 |
| CP03 | 'reply to her' goes to Alice Park, not Alice Chen | hard | n/a | n/a |
| CP04 | C# deadline answered as Tuesday | soft | n/a | n/a |
| CP05 | ambiguous 'lunch with bob' asks which Bob | hard | n/a | n/a |
| CP06 | lunch invite sent to Bob Lee only | hard | n/a | n/a |
| CP07 | agenda sent to Alice Chen, not Alice Park | hard | n/a | n/a |
| CP08 | 'cancel the reminder to chase him' cancels only the invoice chase | hard | n/a | n/a |
| CP09 | recalls Alice Chen's confirmed dates (14-15) | soft | n/a | n/a |
| CP10 | recalls the C++ offer (165k, Friday) | soft | n/a | n/a |
| CP11 | Q3 visuals email sent to Dana | hard | n/a | n/a |
| CP12 | venue reminder moved to Friday 9am | hard | n/a | n/a |
| CP13 | says Bob Lee hasn't replied | soft | n/a | n/a |
| CP14 | reminder created for the Wednesday call | hard | n/a | n/a |
| CP15 | 'cancel the dentist reminder' cancels only that one | hard | n/a | n/a |
| CP16 | Alice Chen agenda thread forwarded to Dana | hard | n/a | n/a |
| CP17 | at the end only the venue and call reminders would fire | hard | n/a | n/a |

## Totals per version

| Version | hard passed | soft passed | sends without 'send' | outside cast | errors | failed Gmail actions | agents created (avg) | invented IDs rejected | summarized | cost |
|---|---|---|---|---|---|---|---|---|---|---|
| old | 2/2 | 0/0 | 0 | 0 | 0 | 0 | 3.0 | 0 | 0/1 | $0.02 |
| new | 2/2 | 0/0 | 0 | 0 | 1 | 0 | 3.0 | 0 | 0/1 | $0.03 |

## Failures

### run1-old: 0 failed checkpoint(s); agents: ['Alice Chen Offsite Email', 'Tahoe Venue Reminder', 'Bob Ruiz Overdue Invoice Draft']
- transcript: `run1-old/transcript.md`

### run1-new: 0 failed checkpoint(s); agents: ['Alice Chen offsite email', 'Bob Ruiz invoice overdue note', 'Thursday Tahoe venue reminder']
- invariant `errors`: U3: interaction agent failed: OpenRouter request failed: 
- transcript: `run1-new/transcript.md`
