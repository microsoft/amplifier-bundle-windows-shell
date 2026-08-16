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
| `pwsh` tool | PowerShell execution with a Win32 Job Object, pinned encoding, and correct exit-code semantics |
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
`tool-bash`. On POSIX the shell path stays byte-identical to what shipped —
structurally impossible to regress, which is stronger than any argument that a
shared change is probably fine.

## Verified on real Windows

```
Windows 11 (build 26200):  185 passed, 0 skipped
Linux:                     171 passed, 14 skipped
```

The 14 skipped tests are the live-execution suite — inert on Linux by design,
and **confirmed to have actually run** on Windows against both `pwsh` 7 and
`powershell` 5.1, counted rather than assumed.

That distinction is not pedantry. The Linux suite was green while **two real
bugs were live** — output encoding pinned in only one direction, and `$?` read
in a position where it reports the wrong thing. The first Windows run found
both immediately. Three contract tests turned out to be *pinning the bug*: they
passed, and fixing the runner made them fail.

[`docs/EVIDENCE.md`](docs/EVIDENCE.md) has the measured mojibake, the exit
codes, the teeth-checks, and a plain list of what is still **not** proven.

## Credits

The implementation is ours. Two prior works were studied as reference and
shaped the design — [@colombod](https://github.com/colombod)'s
`amplifier-module-tool-pwsh` and
[@DavidKoleczek](https://github.com/DavidKoleczek)'s `agent-server`. No code
was copied from either.

[`ATTRIBUTION.md`](ATTRIBUTION.md) records what each contributed, including a
decision of David's we got wrong first and had to rediscover the expensive way.
It is worth reading before the code.

## Status

Local proof-of-concept, destined for the `microsoft` org. Open items and known
foundation-level gaps are tracked in [`docs/ROADMAP.md`](docs/ROADMAP.md).
