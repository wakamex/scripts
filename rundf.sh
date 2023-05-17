#/bin/zsh
export PROTON_DUMP_DEBUG_COMMANDS=1
export PROTON_LOG=1
export STEAM_COMPAT_DATA_PATH='/home/mihai/.local/share/Steam/steamapps/compatdata'
export STEAM_COMPAT_CLIENT_INSTALL_PATH='/home/mihai/.local/share/Steam/'

# set proton command
export PROTON=/nvme/SteamLibrary/steamapps/common/Proton\ 8.0/proton

# set game path
export GAME_PATH=/nvme/SteamLibrary/steamapps/common/Dwarf\ Fortress

# set game executable
# export GAME_EXEC=AOW4.exe
export GAME_EXEC=Dwarf\ Fortress.exe

# set game arguments
export GAME_ARGS=

# set game proton options
export PROTON_OPTIONS="PROTON_HIDE_NVIDIA_GPU=0 PROTON_ENABLE_NVAPI=1"

# df-specific issue (http://www.bay12games.com/dwarves/mantisbt/view.php?id=2688)
export LD_PRELOAD=/usr/lib/libz.so.1

# print the command
echo $PROTON run $GAME_PATH/$GAME_EXEC $GAME_ARGS $PROTON_OPTIONS

# run the game
$PROTON run $GAME_PATH/$GAME_EXEC $GAME_ARGS $PROTON_OPTIONS

# /home/mihai/.local/share/Steam/steamapps/common/Proton\ -\ Experimental/proton run /nvme /SteamLibrary /steamapps /common /Age\ of\ Wonders\ 4 /AOW4.exe
# export STEAM_COMPAT_DATA_PATH='/nvme/SteamLibrary/steamapps/compatdata'
# export STEAM_COMPAT_CLIENT_INSTALL_PATH='/nvme/SteamLibrary/'

# /nvme/SteamLibrary/steamapps/common/Proton\ 8.0/proton run /nvme /SteamLibrary /steamapps /common /Age\ of\ Wonders\ 4 /AOW4.exe

# ./proton run /nvme /SteamLibrary /steamapps /common /Age\ of\ Wonders\ 4 /AOW4.exe

# STEAM LAUNCH OPTIONS
# PROTON_HIDE_NVIDIA_GPU=0 PROTON_ENABLE_NVAPI=1 gamemoderun %command%
# VKD3D_CONFIG=no_upload_hvv %command%
