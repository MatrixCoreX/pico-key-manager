#!/usr/bin/env bash
set -euo pipefail

task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_build_dir="${YUNBIKEY_BUILD_DIR:-$task_root/build/pico2w}"
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
cp -- "$task_build_dir/pico_fido.uf2" "$task_root/firmware/pico_fido_pico2_w_EXPERIMENTAL.uf2"
echo 'Built experimental firmware; hardware behavior and OpenAI Daybreak eligibility still require testing.'
