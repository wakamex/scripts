# %%
import os

# %%
# grab $PATH
print(os.environ['PATH'])
path = [os.path.normpath(p) for p in os.environ['PATH'].split(':')]
for p in path:
    print(p)

# %%
# remove duplicates via a dictionary
clean = dict.fromkeys(path)
for p in clean:
    print(p)

# %%
# combine back into one path
clean_path = ':'.join(clean.keys())

# dump to stdout
print(f"PATH={clean_path}")
# %% oneline as fuck
print(':'.join(dict.fromkeys([os.path.normpath(p) for p in os.environ['PATH'].split(':')]).keys()))
# %% copilot suggestion
print(':'.join(set(os.environ['PATH'].split(':'))))
# %%
