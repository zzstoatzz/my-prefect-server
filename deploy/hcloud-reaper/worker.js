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

export async function reap(env) {
  if (!env.HCLOUD_TOKEN) throw new Error("Missing project credential");
  const now = Math.floor(Date.now() / 1000);
  const removed = [];
  for (const candidate of await inventory(env.HCLOUD_TOKEN, "servers")) {
    const owned = identity(candidate);
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
  console.log(JSON.stringify({ event: "builder_expiry_cleanup", checked_at: now, removed }));
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
