"""CLI entry point for klaus-kode."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

from klaus_kode import github
from klaus_kode.context import PipelineContext, Session
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
from klaus_kode.run_logger import RunLogger


# ---------------------------------------------------------------------------
# Pipeline steps — each takes a PipelineContext, mutates it, and returns it.
# ---------------------------------------------------------------------------

def _check_prerequisites(ctx: PipelineContext) -> PipelineContext:
    """Verify all prerequisites are met."""
    print("Checking prerequisites...")

    # Check GitHub auth
    print("  Checking GitHub authentication...")
    if not check_gh_auth():
        print("Error: No GitHub authentication found.", file=sys.stderr)
        print("Set the GH_TOKEN environment variable.", file=sys.stderr)
        raise SystemExit(1)
    print("  GitHub: OK")

    # Check token permissions
    if ctx.verbose:
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

    return ctx


def _find_repo(ctx: PipelineContext) -> PipelineContext:
    """Find a repo if --find-repo was used, otherwise validate --repo."""
    if ctx.find_repo:
        print(f"\nSearching GitHub for repos matching: '{ctx.find_repo}'...")
        ctx.candidates_repos = search_repos(ctx.find_repo)
        if not ctx.candidates_repos:
            print("Error: No repositories found.", file=sys.stderr)
            raise SystemExit(1)
        print(f"  Found {len(ctx.candidates_repos)} candidate repos:")
        for i, r in enumerate(ctx.candidates_repos, 1):
            print(f"    {i}. {r.full_name} ({r.language}, {r.stars}\u2605) \u2014 {r.description[:80]}")

        from klaus_kode.selection import pick_repo
        chosen = pick_repo(ctx.candidates_repos, ctx.find_repo, logger=ctx.logger)
        ctx.repo = chosen.full_name
        print(f"  Selected repo: {ctx.repo}")
        ctx.logger.log_decision(
            decision="repo_selected",
            reason=f"Claude picked {ctx.repo} from {len(ctx.candidates_repos)} candidates",
            repo=ctx.repo,
        )

    ctx.logger.set_context(repo=ctx.repo)

    # Validate repo
    print(f"\nValidating repo {ctx.repo}...")
    if not validate_repo(ctx.repo):
        print(f"Error: Repository '{ctx.repo}' not found.", file=sys.stderr)
        raise SystemExit(1)

    return ctx


def _find_issue(ctx: PipelineContext) -> PipelineContext:
    """Fetch or find an issue to work on."""
    if ctx.issue_number is not None:
        # Explicit issue number
        print(f"Fetching issue #{ctx.issue_number}...")
        issue = fetch_issue(ctx.repo, ctx.issue_number)
        if issue is None:
            raise SystemExit(1)
        if issue.state != "open":
            print(
                f"Error: Issue #{ctx.issue_number} is not open (state: {issue.state}).",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"  Issue #{issue.number}: {issue.title}")
        if ctx.verbose and issue.labels:
            print(f"  Labels: {', '.join(issue.labels)}")

        # Check if issue is already being worked on
        print(f"Checking if issue #{ctx.issue_number} is already being worked on...")
        is_active, reason = check_issue_active_work(ctx.repo, issue)
        if is_active:
            print(f"Skipping issue #{ctx.issue_number}: {reason}", file=sys.stderr)
            raise SystemExit(1)
        print("  No active work found, proceeding.")
    else:
        # Use --find description or default to easy beginner-friendly issues
        find_description = ctx.find_description or "easy beginner-friendly good first issue"
        print(f"Searching open issues in {ctx.repo} matching: '{find_description}'...")
        candidates = search_issues(ctx.repo)

        # If no issues found and we came from --find-repo, try remaining candidates
        if not candidates and ctx.candidates_repos:
            tried = {ctx.repo}
            for fallback_repo in ctx.candidates_repos:
                if fallback_repo.full_name in tried:
                    continue
                tried.add(fallback_repo.full_name)
                print(f"  No open issues in {ctx.repo}, trying {fallback_repo.full_name}...")
                if not validate_repo(fallback_repo.full_name):
                    continue
                candidates = search_issues(fallback_repo.full_name)
                if candidates:
                    ctx.repo = fallback_repo.full_name
                    ctx.logger.set_context(repo=ctx.repo)
                    print(f"  Switched to repo: {ctx.repo}")
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
                if ctx.verbose:
                    print(f"  Skipping #{candidate.number}: non-coding label(s): {', '.join(skipped_labels)}")
                continue

            is_active, reason = check_issue_active_work(ctx.repo, candidate)
            if not is_active:
                available.append(candidate)
            elif ctx.verbose:
                print(f"  Skipping #{candidate.number}: {reason}")

        if not available:
            print("Error: All candidate issues are already being worked on.", file=sys.stderr)
            raise SystemExit(1)
        print(f"  {len(available)} issues available (not claimed)")

        from klaus_kode.selection import pick_issue
        issue = pick_issue(available, find_description, logger=ctx.logger)
        print(f"  Selected issue #{issue.number}: {issue.title}")
        if issue.labels:
            print(f"  Labels: {', '.join(issue.labels)}")
        ctx.logger.log_decision(
            decision="issue_selected",
            reason=f"Claude picked issue #{issue.number} from {len(available)} candidates",
            issue_number=issue.number,
            issue_title=issue.title,
        )

    ctx.issue = issue
    ctx.logger.set_context(
        issue={"number": issue.number, "title": issue.title, "body": issue.body},
    )
    return ctx


def _fork_and_clone(ctx: PipelineContext) -> PipelineContext:
    """Fork the repo, clone it, and read contributing guidelines."""
    from klaus_kode.repo_ops import clone_repo, read_contributing_guidelines

    # Fork repo
    print(f"\nForking {ctx.repo}...")
    ctx.fork = fork_repo(ctx.repo)
    print(f"  Fork: {ctx.fork}")
    ctx.logger.set_context(fork=ctx.fork)

    # Clone repo
    print("\nSetting up repository...")
    ctx.default_branch = clone_repo(ctx.repo, ctx.fork, logger=ctx.logger)

    # Read contributing guidelines
    ctx.guidelines = read_contributing_guidelines()

    return ctx


def _prepare_branch(ctx: PipelineContext) -> PipelineContext:
    """Suggest branch name, check guidelines compliance, create branch."""
    from klaus_kode.repo_ops import create_branch, gather_repo_context, write_inner_claude_md
    from klaus_kode.selection import parallel_pre_work

    # Suggest branch name + check guidelines compliance (parallel)
    print("\n[6/9] Checking guidelines and suggesting branch name...")
    ctx.branch_name, should_proceed = parallel_pre_work(ctx.issue, ctx.guidelines)
    print(f"  Branch name: {ctx.branch_name}")
    if not should_proceed:
        raise SystemExit(1)
    ctx.logger.set_context(branch=ctx.branch_name, default_branch=ctx.default_branch)

    # Create branch
    create_branch(ctx.branch_name, ctx.default_branch)

    # Write inner CLAUDE.md for the target repo
    write_inner_claude_md(ctx.issue, ctx.repo, ctx.guidelines, ctx.branch_name)

    # Pre-fetch repo context to reduce Claude's exploration overhead
    print("  Pre-fetching repository context...")
    ctx.repo_context = gather_repo_context()

    return ctx


def _run_work(ctx: PipelineContext) -> PipelineContext:
    """Run Claude to work on the issue."""
    from klaus_kode.claude_sdk import run_claude_streaming
    from klaus_kode.prompts import WORK_TOOLS, WORKER_SYSTEM_PROMPT, build_work_prompt
    from klaus_kode.repo_ops import cleanup_inner_claude_md, commit_changes

    prompt = build_work_prompt(ctx.issue, ctx.repo, ctx.guidelines, repo_context=ctx.repo_context)
    run_claude_streaming(
        prompt=prompt,
        header="[7/9] Claude is working on the issue...",
        activity="implementing",
        verbose=ctx.verbose,
        max_turns=25,
        logger=ctx.logger,
        step_name="work",
        system_prompt=WORKER_SYSTEM_PROMPT,
        allowed_tools=WORK_TOOLS,
        max_budget_usd=ctx.max_budget_usd,
        start_time_global=ctx.start_time,
    )

    # Clean up inner CLAUDE.md and ensure changes are committed
    cleanup_inner_claude_md()
    if not commit_changes(ctx.issue.number, ctx.default_branch, logger=ctx.logger):
        print("No changes were made. Nothing to push.", file=sys.stderr)
        raise SystemExit(1)

    # Capture diff once for reuse in review + PR description
    from klaus_kode.repo_ops import REPO_PATH
    diff_result = subprocess.run(
        ["git", "--no-pager", "diff", f"upstream/{ctx.default_branch}"],
        capture_output=True, text=True, cwd=REPO_PATH,
    )
    ctx.diff_output = diff_result.stdout[:50000]  # Cap at 50KB

    return ctx


def _review_and_push(ctx: PipelineContext) -> PipelineContext:
    """Review changes, generate PR description, push, and save."""
    from klaus_kode.pr_description import (
        generate_pr_description,
        run_claude_review,
        save_pr_description,
        show_changes,
    )
    from klaus_kode.repo_ops import push_branch

    # Show changes to human + self-review with diff injected
    show_changes(ctx.default_branch)
    run_claude_review(
        ctx.default_branch,
        verbose=ctx.verbose,
        logger=ctx.logger,
        max_budget_usd=ctx.max_budget_usd,
        diff_output=ctx.diff_output,
        start_time_global=ctx.start_time,
    )

    # Generate PR description (reuse captured diff)
    ctx.pr_title, ctx.pr_body = generate_pr_description(
        ctx.issue, ctx.repo, ctx.default_branch, diff_output=ctx.diff_output,
    )

    # Push branch
    push_branch(ctx.branch_name, logger=ctx.logger)

    # Save PR description and print ready-to-run command
    save_pr_description(
        title=ctx.pr_title,
        body=ctx.pr_body,
        repo=ctx.repo,
        fork=ctx.fork,
        branch=ctx.branch_name,
        default_branch=ctx.default_branch,
    )

    return ctx


# ---------------------------------------------------------------------------
# Pipeline definition
# ---------------------------------------------------------------------------

PIPELINE: list[tuple[str, callable]] = [
    ("check_prerequisites", _check_prerequisites),
    ("find_repo",           _find_repo),
    ("find_issue",          _find_issue),
    ("fork_and_clone",      _fork_and_clone),
    ("prepare_branch",      _prepare_branch),
    ("run_work",            _run_work),
    ("review_and_push",     _review_and_push),
]


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
        "--budget",
        type=float,
        default=None,
        help="Maximum USD budget for Claude API usage (only applies with ANTHROPIC_API_KEY)",
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

    # Set module-level verbosity (backwards compat for github.py)
    github.verbose = args.verbose

    # Build PipelineContext
    logger = RunLogger()
    logger.log_run_start(args=vars(args))

    session = Session.load()

    ctx = PipelineContext(
        repo=args.repo,
        find_repo=args.find_repo,
        issue_number=args.issue,
        find_description=args.find,
        verbose=args.verbose,
        max_budget_usd=args.budget,
        logger=logger,
        session=session,
    )

    exit_code = 0
    pr_url = ""
    try:
        # Run pipeline steps, skipping already-completed ones
        for step_name, step_fn in PIPELINE:
            if ctx.session.is_completed(step_name):
                print(f"  Skipping {step_name} (already completed)")
                continue
            ctx = step_fn(ctx)
            ctx.session.mark_completed(step_name)

        elapsed = time.time() - ctx.start_time
        minutes, seconds = divmod(int(elapsed), 60)
        print(f"\nTotal runtime: {minutes}m {seconds}s")

    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
        raise
    except Exception as e:
        exit_code = 1
        logger.log_error(e)
        raise
    finally:
        logger.log_run_end(exit_code=exit_code, pr_url=pr_url)
        logger.flush_final_summary()


if __name__ == "__main__":
    main()
