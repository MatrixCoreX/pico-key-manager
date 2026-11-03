#!/usr/bin/env bash
set -euo pipefail

task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_profile="${1:-native}"
case "$task_profile" in
    native)
        task_vidpid=None
        task_filename=pico_fido_pico2_w_EXPERIMENTAL.uf2
        task_default_build="$task_root/build/pico2w"
        ;;
    yubikey-compat)
        task_vidpid=Yubikey5
        task_filename=pico_fido_pico2_w_YUBIKEY_USB_COMPAT_EXPERIMENTAL.uf2
        task_default_build="$task_root/build/pico2w-yubikey-compat"
        ;;
    usb-repair)
        task_vidpid=None
        task_filename=pico_fido_pico2_w_USB_REPAIR_EXPERIMENTAL.uf2
        task_default_build="$task_root/build/pico2w-usb-repair"
        ;;
    *)
        echo 'Usage: bash build-pico2w.sh [native|yubikey-compat|usb-repair]' >&2
        exit 2
        ;;
esac
task_build_dir="${YUNBIKEY_BUILD_DIR:-$task_default_build}"
export PICO_SDK_PATH="${PICO_SDK_PATH:-$task_root/.deps/pico-sdk}"
firmware_source="$task_root/firmware/source"
mkdir -p "$task_root/.tmp"
export TMPDIR="$task_root/.tmp"

if [[ ! -f "$PICO_SDK_PATH/pico_sdk_init.cmake" ]]; then
    echo 'Set PICO_SDK_PATH to an installed Raspberry Pi Pico SDK (tested: 2.3.1).' >&2
    exit 1
fi

cmake -S "$firmware_source" -B "$task_build_dir" -G Ninja \
    -DPICO_BOARD=pico2_w \
    -DPICO_PLATFORM=rp2350-arm-s \
    "-DVIDPID:STRING=$task_vidpid" \
    -DCMAKE_BUILD_TYPE=Release \
    '-DCMAKE_C_FLAGS_RELEASE=-g -O3 -DNDEBUG' \
    '-DCMAKE_CXX_FLAGS_RELEASE=-g -O3 -DNDEBUG' \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DENABLE_EDDSA=ON \
    -DFORCE_BUTTON_WAIT=ON \
    -DENABLE_OATH_APP=OFF \
    -DENABLE_OTP_APP=OFF \
    -DENABLE_PQC=OFF \
    -DENABLE_LIBCVC=OFF
cmake --build "$task_build_dir" --parallel "${YUNBIKEY_BUILD_JOBS:-4}"
mkdir -p "$task_root/firmware"
cp -- "$task_build_dir/pico_fido.uf2" "$task_root/firmware/$task_filename"
python3 "$task_root/record-firmware.py" "$task_profile"
echo 'Built experimental firmware; hardware behavior and OpenAI Daybreak eligibility still require testing.'
