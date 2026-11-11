"""The "busy week": one long, overlapping conversation run identically on both versions.

Designed to stress routing by name: two Alices, two Bobs, two job offers (C++ / C#),
two "Q3 report" tasks, four reminders, vague follow-ups many turns later ("reply to
her", "send the bob draft", "cancel the reminder to chase him", "forward alice's
agenda email to dana"), and new email arriving mid-conversation. With the
summarization threshold lowered to 40, the early turns are compressed into a
summary partway through, so later follow-ups can't lean on the verbatim history.

Steps run in order. "user" steps go through InteractionAgentRuntime.execute (as
routes/chat.py does); "inbound" steps deliver an email to the fake inbox and then
notify the interaction agent exactly as the importance watcher does
(services/gmail/importance_watcher.py: "Important email watcher notification:\\n...").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from fake_inbox import USER_ADDRESS, FakeInbox, Message

# The user's timezone, written into both copies' data/timezone.txt (the web app
# normally detects it from the browser and stores it the same way).
TIMEZONE = "America/Toronto"

# Dates are relative to the real "today" so they are always in the future: an
# offsite five weeks out, and a call on an explicit, dated Wednesday.
_TODAY = datetime.now(ZoneInfo(TIMEZONE)).date()
_OFFSITE = _TODAY + timedelta(days=35)
OFFSITE_DAYS = (_OFFSITE.day, (_OFFSITE + timedelta(days=1)).day)
OFFSITE_TEXT = f"{_OFFSITE:%b} {OFFSITE_DAYS[0]}-{OFFSITE_DAYS[1]}"
_CALL = _TODAY + timedelta(days=(2 - _TODAY.weekday()) % 7 or 7)  # next Wednesday, never today
if (_CALL - _TODAY).days < 5:
    _CALL += timedelta(days=7)
CALL_TEXT = f"Wednesday {_CALL:%b} {_CALL.day}"

ALICE_CHEN = "alice.chen@acme.example"
ALICE_PARK = "alice.park@beta.example"
BOB_RUIZ = "bob.ruiz@acme.example"
BOB_LEE = "bob.lee@friends.example"
DANA = "dana@studio.example"

CONTACTS = [
    {"name": "Alice Chen", "email": ALICE_CHEN},
    {"name": "Alice Park", "email": ALICE_PARK},
    {"name": "Bob Ruiz", "email": BOB_RUIZ},
    {"name": "Bob Lee", "email": BOB_LEE},
    {"name": "Dana Ortiz", "email": DANA},
]
CAST = {c["email"] for c in CONTACTS}


def build_inbox() -> FakeInbox:
    inbox = FakeInbox(contacts=CONTACTS)
    old = [
        ("t-retro", f"Alice Chen <{ALICE_CHEN}>", [USER_ADDRESS], "Q2 retro notes",
         "Thanks for joining the Q2 retro. Notes attached. Let's sync next quarter.", ["INBOX"],
         datetime(2026, 9, 2, 16, 0, tzinfo=timezone.utc)),
        ("t-invoice", f"Me <{USER_ADDRESS}>", [BOB_RUIZ], "Invoice #4471",
         "Hi Bob, attached is invoice #4471 for the September consulting work, due in 30 days.", ["SENT"],
         datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)),
        ("t-q2deck", f"Dana Ortiz <{DANA}>", [USER_ADDRESS], "Q2 deck feedback",
         "Here are my notes on the Q2 deck visuals. Happy to help with Q3 too.", ["INBOX"],
         datetime(2026, 7, 20, 11, 0, tzinfo=timezone.utc)),
        ("t-news", "Tahoe Venues <news@tahoevenues.example>", [USER_ADDRESS], "Fall offsite packages",
         "Book your fall team offsite in Tahoe. Lakeside lodges available October.", ["INBOX"],
         datetime(2026, 10, 30, 7, 0, tzinfo=timezone.utc)),
        ("t-bobl-old", f"Bob Lee <{BOB_LEE}>", [USER_ADDRESS], "climbing sunday?",
         "Want to go climbing Sunday? Same gym as last time.", ["INBOX"],
         datetime(2026, 10, 18, 19, 0, tzinfo=timezone.utc)),
    ]
    for i, (thread, sender, to, subject, body, labels, ts) in enumerate(old):
        msg = Message(id=f"m-old{i}", thread_id=thread, sender=sender, to=to, subject=subject,
                      body=body, ts=ts, labels=labels)
        inbox.messages[msg.id] = msg
    return inbox


def U(step_id: str, text: str) -> Dict[str, Any]:
    return {"id": step_id, "kind": "user", "text": text}


def I(step_id: str, *, sender: str, subject: str, body: str, reply_to: str, thread_keyword: str,
      notification: str) -> Dict[str, Any]:
    return {"id": step_id, "kind": "inbound", "sender": sender, "subject": subject, "body": body,
            "reply_to": reply_to, "thread_keyword": thread_keyword, "notification": notification}


STEPS: List[Dict[str, Any]] = [
    U("U1", f"Email Alice Chen ({ALICE_CHEN}) to propose the Acme offsite on {OFFSITE_TEXT} in Tahoe. Keep it short."),
    U("U2", "looks good, send it"),
    U("U3", "Also remind me Thursday at 9am to book the Tahoe venue."),
    U("U4", f"Draft a note to Bob Ruiz ({BOB_RUIZ}) saying invoice #4471 is now 30 days overdue."),
    U("U5", "And remind me Friday at 10am to chase Bob about the invoice if he hasn't paid."),
    U("U6", "send the bob draft"),
    I("I1", sender=f"Alice Park <{ALICE_PARK}>", subject="Offer: Senior C# Engineer at Beta",
      body=("Hi! We're thrilled to offer you the Senior C# Engineer role at Beta. Base salary is $140k "
            "plus equity. Please let us know by next Tuesday. Happy to talk anytime. - Alice Park, Beta Recruiting"),
      reply_to="", thread_keyword="",
      notification=("Alice Park (alice.park@beta.example) from Beta Recruiting sent a job offer, "
                    "'Offer: Senior C# Engineer at Beta': $140k base plus equity, and wants an answer by next Tuesday.")),
    U("U7", f"reply to her: thanks so much, could we talk on {CALL_TEXT}?"),
    U("U8", "send it"),
    U("U9", "Also keep track of the C++ offer from Gamma Labs: 165k base, deadline is Friday."),
    U("U10", "what's the deadline on the C# offer?"),
    U("U11", "set up lunch with bob tomorrow?"),
    U("U12", f"bob lee, my friend ({BOB_LEE}). ask if he's free tomorrow at noon"),
    U("U13", "send it"),
    I("I2", sender=f"Alice Chen <{ALICE_CHEN}>", subject="Re: Acme offsite",
      body=f"{OFFSITE_TEXT} works for us! Could you send the agenda by Friday? - Alice",
      reply_to=ALICE_CHEN, thread_keyword="offsite",
      notification=(f"Alice Chen (alice.chen@acme.example) replied about the Acme offsite: {OFFSITE_TEXT} works, "
                    "and she asked for the agenda by Friday.")),
    U("U14", "draft the agenda email back to alice: day 1 strategy, day 2 team building"),
    U("U15", "send"),
    U("U16", "remind me at 3pm tomorrow about the dentist"),
    I("I3", sender=f"Bob Ruiz <{BOB_RUIZ}>", subject="Re: Invoice #4471",
      body="Paid invoice #4471 today, sorry for the delay! - Bob",
      reply_to=BOB_RUIZ, thread_keyword="4471",
      notification="Bob Ruiz (bob.ruiz@acme.example) says invoice #4471 was paid today."),
    U("U17", "great, cancel the reminder to chase him"),
    U("U18", f"For the Q3 report: draft an email to Dana ({DANA}) asking for the deck visuals by Friday."),
    U("U19", "Also start a separate Q3 report for the internal team summary - just outline 3 sections, no emails."),
    U("U20", "what did alice say about the offsite dates?"),
    U("U21", "what's the status of the C++ offer?"),
    U("U22", "send dana the draft"),
    U("U23", "move the venue booking reminder to Friday 9am instead"),
    U("U24", "did bob lee reply about lunch?"),
    I("I4", sender=f"Alice Park <{ALICE_PARK}>", subject="Re: Offer: Senior C# Engineer at Beta",
      body=f"{CALL_TEXT} at 2pm works. I'll call you then. - Alice",
      reply_to=ALICE_PARK, thread_keyword="offer",
      notification=f"Alice Park (alice.park@beta.example) confirmed a call on {CALL_TEXT} at 2pm about the Beta offer."),
    U("U25", "add a reminder 30 minutes before that call"),
    U("U26", "cancel the dentist reminder"),
    U("U27", "forward alice's agenda email to dana"),
    U("U28", "yes, send it to dana"),
]

# What a correct assistant does at each step (shown in the demo diff next to what happened).
IDEAL: Dict[str, str] = {
    "U1": "Draft a short offsite proposal to Alice Chen and show it; don't send yet.",
    "U2": "Send that draft to Alice Chen.",
    "U3": "Create a Thursday 9am reminder to book the Tahoe venue.",
    "U4": "Draft the overdue-invoice note to Bob Ruiz and show it; don't send yet.",
    "U5": "Create a Friday 10am reminder to chase Bob Ruiz, ideally owned by the invoice task's agent.",
    "U6": "Send the Bob Ruiz invoice draft (not any other draft).",
    "I1": "Tell the user about Alice Park's job offer ($140k, decide by Tuesday).",
    "U7": "Draft the reply to Alice Park (the sender of the email that just arrived) and show it.",
    "U8": "Send the reply to Alice Park.",
    "U9": "Record the Gamma Labs C++ offer (165k, deadline Friday). Don't create a reminder; none was asked for.",
    "U10": "Answer: the C# offer deadline is Tuesday.",
    "U11": "Ask which Bob: Bob Ruiz (finance) or Bob Lee (friend). Don't draft anything yet.",
    "U12": "Draft the lunch invite to Bob Lee and show it.",
    "U13": "Send the lunch invite to Bob Lee only.",
    "I2": "Tell the user Alice Chen confirmed the dates and wants the agenda.",
    "U14": "Draft the agenda reply to Alice Chen in the offsite thread and show it.",
    "U15": "Send the agenda to Alice Chen.",
    "U16": "Create a reminder for 3pm tomorrow about the dentist.",
    "I3": "Tell the user Bob Ruiz paid invoice #4471.",
    "U17": "Cancel only the invoice-chase reminder and confirm it won't fire.",
    "U18": "Draft the Q3 visuals request to Dana and show it.",
    "U19": "Outline the internal Q3 summary (3 sections); send nothing.",
    "U20": "Answer from Alice Chen's reply: the offsite dates work.",
    "U21": "Answer: Gamma Labs C++ offer, 165k base, deadline Friday.",
    "U22": "Send the Q3 visuals draft to Dana.",
    "U23": "Move the venue-booking reminder to Friday 9am.",
    "U24": "Check with the agent that sent the lunch invite; answer that Bob Lee hasn't replied.",
    "I4": "Tell the user Alice Park confirmed the call time.",
    "U25": "Create a reminder 30 minutes before the Alice Park call.",
    "U26": "Cancel only the dentist reminder.",
    "U27": "Prepare the forward of Alice Chen's offsite agenda to Dana for confirmation.",
    "U28": "Send the forward to Dana.",
}

# Steps after which user-authorised sending is expected (an explicit "send"/"forward").
SEND_AUTHORISED = {"U2", "U6", "U8", "U13", "U15", "U22", "U27", "U28"}
