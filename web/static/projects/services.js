export const PROXY_URL = "/api";

export async function loadInventory() {
  const response = await fetch("/api/projects.json", {
    signal: AbortSignal.timeout(15_000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Inventory HTTP ${response.status}`);
  const projects = await response.json();
  if (
    !Array.isArray(projects) ||
    !projects.length ||
    projects.some(
      (project) =>
        typeof project.name !== "string" ||
        typeof project.purpose !== "string" ||
        !Array.isArray(project.resources) ||
        !Array.isArray(project.services) ||
        !Array.isArray(project.costKeys) ||
        project.services.some(
          (service) =>
            typeof service.name !== "string" ||
            !service.url?.startsWith("https://"),
        ),
    )
  )
    throw new Error("Invalid project inventory");
  return projects;
}
