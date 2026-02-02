# %%
import os
import json
import subprocess
import aiohttp
import asyncio
from tqdm import tqdm
from time import time

import pandas as pd
import __main__ as main_to_check_for_interactive_mode_only

# make it run in both interactive and non-interactive mode
if hasattr(main_to_check_for_interactive_mode_only, '__file__'):
    from IPython.display import display
    try:
        assert display
    except AssertionError as exc:
        raise ValueError("display failed to import") from exc
    INTERACTIVE = True
else:
    display = print  # If not in interactive mode, display is equivalent to print
    INTERACTIVE = False

GITHUB_CACHE = os.path.expanduser('~/.github_cache.csv')
GITHUB_TOKEN = os.path.expanduser('~/.github_token.csv')

# %%
async def test_response(url):
    async with aiohttp.ClientSession(headers=headers) as session:
        response = await session.get(url, headers=headers)
        return await response.json()

if not os.path.exists(GITHUB_TOKEN):
    print(f"Please create a file {GITHUB_TOKEN} with your GitHub token")
    raise FileNotFoundError(GITHUB_TOKEN)
token = open(GITHUB_TOKEN, 'r').read().strip()
headers = {
    "Authorization": f"token {token}",
    'X-GitHub-Api-Version': '2022-11-28'
}
# test_url = "https://api.github.com/repos/multiformats/multihash/git/ref/tags/2.0.1"
# print(f"{INTERACTIVE=}")
# if INTERACTIVE:
#     response = await test_response(test_url)
# else:
#     response = asyncio.run(test_response(test_url))
# print(f"{response=}")

# %%

async def async_get_github_commit_message(session, repo, tag, retries=5, timeout=0.2):
    if not repo:
        return None
    for _ in range(1,retries+1):
        try:
            url = f'{repo}/git/ref/tags/{tag}'
            async with session.get(url, timeout=timeout) as response:
                data = await response.json()
                if 'object' not in data:
                    # print(f'Warning: No object found for version {tag} at {url}')
                    return None
                commit_sha = data['object']['sha']
            async with session.get(f'{repo}/git/commits/{commit_sha}', timeout=timeout) as response:
                data = await response.json()
                commit_message = data['message'].strip()
                commit_message = commit_message.split('\n', 1)[0]
        except (aiohttp.ClientError, asyncio.TimeoutError):
            continue
        return commit_message

def get_repo(data, package, cache):
    # If the package is in the cache, return the cached URL
    if package in cache:
        return cache[package]
    if 'project_urls' in data['info']:
        for _, repo in data['info']['project_urls'].items():
            if 'github.com' in repo.lower():
                return parse_repo(repo, cache, package)
    return None

def parse_repo(repo, cache, package):
    if repo.endswith('/'):
        repo = repo[:-1]
    parts = repo.split('/')
    repo = parts[0] + '/' + parts[1] + '/api.' + parts[2] + '/repos/' + parts[3] + '/' + parts[4]
    cache[package] = repo
    return repo

async def async_get_release_date(semaphore, session, package, cache, retries=5, timeout=0.2):
    async with semaphore:
        query_start = time()
        for attempt in range(1,retries+1):
            try:  # Make a request to PyPi's JSON API for the package
                async with session.get(f'https://pypi.org/pypi/{package}/json',timeout=timeout) as response:
                    response.raise_for_status()
                    data = await response.json()  # Parse the JSON response
                    latest_version = data['info']['version']  # Get the release date of the latest version
                    latest_release = data['releases'][latest_version][-1]  # Get the release date of the latest version
                    commit_message = await async_get_github_commit_message(session, get_repo(data, package, cache), latest_version)
                    return {'Package': package, 'Latest Version': latest_version, 'Release Date': latest_release['upload_time'], 'Query Duration': time() - query_start, 'Attempt': attempt, "Commit Mesage": commit_message}
            except (aiohttp.ClientError, asyncio.TimeoutError):
                continue
        return {'Package': package, 'Latest Version': 'not found', 'Release Date': 'not found', 'Query Duration': time() - query_start, 'Attempt': retries, "Commit Mesage": "not found"}

async def async_get_release_dates(package_names, headers, concurrency=5):
    if os.path.exists(GITHUB_CACHE):
        with open(GITHUB_CACHE, 'r', encoding="utf-8") as file:
            cache = json.load(file)
    else:
        cache = {}
    print(f"cache has {len(cache)} entries at the start")
    # Create a Semaphore for the specified concurrency
    semaphore = asyncio.Semaphore(concurrency)
    total_start = time()
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [
            async_get_release_date(semaphore, session, package, cache)
            for package in package_names
        ]
        results = [await future for future in tqdm(asyncio.as_completed(tasks), total=len(tasks))]
    df = pd.DataFrame(results).sort_values('Release Date', ascending=False)
    print(f'Total time: {time() - total_start} seconds')

    print(f"cache has {len(cache)} entries at the end")
    with open(GITHUB_CACHE, 'w', encoding="utf-8") as file:
        json.dump(cache, file)
    return df

async def get_release_dates(pip, short=False):
    installed_packages = subprocess.check_output([pip, 'list']).decode().split('\n')[2:-1]
    package_names = [pkg.split()[0] for pkg in installed_packages]
    df = await async_get_release_dates(package_names, headers)
    return get_table(df, short)

def get_table(df, short=False):
    msg = "```\n"  # Start a code block
    if short is True:
        msg += f"{'Package':<20} {'Latest Version':<15} {'Release Date':<15} {'Commit Message':<20}\n"  # Add the headers
        for _, row in df[df['Release Date'] != 'not found'].head(5).iterrows():
            msg += f"{str(row['Package']):<20} {str(row['Latest Version']):<15} {str(row['Release Date']):<15} {str(row['Commit Mesage']):<20}\n"  # Add each row of data
    else:
        msg += f"{'Package':<20} {'Latest Version':<15} {'Release Date':<15} {'Query Duration':<15} {'Attempt':<10} {'Commit Message':<20}\n"  # Add the headers
        for _, row in df[df['Release Date'] != 'not found'].head(5).iterrows():
            msg += f"{str(row['Package']):<20} {str(row['Latest Version']):<15} {str(row['Release Date']):<15} {str(row['Query Duration']):<15} {str(row['Attempt']):<10} {str(row['Commit Mesage']):<20}\n"  # Add each row of data
    msg += "```"  # End the code block
    return msg

async def main():
    elf_env_pip = "/home/mihai/.pyenv/versions/elf-env/bin/pip"
    msg = await get_release_dates(elf_env_pip)
    print(msg)

if __name__ == '__main__':
    asyncio.run(main())
