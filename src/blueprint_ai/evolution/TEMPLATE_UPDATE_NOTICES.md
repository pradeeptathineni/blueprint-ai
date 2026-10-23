# Template update source notes

The implementation was written for Blueprint. No donor file was copied verbatim, so there are no transferred path hashes. The bounded behavior and cases were adapted from these exact pinned sources, inspected on 2026-10-21:

- Copier `a00a46b27441fceec9b15bcf3b90e4a123360ce3`: `copier/_main.py` (`run_update`, `_apply_update`), `tests/test_updatediff.py` (authored diff, overlap, and mode cases), and `LICENSE`. Blueprint reconstructs rendered base and target snapshots and uses Git's `merge-file` for a three-way merge. Copier's full worker closure includes prompts, Jinja, migrations, VCS object plumbing and task execution; Blueprint's owned local template lane does not import that closure.
- Cruft `33f6b722fc6fe4b5d26a351e487372e5e4375ab2`: `cruft/_cli.py`, `cruft/_commands/__init__.py`, `cruft/_commands/check.py`, `cruft/_commands/utils/cruft.py`, and `LICENSE`. Blueprint adapts the explicit recorded-commit versus target-commit freshness check with one provenance record. It uses strict commit identity; Cruft's equivalent-content and ancestry options are not implemented.

The donor sources are MIT licensed. These notices retain the required copyright and permission terms for the adapted behavior.

## Copier license

MIT License

Copyright (c) 2011 Juan-Pablo Scaletti

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Cruft license

MIT License

Copyright (c) 2024 Timothy Crosley, Sambhav Kothari

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
