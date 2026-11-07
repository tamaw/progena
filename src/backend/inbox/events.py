"""Connection-scoped inbox detail interests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InboxMessageInterest:
    """A tab's currently selected inbox message and its stale-delivery token."""

    client_id: str
    message_id: str
    token: object


class InboxMessageEvents:
    """Keep one selected-message interest per canonical stream client."""

    def __init__(self) -> None:
        self.interests: dict[str, InboxMessageInterest] = {}

    def subscribe(self, client_id: str, message_id: str) -> object:
        token = object()
        self.interests[client_id] = InboxMessageInterest(client_id, message_id, token)
        return token

    def unsubscribe(self, client_id: str, message_id: str | None = None) -> None:
        interest = self.interests.get(client_id)
        if interest is not None and (message_id is None or interest.message_id == message_id):
            del self.interests[client_id]

    def disconnected(self, client_id: str) -> None:
        self.unsubscribe(client_id)

    def has_interest(self, client_id: str, token: object) -> bool:
        interest = self.interests.get(client_id)
        return interest is not None and interest.token is token

    def interested_in(self, message_id: str) -> list[InboxMessageInterest]:
        return [
            interest for interest in self.interests.values() if interest.message_id == message_id
        ]


inbox_message_events = InboxMessageEvents()
