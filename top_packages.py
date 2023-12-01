import os
import subprocess
import tempfile
import time

import polars as pl
import requests
from google.cloud import bigquery


def get_top_packages_from_bigquery(client, limit=100):
    query = """
    SELECT file.project as package_name, COUNT(*) as download_count
    FROM `bigquery-public-data.pypi.file_downloads`
    WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    GROUP BY file.project
    ORDER BY download_count DESC
    LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("limit", "INT64", limit)
        ]
    )
    query_job = client.query(query, job_config=job_config)

    return [row.package_name for row in query_job], [row.download_count for row in query_job]

def check_compatibility_with_pypi(package_name, python_version='3.12'):
    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        for version, files in data.get("releases", {}).items():
            for file in files:
                if file["packagetype"] == "bdist_wheel" and f"cp{python_version.replace('.', '')}" in file["filename"]:
                    return True
        return False
    except requests.RequestException as e:
        print(f"Error checking package {package_name}: {e}")
        return None

def check_compatibility_with_pip(package_name, python_version='3.12'):
    # Create a temporary directory to download the files
    with tempfile.TemporaryDirectory() as download_dir:
        command = f"pip download {package_name} --python-version {python_version} --only-binary=:all: --no-deps -d {download_dir}"
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
    
        # The presence of a wheel file in the temporary directory indicates compatibility
        return result.returncode == 0

def main():
    client = bigquery.Client()

    # Load or fetch top packages
    if os.path.exists("top_packages.csv"):
        pkgs_df = pl.read_csv("top_packages.csv")
    else:
        start_time = time.time()
        top_packages, download_counts = get_top_packages_from_bigquery(client)
        print(f"Got top packages in: {time.time() - start_time:.2f} seconds")
        pkgs_df = pl.DataFrame({"package": top_packages, "download_count": download_counts})
        pkgs_df.write_csv("top_packages.csv")

    # Check compatibility
    python_version = "3.12"
    compatibility_results = []
    for package in pkgs_df["package"]:
        is_compatible = check_compatibility_with_pip(package, python_version)
        compatibility_results.append(is_compatible)
        download_count = pkgs_df.filter(pl.col("package") == package)["download_count"].item()
        print(f"{package}: {'Yes' if is_compatible else 'No'} ({download_count:,.0f} downloads)")
    
    pkgs_df = pkgs_df.with_columns(pl.Series("compatible", compatibility_results))

    # Save results to csv
    pkgs_df.write_csv("top_packages.csv")

    # Count and print results
    compatible_count = pkgs_df.filter(pl.col("compatible") == True).shape[0]
    total_count = pkgs_df.shape[0]
    print(f"Compatible packages: {compatible_count}/{total_count} ({compatible_count / total_count * 100:.2f}%)")
    compatible_downloads = pkgs_df.filter(pl.col("compatible") == True)["download_count"].sum()
    total_downloads = pkgs_df["download_count"].sum()
    print(f"  by download count: {compatible_downloads:,.0f}/{total_downloads:,.0f} ({compatible_downloads / total_downloads * 100:.2f}%)")

if __name__ == "__main__":
    main()