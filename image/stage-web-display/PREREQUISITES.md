This stage targets Raspberry Pi OS Trixie armhf and is built from the exact
pi-gen commit recorded in `build-image.sh`. The selected official compatibility
baseline is Raspberry Pi OS Lite dated 2026-09-15:

`c766b3fb279b95c12cb4dd22d06f8eab31972c372675d05bd0ca95b060523a7f`

The image is assembled from the matching supported repositories rather than
modifying that binary image. Package versions are recorded in the generated
image manifest and must be retained with release artifacts.

