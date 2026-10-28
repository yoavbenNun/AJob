# Desktop runtime updates

The installed shortcut keeps pointing to `.local-runtime/start_dashboard.ps1`.
Install copies of `scripts/update_dashboard_runtime.ps1` at that path and
`scripts/launch_published_dashboard.ps1` alongside it. Keep the existing
`runtime-version.json`, initial `code/` worktree and protected local configuration.
The installed scripts are a stable bootstrap and are not replaced by fetched code.

Each launch checks the exact main SHA using authenticated `gh api`. Both `ci.yml`
and `security.yml` must have completed successful push-to-main runs for that SHA.
Missing credentials, network errors, running/failed checks or changed dependencies
retain the installed version. No dependency installation or migrations occur.

A dedicated bare repository under `.local-runtime/repository.git` creates detached
`releases/<sha>` worktrees without touching development checkouts or refs. Dirty
active/candidate trees are not updated. A synthetic, cloud-free import/HTTP probe
precedes activation. Existing listeners defer updates; the verified healthy server
is reused. Concurrent shortcut launches coalesce under a nonblocking mutex.

With no live server, start the candidate and check health, home and an existing job
detail on port 8001. Only then atomically replace the version pointer, retaining
the previous SHA/path. Startup or publication failure starts the previous version.
Never prune old releases automatically. If the launcher is interrupted before
publication, the old pointer remains authoritative on the next launch.

Python, working directory, databases, resumes and DPAPI credentials stay in their
original locations. No secrets enter Git arguments or release directories. Active
servers are never replaced by an automatic update; close the AJob server before
the next shortcut launch to adopt a newly approved release.

Run focused offline regressions with Windows PowerShell:
`powershell -NoProfile -File tests/test_runtime_updater.ps1`.
Validate a real shortcut launch and concurrent launch separately; no scan/mail is
needed. Bootstrap upgrades remain a deliberate local installation step.
