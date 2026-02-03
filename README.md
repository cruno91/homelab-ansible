# Ansible Management for Homelab

This repo manages OSes for the fleet of hardware and VMs running in the homelab.

Current roles:
- Apply a safe baseline for all OSes
- Manage tagged upgrades
- Manage Portainer for the simple Docker containers needed in the lab
- Keep roles semantic and not procedural

---

## Managed Hosts
| Hostname        | Purpose        | OS                     |
|-----------------|----------------|------------------------|
| `portainer-pi`  | Docker + Portainer | Raspberry Pi OS Lite |
| `codeserver-pi` | Baseline only | Raspberry Pi OS Lite |

---

## Repo Structure

```text
├── group_vars
│ └── all.yml
├── inventory
│ └── hosts.ini
├── playbooks
│ ├── portainer.yml
│ ├── roles
│ │ ├── base
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ └── portainer
│ │     └── tasks
│ │         └── main.yml
│ └── site.yml
```

### Layout notes

- Groups define behavior rather than host-specific conditionals
- OS management and application management are separate playbooks
- Upgrades are opt-in via tags

## Commands

1. Sanity check

	```bash
	ansible -i inventory/hosts.ini rpis -m ping
	```

2. Apply base config (sans-upgrades)

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/site.yml --ask-become-pass
	```

3. Perform OS upgrades

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags os_upgrade --ask-become-pass
	```

4. Update Portainer

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/portainer.yml --ask-become-pass
	```

5. Reboot manually

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/site.yml --ask-become-pass --tags manual_reboot
	```

### Ad-hoc commands

Example:

```bash
ansible -i inventory/hosts.ini rpis \
  -m shell \
  -a "readlink -f /usr/bin/python3 && /usr/bin/python3 --version"
```

### Dry-runs

Add `--check --deff` to commands.
