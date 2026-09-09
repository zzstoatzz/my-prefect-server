"""Resolve costs against declared provider/resource ownership in the inventory."""

from mps.inventory import load_projects

UNATTRIBUTED = "unattributed"


def project_for(resource: str, provider: str | None = None) -> str:
    """Match a resource name or colon-delimited billing suffix; never guess ownership."""
    owners: set[str] = set()
    for project in load_projects():
        for key in project.costKeys:
            declared_provider, name = key.split(":", 1)
            if provider is not None and provider != declared_provider:
                continue
            if resource == name or resource.startswith(name + ":"):
                owners.add(project.name)
    return owners.pop() if len(owners) == 1 else UNATTRIBUTED
