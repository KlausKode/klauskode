"""CLI entry point for klaus-kode."""

from __future__ import annotations

import argparse
import os
import sys
import time

from klaus_kode import claude_runner
from klaus_kode.claude_runner import (
    check_guidelines_compliance,
    clone_repo,
    commit_changes,
    create_branch,
    generate_pr_description,
    pick_issue,
    pick_repo,
    push_branch,
    read_contributing_guidelines,
    run_claude_review,
    run_claude_work,
    save_pr_description,
    show_changes,
    suggest_branch_name,
)
from klaus_kode import github
from klaus_kode.github import (
    check_gh_auth,
    check_issue_active_work,
    check_token_scopes,
    fetch_issue,
    fork_repo,
    search_issues,
    search_repos,
    validate_repo,
)


def _check_prerequisites(verbose: int = 0) -> None:
    """Verify all prerequisites are met."""
    # Check GitHub auth
    print("  Checking GitHub authentication...")
    if not check_gh_auth():
        print("Error: No GitHub authentication found.", file=sys.stderr)
        print("Set the GH_TOKEN environment variable.", file=sys.stderr)
        raise SystemExit(1)
    print("  GitHub: OK")

    # Check token permissions
    if verbose:
        print("  Checking token permissions...")
        scopes = check_token_scopes()
        for scope, ok in scopes.items():
            status = "OK" if ok else "MISSING"
            print(f"    {scope}: {status}")
        if not scopes["can_fork"]:
            print("\n  WARNING: Token may lack fork/write permissions.", file=sys.stderr)
            print("  For fine-grained PATs: enable 'Contents: Read and write' permission.", file=sys.stderr)
            print("  For classic tokens: enable the 'public_repo' scope.", file=sys.stderr)
            print("  Create a new token at: https://github.com/settings/tokens\n", file=sys.stderr)

    # Check Claude auth
    print("  Checking Claude authentication...")
    has_oauth = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not has_oauth and not has_api_key:
        print("Error: No Claude authentication found.", file=sys.stderr)
        print("Set one of: CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY.", file=sys.stderr)
        print("Create an API key at: https://console.anthropic.com/settings/keys", file=sys.stderr)
        raise SystemExit(1)
    auth_method = "OAuth token" if has_oauth else "API key"
    print(f"  Claude: OK ({auth_method})")


def main(argv: list[str] | None = None) -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="klaus-kode",
        description="Donate your Claude credits to open source by working on GitHub issues.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="GitHub repository in owner/repo format",
    )
    parser.add_argument(
        "--find-repo",
        type=str,
        default=None,
        help="Search GitHub for a repository matching this description (e.g. 'python web framework')",
    )
    issue_group = parser.add_mutually_exclusive_group(required=False)
    issue_group.add_argument(
        "--issue",
        type=int,
        help="Issue number to work on",
    )
    issue_group.add_argument(
        "--find",
        type=str,
        help="Search open issues and pick one matching this description (e.g. 'easy', 'documentation fix')",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase output verbosity (-v for details, -vv for full output)",
    )

    args = parser.parse_args(argv)

    # Custom validation: --repo and --find-repo are mutually exclusive; one is required
    if args.repo and args.find_repo:
        parser.error("--repo and --find-repo are mutually exclusive")
    if not args.repo and not args.find_repo:
        parser.error("one of --repo or --find-repo is required")
    # --find-repo + --issue is an error (can't know issue numbers for an unknown repo)
    if args.find_repo and args.issue is not None:
        parser.error("--issue cannot be used with --find-repo (issue numbers are repo-specific)")

    t0 = time.time()
    claude_runner._global_start = t0

    # Set module-level verbosity
    github.verbose = args.verbose

    # 1. Check prerequisites
    print("Checking prerequisites...")
    _check_prerequisites(verbose=args.verbose)

    # 2. Find repo if --find-repo was used
    candidates_repos: list | None = None
    if args.find_repo:
        print(f"\nSearching GitHub for repos matching: '{args.find_repo}'...")
        candidates_repos = search_repos(args.find_repo)
        if not candidates_repos:
            print("Error: No repositories found.", file=sys.stderr)
            raise SystemExit(1)
        print(f"  Found {len(candidates_repos)} candidate repos:")
        for i, r in enumerate(candidates_repos, 1):
            print(f"    {i}. {r.full_name} ({r.language}, {r.stars}\u2605) \u2014 {r.description[:80]}")
        chosen = pick_repo(candidates_repos, args.find_repo)
        args.repo = chosen.full_name
        print(f"  Selected repo: {args.repo}")

    # 3. Validate repo
    print(f"\nValidating repo {args.repo}...")
    if not validate_repo(args.repo):
        print(f"Error: Repository '{args.repo}' not found.", file=sys.stderr)
        raise SystemExit(1)

    # 4. Fetch or find issue
    if args.issue is not None:
        # Explicit issue number
        print(f"Fetching issue #{args.issue}...")
        issue = fetch_issue(args.repo, args.issue)
        if issue is None:
            raise SystemExit(1)
        if issue.state != "open":
            print(
                f"Error: Issue #{args.issue} is not open (state: {issue.state}).",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"  Issue #{issue.number}: {issue.title}")
        if args.verbose and issue.labels:
            print(f"  Labels: {', '.join(issue.labels)}")

        # Check if issue is already being worked on
        print(f"Checking if issue #{args.issue} is already being worked on...")
        is_active, reason = check_issue_active_work(args.repo, issue)
        if is_active:
            print(f"Skipping issue #{args.issue}: {reason}", file=sys.stderr)
            raise SystemExit(1)
        print("  No active work found, proceeding.")
    else:
        # Use --find description or default to easy beginner-friendly issues
        find_description = args.find or "easy beginner-friendly good first issue"
        print(f"Searching open issues in {args.repo} matching: '{find_description}'...")
        candidates = search_issues(args.repo)

        # If no issues found and we came from --find-repo, try remaining candidates
        if not candidates and candidates_repos:
            tried = {args.repo}
            for fallback_repo in candidates_repos:
                if fallback_repo.full_name in tried:
                    continue
                tried.add(fallback_repo.full_name)
                print(f"  No open issues in {args.repo}, trying {fallback_repo.full_name}...")
                if not validate_repo(fallback_repo.full_name):
                    continue
                candidates = search_issues(fallback_repo.full_name)
                if candidates:
                    args.repo = fallback_repo.full_name
                    print(f"  Switched to repo: {args.repo}")
                    break

        if not candidates:
            print("Error: No open issues found.", file=sys.stderr)
            raise SystemExit(1)
        print(f"  Found {len(candidates)} open issues, filtering...")

        # Labels that indicate non-coding issues
        non_coding_labels = {
            "question", "discussion", "support",
            "wontfix", "won't fix", "duplicate", "invalid",
            "needs info", "needs-info", "needs more info",
            "waiting for response", "waiting-for-response",
        }

        # Filter out non-coding issues and issues already being worked on
        available: list[github.Issue] = []
        for candidate in candidates:
            # Skip issues with non-coding labels
            candidate_labels = {label.lower() for label in candidate.labels}
            skipped_labels = candidate_labels & non_coding_labels
            if skipped_labels:
                if args.verbose:
                    print(f"  Skipping #{candidate.number}: non-coding label(s): {', '.join(skipped_labels)}")
                continue

            is_active, reason = check_issue_active_work(args.repo, candidate)
            if not is_active:
                available.append(candidate)
            elif args.verbose:
                print(f"  Skipping #{candidate.number}: {reason}")

        if not available:
            print("Error: All candidate issues are already being worked on.", file=sys.stderr)
            raise SystemExit(1)
        print(f"  {len(available)} issues available (not claimed)")

        issue = pick_issue(available, find_description)
        print(f"  Selected issue #{issue.number}: {issue.title}")
        if issue.labels:
            print(f"  Labels: {', '.join(issue.labels)}")

    # 4. Fork repo
    print(f"\nForking {args.repo}...")
    fork = fork_repo(args.repo)
    print(f"  Fork: {fork}")

    # 5. Clone repo
    print("\nSetting up repository...")
    default_branch = clone_repo(args.repo, fork)

    # 6. Read contributing guidelines
    guidelines = read_contributing_guidelines()

    # 7. Suggest branch name based on guidelines
    branch_name = suggest_branch_name(issue, guidelines)
    print(f"  Branch name: {branch_name}")

    # 8. Create branch
    create_branch(branch_name, default_branch)

    # 9. Check guidelines compliance
    if not check_guidelines_compliance(guidelines):
        raise SystemExit(1)

    # 10. Run Claude to work on the issue
    run_claude_work(issue, args.repo, guidelines, verbose=args.verbose)

    # 10.5 Ensure changes are committed
    if not commit_changes(issue.number, default_branch):
        print("No changes were made. Nothing to push.", file=sys.stderr)
        raise SystemExit(1)

    # 11. Show changes + self-review (step 8/9)
    show_changes(default_branch)
    run_claude_review(default_branch, verbose=args.verbose)

    # 12. Generate PR description
    title, body = generate_pr_description(issue, args.repo, default_branch)

    # 13. Push branch
    push_branch(branch_name)

    # 14. Save PR description and print ready-to-run command
    save_pr_description(
        title=title,
        body=body,
        repo=args.repo,
        fork=fork,
        branch=branch_name,
        default_branch=default_branch,
    )

    elapsed = time.time() - t0
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"\nTotal runtime: {minutes}m {seconds}s")


if __name__ == "__main__":
    main()
