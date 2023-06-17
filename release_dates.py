# %%
import subprocess
import aiohttp
from rich.progress import Progress
import asyncio

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

async def async_get_release_date(semaphore, session, progress, task_id, package):
    async with semaphore:
        # Make a request to PyPi's JSON API for the package
        async with session.get(f'https://pypi.org/pypi/{package}/json') as response:
            # If the response status is not 200, the package was not found on PyPi
            if response.status != 200:
                print(f'Package {package} not found on PyPi.')
                return {'Package': package, 'Latest Version': 'not found', 'Release Date': 'not found'}

            # Parse the JSON response
            data = await response.json()

            # Get the release date of the latest version
            latest_version = data['info']['version']
            release_date = data['releases'][data['info']['version']][0]['upload_time']

            # Update the progress bar
            progress.update(task_id, advance=1)

            return {'Package': package, 'Latest Version': latest_version, 'Release Date': release_date}

async def async_get_release_dates(package_names, concurrency=5):
    # Create a Semaphore for the specified concurrency
    semaphore = asyncio.Semaphore(concurrency)
    progress = Progress()
    progress.start()

    async with aiohttp.ClientSession() as session:
        tasks = []
        for package in package_names:
            task_id = progress.add_task(f"[cyan]{package}...", total=1)
            tasks.append(async_get_release_date(semaphore, session, progress, task_id, package))

        results = await asyncio.gather(*tasks)

    progress.stop()

    return pd.DataFrame(results).sort_values('Release Date', ascending=False)


async def main():
    # Use pip to get list of installed packages
    installed_packages = subprocess.check_output(['pip', 'list']).decode().split('\n')[2:-1]

    # Parse the output to get package names
    package_names = [pkg.split()[0] for pkg in installed_packages]

    # get the release dates
    df = await async_get_release_dates(package_names)
    print(f"{df=}")

if __name__ == '__main__':
    asyncio.run(main())
