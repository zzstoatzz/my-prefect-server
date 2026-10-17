import threading
import time

from mps.inbox.store import Decision, Store


class Inbox:
    def __init__(self, store: Store, providers: dict):
        self.store = store
        self.providers = providers
        self.lock = threading.Lock()
        self.sync_lock = threading.Lock()
        self.syncing = False

    def refresh(self):
        if not self.sync_lock.acquire(blocking=False):
            return
        self.syncing = True
        try:
            for name in ("gmail", "proton"):
                provider = self.providers.get(name)
                if provider is None:
                    self.store.source_error(name, "Account connection required")
                    continue
                try:
                    with self.lock:
                        account, messages = provider.fetch()
                        self.store.ingest(name, account, messages, complete=True)
                except Exception:
                    self.store.source_error(
                        name,
                        "Refresh failed. The previous batch is retained; check the account connection.",
                    )
        finally:
            self.syncing = False
            self.sync_lock.release()

    def decide(self, decision: Decision):
        if len(set(decision.ids)) != len(decision.ids):
            raise ValueError("Duplicate message IDs")
        if decision.action == "defer" and (decision.until is None or decision.until <= time.time()):
            raise ValueError("Choose a future date for deferral")
        with self.lock:
            messages = [(key, self.store.get(key)) for key in decision.ids]
            existing = self.store.begin(decision)
            if existing is not None:
                return existing
            results = []
            for key, message in messages:
                try:
                    if decision.action in ("read", "keep", "ignore"):
                        provider = self.providers.get(message.provider)
                        if provider is None:
                            raise ValueError(
                                "This account needs a live connection before mailbox changes can be applied"
                            )
                        provider.apply(message, decision.action)
                    self.store.record(key, decision)
                    results.append({"id": key, "ok": True})
                except Exception:
                    results.append(
                        {
                            "id": key,
                            "ok": False,
                            "error": "Mailbox change was not confirmed. Refresh and check the message before trying again.",
                        }
                    )
            result = {"results": results, "completed": sum(1 for item in results if item["ok"])}
            self.store.finish(decision.request_id, result)
            return result
