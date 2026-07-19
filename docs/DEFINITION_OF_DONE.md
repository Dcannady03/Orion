# Orion Definition of Done

A feature, fix, migration, or release is not done until every applicable item below is
complete. An item may be marked not applicable only with a written reason in the work
record or review.

## Required checklist

- [ ] Code is implemented through Orion's existing architecture and shared services.
- [ ] Focused tests cover the new behavior, failures, and safety boundaries.
- [ ] The complete automated test suite passes.
- [ ] Backward compatibility and persistence/migration behavior are verified.
- [ ] Secrets, tokens, private content, and raw provider errors are absent from normal
      configuration, logs, artifacts, test output, and documentation examples.
- [ ] CLI help and interactive completion are updated for command changes.
- [ ] `docs/USER_GUIDE.md` is updated with the user workflow, concepts, examples,
      limitations, and troubleshooting guidance.
- [ ] Feature-specific documentation is added or updated.
- [ ] README and configuration reference are updated when the public setup, command
      surface, or configuration contract changes.
- [ ] Root and documentation changelogs are updated without obsolete duplicate entries.
- [ ] Automatic Documentation Review is Passed, Not Required with recorded reasons, or
      its Warnings/Failures are explicitly resolved or accepted during human review.
- [ ] Version and release notes are updated only when the repository's release process
      assigns the feature to a release.
- [ ] Manual end-to-end verification is completed for the intended platform and the
      observed result is recorded.
- [ ] The final diff is reviewed for unrelated changes, generated files, temporary
      artifacts, and accidental Vault or credential modifications.

## Documentation rules

Every shipped user-facing feature must document:

1. what the feature does;
2. the concepts a user needs before the commands;
3. setup and normal usage;
4. why its important safety boundaries exist;
5. representative real-world examples;
6. failure recovery and troubleshooting;
7. its command and configuration reference;
8. the introduced release once that release number is finalized.

Screenshots should be added when a stable visual interface exists. CLI output examples
should remain representative and clearly say when IDs, hashes, paths, models, costs, or
results vary by installation.

## Release gate

Before commit, push, merge, or tag, report:

- changed files;
- focused and full test totals;
- manual verification status;
- documentation files updated;
- version, codename, and release-note status;
- confirmation that Vault and credential files were not modified.

Historical release checklists remain historical records. This file is the evergreen
baseline for all future Orion work.
