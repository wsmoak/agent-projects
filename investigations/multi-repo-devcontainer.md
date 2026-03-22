  what do you think will happen if we have devpod work on a 'repository' that is a combined devcontainer that pulls together multiple projects and makes a devcontainer that has all of the dependencies (database etc) running with the various projects (two monoliths that talk to each other via rabbitmq).  Will it be able to work in multiple directories and open a PR in each repo separately?

    Based on what I know about the current architecture, there are some significant challenges:

    What would work:
    - DevPod with --source git:<repo-url> would clone the combined devcontainer repo and
    spin up the full environment (databases, RabbitMQ, both monoliths) via Docker Compose
    in the devcontainer config. That part is well-supported by DevPod and the Dev Container
     spec.

    What would be problematic:

    1. Single repo assumption — The entire OpenSWE agent is built around a single
    repo.owner / repo.name from the webhook payload. The commit_and_open_pr tool uses that
    single repo config to push and create PRs. It doesn't have a concept of "I'm working in
     repo A and repo B simultaneously."
    2. Working directory — aresolve_repo_dir resolves to a single repo path. The agent's
    system prompt tells it to work in {working_dir}, singular. All git operations
    (git_push, git_checkout_branch, etc.) take a single repo_dir.
    3. PR creation — create_github_pr takes one repo_owner/repo_name. The agent would need
    to call commit_and_open_pr once per repo, but the tool gets repo info from get_config()
     which has a single repo config.
    4. Git credentials — The credential file is scoped to github.com broadly, so that part
    would actually work across repos.

    What it would take:
    - The agent would need to be aware of multiple repos and their paths within the
    workspace
    - commit_and_open_pr would need to accept repo owner/name as parameters (or infer from
    the git remote in each directory)
    - The webhook/trigger mechanism would need to know which repos are involved
    - The devcontainer repo itself might not be either of the repos you want PRs in — it's
    a third "orchestration" repo

    It's an interesting use case but would require meaningful changes to the agent's repo
    model. The LLM itself could probably navigate multiple directories fine — it's the
    tooling layer that's single-repo.
