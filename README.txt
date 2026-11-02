Pico 2 W / OpenAI Daybreak 调研与实验构建（2026-11-02）

结论
Pico FIDO 可以实现 FIDO2 / WebAuthn。现有公开反馈不能证明 Pico 2 W
能稳定满足 OpenAI Daybreak 的合格硬件密钥要求。
Pico FIDO 作者报告完成了高级账户安全设置，但未报告完成 Daybreak 的独立
硬件密钥检查；另有 Pico 2 W 用户在主分支 + EdDSA + Brave 下仍未通过。
FIDO MDS / 证书链是社区提出的可能原因，尚非已确认的 OpenAI 拒绝原因。

重新进入烧录模式
1. 拔掉 USB。
2. 按住板上 BOOTSEL，保持按住并插回 USB。
3. 看到 RP2350 磁盘后松开。
4. 将适用于 Pico 2 W 的 UF2 复制进去，等待设备自动重启。
进入 BOOTSEL 模式本身不删除现有数据，也不需要 flash_nuke。

构建
源码：firmware/source（包含修改后的 Pico FIDO 与 PicoKeys SDK 源码）
Pico FIDO commit：b1df38dbab49b7365282e1bad12f0c38862cf643
PicoKeys SDK commit：50699e53e8ada214c27f6c9b66ea3b6f127fc655
Pico SDK 2.3.1 commit：079c6f39023649b154152db30f1d781e884879bc
源码修改：CYW43 LED 驱动编号改为无条件定义；启用 FORCE_BUTTON_WAIT 时，
设置缺省按键等待时间为 30 秒；补齐未传 PIN 授权参数时创建凭据的按键确认。
完整改动保存在 patches/，分别对应 LED、默认等待时间、注册时用户在场检查。
配置和构建：bash build-pico2w.sh
构建目录为 build/pico2w/，编译器临时目录为 .tmp/；Pico SDK 默认位于
.deps/pico-sdk。也可通过 PICO_SDK_PATH 指向其他安装位置。
输出固件标记 EXPERIMENTAL。板上测试进度与结果见 build-info.json 和 logs/；
烧录脚本为 python3 flash-pico2w.py，只写入已挂载的 RP2350 烧录盘。
完成 PIN、物理按键确认和拒绝无按键操作的测试后，再测试真实账户兼容性。
保留 PicoKeys 默认 USB 身份与 attestation。没有烧写 OTP 或启用 Secure Lock。

本地完成 PIN 与签名测试
在本地终端运行：
.venv/bin/python complete-key-setup.py
PIN 由 getpass 隐藏输入，不写入日志。脚本设置并验证 PIN，再分别执行受 PIN
保护的注册和登录签名验证。每轮先按 Enter 开始，再短按 BOOTSEL；超时可重试。
测试使用本地 .invalid 站点和临时非驻留凭据，不访问真实网站或 OpenAI 账号。
结果保存在 logs/complete-key-setup.json；完整通过后会更新 build-info.json。
日志和本机状态不提交到 Git。可发布的构建信息见 firmware/build-manifest.json。

OpenAI 规则与更换密钥
官方说明支持添加和删除 passkeys。Daybreak 个人资格持续要求高级账户安全
和至少一把合格的物理 FIDO2 密钥。高级账户安全通常要求两种安全登录方式，
其中一种可跨设备使用。Daybreak 的第二把物理钥匙可选；可以保留其他 passkeys。
换钥匙时，先注册新钥匙、验证登录并检查 Daybreak 资格，再删除旧钥匙。
关闭高级账户安全或删除最后一把合格钥匙可能失去 Daybreak 资格，恢复后
可能需要重新身份验证和审核。不要假定使用真钥匙申请后只保留 Pico 仍能用。

来源
https://github.com/polhenarejos/pico-fido/issues/304
https://github.com/TheMaxMur/RS-Key/issues/128
https://help.openai.com/en/articles/20001039-passkeys-to-secure-your-openai-account
https://help.openai.com/en/articles/20001258-openai-daybreak-trusted-access-for-cyber-overview
https://help.openai.com/en/articles/20001259-troubleshooting-openai-daybreak
https://www.yubico.com/works-with-yubikey/catalog/openai/
https://www.picokeys.com/getting-started/
