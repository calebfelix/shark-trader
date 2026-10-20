"""What this bot is allowed to touch on the exchange.

Shark can't tag a position as 'mine', and the exchange account is shared with the owner's own
(manual) trading. So the bot records every order id it places, and treats a position as its own only
if one of its fills came from one of those orders. Anything else on the symbol - a manual position, a
manual stop order - is left strictly alone (never adopted, managed, cancelled or counted).
"""

import json
import os
import tempfile


class BotRegistry:
    def __init__(self, path: str | None = None):
        self.path = path
        self.order_ids: set[str] = set()
        self.position_ids: set[str] = set()
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.order_ids = set(data.get("order_ids", []))
                self.position_ids = set(data.get("position_ids", []))
            except (OSError, ValueError):
                pass  # unreadable state -> start empty: the bot then refuses to touch anything it can't prove is its own

    def _save(self) -> None:
        if not self.path:
            return
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.path)), prefix=".bot_state_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"order_ids": sorted(self.order_ids), "position_ids": sorted(self.position_ids)}, f, indent=1)
        os.replace(tmp, self.path)

    def add_order(self, client_order_id: str | None) -> None:
        if client_order_id and client_order_id not in self.order_ids:
            self.order_ids.add(client_order_id)
            self._save()

    def owns_order(self, client_order_id: str | None) -> bool:
        return client_order_id in self.order_ids

    def add_position(self, position_id: str | None) -> None:
        if position_id and position_id not in self.position_ids:
            self.position_ids.add(position_id)
            self._save()

    def owns_position(self, position_id: str | None) -> bool:
        return position_id in self.position_ids
