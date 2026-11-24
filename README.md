# openIMIS Backend deduplication reference module

## Benefit Plan access-control merge dependencies

The Benefit Plan-scoped deduplication changes must be merged and deployed only
after the corresponding access-control changes from these backend modules:

- [`openimis-be-social_protection_py` PR #27](https://github.com/nlgfc2024/openimis-be-social_protection_py/pull/27),
  branch `feat/track-08-shared-benefit-plan-authorization` (shared feature
  flag and authorization helpers)
- [`openimis-be-payroll_py` PR #20](https://github.com/nlgfc2024/openimis-be-payroll_py/pull/20),
  branch `feature/track-07-benefit-plan-payroll-access` (Payroll ownership and
  authorization helpers)
- [`openimis-be-tasks_management_py` PR #7](https://github.com/nlgfc2024/openimis-be-tasks_management_py/pull/7),
  branch `feature/track-08-benefit-plan-task-access` (Task ownership fields,
  migrations, and authorization)

Apply the Tasks Management migrations before enabling Benefit Plan access
control in Deduplication.
