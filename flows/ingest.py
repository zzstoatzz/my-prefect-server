"""
Fetch GitHub notifications and tangled.org items, persist both to DuckDB.

Combines the two data sources into one flow so DuckDB's single-writer lock
is never contested — both persists happen sequentially in the same process.

Cache policy: each GitHub issue is cached by repo+number for 24h.

Requires:
  - Secret block "github-token" (notifications scope)
  - PREFECT_LOCAL_STORAGE_PATH env var pointing at the mounted PVC
"""

import datetime
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from mps.blocks import secret_mapping_sync, secret_sync
from mps.db import (
    write_emails,
    write_github_issues,
    write_liked_posts,
    write_likes,
    write_phi_interactions,
    write_phi_observations,
    write_tangled_items,
)
from mps.email import (
    EmailItem,
    fetch_inbox,
)
from mps.github import IssueOrPR, IssueRef, gh_headers
from mps.likes import LikedPost, LikeRecord, fetch_likes, summarize_embed
from mps.lock import analytics_write_slot
from mps.phi import PhiInteraction, PhiObservation, restore_handle, row_strings, row_text
from mps.tangled import PDS_BASE, TangledItem, fetch_items, fetch_repo_at_uris
from prefect import flow, get_run_logger, task, unmapped
from prefect.artifacts import create_table_artifact
from prefect.cache_policies import CachePolicy
from prefect.context import TaskRunContext
from prefect.futures import PrefectFuture
from prefect.states import Completed

GITHUB_API = "https://api.github.com"

# every external fetch here is one blip away from failing the whole hourly run.
# 3 jittered attempts absorbs the transient timeouts we actually see; anything
# still down after that degrades the run rather than failing it (see _tolerate).
NETWORK_RETRIES: dict[str, Any] = {
    "retries": 3,
    "retry_delay_seconds": [2, 5, 10],
    "retry_jitter_factor": 1,
}

# likes are upserted by at_uri and listRecords returns newest-first, so an
# hourly run only needs the newest pages. walking the whole history (back to
# 2024) every hour re-fetched records that cannot have changed, each page
# another chance at the timeout that killed ingest-81cdf915. 300 newest likes
# is far more than an hour ever produces. pass max_pages=None to backfill.
LIKES_PAGES = 3


def gh_client(token: str, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    # follow_redirects matters: renamed/transferred repos 301 to their
    # numeric /repositories/<id>/ url and httpx won't follow by default
    return httpx.Client(headers=gh_headers(token), follow_redirects=True, transport=transport)


TANGLED_COLLECTIONS = [
    "sh.tangled.repo.issue",
    "sh.tangled.repo.pull",
    "sh.tangled.repo.issue.comment",
    "sh.tangled.repo.pull.comment",
]

# bump to invalidate all cached results (e.g. when fetch shape changes)
_CACHE_VERSION = "v3"


@dataclass
class ByRepoAndNumber(CachePolicy):
    """Cache key is repo + number only — ignores token and other args."""

    def compute_key(
        self,
        task_ctx: TaskRunContext,
        inputs: dict[str, Any],
        flow_parameters: dict[str, Any],
        **kwargs: Any,
    ) -> str | None:
        ref: IssueRef | None = inputs.get("ref")
        if ref is None:
            return None
        return f"gh/{_CACHE_VERSION}/{ref.repo}/{ref.number}"


# --- github tasks ---


@task
def load_token() -> str:
    return secret_sync("github-token")


@task(**NETWORK_RETRIES)
def fetch_notifications(token: str, only_unread: bool = True) -> list[IssueRef]:
    """Fetch notifications and parse into IssueRef objects (Issues/PRs only)."""
    logger = get_run_logger()
    with gh_client(token) as client:
        resp = client.get(
            f"{GITHUB_API}/notifications",
            params={"all": str(not only_unread).lower(), "per_page": 50},
        )
        resp.raise_for_status()

    refs: list[IssueRef] = []
    for n in resp.json():
        subject = n.get("subject", {})
        subject_type = subject.get("type")
        if subject_type not in ("Issue", "PullRequest"):
            continue
        url = subject.get("url", "")
        try:
            number = int(url.rstrip("/").split("/")[-1])
        except (ValueError, IndexError):
            continue
        refs.append(
            IssueRef(
                repo=n["repository"]["full_name"],
                number=number,
                subject_type=subject_type,
            )
        )

    logger.info(f"fetched {len(refs)} issue/PR notifications")
    return refs


@task(
    **NETWORK_RETRIES,
    cache_policy=ByRepoAndNumber(),
    # short enough that a merge/close disappears from the hub within hours —
    # stored rows only update on re-fetch, so this bounds state staleness
    cache_expiration=datetime.timedelta(hours=4),
    persist_result=True,
    result_serializer="json",
)
def fetch_issue_or_pr(ref: IssueRef, token: str) -> IssueOrPR | None:
    """Fetch a single issue or PR. Cached by repo+number for 4h."""
    with gh_client(token) as client:
        resp = client.get(f"{GITHUB_API}/repos/{ref.repo}/issues/{ref.number}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()

    # a followed 301 means the repo was renamed/transferred — html_url
    # carries the canonical name, so stored rows converge on it
    html_url = data.get("html_url") or ""
    parts = html_url.removeprefix("https://github.com/").split("/")
    canonical_repo = "/".join(parts[:2]) if len(parts) >= 4 else ref.repo

    return IssueOrPR(
        repo=canonical_repo,
        number=ref.number,
        type=ref.subject_type,
        title=data.get("title"),
        state=data.get("state"),
        body=data.get("body") or "",
        url=data.get("html_url"),
        labels=[la["name"] for la in data.get("labels", [])],
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        user=(data.get("user") or {}).get("login"),
        comments=data.get("comments", 0),
        reactions_total=(data.get("reactions") or {}).get("total_count", 0),
    )


@task(**NETWORK_RETRIES)
def fetch_authored_items(token: str, username: str = "zzstoatzz") -> list[IssueRef]:
    """Fetch open issues/PRs authored by the user via the search API."""
    logger = get_run_logger()
    with gh_client(token) as client:
        resp = client.get(
            f"{GITHUB_API}/search/issues",
            params={
                "q": f"author:{username} is:open",
                "per_page": 50,
                "sort": "updated",
            },
        )
        resp.raise_for_status()

    refs: list[IssueRef] = []
    for item in resp.json().get("items", []):
        html_url = item.get("html_url", "")
        is_pr = "/pull/" in html_url
        parts = html_url.split("/")
        try:
            repo = f"{parts[3]}/{parts[4]}"
            number = int(parts[-1])
        except (IndexError, ValueError):
            continue
        refs.append(
            IssueRef(
                repo=repo,
                number=number,
                subject_type="PullRequest" if is_pr else "Issue",
            )
        )

    logger.info(f"fetched {len(refs)} authored items for {username}")
    return refs


@task
def stored_open_refs() -> list[IssueRef]:
    """Issues/PRs sitting in DuckDB as 'open'.

    Notifications only surface items with fresh activity, so a PR merged
    quietly stays 'open' in raw_github_issues forever unless re-verified.
    Re-fetching everything stored as open (through the 4h cache) keeps the
    hub from showing closed/merged work.
    """
    import shutil

    import duckdb

    logger = get_run_logger()
    src = _db_path()
    if not os.path.exists(src):
        return []
    # snapshot to bypass the writer's exclusive flock (same pattern as brief's
    # load_items) — an off-schedule ingest may overlap a running transform
    snap = "/tmp/ingest_open_refs_snapshot.duckdb"
    shutil.copy2(src, snap)
    con = duckdb.connect(snap, read_only=True)
    try:
        rows = con.execute(
            "SELECT repo, number, type FROM raw_github_issues WHERE state = 'open'"
        ).fetchall()
    except duckdb.CatalogException:
        return []
    finally:
        con.close()

    refs = [IssueRef(repo=repo, number=number, subject_type=type_) for repo, number, type_ in rows]
    logger.info(f"re-verifying {len(refs)} stored-open issues/PRs")
    return refs


# --- tangled tasks ---


@task(**NETWORK_RETRIES)
def fetch_all_tangled_items() -> list[TangledItem]:
    """Fetch issues, PRs, and comments from the tangled.org PDS."""
    logger = get_run_logger()
    with httpx.Client(base_url=PDS_BASE, timeout=30) as client:
        repo_uris = fetch_repo_at_uris(client)
        logger.info(f"found {len(repo_uris)} target repos on PDS")

        items: list[TangledItem] = []
        for collection in TANGLED_COLLECTIONS:
            batch = fetch_items(client, collection, repo_uris)
            logger.info(f"{collection}: {len(batch)} records")
            items.extend(batch)

    return items


# --- email tasks ---


@task(**NETWORK_RETRIES)
def fetch_emails() -> list[EmailItem]:
    """Fetch recent inbox mail from the local hydroxide bridge.

    Skips (returns []) if the bridge isn't running or creds aren't set up yet,
    so the rest of ingest keeps flowing while proton is unconfigured.
    """
    logger = get_run_logger()

    try:
        creds = secret_mapping_sync("proton-bridge-creds")
    except ValueError:
        logger.warning("proton-bridge-creds Secret block not found — skipping email")
        return []

    host = os.environ.get("PROTON_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("PROTON_BRIDGE_PORT", "1143"))

    try:
        items = fetch_inbox(host, port, creds["username"], creds["password"])
    except (ConnectionRefusedError, OSError) as e:
        logger.warning(f"proton bridge unreachable at {host}:{port} — skipping email ({e})")
        return []

    logger.info(f"fetched {len(items)} emails from bridge")
    return items


@task
def persist_emails(items: list[EmailItem]) -> int:
    return write_emails(items, _db_path())


# --- phi tasks ---

USER_NS_PREFIX = "phi-users-"


@task(**NETWORK_RETRIES)
def fetch_phi_memory(
    tpuf_key: str,
) -> tuple[list[PhiObservation], list[PhiInteraction]]:
    """Fetch observations and interactions from all phi-users-* TurboPuffer namespaces."""
    import turbopuffer

    logger = get_run_logger()
    client = turbopuffer.Turbopuffer(api_key=tpuf_key, region="gcp-us-central1")

    observations: list[PhiObservation] = []
    interactions: list[PhiInteraction] = []

    page = client.namespaces(prefix=USER_NS_PREFIX)
    ns_ids = [ns.id for ns in page.namespaces]
    logger.info(f"found {len(ns_ids)} phi user namespaces")

    for ns_id in ns_ids:
        handle = restore_handle(ns_id)
        ns = client.namespace(ns_id)

        # fetch observations
        try:
            resp = ns.query(
                rank_by=("vector", "ANN", [0.5] * 1536),
                top_k=200,
                filters=("kind", "Eq", "observation"),
                include_attributes=["content", "tags", "created_at"],
            )
            if resp.rows:
                for row in resp.rows:
                    observations.append(
                        PhiObservation(
                            handle=handle,
                            observation_id=str(row.id),
                            content=row_text(row, "content"),
                            tags=row_strings(row, "tags"),
                            created_at=row_text(row, "created_at"),
                        )
                    )
        except Exception as e:
            if "not found" not in str(e).lower():
                logger.warning(f"failed to fetch observations for {ns_id}: {e}")

        # fetch interactions
        try:
            resp = ns.query(
                rank_by=("vector", "ANN", [0.5] * 1536),
                top_k=200,
                filters=("kind", "Eq", "interaction"),
                include_attributes=["content", "created_at"],
            )
            if resp.rows:
                for row in resp.rows:
                    interactions.append(
                        PhiInteraction(
                            handle=handle,
                            interaction_id=str(row.id),
                            content=row_text(row, "content"),
                            created_at=row_text(row, "created_at"),
                        )
                    )
        except Exception as e:
            if "not found" not in str(e).lower():
                logger.warning(f"failed to fetch interactions for {ns_id}: {e}")

    logger.info(f"fetched {len(observations)} observations, {len(interactions)} interactions")
    return observations, interactions


@task(**NETWORK_RETRIES)
def fetch_nate_likes(max_pages: int | None = LIKES_PAGES) -> list[LikeRecord]:
    """Fetch recent likes from nate's PDS."""
    logger = get_run_logger()
    with httpx.Client(base_url=PDS_BASE, timeout=30) as client:
        likes = fetch_likes(client, max_pages=max_pages)
    logger.info(f"fetched {len(likes)} likes from PDS")
    return likes


@task
def persist_likes(items: list[LikeRecord]) -> int:
    return write_likes(items, _db_path())


@task(**NETWORK_RETRIES)
def resolve_liked_posts(db_path: str) -> list[LikedPost]:
    """Find recent unresolved likes and batch-resolve post content via public API."""
    import duckdb

    logger = get_run_logger()

    with analytics_write_slot():
        con = duckdb.connect(db_path)
        # bootstrap raw_liked_posts if it doesn't exist yet
        con.execute("""
            CREATE TABLE IF NOT EXISTS raw_liked_posts (
                subject_uri VARCHAR PRIMARY KEY,
                author_handle VARCHAR,
                author_did VARCHAR,
                text VARCHAR,
                created_at VARCHAR,
                liked_at VARCHAR,
                embed_type VARCHAR,
                embed_text VARCHAR,
                fetched_at TIMESTAMP DEFAULT now()
            )
        """)
        # find likes from last 7 days not yet resolved
        rows = con.execute("""
            SELECT l.subject_uri, l.created_at AS liked_at
            FROM raw_likes l
            LEFT JOIN raw_liked_posts lp ON l.subject_uri = lp.subject_uri
            WHERE lp.subject_uri IS NULL
              AND l.created_at >= (now() - INTERVAL '7 days')::VARCHAR
            ORDER BY l.created_at DESC
            LIMIT 200
        """).fetchall()
        con.close()

    if not rows:
        logger.info("no unresolved likes to fetch")
        return []

    uri_to_liked_at = {row[0]: row[1] for row in rows}
    uris = list(uri_to_liked_at.keys())
    logger.info(f"resolving {len(uris)} liked posts via public API")

    posts: list[LikedPost] = []
    with httpx.Client(timeout=15) as client:
        # getPosts accepts up to 25 URIs per call
        for i in range(0, len(uris), 25):
            batch = uris[i : i + 25]
            resp = client.get(
                "https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts",
                params=[("uris", u) for u in batch],
            )
            if resp.status_code != 200:
                logger.warning(f"getPosts returned {resp.status_code} for batch {i}")
                continue
            for post in resp.json().get("posts", []):
                uri = post.get("uri", "")
                record = post.get("record", {})
                author = post.get("author", {})
                embed_type, embed_text = summarize_embed(
                    post.get("embed") or record.get("embed") or {}
                )
                posts.append(
                    LikedPost(
                        subject_uri=uri,
                        author_handle=author.get("handle", ""),
                        author_did=author.get("did", ""),
                        text=record.get("text", ""),
                        created_at=record.get("createdAt", ""),
                        liked_at=uri_to_liked_at.get(uri, ""),
                        embed_type=embed_type,
                        embed_text=embed_text,
                    )
                )

    logger.info(f"resolved {len(posts)} liked posts")
    return posts


@task
def persist_liked_posts(items: list[LikedPost]) -> int:
    return write_liked_posts(items, _db_path())


@task
def persist_phi(
    observations: list[PhiObservation],
    interactions: list[PhiInteraction],
) -> tuple[int, int]:
    db = _db_path()
    obs_count = write_phi_observations(observations, db) if observations else 0
    ix_count = write_phi_interactions(interactions, db) if interactions else 0
    return obs_count, ix_count


# --- persist tasks ---


def _db_path() -> str:
    return os.environ.get(
        "ANALYTICS_DB_PATH",
        os.environ.get("PREFECT_LOCAL_STORAGE_PATH", "/tmp") + "/analytics.duckdb",
    )


@task
def persist_github(items: list[IssueOrPR]) -> int:
    return write_github_issues(items, _db_path())


@task
def persist_tangled(items: list[TangledItem]) -> int:
    return write_tangled_items(items, _db_path())


# --- flow ---


def _tolerate[T](
    future: PrefectFuture[T],
    source: str,
    default: T,
    degraded: list[str],
    logger: logging.Logger | logging.LoggerAdapter[logging.Logger],
) -> T:
    """Resolve a fetch that stayed down through all its retries.

    Every persist below is already guarded on an empty source, so one dead
    upstream should cost us that source for an hour — not the github, tangled
    and phi rows the same run already fetched successfully.
    """
    value = future.result(raise_on_failure=False)
    if isinstance(value, BaseException):
        logger.warning(f"{source} unavailable this run: {value!r}")
        degraded.append(source)
        return default
    return value


@flow(name="ingest", log_prints=True, timeout_seconds=1800)
def ingest(only_unread: bool = True):
    """
    Fetch GitHub, tangled.org, and phi memory concurrently, then persist sequentially.
    """
    logger = get_run_logger()
    degraded: list[str] = []

    token = load_token()

    # kick off tangled + phi + likes fetches immediately (no deps on github token)
    tangled_future = fetch_all_tangled_items.submit()
    likes_future = fetch_nate_likes.submit()
    email_future = fetch_emails.submit()

    tpuf_key = secret_sync("turbopuffer-api-key")
    phi_future = fetch_phi_memory.submit(tpuf_key)

    # github fetches need the token
    notif_refs = fetch_notifications(token, only_unread=only_unread)
    authored_refs = fetch_authored_items(token)

    open_refs = stored_open_refs()

    # merge and dedupe by (repo, number)
    seen: set[tuple[str, int]] = set()
    refs: list[IssueRef] = []
    for ref in notif_refs + authored_refs + open_refs:
        key = (ref.repo, ref.number)
        if key not in seen:
            seen.add(key)
            refs.append(ref)
    logger.info(
        f"github refs: {len(notif_refs)} notifications, {len(authored_refs)} authored, "
        f"{len(refs)} unique"
    )

    # fetch full issue/PR details (cached)
    gh_items: list[IssueOrPR] = []
    if refs:
        futures = fetch_issue_or_pr.map(refs, unmapped(token))
        gh_items = [r for r in futures.result() if r is not None]
    logger.info(f"resolved {len(gh_items)} github issues/PRs")

    # wait for tangled + phi fetches
    tangled_items = _tolerate(tangled_future, "tangled", [], degraded, logger)
    logger.info(f"fetched {len(tangled_items)} tangled items")

    phi_observations, phi_interactions = _tolerate(phi_future, "phi", ([], []), degraded, logger)

    likes = _tolerate(likes_future, "likes", [], degraded, logger)
    logger.info(f"fetched {len(likes)} likes")

    emails = _tolerate(email_future, "email", [], degraded, logger)

    # sequential writes — same process, no DuckDB lock contention
    if gh_items:
        total = persist_github(gh_items)
        logger.info(f"upserted {len(gh_items)} github rows; {total} total in raw_github_issues")

    if tangled_items:
        total = persist_tangled(tangled_items)
        logger.info(
            f"persisted {len(tangled_items)} tangled rows; {total} total in raw_tangled_items"
        )

    if likes:
        total = persist_likes(likes)
        logger.info(f"persisted {len(likes)} likes; {total} total in raw_likes")

        # resolve liked post content for recent unresolved likes
        liked_posts = resolve_liked_posts(_db_path())
        if liked_posts:
            lp_total = persist_liked_posts(liked_posts)
            logger.info(
                f"resolved {len(liked_posts)} liked posts; {lp_total} total in raw_liked_posts"
            )

    if emails:
        total = persist_emails(emails)
        logger.info(f"persisted {len(emails)} emails; {total} total in raw_emails")

    if phi_observations or phi_interactions:
        obs_total, ix_total = persist_phi(phi_observations, phi_interactions)
        logger.info(
            f"persisted {len(phi_observations)} phi observations ({obs_total} total), "
            f"{len(phi_interactions)} interactions ({ix_total} total)"
        )

    create_table_artifact(
        key="ingest-counts",
        table=[
            {"source": "github", "fetched": len(gh_items)},
            {"source": "tangled", "fetched": len(tangled_items)},
            {"source": "email", "fetched": len(emails)},
            {"source": "likes", "fetched": len(likes)},
            {"source": "phi observations", "fetched": len(phi_observations)},
            {"source": "phi interactions", "fetched": len(phi_interactions)},
        ],
        description="rows fetched per source this run",
    )

    if degraded:
        # a COMPLETED-type state with its own name: the run did its job with
        # what it could reach, and the failure automation only expects
        # Failed/TimedOut/Crashed, so this stays visible without paging.
        return Completed(
            name="Degraded",
            message=f"persisted what we could; unavailable: {', '.join(degraded)}",
        )


if __name__ == "__main__":
    ingest()
