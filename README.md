<!--
SPDX-License-Identifier: CC-BY-SA-4.0

Copyright (C) 2026 embedded brains GmbH & Co. KG
-->

## Overview

The *spectestrunner* Python package provides commands and modules which run
test executables on a test server.  The package provides the protocol
definitions.

The package is maintained by the
[specthings](https://github.com/specthings)
project.

## Contributing

Please refer to our
[Contributing Guidelines](https://github.com/specthings/spectestrunner/blob/main/CONTRIBUTING.md).

## Step sequences

The `spectestrun` and `spectestgitrun` commands both run a sequence of steps on
a test server.  They differ only in the transport which carries the sequence,
so everything in this section applies to either of them.

Use `--image`, `--action` and `--wait` to build the sequence, which runs in the
order of the command line:

```
spectestrun --target=aarch64/zynqmp_apu \
  --action=/service/some-peer:activate:bc --image=bc.exe --wait=5 \
  --image=rt.exe --action=/service/some-peer:deactivate
```

An `--image` step runs an executable, an `--action` step requests an action of
an agent, and a `--wait` step delays the sequence.  Several images may also be
given as positional arguments, which is the short form of a sequence of nothing
but images.  Positional arguments cannot be mixed with the step options, since
the command line gives no order between the two.

### Failure policy

A step which fails stops the sequence and the steps after it are reported as
skipped.  A failed image step is the exception, because a run which reports a
failure produced the result the sequence asked for, while a failed action
falsified the precondition of everything after it.

Use `--continue-on-failure` and `--stop-on-failure` to override this for the
step which precedes the option.

### Waits

A wait lets the hardware settle between two runs.  It delays only the sequence
it belongs to, and the resources the sequence activated stay claimed for its
duration.

With `spectestgitrun` the wait happens on the bridge, which serves the requests
of everybody one after the other, so **a wait holds up the whole bench for its
duration**.  Keep the waits short and remember that the bridge has no limit of
its own.  A wait which is longer than `--wait-timeout` makes the command give up
before the response arrives.  It warns about this, and the response is still
collectable later with `--collect`.

An activation lease keeps running during a wait, so a wait which is longer than
the lease of a resource the sequence activated loses that resource.

### Expected run status

Use `--fail-on-status` to exit with a non-zero status if a run reports another
status than the expected one, for example `--fail-on-status=success`.  Only an
image step reports a status which the caller can expect of it.

A step which never reached its target is a failure of its own, whether the
server was unreachable or the target has no image runner.  Such a step is not a
run which merely reported a failure, so it exits non-zero without
`--fail-on-status`.

### Interrupts

An interrupt ends a sequence which runs on this side of the transport, which
means `spectestrun` and `spectestaction`.  A wait ends at once, since it is
doing nothing anyway, while a step which is doing work runs to its end, because
the steps after it may be the ones which release what it claimed.  The sequence
then stops instead of starting the next step, and the steps which never ran are
reported, so that the resources it left activated are visible.

An in-flight run therefore delays the exit by up to the execution timeout.
Nothing cancels a call which the server is already serving.

## Commands

### Command - spectestrun

The `spectestrun` command runs a step sequence on a test server through gRPC,
for example:

```
spectestrun --server-address=foobar:50051 --target=aarch64/zynqmp_apu ticker.exe
```

It reports each result as it arrives, so a long sequence shows the output of
every run as it happens rather than at its end.

### Command - spectestgitrun

The `spectestgitrun` command runs a step sequence on a test server which is
reachable only through a Git remote, for example:

```
spectestgitrun --remote=git@host:bench.git --target=aarch64/zynqmp_apu ticker.exe
```

It prepares the executables exactly like `spectestrun` does, commits them,
pushes the commit, waits for the response commit, and reports the results.  See
the *Git mediated transport* section below.

The `--remote` is a URL and not the name of a remote.  The command works in a
bare repository of its own, which has no remotes to look a name up in.  A local
remote therefore reads `--remote=file:///path/to/bench.git`.

That repository is a temporary directory unless `--work-dir` names one, in
which case the command creates it and owns it.  Do not point it at a clone of
the remote; the command refuses a directory which holds anything else.  Naming
one is worth it for a large executable, since the objects of the previous
requests are then still there.

Use `--no-wait` to print the request identifier and exit, and `--collect` to
report the response of a request which was submitted before.

Note that `--wait` used to be an unambiguous abbreviation of `--wait-timeout`
and now appends a step instead.  Spell `--wait-timeout` out.

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

The requests are independent of each other, so a failed one does not stop the
ones after it and an operator who releases two resources gets both attempted.
This is the opposite of an `--action` step of a sequence, where a failed action
falsified the precondition of everything after it.

The command exits with a non-zero status if an action reports an error.

### Activation leases

An agent of the test server may hold a resource under a lease which expires
unless a client renews it.  A client renews a lease by repeating the
activation, for example

```
spectestaction --server-address=foobar:50051 /service/some-peer:activate:bc:600
```

every few minutes.  The optional third field is the lease in seconds and
overrides the one of the agent.  A value of zero activates without a lease.
What an agent does with a lease is documented by
[spectestserver](https://github.com/specthings/spectestserver).

### Command - spectestlog

The `spectestlog` command displays the test server log messages.  Every agent
runs as a thread of the test server, so the messages of all of them are
displayed.

The command streams until it is interrupted.  Use `--max-lines` to stop after a
number of messages and `--timeout` to stop after a number of seconds, so that
the command can be used in a script.

### Command - spectestio

The `spectestio` command displays input multicasts.  This command is work in
progress.

The command streams until it is interrupted.  Use `--max-lines` to stop after a
number of responses and `--timeout` to stop after a number of seconds.

### Exit codes

The commands share these exit codes:

| Code | Meaning |
| ---- | ------- |
| 0 | The command did its work.  A run may still have reported a failure. |
| 1 | The request was permanently refused. |
| 2 | The command line is not usable. |
| 3 | A step never reached its target, for example the server is unreachable. |
| 4 | A run reported a status other than the one of `--fail-on-status`. |
| 5 | The request vanished before a response arrived. |
| 6 | An action failed, so the steps after it did not run. |
| 7 | The specification of the server is not valid. |
| 8 | No response arrived before the wait timeout expired. |
| 130 | The command was interrupted. |

Code 2 is the one `argparse` itself uses, so an unknown option and a malformed
value of ours mean the same thing.  Code 130 is the conventional 128 plus the
number of `SIGINT`.

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
spectest: request 3 steps for aarch64/zynqmp_apu

--- spectest-request ---
version: 1
kind: run-steps
submitter: sebhub-at-workstation
target: aarch64/zynqmp_apu
timeout: 180.0
steps:
- kind: image
  path: build/ticker.exe
  file: images/0000-ticker.exe
  digest: sha256:3f8a2c1e...
  breakpoints:
  - 4096
- kind: wait
  seconds: 5.0
- kind: action
  uid: /service/some-peer
  action: deactivate
--- end ---
```

The `steps` run in the order of the list and have the kinds described in the
*Step sequences* section above.  The `file` of an image step names the blob of
the commit which holds it and exists only in this transport, since the direct
one sends the bytes with the request.

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

A stop of the bridge during a wait leaves the request pending without counting
an attempt, so the next run of the bridge repeats the whole request.  A long
wait therefore widens the window in which a restart repeats the steps which
already ran.

The bridge creates a response reference with a lease so that it fails instead
of overwriting a response which another bridge pushed first.  Git enforces fast
forward updates only below `refs/heads` and `refs/tags`, so an ordinary push
would silently replace the existing response.

The `spectestgitrun` command uses the shared exit codes described above.  Two of
them mean something specific to this transport: 3 also covers a Git error, and 5
means that the remote has neither a request nor a response reference for the
request, so that no response can ever arrive.

### Security

The Git remote is the only authorization boundary.  The bridge does not verify
commit signatures, so everybody who can push to the remote can run executables
on the test hardware, and so can everybody who compromises the Git host.  Use a
remote whose write access is restricted to the submitters you trust.

Deleting a reference only makes its objects unreachable.  Reclaiming the disk
space is up to the hosting side, which has to run `git gc --prune`.  A
dedicated repository you administer yourself is therefore the deployment which
actually bounds the growth.
