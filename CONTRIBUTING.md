<!--
SPDX-License-Identifier: CC-BY-SA-4.0

Copyright (C) 2026 embedded brains GmbH & Co. KG
-->
## Submitting issues

Please submit issues related to the *spectestrunner* project through
[Github spectestrunner issues](https://github.com/specthings/spectestrunner/issues).

## Contributing to the project

### Python development guidelines

The project tries to follow the
[PEP 8 - Style Guide for Python Code](https://www.python.org/dev/peps/pep-0008/)
and the
[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).
If the Google and PEP 8 guidelines disagree with each other, the PEP 8
guidelines have precedence.

For documenting Python code, use the
[Google Python documentation style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
in the imperative form.

The [yapf](https://github.com/google/yapf>) Python code formatter with
default settings (PEP 8 formatting) is used by the project.

The `flake8` and `pylint` static analysis tools for Python with default
settings are used by the project.

Use type annotations for contributions.  The `mypy` static type checker is used
by the project.

Type annotations and the use of analysis tools is not required for test code.

### Tests

Use `pytest` for tests.  Run `make check` to run the tests.  Make sure the code
and branch coverage stays at 100% when you make a contribution.

### Conventional commits

Use [Conventional Commits](https://www.conventionalcommits.org) for your commit
messages.
```
feat(items): Add foobar support
```

### Sign your work

We use the
[Developer Certificate of Origin (DCO)](https://github.com/specthings/spectestrunner/blob/main/DCO.txt)
which is included in the repository.  Please read this text.  If you can
certify it, then add a line like this to every Git commit message of your
contributions:

```
Signed-off-by: Erika Mustermann <erika.mustermann@example.com>
```

Use your real name for contributions to this project.
