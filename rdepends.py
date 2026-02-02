# %%
import pkg_resources
import sys

def find_reverse_deps(package_name):
    return [
        pkg.project_name for pkg in pkg_resources.WorkingSet()
        if package_name in {req.project_name for req in pkg.requires()}
    ]

if __name__ == '__main__':
    print(find_reverse_deps(sys.argv[1]))

# %%