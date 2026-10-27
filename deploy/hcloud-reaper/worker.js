const selector = "owner=my-prefect-server,purpose=ephemeral-builder";

function identity(resource, suffix = "") {
  const labels = resource.labels;
  if (
    labels?.owner !== "my-prefect-server" ||
    labels.purpose !== "ephemeral-builder" ||
    !/^[0-9a-f]{32}$/.test(labels["flow-run"]) ||
    !/^(0|[1-9][0-9]*)$/.test(labels.attempt) ||
    !/^[0-9]{10}$/.test(labels.expires)
  ) throw new Error("Invalid builder ownership labels");
  const name = `prefect-build-${labels["flow-run"]}-r${labels.attempt}`;
  if (resource.name !== name + suffix) throw new Error("Builder ownership name disagrees");
  return { name, expires: Number(labels.expires) };
}

async function api(token, method, path, body) {
  const response = await fetch("https://api.hetzner.cloud/v1/" + path, {
    method,
    headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(20000),
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Hetzner ${method}: HTTP ${response.status}`);
  return response.status === 204 ? null : response.json();
}

async function inventory(token, kind) {
  const result = [];
  for (let page = 1; page <= 20; page++) {
    const query = new URLSearchParams({ label_selector: selector, per_page: "50", page: String(page) });
    const data = await api(token, "GET", `${kind}?${query}`);
    if (!Array.isArray(data?.[kind])) throw new Error("Invalid resource inventory");
    result.push(...data[kind]);
    if (!data.meta.pagination.next_page) return result;
  }
  throw new Error("Resource inventory exceeded safety limit");
}

async function bill(env, server, pricing, phase) {
  const location = server.location?.name ?? server.datacenter.location.name;
  const serverPrice = pricing.server_types.find(type => type.id === server.server_type.id)
    ?.prices.find(price => price.location === location);
  const ipPrice = pricing.primary_ips.find(ip => ip.type === "ipv4")
    ?.prices.find(price => price.location === location);
  if (!serverPrice || !ipPrice || pricing.currency !== "USD") throw new Error("Builder pricing unavailable");
  const key = `billing/hetzner-builders/${server.id}-reaper-${phase}.json`;
  if (await env.BILLING.head(key)) return;
  await env.BILLING.put(key, JSON.stringify({
    schema: 1, id: server.id, name: server.name, flow_run_id: server.labels["flow-run"],
    project: "typeahead", created_at: server.created, observed_at: new Date().toISOString(),
    phase, currency: pricing.currency, server_type: server.server_type.name, location,
    server_price: serverPrice, ipv4_price: ipPrice,
    method: "Independent reaper lifecycle observation; hourly list prices, excludes traffic overage",
  }));
}

export async function reap(env) {
  if (!env.HCLOUD_TOKEN) throw new Error("Missing project credential");
  const now = Math.floor(Date.now() / 1000);
  const removed = [];
  let pricing;
  const billingErrors = [];
  try { pricing = (await api(env.HCLOUD_TOKEN, "GET", "pricing")).pricing; }
  catch { billingErrors.push("pricing unavailable"); }
  async function safeBill(server, phase) {
    try { await bill(env, server, pricing, phase); }
    catch { billingErrors.push(`receipt unavailable for ${server.id} ${phase}`); }
  }
  for (const candidate of await inventory(env.HCLOUD_TOKEN, "servers")) {
    const owned = identity(candidate);
    await safeBill(candidate, "created");
    if (owned.expires >= now) continue;
    const current = await api(env.HCLOUD_TOKEN, "GET", `servers/${candidate.id}`);
    if (!current) continue;
    const server = current.server;
    if (identity(server).expires >= now || server.id !== candidate.id) continue;
    const ipId = server.public_net.ipv4?.id;
    if (ipId) {
      const result = await api(env.HCLOUD_TOKEN, "GET", `primary_ips/${ipId}`);
      if (result?.primary_ip.assignee_id !== server.id) throw new Error("IP ownership changed");
      await api(env.HCLOUD_TOKEN, "PUT", `primary_ips/${ipId}`, { auto_delete: true });
    }
    await api(env.HCLOUD_TOKEN, "DELETE", `servers/${server.id}`);
    const after = await api(env.HCLOUD_TOKEN, "GET", `servers/${server.id}`);
    if (!after) await safeBill(server, "deleted");
    removed.push({ kind: "server", id: server.id, name: owned.name, created: server.created });
  }
  for (const key of await inventory(env.HCLOUD_TOKEN, "ssh_keys")) {
    const owned = identity(key, "-key");
    if (owned.expires >= now) continue;
    const query = new URLSearchParams({ name: owned.name });
    const data = await api(env.HCLOUD_TOKEN, "GET", `servers?${query}`);
    if (!Array.isArray(data?.servers)) throw new Error("Invalid server existence response");
    if (data.servers.length) continue;
    await api(env.HCLOUD_TOKEN, "DELETE", `ssh_keys/${key.id}`);
    removed.push({ kind: "ssh_key", id: key.id, name: owned.name });
  }
  for (const candidate of await inventory(env.HCLOUD_TOKEN, "primary_ips")) {
    if (identity(candidate, "-ipv4").expires >= now || candidate.assignee_id !== null) continue;
    const result = await api(env.HCLOUD_TOKEN, "GET", `primary_ips/${candidate.id}`);
    if (!result) continue;
    const ip = result.primary_ip;
    if (identity(ip, "-ipv4").expires >= now || ip.assignee_id !== null) continue;
    await api(env.HCLOUD_TOKEN, "DELETE", `primary_ips/${ip.id}`);
    removed.push({ kind: "primary_ip", id: ip.id, name: ip.name });
  }
  let cursor;
  try { do {
    const page = await env.BILLING.list({ prefix: "billing/hetzner-builders/", cursor });
    for (const object of page.objects) {
      if (!object.key.endsWith("-reaper-created.json")) continue;
      const receipt = await (await env.BILLING.get(object.key)).json();
      const deletedKey = `billing/hetzner-builders/${receipt.id}-reaper-deleted.json`;
      if (await env.BILLING.head(deletedKey)) continue;
      if (await api(env.HCLOUD_TOKEN, "GET", `servers/${receipt.id}`)) continue;
      await env.BILLING.put(deletedKey, JSON.stringify({ ...receipt, phase: "deleted", observed_at: new Date().toISOString() }));
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor); } catch { billingErrors.push("billing reconciliation unavailable"); }
  console.log(JSON.stringify({ event: "builder_expiry_cleanup", checked_at: now, removed }));
  if (billingErrors.length) throw new Error("Builder cleanup completed; " + billingErrors.join("; "));
  return removed;
}

export default {
  async scheduled(_controller, env) {
    await reap(env);
  },
  fetch() {
    return new Response(null, { status: 404 });
  },
};
