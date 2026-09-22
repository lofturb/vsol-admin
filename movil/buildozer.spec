[app]
title = VSOL Admin
package.name = vsoladmin
package.domain = org.vsol
source.dir = .
source.include_exts = py,png,jpg,kv
source.exclude_dirs = tests, bin, .buildozer
version = 0.3.0
requirements = python3==3.11.9,hostpython3==3.11.9,kivy==2.3.0
orientation = portrait
fullscreen = 0
android.permissions = INTERNET
android.archs = arm64-v8a
android.api = 27
android.minapi = 21
android.ndk = 25b
android.sdk = 34
android.build_tools = 34.0.0
android.accept_sdk_license = True
android.extra_manifest_application_arguments =
icon.filename = icon.png
presplash.filename = icon.png

[buildozer]
log_level = 2
warn_on_root = 1