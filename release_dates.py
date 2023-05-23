# %%
import subprocess
import json
import requests
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

# Use pip to get list of installed packages
installed_packages = subprocess.check_output(['pip', 'list']).decode().split('\n')[2:-1]

# Parse the output to get package names
package_names = [pkg.split()[0] for pkg in installed_packages]

# Initialize an empty DataFrame
df = pd.DataFrame(columns=['Package', 'Release Date'])

records = []
# Loop through all packages
for package in package_names:
    # Make a request to PyPi's JSON API for the package
    response = requests.get(f'https://pypi.org/pypi/{package}/json')

    # If the response status is not 200, the package was not found on PyPi
    if response.status_code != 200:
        print(f'Package {package} not found on PyPi.')
        continue

    # Parse the JSON response
    data = json.loads(response.text)

    # Get the release date of the latest version
    latest_version = data['info']['version']
    release_date = data['releases'][data['info']['version']][0]['upload_time']

    # Add the package and release date to the DataFrame
    # df = df.append({'Package': package, 'Latest Version': latest_version, 'Release Date': release_date}, ignore_index=True)
    records.append({'Package': package, 'Latest Version': latest_version, 'Release Date': release_date})

# Print the DataFrame
df = pd.DataFrame(records).sort_values(by='Release Date', ascending=False)

# %%
df_str = df.head(10).to_string(index=False)
display(df_str)
