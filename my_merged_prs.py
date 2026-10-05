#!/usr/bin/env python3
"""Fetch all merged PRs for wakamex using the GitHub GraphQL API."""

import argparse
import subprocess

import requests

GITHUB_USER = "wakamex"

# Repos whose name alone doesn't identify the project, shown as owner/repo
SHOW_OWNER = {"Polymarket/agents"}


def load_token() -> str:
    return subprocess.run(
        ["gh", "auth", "token"], check=True, text=True, capture_output=True
    ).stdout.strip()


QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    pullRequests(
      states: MERGED
      first: 100
      after: $after
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        title
        url
        mergedAt
        repository {
          nameWithOwner
          stargazerCount
          owner {
            login
          }
        }
      }
    }
  }
}
"""


def run_query(token: str, variables: dict) -> dict:
    response = requests.post(
        "https://api.github.com/graphql",
        headers={"Authorization": f"bearer {token}"},
        json={"query": QUERY, "variables": variables},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-stars", type=int, default=1000,
        help="leave repos with fewer stars out of the resume line (default: 1000)",
    )
    args = parser.parse_args()
    token = load_token()

    all_prs = []
    after = None

    while True:
        data = run_query(token, {"login": GITHUB_USER, "after": after})
        prs_data = data["data"]["user"]["pullRequests"]
        nodes = prs_data["nodes"]
        all_prs.extend(nodes)

        if not prs_data["pageInfo"]["hasNextPage"]:
            break
        after = prs_data["pageInfo"]["endCursor"]

    # Filter out PRs in own repos (only show contributions to others' repos)
    external = [pr for pr in all_prs if pr["repository"]["owner"]["login"] != GITHUB_USER]
    own = [pr for pr in all_prs if pr["repository"]["owner"]["login"] == GITHUB_USER]

    print(f"\n{'='*60}")
    print(f"Total merged PRs: {len(all_prs)}")
    print(f"  External (other repos): {len(external)}")
    print(f"  Own repos: {len(own)}")

    # Group external PRs by repo
    by_repo: dict[str, list] = {}
    for pr in external:
        repo = pr["repository"]["nameWithOwner"]
        by_repo.setdefault(repo, []).append(pr)

    print(f"\n{'='*60}")
    print("Merged PRs in external repos:\n")
    for repo, prs in sorted(by_repo.items(), key=lambda x: x[1][0]["mergedAt"], reverse=True):
        print(f"  {repo} ({len(prs)} PR{'s' if len(prs) > 1 else ''})")
        for pr in prs:
            merged = pr["mergedAt"][:10]
            print(f"    [{merged}] {pr['title']}")
            print(f"             {pr['url']}")

    # Resume lines, most-starred repos first
    ranked = sorted(by_repo.values(), key=lambda prs: prs[0]["repository"]["stargazerCount"], reverse=True)
    if ranked:
        full = [prs[0]["repository"]["nameWithOwner"] for prs in ranked]
        short = [name.split("/")[1] for name in full]
        names = [
            f if f in SHOW_OWNER or short.count(s) > 1 else s
            for f, s in zip(full, short)
        ]
        details = [
            f"{name} (★{format_stars(prs[0]['repository']['stargazerCount'])}) "
            f"({len(prs)} PR{'s' if len(prs) > 1 else ''})"
            for name, prs in zip(names, ranked)
        ]
        print(f"\n{'='*60}")
        print(f"Resume line (repos with {args.min_stars}+ stars):")
        stars = [prs[0]["repository"]["stargazerCount"] for prs in ranked]
        notable = [name for name, n in zip(names, stars) if n >= args.min_stars]
        print(f"  Accepted PRs in {join_english(notable)}")
        print("Detailed (all repos):")
        print(f"  Accepted PRs in {join_english(details)}")


def format_stars(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def join_english(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f", and {items[-1]}"


if __name__ == "__main__":
    main()
