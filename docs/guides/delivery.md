# Where your code goes, and how to get it out

The orchestrator never writes your default branch. Verified work is promoted
`task/… → goal/… → cycle/<id>`, and at the publication gate you record one
disposition for the cycle. What that means in practice depends on one choice you
make when you create a project.

## The three repository bindings

You name the binding when you add a project. It is a named choice rather than
something inferred from a blank field, so "clone a remote" with no URL is
refused instead of quietly becoming a demo repository.

| Binding | What you give it | Where the work lands |
|---|---|---|
| **Point at a local repository** | a path on this machine | your own checkout — `cycle/<id>` is already there |
| **Clone a remote** | an `https://` or `ssh://` URL | a clone the orchestrator owns, under `$ORCHESTRATOR_HOME/projects/<id>/repos/<hash>` |
| **Create an empty one** | nothing | a scratch repository — good for trying the flow, not code you will keep |

`scp`-style remotes (`git@github.com:acme/widgets.git`) are **not** supported.
Use the `ssh://git@github.com/acme/widgets.git` form or `https://`.

### Checking a remote before you commit to it

The wizard's **Check** button runs `git ls-remote` against the URL and tells you
which of four things is wrong when something is: the repository needs
credentials, there is nothing at that URL, the host is unreachable, or it did
not answer in time. This runs at setup, on purpose — before this existed a
typo'd URL failed in the middle of a cycle instead.

Nothing about the check is cached. A repository reachable now can be gone later,
and the run will say so when that happens.

## Getting the work out

### If you pointed at a local repository

It is already in your checkout. From the repository:

```bash
git diff <default-branch>..cycle/<id>     # read the change
git switch cycle/<id>                     # try it
git difftool <default-branch>..cycle/<id> # in your own tool
```

### If you cloned a remote

The branch is in the orchestrator's clone, not yours. The evidence panel shows
the resolved path. Add it as a remote and fetch:

```bash
git remote add orchestrator <resolved-path>
git fetch orchestrator cycle/<id>
```

### If you connected GitHub

Choose `open_pr` at the publication gate and the orchestrator pushes
`cycle/<id>` and opens the pull request itself, with the verification evidence
rendered into the body — the exact command each task ran, its exit code, and
the candidate and test commit SHAs.

The recorded `output_reference` is then the real pull-request URL, produced by
the orchestrator, rather than a link you typed.

## Connecting GitHub

Per project, under **Settings → Projects**. Two questions stay separate on
purpose: *where the code lives* needs no credentials, and *whether we can open a
pull request* does. Declining the second changes only how work comes back — it
never changes which repository you named.

**The token needs write access to that one repository.** Either:

- a fine-grained personal access token scoped to the repository, with
  **Contents: read and write** and **Pull requests: read and write**; or
- a classic token with the `repo` scope.

It is verified the moment you save it, against the exact repository you named —
one call that confirms the repository exists, that the token reaches it, and
that it can push. A token that can only read is refused there rather than at a
publication gate at the end of a cycle.

The token is stored envelope-encrypted in the secret store, like every provider
key. No endpoint ever returns it.

## What the orchestrator will not do

- **It never merges.** `open_pr` opens a pull request; merging is yours. The
  `merge` disposition records that *you* merged, and performs nothing.
- **It never writes your default branch.** Only `cycle/<id>` is pushed.
- **It never force-pushes.**
- **GitHub only, for now.** The forge port has one adapter. A second one is
  cheap to add when somebody needs it.

## If publication fails

Pushing and opening the pull request happen *before* the disposition is
recorded, so a forge failure leaves the gate open and nothing written. Fix the
cause and choose the disposition again, or pick `retain_branch` and hand the
work over yourself with the commands above.

Common causes:

| Error | Cause |
|---|---|
| `FORGE_AUTH_FAILED` | the token expired, was revoked, or lost write access |
| `FORGE_REPO_NOT_FOUND` | the repository was renamed or deleted — a private repo the token cannot see also reports this |
| `FORGE_PUSH_FAILED` | the remote refused the push, e.g. a branch protection rule |
| `FORGE_REQUEST_FAILED` | GitHub was unreachable or returned an error |
