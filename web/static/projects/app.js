import { loadInventory } from "./services.js";
import { checkAll } from "./checker.js";
import { loadCosts, usd, COSTS_URL } from "./costs.js";

const $ = (id) => document.getElementById(id);
const escape = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const link = (href, label) =>
  /^(https:\/\/|\/(?!\/))/.test(href || "")
    ? `<a href="${escape(href)}" target="_blank" rel="noopener">${escape(label)} ↗</a>`
    : escape(label);
let projects = [],
  results = new Map(),
  deepChecks = [],
  costs = null,
  monitorError = null,
  checkedAt = null;
let view = ["attention", "resources"].includes(location.hash.slice(1))
  ? location.hash.slice(1)
  : "projects";
let busy = false;
const expanded = new Set();
if (new URLSearchParams(location.search).get("sort") === "cost")
  $("sort").value = "cost";
const itemsFor = (p) => costs?.byProject.get(p.name) || [];
const amountFor = (p) => itemsFor(p).reduce((sum, i) => sum + i.amount, 0);

function health(p) {
  if (!p.services.length) return { label: "No checks", tone: "warn" };
  if (monitorError) return { label: "Health unknown", tone: "warn" };
  if (!checkedAt) return { label: "Checking…", tone: "muted" };
  if (deepChecks.some((d) => d.project === p.name && !d.ok))
    return { label: "Deep check failing", tone: "bad" };
  const failed = p.services.filter((s) => !results.get(s.url)?.ok).length;
  return failed
    ? { label: `${failed} failing`, tone: "bad" }
    : {
        label: `${p.services.length} check${p.services.length === 1 ? "" : "s"} passing`,
        tone: "good",
      };
}
function reasons(p) {
  const list = [];
  if (p.ownership === "unknown") list.push("Ownership unknown");
  if (!p.services.length) list.push("No health checks");
  else if (monitorError) list.push("Monitor unavailable");
  else if (checkedAt && p.services.some((s) => !results.get(s.url)?.ok))
    list.push("Failing checks");
  if (deepChecks.some((d) => d.project === p.name && !d.ok))
    list.push("Deep check failing");
  if (!p.costs) list.push("No COSTS.md linked");
  if (costs && !itemsFor(p).length) list.push("No matched cost data");
  if (p.resources.some((r) => r.state === "suspended"))
    list.push("Suspended resources");
  return list;
}
function resourceRows(resources) {
  return `<ul class="resource-list">${resources.map((r) => `<li><span>${escape(r.name)}<small>${escape(r.provider)} · ${escape(r.kind)}</small></span><span class="${r.state === "suspended" ? "warn" : "muted"}">${escape(r.state)}<small>${escape(r.source)}</small></span></li>`).join("")}</ul>`;
}
function costRows(items) {
  return `<ul class="cost-list">${items.map((i) => `<li><span>${escape(i.service)}<small>${escape(i.provider)}${i.reason ? " · " + escape(i.reason) : ""}${i.note ? " · " + escape(i.note) : ""}</small></span><span>${i.estimated ? "~ " : ""}${usd(i.amount)}</span></li>`).join("")}</ul>`;
}
function projectRow(p) {
  const state = health(p),
    items = itemsFor(p),
    issues = reasons(p);
  return `<details class="project" data-project="${escape(p.name)}" ${expanded.has(p.name) ? "open" : ""}><summary><span class="identity"><span class="project-name">${escape(p.name)}</span><span class="purpose">${escape(p.purpose)}</span></span><span class="status ${state.tone}">${state.label}<small>${p.resources.length} observed resources</small></span><span class="amount">${items.length ? usd(amountFor(p)) : "—"}<small>${items.length ? "recorded / month · partial" : costs ? "cost unknown" : "costs unavailable"}</small></span></summary><div class="details"><div class="links">${p.repo ? link(p.repo, "Repository") : "Repository unverified"}${p.costs ? link(p.costs, "COSTS.md") : "No COSTS.md linked"}</div>${issues.length ? `<div class="reasons">${issues.map((i) => `<span class="reason">${i}</span>`).join("")}</div>` : ""}<h3>Endpoint checks</h3>${
    p.services.length
      ? `<ul class="checks">${p.services
          .map((s) => {
            const r = results.get(s.url);
            return `<li><span>${link(s.href || s.url, s.name)}</span><span class="${monitorError || !r ? "muted" : r.ok ? "good" : "bad"}">${monitorError || !r ? "Unknown" : `HTTP ${r.status || "timeout"} · ${r.ms} ms`}</span></li>`;
          })
          .join(
            "",
          )}</ul><p class="note">Availability only. Background work and dependent resources need separate coverage.</p>`
      : '<p class="note">No health check is configured. Resource presence does not establish availability.</p>'
  }${deepChecks
    .filter((d) => d.project === p.name)
    .map(
      (d) =>
        `<h3>${escape(d.name)}</h3><p class="${d.ok ? "good" : "bad"}">${escape(d.detail)}</p>`,
    )
    .join(
      "",
    )}<h3>Resources · observed ${escape(p.observedAt)}</h3>${p.resources.length ? resourceRows(p.resources) : '<p class="note">No resource ownership recorded in the surveyed accounts.</p>'}<h3>Recorded monthly costs</h3>${items.length ? costRows(items) : '<p class="note">No matched line items. This does not mean the project is free.</p>'}</div></details>`;
}
function render() {
  const query = $("search").value.trim().toLowerCase();
  const matching = (p) =>
    [
      p.name,
      p.purpose,
      p.repo,
      ...p.resources.flatMap((r) => [r.name, r.provider, r.kind, r.state]),
    ]
      .join(" ")
      .toLowerCase()
      .includes(query);
  let filtered = projects
    .filter(matching)
    .filter((p) => view !== "attention" || reasons(p).length);
  filtered.sort((a, b) =>
    $("sort").value === "cost"
      ? amountFor(b) - amountFor(a) || a.name.localeCompare(b.name)
      : $("sort").value === "resources"
        ? b.resources.length - a.resources.length ||
          a.name.localeCompare(b.name)
        : a.name.localeCompare(b.name),
  );
  document
    .querySelectorAll("[data-view]")
    .forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.view === view)),
    );
  $("totals").innerHTML =
    `<span><strong>${projects.filter((p) => p.ownership !== "unknown").length}</strong> projects</span><span><strong>${projects.reduce((n, p) => n + p.resources.length, 0)}</strong> resources observed</span><span><strong>${projects.reduce((n, p) => n + p.services.length, 0)}</strong> endpoint checks</span><span><strong>${costs ? usd(costs.total) : "—"}</strong> recorded / month${costs?.stale ? " · stale" : ""}</span>`;
  $("view-note").textContent =
    view === "resources"
      ? "Observed resources, including suspended and unassigned assets. Presence is not health; billed resources have not been independently checked."
      : view === "attention"
        ? "Failures, missing coverage, unresolved costs, and suspended resources to review. Nothing here is automatically retired."
        : `${filtered.length} entries · Open a project for its checks, resources, and cost ownership.`;
  if (view === "resources") {
    const rows = filtered
      .map((p) => ({
        ...p,
        resources: p.resources.filter(
          (r) =>
            !query ||
            [p.name, p.purpose, p.repo, r.name, r.provider, r.kind, r.state]
              .join(" ")
              .toLowerCase()
              .includes(query),
        ),
      }))
      .filter((p) => p.resources.length);
    $("groups").innerHTML = rows
      .map(
        (p) =>
          `<details class="project" data-project="${escape(p.name)}" ${expanded.has(p.name) ? "open" : ""}><summary><span class="identity"><span class="project-name">${escape(p.name)}</span><span class="purpose">${p.resources.length} resources · observed ${escape(p.observedAt)}</span></span><span class="status muted">${p.ownership === "unknown" ? "Owner unknown" : "Ownership recorded"}</span><span class="amount">${itemsFor(p).length ? usd(amountFor(p)) : "—"}<small>recorded / month</small></span></summary><div class="details">${resourceRows(p.resources)}</div></details>`,
      )
      .join("");
    const unresolved = (costs?.unresolved || []).filter((i) =>
      [i.service, i.provider, i.reason].join(" ").toLowerCase().includes(query),
    );
    if (unresolved.length)
      $("groups").innerHTML +=
        `<details class="project"><summary><span class="identity"><span class="project-name">Unassigned cost lines</span><span class="purpose">${unresolved.length} items need explicit resource ownership.</span></span><span class="status warn">Needs attribution</span><span class="amount">${usd(unresolved.reduce((n, i) => n + i.amount, 0))}</span></summary><div class="details">${costRows(unresolved)}</div></details>`;
  } else $("groups").innerHTML = filtered.map(projectRow).join("");
  if (!$("groups").children.length)
    $("groups").innerHTML =
      '<p class="empty">No matching entries.<br>Try a project name, provider, or resource.</p>';
  document.querySelectorAll("details[data-project]").forEach((d) =>
    d.addEventListener("toggle", () => {
      if (!d.isConnected) return;
      d.open
        ? expanded.add(d.dataset.project)
        : expanded.delete(d.dataset.project);
    }),
  );
}
async function refresh() {
  if (busy || !projects.length) return;
  busy = true;
  $("refresh").disabled = true;
  $("summary").textContent = "Loading the latest fleet results…";
  try {
    const report = await checkAll(projects);
    results = report.results;
    deepChecks = report.deepChecks || [];
    checkedAt = report.checkedAt;
    monitorError = null;
    const failed = [...results.values()].filter((r) => !r.ok).length;
    $("summary").textContent =
      `${failed ? `${failed} endpoint checks failing` : `${results.size} endpoint checks passing`} · ${new Date(checkedAt).toLocaleTimeString()} · checked by Prefect every 15m`;
  } catch (error) {
    monitorError = error;
    results = new Map();
    deepChecks = [];
    $("summary").textContent =
      `Monitor unavailable: ${error.message}. Service health is unknown.`;
  } finally {
    busy = false;
    $("refresh").disabled = false;
    render();
  }
}
document.querySelectorAll("[data-view]").forEach((b) =>
  b.addEventListener("click", () => {
    view = b.dataset.view;
    history.replaceState(null, "", `#${view}`);
    render();
  }),
);
$("search").addEventListener("input", render);
$("sort").addEventListener("change", render);
$("refresh").addEventListener("click", refresh);
try {
  projects = await loadInventory();
  $("inventory-date").textContent = projects[0].observedAt;
  render();
  refresh();
  loadCosts(projects)
    .then((loaded) => {
      costs = loaded;
      $("cost-note").innerHTML =
        `${loaded.stale ? "Stale snapshot. " : ""}Costs as of ${escape(new Date(loaded.generatedAt).toLocaleString())}; estimates and gaps included. ${loaded.unresolved.length} unassigned lines. ${link(COSTS_URL, "Source snapshot")}`;
      render();
    })
    .catch((error) => {
      $("cost-note").textContent =
        `Cost snapshot unavailable: ${error.message}. No totals inferred.`;
    });
  setInterval(() => {
    if (!document.hidden) refresh();
  }, 60_000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
} catch (error) {
  $("search").disabled = true;
  $("sort").disabled = true;
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.disabled = true;
  });
  $("totals").textContent = "Inventory unavailable";
  $("summary").textContent = error.message;
  $("cost-note").textContent = "";
  $("refresh").disabled = true;
  $("groups").innerHTML =
    '<p class="empty">The inventory could not be loaded. Reload this page to retry.</p>';
}
