"""Stateful fake Gmail shared by both OpenPoke versions in the live comparison.

Replaces ``execute_gmail_tool`` (same signature as server/services/gmail/client.py).
Responses use the Composio shapes the code parses:
  * GMAIL_FETCH_EMAILS -> {"successful", "data": {"messages": [{messageId, threadId,
    messageTimestamp, subject, sender, to, textBody, labelIds}]}}  (services/gmail/processing.py)
  * GMAIL_CREATE_EMAIL_DRAFT -> {"data": {"id": <draft>, "message": {"id", "threadId"}}}

Every call is logged with the scenario step it happened in, so graders can tell
exactly what was drafted, sent or forwarded, to whom, in which thread and when.
Nothing leaves the process.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

USER_ADDRESS = "me@openpoke.example"
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _addresses(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for v in value:
            out.extend(_addresses(v))
        return out
    return [m.group(0).lower() for m in _EMAIL_RE.finditer(str(value))]


@dataclass
class Message:
    id: str
    thread_id: str
    sender: str
    to: List[str]
    subject: str
    body: str
    ts: datetime
    labels: List[str]


@dataclass
class Draft:
    id: str
    message_id: str
    thread_id: str
    to: List[str]
    subject: str
    body: str
    status: str = "open"  # open | sent | deleted
    step: str = ""


@dataclass
class FakeInbox:
    contacts: List[Dict[str, str]]
    messages: Dict[str, Message] = field(default_factory=dict)
    drafts: Dict[str, Draft] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    step: str = "setup"
    _seq: int = 0
    _clock: datetime = field(default_factory=lambda: datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc))

    # ---- helpers ---------------------------------------------------------
    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq}"

    def _tick(self) -> datetime:
        self._clock += timedelta(minutes=7)
        return self._clock

    def _log(self, action: str, **detail: Any) -> None:
        self.events.append({"step": self.step, "action": action, **detail})

    def thread_with(self, address: str, keyword: str = "") -> Optional[str]:
        """Newest thread containing a message to/from ``address`` (optionally matching keyword)."""
        address = address.lower()
        for msg in sorted(self.messages.values(), key=lambda m: m.ts, reverse=True):
            people = [msg.sender.lower(), *msg.to]
            if any(address in p for p in people):
                if not keyword or keyword.lower() in f"{msg.subject} {msg.body}".lower():
                    return msg.thread_id
        return None

    # ---- scenario hooks ---------------------------------------------------
    def deliver(self, *, sender: str, subject: str, body: str, reply_to: Optional[str] = None,
                thread_keyword: str = "") -> Message:
        """An email arrives. ``reply_to`` = address whose existing thread it continues (if any)."""
        thread = self.thread_with(reply_to, thread_keyword) if reply_to else None
        msg = Message(
            id=self._next("m-"), thread_id=thread or self._next("t-"), sender=sender,
            to=[USER_ADDRESS], subject=subject, body=body, ts=self._tick(), labels=["INBOX", "UNREAD"],
        )
        self.messages[msg.id] = msg
        self._log("inbound", message_id=msg.id, thread_id=msg.thread_id, sender=sender,
                  subject=subject, continued_thread=bool(thread))
        return msg

    # ---- tool dispatch ----------------------------------------------------
    def __call__(self, tool_name: str, composio_user_id: str, *, arguments: Optional[Dict[str, Any]] = None):
        args = dict(arguments or {})
        handler = getattr(self, "_" + tool_name.lower(), None)
        if handler is None:
            self._log("unsupported", tool=tool_name, args=args)
            return {"successful": False, "error": f"{tool_name} is not available", "data": {}}
        return handler(args)

    def _sent_message(self, *, thread_id: str, to: List[str], subject: str, body: str) -> Message:
        msg = Message(id=self._next("m-"), thread_id=thread_id, sender=f"Me <{USER_ADDRESS}>", to=to,
                      subject=subject, body=body, ts=self._tick(), labels=["SENT"])
        self.messages[msg.id] = msg
        return msg

    def _gmail_create_email_draft(self, a: Dict[str, Any]):
        to = _addresses([a.get("recipient_email"), a.get("cc"), a.get("bcc"), a.get("extra_recipients")])
        thread_id = a.get("thread_id")
        if thread_id and not any(m.thread_id == thread_id for m in self.messages.values()):
            self._log("draft_failed", reason="unknown thread", thread_id=thread_id, to=to)
            return {"successful": False, "error": f"Thread {thread_id} not found", "data": {}}
        draft = Draft(id=self._next("r-"), message_id=self._next("m-"), thread_id=thread_id or self._next("t-"),
                      to=to, subject=str(a.get("subject") or ""), body=str(a.get("body") or ""), step=self.step)
        self.drafts[draft.id] = draft
        self._log("draft", draft_id=draft.id, thread_id=draft.thread_id, to=to, subject=draft.subject,
                  body=draft.body[:400], in_existing_thread=bool(thread_id))
        return {"successful": True, "error": None,
                "data": {"id": draft.id, "message": {"id": draft.message_id, "threadId": draft.thread_id}}}

    def _gmail_send_draft(self, a: Dict[str, Any]):
        draft = self.drafts.get(str(a.get("draft_id")))
        if draft is None or draft.status != "open":
            self._log("send_failed", draft_id=a.get("draft_id"), reason="unknown or already sent")
            return {"successful": False, "error": "Draft not found", "data": {}}
        draft.status = "sent"
        msg = self._sent_message(thread_id=draft.thread_id, to=draft.to, subject=draft.subject, body=draft.body)
        self._log("send", via="draft", draft_id=draft.id, message_id=msg.id, thread_id=msg.thread_id,
                  to=draft.to, subject=draft.subject, body=draft.body[:400], draft_step=draft.step)
        return {"successful": True, "error": None, "data": {"id": msg.id, "threadId": msg.thread_id}}

    def _gmail_delete_draft(self, a: Dict[str, Any]):
        draft = self.drafts.get(str(a.get("draft_id")))
        if draft is None or draft.status != "open":
            return {"successful": False, "error": "Draft not found", "data": {}}
        draft.status = "deleted"
        self._log("delete_draft", draft_id=draft.id, to=draft.to)
        return {"successful": True, "error": None, "data": {}}

    def _gmail_reply_to_thread(self, a: Dict[str, Any]):
        thread_id = str(a.get("thread_id") or "")
        if not any(m.thread_id == thread_id for m in self.messages.values()):
            self._log("send_failed", via="reply", thread_id=thread_id, reason="unknown thread")
            return {"successful": False, "error": f"Thread {thread_id} not found", "data": {}}
        to = _addresses([a.get("recipient_email"), a.get("cc"), a.get("bcc"), a.get("extra_recipients")])
        subject = next((m.subject for m in self.messages.values() if m.thread_id == thread_id), "")
        body = str(a.get("message_body") or "")
        msg = self._sent_message(thread_id=thread_id, to=to, subject=f"Re: {subject}", body=body)
        self._log("send", via="reply", message_id=msg.id, thread_id=thread_id, to=to, subject=msg.subject,
                  body=body[:400])
        return {"successful": True, "error": None, "data": {"id": msg.id, "threadId": thread_id}}

    def _gmail_forward_message(self, a: Dict[str, Any]):
        original = self.messages.get(str(a.get("message_id")))
        to = _addresses(a.get("recipient_email"))
        if original is None:
            self._log("send_failed", via="forward", message_id=a.get("message_id"), reason="unknown message")
            return {"successful": False, "error": "Message not found", "data": {}}
        body = f"{a.get('additional_text') or ''}\n---------- Forwarded message ----------\n{original.body}"
        msg = self._sent_message(thread_id=self._next("t-"), to=to, subject=f"Fwd: {original.subject}", body=body)
        self._log("send", via="forward", message_id=msg.id, forwarded_message_id=original.id,
                  forwarded_thread_id=original.thread_id, forwarded_sender=original.sender, to=to,
                  subject=msg.subject, body=body[:400])
        return {"successful": True, "error": None, "data": {"id": msg.id, "threadId": msg.thread_id}}

    def _gmail_list_drafts(self, a: Dict[str, Any]):
        drafts = [
            {"id": d.id, "message": {"id": d.message_id, "threadId": d.thread_id, "to": d.to,
                                     "subject": d.subject, "snippet": d.body[:120]}}
            for d in self.drafts.values() if d.status == "open"
        ]
        self._log("list_drafts", count=len(drafts))
        return {"successful": True, "error": None, "data": {"drafts": drafts}}

    def _people(self, query: str = ""):
        q = query.lower().strip()
        people = [
            {"names": [{"displayName": c["name"]}], "emailAddresses": [{"value": c["email"]}],
             "name": c["name"], "email": c["email"]}
            for c in self.contacts
            if not q or any(part in f"{c['name']} {c['email']}".lower() for part in q.split())
        ]
        return {"successful": True, "error": None, "data": {"people": people, "connections": people}}

    def _gmail_search_people(self, a: Dict[str, Any]):
        self._log("people_lookup", query=a.get("query"))
        return self._people(str(a.get("query") or ""))

    def _gmail_get_contacts(self, a: Dict[str, Any]):
        self._log("people_lookup", query=None)
        return self._people()

    _gmail_get_people = _gmail_get_contacts

    def _gmail_fetch_emails(self, a: Dict[str, Any]):
        query = str(a.get("query") or "")
        hits = [m for m in self.messages.values() if _matches(m, query)]
        hits.sort(key=lambda m: m.ts, reverse=True)
        hits = hits[: int(a.get("max_results") or 10)]
        self._log("search", query=query, results=[m.id for m in hits])
        return {"successful": True, "error": None, "data": {"messages": [
            {"messageId": m.id, "threadId": m.thread_id, "messageTimestamp": m.ts.isoformat(),
             "subject": m.subject, "sender": m.sender, "to": ", ".join(m.to), "textBody": m.body,
             "labelIds": m.labels}
            for m in hits
        ], "nextPageToken": None}}

    # ---- evidence ---------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return {
            "events": self.events,
            "drafts": [vars(d) for d in self.drafts.values()],
            "messages": [{**vars(m), "ts": m.ts.isoformat()} for m in self.messages.values()],
        }


_OP_RE = re.compile(r'(-?)(\w+):("[^"]*"|\S+)|"([^"]*)"|(\S+)')
_FIELD_OPS = {"from", "to", "subject", "cc"}


def _matches(msg: Message, query: str) -> bool:
    """Forgiving Gmail-query matcher: field operators AND together; free words OR together.

    Unknown operators (newer_than, after, in:, is:, label:, has:) are ignored except
    in:sent / in:inbox, which filter on labels. Real Gmail is stricter; being forgiving
    keeps search from failing for reasons unrelated to routing (same for both versions).
    """
    text = f"{msg.sender} {' '.join(msg.to)} {msg.subject} {msg.body}".lower()
    words: List[str] = []
    for neg, op, val, quoted, word in _OP_RE.findall(query):
        if op:
            val = val.strip('"').lower()
            op = op.lower()
            if op in _FIELD_OPS:
                field_text = {"from": msg.sender, "to": " ".join(msg.to), "cc": " ".join(msg.to),
                              "subject": msg.subject}[op].lower()
                hit = any(part in field_text for part in re.split(r"[\s{}()]+|\bOR\b", val) if part)
                if bool(neg) == hit:
                    return False
            elif op == "in" and val in {"sent", "inbox"}:
                if (val.upper() in msg.labels) == bool(neg):
                    return False
        else:
            token = (quoted or word).lower().strip("(){}")
            if token and token not in {"or", "and"}:
                words.append(token)
    return not words or any(w in text for w in words)
