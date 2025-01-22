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

### Command - spectestaction

The `spectestaction` command runs actions on a test server, for example:

```
spectestaction --server-address=foobar:50051 /switch/net-pwr-ctrl-2:activate:zc702
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
