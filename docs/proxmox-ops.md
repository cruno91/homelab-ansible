# Proxmox VE Operations

Operational notes for Proxmox VE hosts managed by this repo. Covers the host facts, the deliberate "no auto-reboot" policy, and the opt-in first-run post-install.

All commands assume you are at the repo root on your workstation. The Ansible control node is your laptop; the managed host is the Proxmox node in `[proxmox]`.

---

## Host facts

| Item | Value |
|---|---|
| Inventory group | `[proxmox]` in `inventory/hosts.ini` |
| Hosts | `proxmox-node-1` (10.0.3.6) |
| OS | Proxmox VE 9.2.x (Debian 13 / Trixie base) |
| SSH user | `root` |
| Post-install marker | `/var/lib/proxmox-post-install.done` on the node |
| Staging dir for post-install | `/opt/proxmox-post-install/` on the node |

---

## Reboot policy

Proxmox hosts run VMs, so the reboot decision belongs to a human. The `base` role's reboot tasks self-skip for any host in the `proxmox` group:

- `--tags os_upgrade` patches packages and leaves any pending reboot for you.
- `--tags manual_reboot` is a no-op on Proxmox hosts.

If `/var/run/reboot-required` is set after an `os_upgrade` run, schedule the reboot yourself once VMs are in a state you're happy with.

The **one exception** is the first-run post-install below, which reboots the node exactly once (driven by Ansible's `reboot` module, not the script) and then drops a marker file so it never runs again.

---

## First-run post-install

Opt-in via `--tags proxmox_post_install`. Runs the community-scripts [`post-pve-install.sh`](https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/tools/pve/post-pve-install.sh) unattended.

### What it does

The upstream script is fully interactive (whiptail menus, no `--yes` flag). The role drops a stub `whiptail` earlier in `PATH` (`playbooks/roles/proxmox/files/whiptail`) that returns canned answers keyed off each dialog's `--title`. Selected options:

| Step | Answer | Effect |
|---|---|---|
| Legacy `*.list` cleanup (yes/no) | yes | If the install uses legacy apt sources, rename every `*.list` → `*.list.bak` and comment `deb` lines in `/etc/apt/sources.list`. Skipped when the install is already deb822. This is also what disables `pve-enterprise.list` — the script's `PVE-ENTERPRISE` step only operates on `.sources` files. |
| `SOURCES` (Debian deb822 rewrite) | yes | Writes `/etc/apt/sources.list.d/debian.sources` for `trixie` + `trixie-security` + `trixie-updates`. Skipped when the install is already deb822. |
| `PVE-ENTERPRISE` | disable | Disables the `pve-enterprise` deb822 source if present (PVE 9 installs that ship deb822 do; legacy installs are covered by the cleanup row above) |
| `CEPH-ENTERPRISE` | disable | Sets `Enabled: false` in `ceph.sources` so `apt update` doesn't 401 on `ceph-squid`. We're not using Ceph; flip to `keep` if you ever do. |
| `PVE-NO-SUBSCRIPTION` | yes | Writes `/etc/apt/sources.list.d/proxmox.sources` for the free PVE package repo |
| `CEPH PACKAGE REPOSITORIES` | no | Not using Ceph |
| `PVETEST` | no | Stay on stable |
| `SUBSCRIPTION NAG` | yes | Patches the web UI to suppress the login nag |
| `HIGH AVAILABILITY` | no | Keep HA state as-is |
| `UPDATE` (apt dist-upgrade) | no | Handled by `--tags os_upgrade` instead |
| `REBOOT` | no | Ansible owns the reboot so the connection re-establishes properly |

End state regardless of whether the node started on legacy or deb822: all apt sources live in `/etc/apt/sources.list.d/*.sources`, any prior `*.list` files are preserved as `*.list.bak`, `pve-enterprise` is disabled, `pve-no-subscription` is active.

### Running it

```bash
ansible-playbook playbooks/site.yml --tags proxmox_post_install --ask-become-pass
```

Routine `site.yml` runs do **not** trigger this. The role is in its own play tagged `[never, proxmox_post_install]`, mirroring `os_upgrade` and `manual_reboot`.

After a successful run the role:

1. Touches `/var/lib/proxmox-post-install.done`.
2. Reboots the node and waits for it to come back (`test_command: pveversion`).

Re-invoking the tag after the marker exists is a single `stat` and exit.

### Forcing a re-run

```bash
ssh root@10.0.3.6 rm /var/lib/proxmox-post-install.done
ansible-playbook playbooks/site.yml --tags proxmox_post_install --ask-become-pass
```

### Verifying it took effect

The Proxmox node's `/bin/sh` is dash (no `shopt`), so the shell-module
commands below redirect stderr away and use `||` to handle the
"no `.sources` files" case instead of relying on bash nullglob.

```bash
# Inspect what's in the apt sources directory. Should show .sources
# files (and possibly .list.bak files from the legacy sweep).
ansible proxmox -a "ls /etc/apt/sources.list.d/"

# pve-enterprise should be disabled — either absent or marked
# `Enabled: false` in its .sources file.
ansible proxmox -m shell -a "grep -H -E '^(Components|Enabled)' /etc/apt/sources.list.d/pve-enterprise.sources 2>/dev/null || echo 'pve-enterprise.sources removed (good)'"

# pve-no-subscription should be present and active in a .sources file
# (expected: /etc/apt/sources.list.d/proxmox.sources).
ansible proxmox -m shell -a "grep -l pve-no-subscription /etc/apt/sources.list.d/*.sources 2>/dev/null || echo 'pve-no-subscription NOT present'"

# apt update should succeed cleanly with no 401s.
ansible proxmox -a "apt update"

# Nag patch helper and apt hook should exist.
ansible proxmox -a "ls -l /usr/local/bin/pve-remove-nag.sh /etc/apt/apt.conf.d/no-nag-script"
```

---

## Caveats

- **Title-coupled wrapper.** The `whiptail` shim decides each answer from the dialog's `--title`. If community-scripts renames a title upstream, that step silently no-ops (default branch returns `"no"`). Pinning the URL in `playbooks/roles/proxmox/tasks/main.yml` to a commit SHA instead of `main` removes this risk at the cost of missing upstream fixes.
- **Telemetry.** The script's `init_tool_telemetry "post-pve-install" "pve"` call at the top has no kill switch upstream; it will fire on each run.
- **SSH access.** Before the first run, your workstation's SSH key must be in `root@10.0.3.6:~/.ssh/authorized_keys`. The inventory uses `ansible_user=root` because Proxmox ships with root SSH enabled and sudo would be redundant.

---

## Reference: file layout

```
playbooks/
├── site.yml                        # Proxmox baseline play + tagged post-install play
└── roles/proxmox/
    ├── files/
    │   └── whiptail                # Canned-answers stub for the post-install script
    └── tasks/
        └── main.yml                # Marker check → download → run → mark → reboot
```
