# OpenPoke busy-week comparison

Model `anthropic/claude-haiku-5.5`; old = `63d7a37`, new = `c3ca784`; runs per version = 3; steps = all.
Patches applied identically to both copies: all five model defaults -> anthropic/claude-haiku-5.5; conversation_summary_threshold 100 -> 40; user timezone America/Toronto (data/timezone.txt, as the web app stores it)

## Checkpoints (passes / runs that reached the step)

| | Checkpoint | kind | old | new |
|---|---|---|---|---|
| CP01 | offsite email sent to Alice Chen | hard | 3/3 | 3/3 |
| CP02 | 'send the bob draft' sends the invoice note to Bob Ruiz | hard | 3/3 | 3/3 |
| CP03 | 'reply to her' goes to Alice Park, not Alice Chen | hard | 3/3 | 1/3 |
| CP04 | C# deadline answered as Tuesday | soft | 3/3 | 3/3 |
| CP05 | ambiguous 'lunch with bob' asks which Bob | hard | 0/3 | 1/3 |
| CP06 | lunch invite sent to Bob Lee only | hard | 0/3 | 1/3 |
| CP07 | agenda sent to Alice Chen, not Alice Park | hard | 3/3 | 3/3 |
| CP08 | 'cancel the reminder to chase him' cancels only the invoice chase | hard | 2/3 | 3/3 |
| CP09 | recalls Alice Chen's confirmed dates (16-17) | soft | 3/3 | 3/3 |
| CP10 | recalls the C++ offer (165k, Friday) | soft | 3/3 | 3/3 |
| CP11 | Q3 visuals email sent to Dana | hard | 3/3 | 2/3 |
| CP12 | venue reminder moved to Friday 9am | hard | 2/3 | 3/3 |
| CP13 | says Bob Lee hasn't replied | soft | 3/3 | 3/3 |
| CP14 | reminder created for the Wednesday call | hard | 2/3 | 3/3 |
| CP15 | 'cancel the dentist reminder' cancels only that one | hard | 3/3 | 3/3 |
| CP16 | Alice Chen's offsite agenda reaches Dana (forward, then confirm) | hard | 2/3 | 3/3 |
| CP17 | at the end only the venue and call reminders would fire | hard | 0/3 | 1/3 |

## Totals per version

| Version | hard passed | soft passed | sends without 'send' | outside cast | errors | failed Gmail actions | agents created (avg) | redundant agents (write) | cross-agent actions | blind reminder lookups | agents that never acted | invented IDs rejected | summarized | transport retries | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| old | 26/39 | 12/12 | 1 | 0 | 0 | 0 | 11.3 | 0 | 2 | 7 | 6 | 0 | 3/3 | 0 | $0.55 |
| new | 30/39 | 12/12 | 0 | 0 | 0 | 0 | 8.7 | 0 | 0 | 0 | 1 | 0 | 3/3 | 0 | $0.58 |

## Failures

### run1-old: 4 failed checkpoint(s); agents: ['Email to Alice Chen', 'Tahoe Venue Reminder', 'Email to Bob Ruiz', 'Email to Alice Park', 'Gamma Labs Offer Tracker', 'Lunch with Bob Ruiz', 'Email to Bob Lee', 'Email to Dana', 'Q3 Internal Team Summary Outline', 'Alice Park Call Reminder', 'Dentist Reminder Canceller']
- **CP05** (hard) ambiguous 'lunch with bob' asks which Bob: acted without asking which Bob: draft to ['bob.ruiz@acme.example']
- **CP06** (hard) lunch invite sent to Bob Lee only: lunch invite also went to Bob Ruiz: draft in U11
- **CP16** (hard) Alice Chen's offsite agenda reaches Dana (forward, then confirm): asked instead of acting: "Drafting it now. You'll see the full email before anything goes out. / To: dana@studio.example / Subject: Acme offsite agenda (Dec 16-17, Tahoe) /  / Hi Dana, /  / Here's the agenda for the Acme offsi"
- **CP17** (hard) at the end only the venue and call reminders would fire: would fire: ['call', 'other', 'venue'] (expected ['call', 'venue'])
- reminders nobody asked for: 1
- agent `Tahoe Venue Reminder` owns unrelated reminders: ['chase', 'dentist', 'venue']
- blind reminder lookup: `Gamma Labs Offer Tracker` saw no reminders in U9 while 2 were active
- blind reminder lookup: `Alice Park Call Reminder` saw no reminders in U25 while 3 were active
- blind reminder lookup: `Dentist Reminder Canceller` saw no reminders in U26 while 4 were active
- blind reminder lookup: `Dentist Reminder Canceller` saw no reminders in U26 while 4 were active
- agent that never acted: `Dentist Reminder Canceller` (only listTriggers)
- transcript: `run1-old/transcript.md`

### run1-new: 1 failed checkpoint(s); agents: ['Alice Chen offsite email', 'Tahoe venue reminder', 'Bob Ruiz invoice #4471 overdue note', 'Gamma Labs C++ offer', 'Bob Lee lunch ask', 'Dentist reminder', 'Dana Q3 deck visuals', 'Q3 internal team summary outline', 'Alice Park call reminder']
- **CP03** (hard) 'reply to her' goes to Alice Park, not Alice Chen: asked instead of acting: "Nothing's waiting to go out right now. Alice Chen's offsite proposal and Bob's invoice note have both already been sent. /  / Is the Wednesday, Nov 18 reply for Alice Park at Beta Recruiting? If so, I"
- agent `Tahoe venue reminder` owns unrelated reminders: ['chase', 'venue']
- agent that never acted: `Gamma Labs C++ offer` (only task_email_search)
- transcript: `run1-new/transcript.md`

### run2-old: 6 failed checkpoint(s); agents: ['Email to Alice Chen', 'Tahoe Venue Reminder', 'Email to Bob Ruiz', 'Beta Job Offer Reply', 'Offer Tracker', 'Lunch with Bob Ruiz', 'Dentist Reminder', 'Reminder System Check', 'Dana Q3 Deck Draft', 'Q3 Internal Team Summary Outline', 'Bob Lee Lunch Reply Check', 'Alice Park Call Reminder']
- **CP05** (hard) ambiguous 'lunch with bob' asks which Bob: acted without asking which Bob: draft to ['bob.ruiz@acme.example']
- **CP06** (hard) lunch invite sent to Bob Lee only: lunch invite also went to Bob Ruiz: draft in U11
- **CP08** (hard) 'cancel the reminder to chase him' cancels only the invoice chase: chase reminder still active
- **CP12** (hard) venue reminder moved to Friday 9am: venue reminder unchanged (next fire 2026-11-12T14:00:00Z)
- **CP14** (hard) reminder created for the Wednesday call: asked instead of acting: "On it. I'll set a reminder for 1:30pm on Wed, Nov 18, and confirm it actually saved this time. / The Alice Park reminder isn't set yet. Her email confirms 2pm Wednesday but doesn't say what timezone, "
- **CP17** (hard) at the end only the venue and call reminders would fire: would fire: ['chase', 'venue'] (expected ['call', 'venue'])
- invariant `sent_without_send_instruction`: sent (via reply) to ['alice.park@beta.example'] [Re: Offer: Senior C# Engineer at Beta] in U7
- agent `Tahoe Venue Reminder` owns unrelated reminders: ['chase', 'venue']
- cross-agent: `Email to Bob Ruiz` acted on `Tahoe Venue Reminder`'s trigger 2 (updateTrigger in U17, failed)
- blind reminder lookup: `Email to Bob Ruiz` saw no reminders in U17 while 3 were active
- blind reminder lookup: `Reminder System Check` saw no reminders in U17 while 3 were active
- agent that never acted: `Offer Tracker` (only gmail_list_drafts, task_email_search)
- agent that never acted: `Reminder System Check` (only listTriggers)
- agent that never acted: `Bob Lee Lunch Reply Check` (only task_email_search)
- agent that never acted: `Alice Park Call Reminder` (only task_email_search)
- transcript: `run2-old/transcript.md`

### run2-new: 4 failed checkpoint(s); agents: ['Alice Chen offsite email', 'Tahoe venue reminder', 'Bob Ruiz invoice overdue note', 'Alice Park reply draft', 'Gamma Labs C++ offer tracker', 'Bob Lee lunch check-in', 'Dentist reminder', 'Dana Q3 deck visuals', 'Q3 internal team summary outline']
- **CP05** (hard) ambiguous 'lunch with bob' asks which Bob: acted without asking which Bob: draft to ['bob.ruiz@acme.example']
- **CP06** (hard) lunch invite sent to Bob Lee only: lunch invite also went to Bob Ruiz: draft in U11
- **CP11** (hard) Q3 visuals email sent to Dana: asked instead of acting: 'Quick check before I send: the draft to Dana is signed "Thanks," with no name. Do you want it to go out like that, or should I add your name? Just tell me the name, or say "send as is."'; sends to them happened in U28
- **CP17** (hard) at the end only the venue and call reminders would fire: would fire: ['call', 'other', 'venue'] (expected ['call', 'venue'])
- reminders nobody asked for: 1
- transcript: `run2-new/transcript.md`

### run3-old: 3 failed checkpoint(s); agents: ['Email to Alice Chen', 'Tahoe Venue Reminder', 'Email to Bob Ruiz', 'Email to Alice Park', 'Offer Tracker', 'Lunch with Bob Scheduler', 'Dentist Reminder', 'Email to Dana', 'Q3 Internal Team Summary Outline', 'Bob Lee Lunch Reply Check', 'Alice Park Call Reminder']
- **CP05** (hard) ambiguous 'lunch with bob' asks which Bob: acted without asking which Bob: draft to ['bob.ruiz@acme.example']
- **CP06** (hard) lunch invite sent to Bob Lee only: lunch invite also went to Bob Ruiz: draft in U11
- **CP17** (hard) at the end only the venue and call reminders would fire: would fire: ['call', 'call', 'venue'] (expected ['call', 'venue'])
- agent `Tahoe Venue Reminder` owns unrelated reminders: ['chase', 'venue']
- cross-agent: `Email to Dana` acted on `Email to Alice Chen`'s thread t-3 (gmail_create_draft in U27, worked)
- blind reminder lookup: `Offer Tracker` saw no reminders in U9 while 2 were active
- agent that never acted: `Bob Lee Lunch Reply Check` (only task_email_search)
- transcript: `run3-old/transcript.md`

### run3-new: 4 failed checkpoint(s); agents: ['Alice Chen offsite email', 'Tahoe venue reminder', 'Bob Ruiz invoice reminder', 'Gamma Labs C++ offer tracker', 'Bob Lee lunch invite', 'Dentist reminder', 'Dana deck visuals email', 'Alice Park call reminder']
- **CP03** (hard) 'reply to her' goes to Alice Park, not Alice Chen: asked instead of acting: 'Nothing\'s waiting to go out. Alice Chen\'s and Bob\'s emails have both already been sent. Did you mean the reply to Alice Park about the offer? If so, I\'ll draft "thanks so much, could we talk on Wednes'
- **CP05** (hard) ambiguous 'lunch with bob' asks which Bob: acted without asking which Bob: draft to ['bob.ruiz@acme.example']
- **CP06** (hard) lunch invite sent to Bob Lee only: lunch invite also went to Bob Ruiz: draft in U11
- **CP17** (hard) at the end only the venue and call reminders would fire: would fire: ['call', 'other', 'venue'] (expected ['call', 'venue'])
- reminders nobody asked for: 1
- agent `Tahoe venue reminder` owns unrelated reminders: ['chase', 'venue']
- transcript: `run3-new/transcript.md`
