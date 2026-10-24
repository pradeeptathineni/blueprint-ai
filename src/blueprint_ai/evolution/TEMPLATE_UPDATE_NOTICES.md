# Template update source notes

The implementation was written for Blueprint. No donor file was copied verbatim, so there are no transferred path hashes; the only literal transfer is Copier's eight-pattern `DEFAULT_EXCLUDE` constant. The bounded behavior and cases were adapted from these exact pinned sources, inspected on 2026-10-21:

- Copier `a00a46b27441fceec9b15bcf3b90e4a123360ce3`: `copier/_main.py` (`run_update`, `_apply_update`), `tests/test_updatediff.py` (authored diff, overlap, and mode cases), and `LICENSE`. Blueprint reconstructs rendered base and target snapshots and uses Git's `merge-file` for a three-way merge. Copier's full worker closure includes prompts, Jinja, migrations, VCS object plumbing and task execution; Blueprint's owned local template lane does not import that closure.
- Copier `a00a46b27441fceec9b15bcf3b90e4a123360ce3`, for `copier_intake.py` (inspected 2026-10-24): `copier/_template.py` (`DEFAULT_EXCLUDE`, `DEFAULT_TEMPLATES_SUFFIX`, `filter_config`), `copier/_user_data.py` (`Question` fields, `_check_type`, `cast_answer`, `get_default`, `_formatted_choices`), `tests/test_prompt.py` and `LICENSE`. The `DEFAULT_EXCLUDE` patterns are copied literally. The setting/question split, short question form, type inference from a default, and dict, pair and scalar choice forms are reimplemented. Blueprint requires exact JSON types where Copier casts answers, refuses a missing type where Copier infers `yaml`, and substitutes only `{{ name }}` instead of evaluating Jinja. The `test_prompt.py` fixtures depend on `_envops`, secret, templated defaults and `path` questions, which this dialect refuses, so none were transferred.
- Copier `a00a46b27441fceec9b15bcf3b90e4a123360ce3`, for the `copier-safe-v2` evaluator in `copier_intake.py` (inspected 2026-10-24): `copier/_user_data.py` (`Question.when` and `Question.multiselect` field documentation, `get_default` precedence, `parse_answer` and `_parse_answer`, `AnswersMap.hide`). No code was copied. Blueprint reimplements sequential `when` skipping, the explicit, recorded, then default answer precedence, and multiselect parsing that keeps declared choice order. It differs in these ways: `when` is limited to a literal bool or `{{ name }}` / `{{ not name }}` over an earlier bool question instead of a rendered Jinja expression cast with `cast_to_bool`; a repeated selection is refused instead of collapsed; items are never cast; a skipped question's default is not kept as a computed value for rendering; and multiselect answers substitute as a JSON array. `copier/_main.py` (`Worker._ask`, `_answers_to_remember`) was not re-inspected for this change; its behavior is taken from the earlier qualification notes and only informs the rule that skipped answers are not remembered.
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
