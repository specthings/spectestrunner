<!--
SPDX-License-Identifier: CC-BY-SA-4.0

Copyright (C) 2026 embedded brains GmbH & Co. KG
-->

## Overview

The *spectestrunner* Python package provides a client and sever to run test
executables.  The package uses the specification item framework provided by
[specitems](https://github.com/specthings/specitems)
for the server configuration.

The package is maintained by the
[specthings](https://github.com/specthings)
project.

## Contributing

Please refer to our
[Contributing Guidelines](https://github.com/specthings/spectestrunner/blob/main/CONTRIBUTING.md).

## Commands

### Command - spectestrun

The `spectestrun` command runs an executable on a test server, for example:

```
spectestrun --target=aarch64/zynqmp_apu ticker.exe
```

Use `--fail-on-status` to exit with a non-zero status if a run reports another
status than the expected one, for example `--fail-on-status=success`.

### Command - spectestgitrun

The `spectestgitrun` command runs an executable on a test server which is
reachable only through a Git remote, for example:

```
spectestgitrun --remote=git@host:bench.git --target=aarch64/zynqmp_apu ticker.exe
```

It prepares the executable exactly like `spectestrun` does, commits it, pushes
the commit, waits for the response commit, and prints the results.  See the
*Git mediated transport* section below.

### Command - spectestgitbridge

The `spectestgitbridge` command polls a Git remote for requests and answers
them through gRPC.  Run it next to the test server, for example:

```
spectestgitbridge --remote=git@host:bench.git --work-dir=/var/lib/spectest/bridge.git
```

### Command - spectestaction

The `spectestaction` command runs actions on a test server, for example:

```
spectestaction --server-address=foobar:50051 /switch/some-switch:activate:some-board
```

Each request has the format `<uid>:<action>`.  The available actions are
`activate:<name>[:<lease>]`, `deactivate[:<name>]` and `status`.

The command exits with a non-zero status if an action reports an error.

### Activation leases

An agent item may have an `activation-lease-in-seconds` attribute.  If it is
present and the value is neither null nor zero, then activations of the agent
have a lease.  Once the lease expires, the agent deactivates the resource of
the most recent activation.  This limits the time a resource stays claimed by a
client which crashed or forgot to release it.

Only the most recent activation is covered by a lease.  Use the attribute for
agents which provide one resource at a time, not for an agent which holds
several resources at once.

Activating an already active agent renews the lease.  There is no separate
renew action.  Clients hold a resource by repeating the activation, for example

```
spectestaction --server-address=foobar:50051 /service/some-peer:activate:bc:600
```

every few minutes.  The optional third field overrides the lease of the item
for this activation.  A value of zero activates without a lease.

The `status` action returns the activation state and the remaining lease
without changing either:

```
spectestaction --server-address=foobar:50051 /service/some-peer:status
```

The activation name selects the resource of the agent.  For a subprocess input
agent with a `command-by-name` attribute the name selects the command to run,
so activating with another name terminates the running subprocess and runs the
command of the new name.  Since such an agent runs at most one subprocess, the
names are mutually exclusive by construction.

### Command - spectestlog

The `spectestlog` command displays the test server log messages.  Logs from
multiprocessing processes are not displayed.  We have to change the logging
handlers to make this work.

The command streams until it is interrupted.  Use `--max-lines` to stop after a
number of messages and `--timeout` to stop after a number of seconds, so that
the command can be used in a script.

### Command - spectestio

The `spectestio` command displays input multicasts.  This command is work in
progress.

The command streams until it is interrupted.  Use `--max-lines` to stop after a
number of responses and `--timeout` to stop after a number of seconds.

### Command - spectestserver

The `spectestserver` command runs the test server.  You have to provide a
server configuration.  The command exits with 7 if the specification of the
server is not valid.

### Exit codes

The commands share these exit codes:

| Code | Meaning |
| ---- | ------- |
| 0 | The command did its work.  A run may still have reported a failure. |
| 1 | The request was permanently refused. |
| 2 | The command line is not usable. |
| 3 | A transport operation failed, for example the server is unreachable. |
| 4 | A run reported a status other than the one of `--fail-on-status`. |
| 5 | The request vanished before a response arrived. |
| 6 | An action failed, so the steps after it did not run. |
| 7 | The specification of the server is not valid. |
| 8 | No response arrived before the wait timeout expired. |

Code 2 is the one `argparse` itself uses, so an unknown option and a
malformed value of ours mean the same thing.

## Git mediated transport

The `spectestgitrun` and `spectestgitbridge` commands use a Git remote as the
only channel between a submitter and a test server.  The submitter never talks
to the test server and the test server never talks to the submitter.

### References

Every request and every response is a parentless commit reachable through
exactly one reference:

```
refs/spectest/requests/<submitter>/<request-id>
refs/spectest/responses/<request-id>
```

The `<request-id>` is the object identifier of the request commit itself.  Only
submitters write request references and only the bridge writes response
references, so the two sides never contend for the same reference.

The submitter deletes the request reference and then the response reference
once it has collected the result.  The bridge deletes both references of a
request which is older than `--response-retention`.  Since the commits have no
parents, nothing accumulates in the history and the objects become unreachable
as soon as the references are gone.

### Request commit

The commit message carries the request as a YAML document delimited by
`--- spectest-request ---` and `--- end ---`.  The stripped executables are
files below `images/` in the commit.  The submitter runs `nm` and `strip`
locally, so the bridge host needs no target toolchain.

```
spectest: request 1 image for aarch64/zynqmp_apu

--- spectest-request ---
version: 1
kind: run-images
submitter: sebhub-at-workstation
target: aarch64/zynqmp_apu
timeout: 180.0
images:
- path: build/ticker.exe
  file: images/0000-ticker.exe
  digest: sha256:3f8a2c1e...
  breakpoints:
  - 4096
--- end ---
```

### Response commit

The commit message carries the response as a YAML document delimited by
`--- spectest-response ---` and `--- end ---`.  The raw output bytes of each
run are files below `output/` in the commit.  The `status` is either
`completed` or `rejected`, and a rejected response states the `reason`.

### Failures

The bridge answers a request which it can never run, for example one with a
malformed YAML document, an unknown kind, or a digest mismatch, with a rejected
response.  It leaves a request pending and retries it on the next poll if the
test server is temporarily unreachable or if the response cannot be pushed.
After `--max-attempts` failed attempts the request is rejected as well.

A retry repeats the whole batch, so an image which already ran before a later
image of the same request failed transiently runs again.  Keep this in mind for
tests with side effects.

The bridge creates a response reference with a lease so that it fails instead
of overwriting a response which another bridge pushed first.  Git enforces fast
forward updates only below `refs/heads` and `refs/tags`, so an ordinary push
would silently replace the existing response.

The `spectestgitrun` command uses the shared exit codes described above.  It
exits with 0 if the round trip completed, whatever the reported run status was,
with 1 if the bridge rejected the request, with 2 if the command line is not
usable, with 3 on a Git or transport error, with 4 if `--fail-on-status` was
given and a run reported another status, with 5 if the remote has neither a
request nor a response reference for the request, so that no response can ever
arrive, with 6 if an action step failed, and with 8 if it gave up waiting.

### Security

The Git remote is the only authorization boundary.  The bridge does not verify
commit signatures, so everybody who can push to the remote can run executables
on the test hardware, and so can everybody who compromises the Git host.  Use a
remote whose write access is restricted to the submitters you trust.

Deleting a reference only makes its objects unreachable.  Reclaiming the disk
space is up to the hosting side, which has to run `git gc --prune`.  A
dedicated repository you administer yourself is therefore the deployment which
actually bounds the growth.
