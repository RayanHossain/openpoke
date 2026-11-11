# OpenPoke busy-week comparison

Model `anthropic/claude-haiku-5.5`; old = `63d7a37`, new = `c3ca784`; runs per version = 1; steps = all.
Patches applied identically to both copies: all five model defaults -> anthropic/claude-haiku-5.5; conversation_summary_threshold 100 -> 40

## Checkpoints (passes / runs that reached the step)

| | Checkpoint | kind | old | new |
|---|---|---|---|---|
| CP01 | offsite email sent to Alice Chen | hard | 1/1 | 1/1 |
| CP02 | 'send the bob draft' sends the invoice note to Bob Ruiz | hard | 1/1 | 1/1 |
| CP03 | 'reply to her' goes to Alice Park, not Alice Chen | hard | 1/1 | 0/1 |
| CP04 | C# deadline answered as Tuesday | soft | 1/1 | 1/1 |
| CP05 | ambiguous 'lunch with bob' asks which Bob | hard | 0/1 | 1/1 |
| CP06 | lunch invite sent to Bob Lee only | hard | 0/1 | 1/1 |
| CP07 | agenda sent to Alice Chen, not Alice Park | hard | 1/1 | 0/1 |
| CP08 | 'cancel the reminder to chase him' cancels only the invoice chase | hard | 0/1 | 1/1 |
| CP09 | recalls Alice Chen's confirmed dates (14-15) | soft | 1/1 | 1/1 |
| CP10 | recalls the C++ offer (165k, Friday) | soft | 0/1 | 1/1 |
| CP11 | Q3 visuals email sent to Dana | hard | 1/1 | 1/1 |
| CP12 | venue reminder moved to Friday 9am | hard | 1/1 | 1/1 |
| CP13 | says Bob Lee hasn't replied | soft | 1/1 | 1/1 |
| CP14 | reminder created for the Wednesday call | hard | 1/1 | 0/1 |
| CP15 | 'cancel the dentist reminder' cancels only that one | hard | 1/1 | 1/1 |
| CP16 | Alice Chen agenda thread forwarded to Dana | hard | 0/1 | 0/1 |
| CP17 | at the end only the venue and call reminders would fire | hard | 0/1 | 0/1 |

## Totals per version

| Version | hard passed | soft passed | sends without 'send' | outside cast | errors | failed Gmail actions | agents created (avg) | invented IDs rejected | summarized | transport retries | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| old | 8/13 | 3/4 | 0 | 0 | 0 | 0 | 10.0 | 0 | 1/1 | 13 | $0.20 |
| new | 8/13 | 4/4 | 0 | 0 | 0 | 0 | 8.0 | 0 | 1/1 | 5 | $0.18 |

## Failures

### run1-old: 6 failed checkpoint(s); agents: ['Email to Alice Chen', 'Tahoe Venue Reminder', 'Email to Bob Ruiz', 'Reply to Alice Park Offer', 'Gamma Labs Offer Tracker', 'Lunch with Bob Ruiz', 'Email to Bob Lee', 'Dentist Reminder', 'Email to Dana', 'Q3 Internal Team Summary Outline']
- **CP05** (hard) ambiguous 'lunch with bob' asks which Bob: acted without asking which Bob: draft to ['bob.ruiz@acme.example']
- **CP06** (hard) lunch invite sent to Bob Lee only: lunch invite also went to Bob Ruiz: draft in U11
- **CP08** (hard) 'cancel the reminder to chase him' cancels only the invoice chase: no invoice-chase reminder exists to cancel
- **CP10** (soft) recalls the C++ offer (165k, Friday): Checking the Gamma Labs offer status now. Last I had, I couldn't find the offer email, so I'm searching again in case it's arrived since. / Still can't find the Gamma Labs offer email after four searches. The only offer I found is Alice Park's Beta Senior C# offer ($140k base plus equity, reply by n
- **CP16** (hard) Alice Chen agenda thread forwarded to Dana: nothing forwarded
- **CP17** (hard) at the end only the venue and call reminders would fire: would fire: ['call', 'other', 'venue'] (expected ['call', 'venue'])
- transcript: `run1-old/transcript.md`

### run1-new: 5 failed checkpoint(s); agents: ['Alice Chen offsite proposal', 'Tahoe venue reminder', 'Bob Ruiz overdue invoice', 'Gamma Labs C++ offer', 'Bob Lee lunch check-in', 'Dentist reminder', 'Dana Q3 deck visuals', 'Q3 internal team summary outline']
- **CP03** (hard) 'reply to her' goes to Alice Park, not Alice Chen: no send to alice.park@beta.example in U8 (nothing sent)
- **CP07** (hard) agenda sent to Alice Chen, not Alice Park: no send to alice.chen@acme.example in U15 (nothing sent); sends to them happened in U2
- **CP14** (hard) reminder created for the Wednesday call: no new active reminder for the call
- **CP16** (hard) Alice Chen agenda thread forwarded to Dana: nothing forwarded
- **CP17** (hard) at the end only the venue and call reminders would fire: would fire: ['other', 'venue'] (expected ['call', 'venue'])
- transcript: `run1-new/transcript.md`
