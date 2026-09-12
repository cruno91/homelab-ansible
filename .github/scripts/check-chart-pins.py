#!/usr/bin/env python3
"""Fail if homelab-ansible's bootstrap chart pins drift from homelab-gitops.

gitops is the source of truth (ADR-0004): Ansible installs these charts once on
day 0, Argo CD owns them from day 1. If the two pins disagree, a from-scratch
rebuild installs one version and Argo immediately converts it to the other.
"""
import sys, urllib.request, yaml

RAW = "https://raw.githubusercontent.com/cruno91/homelab-gitops/main"
ANSIBLE_VARS = "inventory/group_vars/rke2-mgmt/main.yml"

# ansible var name -> (gitops file, chart name as it appears in .sources[].chart)
PAIRS = {
    "certmanager_chart_version": ("platform/cert-manager/appset.yaml", "cert-manager"),
    "argocd_chart_version":      ("bootstrap/argocd-app.yaml",         "argo-cd"),
}

def fetch(path):
    with urllib.request.urlopen(f"{RAW}/{path}", timeout=30) as r:
        return yaml.safe_load(r.read())

def find_sources(node):
    """Yield every `sources` list found anywhere in the document."""
    if isinstance(node, dict):
        if isinstance(node.get("sources"), list):
            yield node["sources"]
        for v in node.values():
            yield from find_sources(v)
    elif isinstance(node, list):
        for v in node:
            yield from find_sources(v)

def chart_revision(doc, chart):
    for sources in find_sources(doc):
        for src in sources:
            if isinstance(src, dict) and src.get("chart") == chart:
                rev = src.get("targetRevision")
                if rev is not None:
                    return str(rev)
    return None

def main():
    with open(ANSIBLE_VARS) as f:
        ansible = yaml.safe_load(f)

    failures, checked = [], []
    for var, (path, chart) in PAIRS.items():
        want = chart_revision(fetch(path), chart)
        have = str(ansible.get(var, "")).strip()
        if want is None:
            failures.append(f"{var}: could not find chart '{chart}' in gitops {path}")
            continue
        if have != want:
            failures.append(
                f"{var}: ansible has '{have}', gitops {path} has '{want}'\n"
                f"    fix: set {var}: \"{want}\" in {ANSIBLE_VARS}"
            )
        else:
            checked.append(f"  OK  {var} = {have}  (matches gitops {path})")

    print("\n".join(checked) or "  (nothing checked)")
    if failures:
        print("\nVERSION DRIFT between homelab-ansible and homelab-gitops:\n")
        for f in failures:
            print(f"  - {f}")
        print("\ngitops is the source of truth (ADR-0004). Update the Ansible pin to match.")
        sys.exit(1)
    print("\nAll bootstrap chart pins match homelab-gitops.")

if __name__ == "__main__":
    main()
