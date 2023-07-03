#!/bin/bash

set -o errexit
set -o pipefail
set -o nounset

# Constants
c_zfs_mount_dir=/mnt
c_deb_packages_repo=http://mirror.hetzner.de/ubuntu/packages
c_deb_security_repo=http://mirror.hetzner.de/ubuntu/security
v_rpool_name=rpool
v_bpool_name=bpool
v_hostname=reth
v_kernel_variant=-generic  #-generic or -virtual
v_zfs_arc_max_mb=1024
v_root_password=changeme
v_selected_disks=("/dev/nvme0n1" "/dev/nvme1n1")

function chroot_execute {
  chroot $c_zfs_mount_dir bash -c "$1"
}

function print_step_info_header {
  echo -n "
###############################################################################
# ${FUNCNAME[1]}"

  if [[ "${1:-}" != "" ]]; then
    echo -n " $1" 
  fi

  echo "
###############################################################################
"
}

function unmount_and_export_fs {
    print_step_info_header

    for virtual_fs_dir in dev sys proc; do
    umount --recursive --force --lazy "$c_zfs_mount_dir/$virtual_fs_dir"
    done

    local max_unmount_wait=5
    echo -n "Waiting for virtual filesystems to unmount "

    SECONDS=0

    for virtual_fs_dir in dev sys proc; do
    while mountpoint -q "$c_zfs_mount_dir/$virtual_fs_dir" && [[ $SECONDS -lt $max_unmount_wait ]]; do
        sleep 0.5
        echo -n .
    done
    done

    echo

    for virtual_fs_dir in dev sys proc; do
    if mountpoint -q "$c_zfs_mount_dir/$virtual_fs_dir"; then
        echo "Re-issuing umount for $c_zfs_mount_dir/$virtual_fs_dir"
        umount --recursive --force --lazy "$c_zfs_mount_dir/$virtual_fs_dir"
    fi
    done

    SECONDS=0
    zpools_exported=99
    echo "===========exporting zfs pools============="
    set +e
    while (( zpools_exported == 99 )) && (( SECONDS++ <= 60 )); do

    if zpool export -a 2> /dev/null; then
        zpools_exported=1
        echo "all zfs pools were succesfully exported"
        break;
    else
        sleep 1
        fi
    done
    set -e
    if (( zpools_exported != 1 )); then
    echo "failed to export zfs pools"
    exit 1
    fi
}

echo "======= partitioning the disk =========="
for selected_disk in "${v_selected_disks[@]}"; do
  wipefs --all --force "$selected_disk"               # wipe disk
  sgdisk -a1 -n1:24K:+1000K -t1:EF02 "$selected_disk" # BIOS boot partition
  sgdisk -n2:0:+2G          -t2:BF01 "$selected_disk" # Boot pool
  sgdisk -n3:0:0            -t3:BF01 "$selected_disk" # Root pool
done
udevadm settle

echo "======= create zfs pools and datasets =========="
for selected_disk in "${v_selected_disks[@]}"; do
  rpool_disks_partitions+=("${selected_disk}p3")
  bpool_disks_partitions+=("${selected_disk}p2")
done

zpool create \
  -O canmount=off -O devices=off \
  -o cachefile=/etc/zpool.cache \
  -O mountpoint=/boot -R $c_zfs_mount_dir -f \
  $v_bpool_name "${bpool_disks_partitions[@]}"

zpool create \
  -o cachefile=/etc/zpool.cache \
  -O mountpoint=/ -R $c_zfs_mount_dir -f \
  $v_rpool_name "${rpool_disks_partitions[@]}"

zfs create -o canmount=off -o mountpoint=none "$v_rpool_name/ROOT"
zfs create -o canmount=off -o mountpoint=none "$v_bpool_name/BOOT"

zfs create -o canmount=noauto -o mountpoint=/ "$v_rpool_name/ROOT/ubuntu"
zfs mount "$v_rpool_name/ROOT/ubuntu"

zfs create -o canmount=noauto -o mountpoint=/boot "$v_bpool_name/BOOT/ubuntu"
zfs mount "$v_bpool_name/BOOT/ubuntu"

zfs create                                 "$v_rpool_name/home"
zfs create -o mountpoint=/root             "$v_rpool_name/home/root"
zfs create -o canmount=off                 "$v_rpool_name/var"
zfs create -o canmount=off                 "$v_rpool_name/var/lib"
zfs create                                 "$v_rpool_name/var/log"
zfs create                                 "$v_rpool_name/var/spool"

zfs create -o com.sun:auto-snapshot=false  "$v_rpool_name/var/cache"
zfs create -o com.sun:auto-snapshot=false  "$v_rpool_name/var/tmp"
chmod 1777 "$c_zfs_mount_dir/var/tmp"

zfs create                                 "$v_rpool_name/srv"

zfs create -o canmount=off                 "$v_rpool_name/usr"
zfs create                                 "$v_rpool_name/usr/local"

zfs create                                 "$v_rpool_name/var/mail"

zfs create -o com.sun:auto-snapshot=false -o canmount=on -o mountpoint=/tmp "$v_rpool_name/tmp"
chmod 1777 "$c_zfs_mount_dir/tmp"

echo "======= setting up initial system packages =========="
debootstrap --arch=amd64 jammy "$c_zfs_mount_dir" "$c_deb_packages_repo"

zfs set devices=off "$v_rpool_name"

echo "======= setting up the network =========="

echo "$v_hostname" > $c_zfs_mount_dir/etc/hostname

cat > "$c_zfs_mount_dir/etc/hosts" <<CONF
127.0.1.1 ${v_hostname}
127.0.0.1 localhost

# The following lines are desirable for IPv6 capable hosts
::1 ip6-localhost ip6-loopback
fe00::0 ip6-localnet
ff00::0 ip6-mcastprefix
ff02::1 ip6-allnodes
ff02::2 ip6-allrouters
ff02::3 ip6-allhosts
CONF

ip6addr_prefix=$(ip -6 a s | grep -E "inet6.+global" | sed -nE 's/.+inet6\s(([0-9a-z]{1,4}:){4,4}).+/\1/p')

cat <<CONF > /mnt/etc/systemd/network/10-eth0.network
[Match]
Name=eth0

[Network]
DHCP=ipv4
Address=${ip6addr_prefix}:1/64
Gateway=fe80::1
CONF

chroot_execute "systemctl enable systemd-networkd.service"
chroot_execute "systemctl enable systemd-resolved.service"


mkdir -p "$c_zfs_mount_dir/etc/cloud/cloud.cfg.d/"
cat > "$c_zfs_mount_dir/etc/cloud/cloud.cfg.d/99-disable-network-config.cfg" <<CONF
network:
  config: disabled
CONF

rm -rf $c_zfs_mount_dir/etc/network/interfaces.d/50-cloud-init.cfg

cp /etc/resolv.conf $c_zfs_mount_dir/etc/resolv.conf

echo "======= preparing the jail for chroot =========="
for virtual_fs_dir in proc sys dev; do
  mount --rbind "/$virtual_fs_dir" "$c_zfs_mount_dir/$virtual_fs_dir"
done

echo "======= setting apt repos =========="
cat > "$c_zfs_mount_dir/etc/apt/sources.list" <<CONF
deb [arch=i386,amd64] $c_deb_packages_repo jammy main restricted
deb [arch=i386,amd64] $c_deb_packages_repo jammy-updates main restricted
deb [arch=i386,amd64] $c_deb_packages_repo jammy-backports main restricted
deb [arch=i386,amd64] $c_deb_packages_repo jammy universe
deb [arch=i386,amd64] $c_deb_security_repo jammy-security main restricted
CONF

chroot_execute "apt update"

echo "======= setting locale, console and language =========="
chroot_execute "apt --yes --fix-broken install"
chroot_execute "apt install --yes -qq locales debconf-i18n apt-utils keyboard-configuration console-setup"
locales=(en_US.UTF-8 fr_FR.UTF-8 de_AT.UTF-8 de_DE.UTF-8)
for locale in "${locales[@]}"; do
  sed -i 's/# '"$locale"'/'"$locale"'/' "$c_zfs_mount_dir/etc/locale.gen"
done

chroot_execute 'cat <<CONF | debconf-set-selections
locales locales/default_environment_locale      select  en_US.UTF-8
keyboard-configuration  keyboard-configuration/store_defaults_in_debconf_db     boolean true
keyboard-configuration  keyboard-configuration/variant  select  German
keyboard-configuration  keyboard-configuration/unsupported_layout       boolean true
keyboard-configuration  keyboard-configuration/modelcode        string  pc105
keyboard-configuration  keyboard-configuration/unsupported_config_layout        boolean true
keyboard-configuration  keyboard-configuration/layout   select  German
keyboard-configuration  keyboard-configuration/layoutcode       string  de
keyboard-configuration  keyboard-configuration/optionscode      string
keyboard-configuration  keyboard-configuration/toggle   select  No toggling
keyboard-configuration  keyboard-configuration/xkb-keymap       select  de
keyboard-configuration  keyboard-configuration/switch   select  No temporary switch
keyboard-configuration  keyboard-configuration/unsupported_config_options       boolean true
keyboard-configuration  keyboard-configuration/ctrl_alt_bksp    boolean false
keyboard-configuration  keyboard-configuration/variantcode      string
keyboard-configuration  keyboard-configuration/model    select  Generic 105-key PC (intl.)
keyboard-configuration  keyboard-configuration/altgr    select  The default for the keyboard layout
keyboard-configuration  keyboard-configuration/compose  select  No compose key
keyboard-configuration  keyboard-configuration/unsupported_options      boolean true
console-setup   console-setup/fontsize-fb47     select  8x16
console-setup   console-setup/store_defaults_in_debconf_db      boolean true
console-setup   console-setup/codeset47 select  # Latin1 and Latin5 - western Europe and Turkic languages
console-setup   console-setup/fontface47        select  Fixed
console-setup   console-setup/fontsize  string  8x16
console-setup   console-setup/charmap47 select  UTF-8
console-setup   console-setup/fontsize-text47   select  8x16
console-setup   console-setup/codesetcode       string  Lat15
tzdata tzdata/Areas select Europe
tzdata tzdata/Zones/Europe select Vienna
grub-pc grub-pc/install_devices_empty   boolean true
CONF'

chroot_execute "dpkg-reconfigure locales -f noninteractive"
echo -e "LC_ALL=en_US.UTF-8\nLANG=en_US.UTF-8\n" >> "$c_zfs_mount_dir/etc/environment"
chroot_execute "dpkg-reconfigure keyboard-configuration -f noninteractive"
chroot_execute "dpkg-reconfigure console-setup -f noninteractive"
chroot_execute "setupcon"

chroot_execute "rm -f /etc/localtime /etc/timezone"
chroot_execute "dpkg-reconfigure tzdata -f noninteractive "

echo "======= installing latest kernel============="
chroot_execute "DEBIAN_FRONTEND=noninteractive apt install --yes linux-headers${v_kernel_variant} linux-image${v_kernel_variant}"
if [[ $v_kernel_variant == "-virtual" ]]; then
  # linux-image-extra is only available for virtual hosts
  chroot_execute "DEBIAN_FRONTEND=noninteractive apt install --yes linux-image-extra-virtual"
fi


echo "======= installing aux packages =========="
chroot_execute "apt install --yes man-db wget curl software-properties-common nano htop gnupg"
chroot_execute "systemctl disable thermald"

echo "======= installing zfs packages =========="
chroot_execute 'echo "zfs-dkms zfs-dkms/note-incompatible-licenses note true" | debconf-set-selections'

chroot_execute "add-apt-repository --yes ppa:jonathonf/zfs"
chroot_execute "apt install --yes zfs-initramfs zfs-dkms zfsutils-linux"
chroot_execute 'cat << DKMS > /etc/dkms/zfs.conf
# override for /usr/src/zfs-*/dkms.conf:
# always rebuild initrd when zfs module has been changed
# (either by a ZFS update or a new kernel version)
REMAKE_INITRD="yes"
DKMS'

echo "======= installing OpenSSH and network tooling =========="
chroot_execute "apt install --yes openssh-server net-tools"

echo "======= setup OpenSSH  =========="
mkdir -p "$c_zfs_mount_dir/root/.ssh/"
cp /root/.ssh/authorized_keys "$c_zfs_mount_dir/root/.ssh/authorized_keys"
sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/g' "$c_zfs_mount_dir/etc/ssh/sshd_config"
sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/g' "$c_zfs_mount_dir/etc/ssh/sshd_config"
chroot_execute "rm /etc/ssh/ssh_host_*"
chroot_execute "dpkg-reconfigure openssh-server -f noninteractive"

echo "======= set root password =========="
chroot_execute "echo root:$(printf "%q" "$v_root_password") | chpasswd"

echo "======= setting up zfs cache =========="
if [ -f /etc/zpool.cache ]; then
    cp /etc/zpool.cache /mnt/etc/zfs/zpool.cache
else
    echo "/etc/zpool.cache does not exist, skipping copy."
fi

echo "========setting up zfs module parameters========"
chroot_execute "echo options zfs zfs_arc_max=$((v_zfs_arc_max_mb * 1024 * 1024)) >> /etc/modprobe.d/zfs.conf"

echo "======= setting up grub =========="
chroot_execute "echo 'grub-pc grub-pc/install_devices_empty   boolean true' | debconf-set-selections"
chroot_execute "DEBIAN_FRONTEND=noninteractive apt install --yes grub-pc"
for disk in "${v_selected_disks[@]}"; do
    chroot_execute "grub-install $disk"
done

chroot_execute "sed -i 's/#GRUB_TERMINAL=console/GRUB_TERMINAL=console/g' /etc/default/grub"
chroot_execute "sed -i 's|GRUB_CMDLINE_LINUX_DEFAULT=.*|GRUB_CMDLINE_LINUX_DEFAULT=\"net.ifnames=0\"|' /etc/default/grub"
chroot_execute "sed -i 's|GRUB_CMDLINE_LINUX=\"\"|GRUB_CMDLINE_LINUX=\"root=ZFS=rpool/ROOT/ubuntu\"|g' /etc/default/grub"

chroot_execute "sed -i 's/quiet//g' /etc/default/grub"
chroot_execute "sed -i 's/splash//g' /etc/default/grub"
chroot_execute "echo 'GRUB_DISABLE_OS_PROBER=true'   >> /etc/default/grub"

echo "============setup root prompt============"
cat > "$c_zfs_mount_dir/root/.bashrc" <<CONF
export PS1='\[\033[01;31m\]\u\[\033[01;33m\]@\[\033[01;32m\]\h \[\033[01;33m\]\w \[\033[01;35m\]\$ \[\033[00m\]'
umask 022
export LS_OPTIONS='--color=auto -h'
eval "\$(dircolors)"
CONF

echo "========running packages upgrade==========="
chroot_execute "apt upgrade --yes"

echo "===========add static route to initramfs via hook to add default routes due to Ubuntu initramfs DHCP bug ========="
mkdir -p "$c_zfs_mount_dir/usr/share/initramfs-tools/scripts/init-premount"
cat > "$c_zfs_mount_dir/usr/share/initramfs-tools/scripts/init-premount/static-route" <<'CONF'
#!/bin/sh
PREREQ=""
prereqs()
{
    echo "$PREREQ"
}

case $1 in
prereqs)
    prereqs
    exit 0
    ;;
esac

. /scripts/functions
# Begin real processing below this line

configure_networking

ip route add 172.31.1.1/255.255.255.255 dev ens3
ip route add default via 172.31.1.1 dev ens3
CONF

chmod 755 "$c_zfs_mount_dir/usr/share/initramfs-tools/scripts/init-premount/static-route"

echo "======= update initramfs =========="
chroot_execute "update-initramfs -u -k all"

echo "======= update grub =========="
chroot_execute "update-grub"

echo "======= setting up zed =========="
chroot_execute "zfs set canmount=noauto rpool"

echo "======= setting mountpoints =========="
chroot_execute "zfs set mountpoint=legacy $v_bpool_name/BOOT/ubuntu"
chroot_execute "zfs set readonly=off $v_bpool_name/BOOT/ubuntu"
chroot_execute "echo $v_bpool_name/BOOT/ubuntu /boot zfs nodev,relatime,x-systemd.requires=zfs-mount.service,x-systemd.device-timeout=10 0 0 > /etc/fstab"

chroot_execute "zfs set mountpoint=legacy $v_rpool_name/var/log"
chroot_execute "echo $v_rpool_name/var/log /var/log zfs nodev,relatime 0 0 >> /etc/fstab"
chroot_execute "zfs set mountpoint=legacy $v_rpool_name/var/spool"
chroot_execute "echo $v_rpool_name/var/spool /var/spool zfs nodev,relatime 0 0 >> /etc/fstab"
chroot_execute "zfs set mountpoint=legacy $v_rpool_name/var/tmp"
chroot_execute "echo $v_rpool_name/var/tmp /var/tmp zfs nodev,relatime 0 0 >> /etc/fstab"
chroot_execute "zfs set mountpoint=legacy $v_rpool_name/tmp"
chroot_execute "echo $v_rpool_name/tmp /tmp zfs nodev,relatime 0 0 >> /etc/fstab"

chroot_execute "echo RESUME=none > /etc/initramfs-tools/conf.d/resume"

echo "======= unmounting filesystems and zfs pools =========="
unmount_and_export_fs

echo "======== setup complete, ask to reboot ==============="
echo "Success: Setup complete, please reboot"
