#!/bin/zsh

# log
export PROTON_LOG=1

# python
PYTHON_ENV_PATH="$(pyenv prefix)"
PYTHON_VERSION="$(python -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"

export PYTHONHOME=$PYTHON_ENV_PATH
export PYTHONPATH=$PYTHONHOME/lib/python$PYTHON_VERSION/site-packages

export STEAM_COMPAT_DATA_PATH='/home/mihai/.local/share/Steam/steamapps/compatdata'
export STEAM_COMPAT_CLIENT_INSTALL_PATH='/home/mihai/.local/share/Steam/'
export PROTON_HIDE_NVIDIA_GPU=0
export PROTON_ENABLE_NVAPI=1
# /home/mihai/.local/share/Steam/ubuntu12_32/reaper SteamLaunch AppId=1044720 -- /home/mihai/.local/share/Steam/ubuntu12_32/steam-launch-wrapper -- '/home/mihai/.local/share/Steam/steamapps/common/SteamLinuxRuntime_sniper'/_v2-entry-point --verb=waitforexitandrun -- '/home/mihai/.local/share/Steam/steamapps/common/Proton - Experimental'/proton waitforexitandrun  '/home/mihai/.local/share/Steam/steamapps/common/Farthest Frontier/Farthest Frontier.exe'

# steam -applaunch 1044720
steam -applaunch 1716740