# run3-new (HEAD)

## U1: User

> Email Alice Chen (alice.chen@acme.example) to propose the Acme offsite on Dec 16-17 in Tahoe. Keep it short.

- IA `send_draft` {"to": "alice.chen@acme.example", "subject": "Acme offsite: Dec 16-17 in Tahoe", "body": "Hi Alice,\n\nI'd like to propose the Acme offsite for December 16-17 in Tahoe. Does that work for you? If so, I'll start pulling together the details.\n\nBest"} -> ok {"status": "draft_recorded", "to": "alice.chen@acme.example", "subject": "Acme offsite: Dec 16-17 in Tahoe"}
- IA `send_message_to_user` {"message": "Here's the draft to Alice:\n\nSubject: Acme offsite: Dec 16-17 in Tahoe\n\nHi Alice,\n\nI'd like to propose the Acme offsite for December 16-17 in Tahoe. Does that wo..."} -> ok {"status": "delivered"}
- **Poke:** To: alice.chen@acme.example
Subject: Acme offsite: Dec 16-17 in Tahoe

Hi Alice,

I'd like to propose the Acme offsite for December 16-17 in Tahoe. Does that work for you? If so, I'll start pulling together the details.

Best
- **Poke:** Here's the draft to Alice:

Subject: Acme offsite: Dec 16-17 in Tahoe

Hi Alice,

I'd like to propose the Acme offsite for December 16-17 in Tahoe. Does that work for you? If so, I'll start pulling together the details.

Best

Want me to send it as is, or change anything?
