# Font Awesome Typst package

`fontawesome-0.6.0.tar.gz` is the unmodified package distributed by Typst at
https://packages.typst.org/preview/fontawesome-0.6.0.tar.gz.

Upstream: https://github.com/duskmoon314/typst-fontawesome
License: MIT; reproduced in `fontawesome-LICENSE`.
SHA-256: `31374c788d6850c7fd3da742a67e8a753e5644fa0c677b845f75be8aa488380c`.

RenderCV 2.8 imports this exact package. Vendoring it lets rendering run without
network access or a writable shared cache. The renderer verifies the digest and
copies only the fixed package source files into each disposable render directory.
Font files are provided by the `rendercv-fonts` Python dependency.
