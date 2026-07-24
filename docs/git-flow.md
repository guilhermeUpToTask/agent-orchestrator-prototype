# Git flow and releases

This repository uses trunk-based development. The default branch, `main`, is
always expected to be releasable. There is no `develop` branch and there are no
long-lived release branches: for a solo maintainer and automated coding agents,
those branches add synchronization work without adding a useful approval gate.

## Branches and pull requests

Create every change from current `main` on a short-lived branch:

- `feat/<description>` for user-visible capabilities
- `fix/<description>` for bug fixes
- `chore/<description>` for maintenance
- `docs/<description>` for documentation
- `ci/<description>` for delivery automation

Branches and pull requests are deliberately decoupled. A branch, worktree,
task, or completed feature does not automatically become a pull request.
Agents first implement and validate work, then present the user with a feature
inventory: completed features, commits/branches, dependencies, overlap, CI
status, and a recommended grouping. The user alone chooses whether to create a
pull request and which features belong in it.

Agents must not open, close, combine, retarget, or merge a pull request without
explicit authorization for that exact action and grouping. A user-approved pull
request targets `main` and uses squash merge. Do not push directly to `main`.
Required CI checks must pass and the PR branch must be up to date before merge.
Reviews are not required while the repository has one maintainer; to enable
them later, set `required_pull_request_reviews.required_approving_review_count`
to `1` in the `main` branch-protection rule.

## Conventional Commits

Commit messages and squash-merge PR titles use:

```text
feat: add plan export
fix(api): preserve retry state
chore: update dependencies
docs: explain worker leases
refactor(worker): isolate claim logic
test: cover expired leases
ci: cache frontend dependencies
```

The accepted types are `feat`, `fix`, `chore`, `docs`, `refactor`,
`test`, and `ci`. Add `!` or a `BREAKING CHANGE:` footer for an incompatible
change. During the `0.x` prototype series, breaking changes bump the minor
version. The semantic PR-title check enforces this convention so squash merges
produce release-ready history.

## What CI enforces

Every pull request to `main` runs these checks in parallel:

- backend on Python 3.11 and 3.12: Ruff, the zero-error `mypy src` ratchet,
  unit tests, and integration tests
- frontend on Node LTS: clean npm install, TypeScript/Vite production build,
  OpenAPI type generation, and a generated-file drift check
- semantic PR title validation

Integration tests use local adapters and do not require a Redis service.
Starlette's synchronous `TestClient` must run through the supported `httpx2`
transport declared in the backend dev dependencies; do not remove it and rely
on Starlette's deprecated plain-`httpx` compatibility fallback.

## Recovering conflicting pull requests

Treat each pull request's last independently green commit as its source of
truth. If a manual merge from `main` corrupts a branch, do not repair the
result by repeatedly combining the affected pull requests. Restore the branch
to its last green commit, then reapply only the required upstream changes.

Generated API artifacts are never conflict-resolution inputs:

- resolve backend route and schema source files first
- discard conflict-marker or hand-edited versions of `frontend/openapi.json`
  and `frontend/src/types/generated/`
- run `npm run generate:api` from `frontend/`
- commit the regenerated output only after the frontend build and generated
  drift check pass

Do not fold two independently green feature pull requests together merely to
avoid a generated-file conflict. That changes the test composition and makes
the combined branch a new, unreviewed integration target. Merge one feature,
update the remaining branch from the new `main`, regenerate once, and rerun
CI. If one pull request is explicitly selected as the source of truth, close
the superseded pull request and keep its branch until the surviving pull
request has merged.

Recovery checklist:

1. Record the last green SHA for every affected pull request.
2. Inspect merge commits with `git show --remerge-diff`.
3. Restore the selected branch to its last green SHA.
4. Resolve only source files; regenerate derived files.
5. Run Ruff, Mypy, focused backend tests, the frontend build, and type
   generation locally.
6. Push and wait for a completely new CI run before declaring the branch
   merge-ready.

## Release PRs

After a conventional commit reaches `main`, release-please opens or updates a
release PR. That PR updates `CHANGELOG.md`, `version.txt`,
`backend/pyproject.toml`, `frontend/package.json`, and both version fields in
`frontend/package-lock.json`.

To cut a release:

1. Confirm the release PR contains the expected version and changelog.
2. Make sure its required checks pass and it is current with `main`.
3. Squash-merge the release PR.
4. Release-please creates `vX.Y.Z` and the GitHub Release.
5. The same workflow builds the Python wheel/sdist and frontend bundle and
   attaches them to the release. It does not publish to PyPI.

The workflow uses the repository's default `GITHUB_TOKEN`. GitHub may suppress
workflows caused by pull requests created with that token. If a new release PR
shows no CI checks, close and reopen it as a maintainer to emit a human-authored
`pull_request` event; do not bypass branch protection.

## Hotfixes

Branch `fix/<description>` from current `main`, commit with a `fix:` title,
and merge the passing PR normally. Release-please then proposes the patch release.
No special hotfix or release branch is needed.
