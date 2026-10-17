# 2026-10-11: exe.dev lost VM creation; strata swapped ns and count

Two unrelated failures landed the same evening and compounded through the
autofix ladder. Nothing merged, five duplicate pulls opened, and discord
carried 42 messages in 24 hours.

## exe.dev

The gardener account (`n8@zzstoatzz.io`) was on the free Basic tier
(`paid: false, max_vms: 0`). VM creation had worked since 2026-10-04 only
because a ChatGPT subscription was connected. On 2026-10-11 exe.dev stopped
the account's VMs at 12:40 UTC (including `gardener-registry`, the private
image registry) and refused `new` from 20:27 UTC with "Choose a plan to start
creating VMs". Every gardener-exe submit crashed: autofix-revise four times,
test-pull-patch once, which the merge flow reported as Tests-Failed.

The docs are explicit: "An active subscription is needed to create and
manage VMs." Personal ($15/month, 2 vCPU / 4 GB shared, 50 VMs) was activated
at 2026-10-12 01:58 UTC, `restart gardener-registry` succeeded, and a retried
autofix-revise created a pool VM alongside the 1 vCPU registry, bootstrapped,
ran Pi, and appended round 2 on pull #9. Standalone VMs (`new --standalone`,
$0.105 per 2 vCPU-hour) are available on this plan and remain an option if
pool contention shows up; the worker still creates pool VMs.

## strata

`strata-hourly` failed every hour from 20:21 UTC with
`TypeError: unsupported operand type(s) for +=: 'int' and 'str'` at
`strata_snapshot.py:152`. The live checkpoint's `segment_collections` table
predates 4c50dff and orders its columns `(idx, nsid, count, ns)`; the
positional insert added in 4c50dff wrote `ns` into `count`. 11,849 rows across
5,055 segments were affected, 10,901 of them through the ON CONFLICT update
path that overwrote the real count with the namespace string.

autofix proposed the same `CAST(count AS INTEGER)` patch five times (pulls
#5 to #9); phi requested changes on every one for hiding the corruption, which
was the right call. The fix on main (992475c) names the insert columns and
forgets any segment holding a non-integer count so reconcile re-reads it from
the archive. The read sites stay strict.

## what else this exposed

- autofix had no one-pull-per-(deployment, sha) guard (docs/autofix.md §7).
  Added in c91d35f.
- the merge gate ran this repo's tests without the dagster extra, so every
  round ended Tests-Failed at collection. Fixed in 1a9bfdf.
- `tangled-merge-ssh-key` is registered with no tangled account, so
  merge-approved blocks at the knot preflight for every repo. Registering it
  is an identity decision for the operator; the public key is derivable from
  the block without printing the private half.
- the Haiku policy judge rejected 9 of 32 autofix briefs as social
  engineering, twice with a reason that called the prompt legitimate.
- `bobbin.klbr.net` (tg's default appview) was unreachable all evening, so
  the duplicate pulls could not be closed from the CLI.
