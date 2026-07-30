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

### Command - spectestlog

The `spectestlog` command displays the test server log messages.  Logs from
multiprocessing processes are not displayed.  We have to change the logging
handlers to make this work.

### Command - spectestio

The `spectestio` command displays input multicasts.  This command is work in
progress.

### Command - spectestserver

The `spectestserver` command runs the test server.  You have to provide a
server configuration.

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

The `spectestgitrun` command exits with 0 if the round trip completed, whatever
the reported run status was, with 1 if the bridge rejected the request, with 2
if it gave up waiting, with 3 on a Git or transport error, with 4 if
`--fail-on-status` was given and a run reported another status, and with 5 if
the remote has neither a request nor a response reference for the request, so
that no response can ever arrive.

### Security

The Git remote is the only authorization boundary.  The bridge does not verify
commit signatures, so everybody who can push to the remote can run executables
on the test hardware, and so can everybody who compromises the Git host.  Use a
remote whose write access is restricted to the submitters you trust.

Deleting a reference only makes its objects unreachable.  Reclaiming the disk
space is up to the hosting side, which has to run `git gc --prune`.  A
dedicated repository you administer yourself is therefore the deployment which
actually bounds the growth.
