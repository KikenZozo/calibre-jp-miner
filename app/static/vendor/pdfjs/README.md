# pdf.js (vendored)

Mozilla pdf.js **v4.6.82**, legacy build, Apache-2.0.
Source: https://github.com/mozilla/pdf.js/releases/tag/v4.6.82

Vendored files:

| Path               | From the dist zip     | Why                                                      |
|--------------------|-----------------------|----------------------------------------------------------|
| `pdf.mjs`          | `build/pdf.mjs`       | the renderer, imported dynamically by `app.js`            |
| `pdf.worker.mjs`   | `build/pdf.worker.mjs`| parsing/rasterising worker                                 |
| `cmaps/`           | `web/cmaps/`          | CID-keyed Adobe-Japan1 encodings — Japanese PDFs render blank without them |
| `standard_fonts/`  | `web/standard_fonts/` | substitutes for PDFs that embed no font                    |

They are served locally rather than from a CDN so the reader keeps working on a
self-hosted box with no outbound internet access, and so PDF rendering can never
change under the app without a commit here.

To upgrade: download `pdfjs-<version>-legacy-dist.zip` from the releases page and
copy those four paths over these.
