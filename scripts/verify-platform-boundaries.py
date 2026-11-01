"""Inspect local Compose boundaries without displaying environment values or documents."""

import json
import subprocess


def inspect(service: str) -> dict:
    identifier = subprocess.check_output(
        ["docker", "compose", "--env-file", "services/backend/.env.app", "-f", "compose.full.yaml", "ps", "-q", service],
        text=True,
    ).strip()
    if not identifier:
        raise SystemExit(f"Service is not running: {service}")
    return json.loads(subprocess.check_output(["docker", "inspect", identifier], text=True))[0]


def networks(container: dict) -> set[str]:
    return set(container["NetworkSettings"]["Networks"])


def main() -> None:
    containers = {service: inspect(service) for service in ("marketing", "web", "inquiries", "api", "worker", "parser")}
    public = networks(containers["marketing"]) | networks(containers["inquiries"])
    operations = networks(containers["api"]) | networks(containers["web"])
    assert public.isdisjoint(operations), "Public and operations networks overlap"
    assert networks(containers["parser"]).isdisjoint(operations), "Parser can reach operations network"
    assert networks(containers["parser"]) <= networks(containers["worker"]), "Worker cannot reach parser"
    for service in ("marketing", "inquiries", "parser"):
        container = containers[service]
        names = {value.split("=", 1)[0] for value in container["Config"]["Env"]}
        assert not any(name.startswith(("CGL_", "AWS_", "OPENAI_")) for name in names), f"Unexpected operations credentials in {service}"
        assert container["Config"]["User"] not in {"", "root", "0"}, f"Root service: {service}"
        assert container["HostConfig"]["ReadonlyRootfs"], f"Writable root filesystem: {service}"
    assert not containers["parser"]["Mounts"], "Parser has a persistent mount"
    for service in ("parser", "inquiries"):
        assert not any(containers[service]["NetworkSettings"]["Ports"].values()), f"Private service has a published port: {service}"
    for service in ("marketing", "web"):
        assert any(containers[service]["NetworkSettings"]["Ports"].values()), f"Browser service has no published port: {service}"
    print("Verified: public and operations networks separated; parser worker-only; public/parser credentials, mounts, ports and runtime privileges bounded.")


if __name__ == "__main__":
    main()
