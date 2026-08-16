# amplifier-bundle-windows-shell

Native Windows shell support for Amplifier.

Amplifier's `bash` tool is the single largest tool surface in the product —
**54.4% of real tool traffic** in a 30-day sample of actual usage. On Windows
it resolves to WSL bash, Git Bash, or nothing at all. This bundle adds `pwsh`,
so agents on Windows get a shell that is actually native to the machine they
are running on.

## Install

```bash
amplifier bundle add ./amplifier-bundle-windows-shell --app
```

Composing it `--app` makes it available to every session. That is safe on any
platform: on non-Windows hosts the tool degrades without error rather than
failing to mount.

## What you get

| | |
|---|---|
| `pwsh` tool | PowerShell command execution, background jobs, output truncation, timeout handling |
| Safety profiles | `strict` / `standard` / `permissive` / `unrestricted` — same config surface as `tool-bash` |
| Windows context | Always-on guidance so agents prefer the native shell and know the Unix→PowerShell equivalents |
| PowerShell skill | On-demand deeper guidance via `load_skill` |

Configure it exactly as you would `tool-bash`:

```yaml
tools:
  - module: tool-pwsh
    config:
      safety_profile: standard
```

## Why a separate tool and not a bash backend

The tool name is the highest-leverage token in a tool description. `bash` is a
one-token, heavily-weighted prior for *syntax* — a tool named `bash` that
executes PowerShell spends description tokens on every single request fighting
its own name, forever. Naming the tool `pwsh` wins that argument for free.

The scoping matters too. This bundle adds a module; it changes nothing about
`tool-bash`. On POSIX, the shell path stays byte-identical to what shipped —
structurally impossible to regress, which is stronger than any argument that a
shared change is probably fine.

## Verified

The `pwsh` tool is proven working on native Windows 11 — not inferred, not
unit-tested-only. See [`docs/EVIDENCE.md`](docs/EVIDENCE.md), which includes
the confound checks and the hazards found but not yet fixed.

## Credits

This bundle is a **packaging effort**. The substance is
[@colombod](https://github.com/colombod)'s module and
[@DavidKoleczek](https://github.com/DavidKoleczek)'s reference architecture.
See [`ATTRIBUTION.md`](ATTRIBUTION.md) — it is worth reading before the code.

## Status

Local proof-of-concept, destined for the `microsoft` org. Known gaps and their
attribution are tracked in [`docs/ROADMAP.md`](docs/ROADMAP.md).
